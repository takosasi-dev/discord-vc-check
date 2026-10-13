# 出力先の確認、JSON の読み込み、原子的な書き出し(UTF-8・BOM なし・LF。INV-12)、
# 共有用のマスク(デバイス名・ユーザー名・パス・ホスト名・IP アドレス。INV-10)。
# 既にあるファイルは上書きしない。失敗したら一時ファイルを消して何も残さない。
from __future__ import annotations

import json
import os
import platform
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NoReturn

MASK_INPUT: Final = "入力デバイスA"
MASK_OUTPUT: Final = "出力デバイスA"
MASK_USER: Final = "(ユーザー名)"
MASK_HOST: Final = "(ホスト名)"
MASK_PATH: Final = "(パス)"
MASK_IP: Final = "(IPアドレス)"

_USER_IN_PATH: Final = re.compile(r"[\\/]Users[\\/]([^\\/\s|:*?\"<>]+)", re.IGNORECASE)
_PATH: Final = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)[^\s|*?\"<>]*")
_IPV4: Final = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_IPV6: Final = re.compile(
    r"(?<![0-9A-Fa-f:])(?:(?:[0-9A-Fa-f]{1,4}:){3,7}[0-9A-Fa-f]{1,4}"
    r"|(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4})*)?::(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4})*)?)"
    r"(?![0-9A-Fa-f:])"
)


class JsonReadError(Exception):
    """JSON として読めない(終了コード 3)。"""


class OutputError(Exception):
    """保存先を作れない・書き込めない(終了コード 1)。"""


class OutputExistsError(Exception):
    """同名の出力が既にある(終了コード 4)。"""


def _reject_constant(name: str) -> NoReturn:
    raise ValueError(f"NaN と Infinity は不可: {name}")


def read_json(path: Path) -> object:
    """UTF-8 の JSON を読む。読めない・NaN や Infinity を含むなら JsonReadError。"""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise JsonReadError(f"UTF-8 として読めない: {path.name}") from error
    try:
        return json.loads(text, parse_constant=_reject_constant)
    except ValueError as error:
        raise JsonReadError(f"JSON として読めない: {path.name}: {error}") from error


def dump_json(obj: object) -> str:
    """結果の JSON の文字列。NaN と Infinity は書かない(ValueError)。"""
    return json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def default_results_dir(results_dir: str | None, env: Mapping[str, str] | None = None) -> Path:
    """--out を省いたときの保存先。measure.json の results_dir、null なら既定の場所。"""
    if results_dir is not None:
        return Path(results_dir)
    base = (os.environ if env is None else env).get("LOCALAPPDATA")
    if not base:
        raise OutputError("保存先を決められない(LOCALAPPDATA が無い)")
    return Path(base) / "VcCheck" / "results"


def write_outputs(out_dir: Path, files: Mapping[str, str]) -> list[Path]:
    """複数のファイルを、全部書けたときだけ残す。同名があれば何も書かない。"""
    targets = {out_dir / name: text for name, text in files.items()}
    for target in targets:
        if target.exists():
            raise OutputExistsError(f"同名の出力が既にある: {target.name}")
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise OutputError(f"保存先を作れない: {type(error).__name__}") from error
    temps: list[Path] = []
    done: list[Path] = []
    try:
        for target, text in targets.items():
            temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
            temps.append(temp)
            with open(temp, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
        for (target, _), temp in zip(targets.items(), temps, strict=True):
            if target.exists():
                raise OutputExistsError(f"同名の出力が既にある: {target.name}")
            os.rename(temp, target)
            done.append(target)
    except BaseException as error:
        for leftover in (*temps, *done):
            try:
                leftover.unlink()
            except OSError:
                pass
        if isinstance(error, FileExistsError):
            raise OutputExistsError("同名の出力が既にある") from error
        if isinstance(error, OSError):
            raise OutputError(f"書き込めない: {type(error).__name__}") from error
        raise
    return done


@dataclass(frozen=True)
class Identity:
    """共有用の出力から消す、この PC の名前。"""

    usernames: tuple[str, ...]
    hostnames: tuple[str, ...]


def local_identity(env: Mapping[str, str] | None = None) -> Identity:
    source = os.environ if env is None else env
    users = [source.get("USERNAME", ""), Path(source.get("USERPROFILE", "")).name]
    hosts = [source.get("COMPUTERNAME", ""), source.get("USERDOMAIN", "")]
    if env is None:
        hosts.append(platform.node())
    return Identity(_unique(users), _unique(hosts))


def _unique(names: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(n for n in names if n))


def _replace_all(text: str, names: Sequence[str], mask: str) -> str:
    for name in sorted(set(names), key=len, reverse=True):
        if name:
            text = re.sub(re.escape(name), lambda _: mask, text, flags=re.IGNORECASE)
    return text


def mask_text(text: str, *, input_name: str | None, output_name: str | None,
              identity: Identity) -> str:
    """共有用に、デバイス名・パス・IP アドレス・ユーザー名・ホスト名を置き換える。"""
    users = [*identity.usernames, *_USER_IN_PATH.findall(text)]
    if input_name:
        text = text.replace(input_name, MASK_INPUT)
    if output_name:
        text = text.replace(output_name, MASK_OUTPUT)
    text = _PATH.sub(lambda _: MASK_PATH, text)
    text = _IPV4.sub(lambda _: MASK_IP, text)
    text = _IPV6.sub(lambda _: MASK_IP, text)
    text = _replace_all(text, users, MASK_USER)
    return _replace_all(text, identity.hostnames, MASK_HOST)
