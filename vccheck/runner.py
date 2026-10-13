# 測定の実行層。CLI の run と GUI が、ここの同じ関数(perform_run)を呼ぶ。
# config\ の場所と読み込み(FR-22)、この版で測れる内容(capabilities)、結果の組み立て、
# 進み具合の通知と中止の合図(RunHooks)を持つ。画面と引数の解釈はここに書かない。
from __future__ import annotations

import sys
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, TypeVar

from vccheck import __version__
from vccheck.io_safe import (
    JsonReadError,
    default_results_dir,
    dump_json,
    read_json,
    write_outputs,
)
from vccheck.judge import Thresholds, judge_items, parse_thresholds
from vccheck.model import (
    MIC_ITEM_IDS,
    NET_ITEM_IDS,
    REASON_SKIPPED,
    REASON_TONE_NOT_DECLARED,
    REASON_TONE_UNCONFIRMED,
    TONE_ITEM_IDS,
    Conditions,
    ConfigError,
    Measure,
    NetTargets,
    RawItem,
    Result,
    RunInfo,
    check_result,
    count_words,
    fixed_unchecked_items,
    measured,
    parse_measure,
    parse_net_targets,
    result_to_obj,
    unchecked,
)
from vccheck.report_md import render_markdown
from vccheck.rules import Rule, parse_advice, parse_rules, pick_candidates
from vccheck.segments import summarize_segments

NOT_IMPLEMENTED: Final = "この版では未実装"
REASON_DEVICE_NOT_READ: Final = "この版ではデバイスの形式を読まない(録音の実装と同時に対応する)"

# 測定の段階(画面設計書 §6 の順)。
STAGE_PREPARE: Final = "prepare"
STAGE_MIC_SILENT: Final = "mic_silent"
STAGE_MIC_SPEECH: Final = "mic_speech"
STAGE_NET: Final = "net"
STAGE_TONE: Final = "tone"
STAGE_SUMMARY: Final = "summary"
STAGES: Final = (STAGE_PREPARE, STAGE_MIC_SILENT, STAGE_MIC_SPEECH, STAGE_NET, STAGE_TONE,
                 STAGE_SUMMARY)
TONE_ANSWERS: Final = ("ok", "crackle", "quiet", "none")
_CANCEL_POLL_S: Final = 0.05
_T = TypeVar("_T")


@dataclass(frozen=True)
class Capabilities:
    """この版の測定部が実行できる内容。False の物は、CLI でも GUI でも測れない。"""

    mic: bool
    net: bool
    tone: bool

    @property
    def any(self) -> bool:
        return self.mic or self.net or self.tone


def capabilities() -> Capabilities:
    """マイクの録音・回線の測定・テスト音は、どれもまだ実装していない。"""
    return Capabilities(mic=False, net=False, tone=False)


# ---- 場所(ソースから動かすときと、PyInstaller の exe のとき) ----


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """exe のあるフォルダ。ソースから動かすときはリポジトリの直下。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_dir() -> Path:
    """同梱したファイルの展開先(exe のときは一時フォルダ)。ここには何も書かない。"""
    unpacked = getattr(sys, "_MEIPASS", None)
    if is_frozen() and isinstance(unpacked, str):
        return Path(unpacked)
    return Path(__file__).resolve().parent.parent


def default_config_dir() -> Path:
    """exe の隣に config\\ があればそれをフォルダごと使い、無ければ同梱の config\\ を使う。"""
    beside = app_dir() / "config"
    return beside if beside.is_dir() else bundle_dir() / "config"


def resolve_results_dir(results_dir: str | None) -> Path:
    """保存先の既定。exe のときの相対パスは exe のフォルダから数える(展開先には書かない)。"""
    path = default_results_dir(results_dir)
    if is_frozen() and not path.is_absolute():
        return app_dir() / path
    return path


# ---- config\ の読み込み ----


class ConfigFileError(ConfigError):
    """どのファイルが壊れていたかを運ぶ(GUI の DLG-03 がファイル名を出す)。"""

    def __init__(self, file_name: str, message: str) -> None:
        super().__init__(message)
        self.file_name = file_name


@dataclass(frozen=True)
class Config:
    measure: Measure
    thresholds: Thresholds
    net_targets: NetTargets
    rules: tuple[Rule, ...]
    advice: dict[str, str]


def _read_config_json(config_dir: Path, name: str) -> object:
    try:
        return read_json(config_dir / name)
    except JsonReadError as error:
        raise ConfigFileError(name, str(error)) from error
    except OSError as error:
        raise ConfigFileError(name, f"{name}: 読めない({type(error).__name__})") from error


def _load(config_dir: Path, name: str, parse: Callable[[object], _T]) -> _T:
    try:
        return parse(_read_config_json(config_dir, name))
    except ConfigFileError:
        raise
    except ConfigError as error:
        raise ConfigFileError(name, str(error)) from error


def load_measure(config_dir: Path) -> Measure:
    return _load(config_dir, "measure.json", parse_measure)


def load_config(config_dir: Path) -> Config:
    """5ファイルの形を検査して読む。壊れていれば ConfigError(FR-22)。"""
    rules = _load(config_dir, "rules.json", parse_rules)
    return Config(
        measure=load_measure(config_dir),
        thresholds=_load(config_dir, "thresholds.json", parse_thresholds),
        net_targets=_load(config_dir, "net_targets.json", parse_net_targets),
        rules=tuple(rules),
        advice=_load(config_dir, "advice.json", lambda obj: parse_advice(obj, rules)),
    )


# ---- 結果の組み立て ----


def build_result(raws: Iterable[RawItem], *, symptoms: Sequence[str], conditions: Conditions,
                 measured_at: datetime, created_at: datetime, thresholds: Thresholds,
                 rules: Sequence[Rule], advice: dict[str, str]) -> Result:
    """項目 → 判定 → 区間 → 原因の可能性 → 件数、の順に結果を組み立てる。"""
    items = judge_items(raws, thresholds)
    done, total = thresholds.calibrated_counts()
    return Result(
        app_version=__version__,
        created_at=created_at.isoformat(timespec="seconds"),
        run=RunInfo(
            measured_at=measured_at.replace(tzinfo=None).isoformat(timespec="seconds"),
            symptoms=tuple(symptoms),
            conditions=conditions,
        ),
        calibrated=done,
        calibrated_total=total,
        items=tuple(items),
        segments=tuple(summarize_segments(items)),
        candidates=tuple(pick_candidates(items, symptoms, rules, advice)),
        counts=count_words(items),
    )


def output_stem(measured_at: datetime) -> str:
    """結果ファイルの名前の「日時」の部分。"""
    return measured_at.strftime("%Y-%m-%d_%H%M%S")


def output_names(stem: str) -> tuple[str, str, str]:
    """1回の測定が書く3ファイルの名前(JSON・Markdown・共有用 Markdown)。"""
    return f"{stem}.json", f"{stem}.md", f"{stem}.share.md"


# ---- 1回の測定 ----


class RunCancelledError(Exception):
    """使用者が中止した。何も保存しない(FR-21)。"""


class RunNotSupportedError(Exception):
    """この版の測定部が実行できない内容を求められた。"""


class CannotMeasureError(Exception):
    """測定を始められない(終了コード 2)。"""


class ResultShapeError(Exception):
    """組み立てた結果が形の規則(§9.2)に合わない。何も保存しない。"""

    def __init__(self, violations: Sequence[str]) -> None:
        super().__init__("結果が形の規則に合わない")
        self.violations = tuple(violations)


@dataclass(frozen=True)
class RunRequest:
    """1回の測定の指定。CLI は引数から、GUI は画面の選択から作る。"""

    symptoms: tuple[str, ...] = ()
    skip_mic: bool = True
    skip_net: bool = True
    confirm_tone: bool = False
    input_declared: str | None = None
    output_declared: str | None = None
    input_default_used: bool | None = None


def _ignore_stage(_stage: str, _seconds: float | None) -> None:
    return None


def _never() -> bool:
    return False


def _no_answer() -> str | None:
    return None


@dataclass(frozen=True)
class RunHooks:
    """測定の途中で呼ぶ関数。on_stage は段階の ID と、その段階の秒数(数えない段階は None)。"""

    on_stage: Callable[[str, float | None], None] = _ignore_stage
    cancelled: Callable[[], bool] = _never
    ask_tone_heard: Callable[[], str | None] = _no_answer

    def check(self) -> None:
        if self.cancelled():
            raise RunCancelledError

    def sleep(self, seconds: float) -> None:
        """短く刻んで待ち、中止の合図があれば RunCancelledError を出す。"""
        end = time.monotonic() + seconds
        while True:
            self.check()
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(left, _CANCEL_POLL_S))


@dataclass(frozen=True)
class RunOutput:
    """1回の測定の結果。obj が結果の JSON(形の版 1)で、残りは書き出す文字列。"""

    obj: dict[str, object]
    json_text: str
    markdown: str
    share_markdown: str

    def files(self, stem: str) -> dict[str, str]:
        names = output_names(stem)
        return {names[0]: self.json_text, names[1]: self.markdown,
                names[2]: self.share_markdown}


def tone_heard_raw(answer: str | None) -> RawItem:
    """テスト音の聞こえ方の申告を項目にする。申告が無ければ未チェック(値を推測しない)。"""
    if answer is None:
        return unchecked("tone.heard", REASON_TONE_NOT_DECLARED)
    if answer not in TONE_ANSWERS:
        raise ValueError(f"tone.heard: 申告の値が不正: {answer!r}")
    return measured("tone.heard", answer)


def _skipped_raws() -> list[RawItem]:
    """この版の run が測らない項目。飛ばした測定は未チェックにする(§9.1)。"""
    return [
        *(unchecked(i, REASON_SKIPPED) for i in (*MIC_ITEM_IDS, *NET_ITEM_IDS)),
        *(unchecked(i, REASON_TONE_UNCONFIRMED) for i in TONE_ITEM_IDS),
        unchecked("dev.in.format", REASON_DEVICE_NOT_READ),
        unchecked("dev.out.format", REASON_DEVICE_NOT_READ),
        *fixed_unchecked_items(),
    ]


def unsupported_reason(request: RunRequest) -> str | None:
    """この版で実行できない指定なら、その理由。実行できるなら None。"""
    able = capabilities()
    if (not request.skip_mic and not able.mic) or (not request.skip_net and not able.net):
        return (
            f"run: マイクと回線の測定は{NOT_IMPLEMENTED}。"
            "--skip-mic と --skip-net を両方付ければ、Windows の設定と PC の負荷だけを測る"
        )
    if request.confirm_tone and not able.tone:
        return f"run: テスト音は{NOT_IMPLEMENTED}"
    return None


def perform_run(request: RunRequest, config: Config, *, measured_at: datetime,
                hooks: RunHooks | None = None) -> RunOutput:
    """1回の測定をして、結果と書き出す文字列を返す。保存はしない(save_run が行う)。

    この版が測るのは、Windows の設定の読み取りと PC の負荷だけ(準備の段階の中で測る)。
    """
    reason = unsupported_reason(request)
    if reason is not None:
        raise RunNotSupportedError(reason)
    try:
        from vccheck import sysinfo
    except ImportError as error:
        raise CannotMeasureError("測定を始められない(Windows でしか動かない)") from error
    used = RunHooks() if hooks is None else hooks
    # 中止の合図を受け取らない呼び出し(CLI)では、従来どおり time.sleep をそのまま使う。
    sleep = time.sleep if hooks is None else used.sleep
    measure = config.measure
    used.on_stage(STAGE_PREPARE, None)
    used.check()
    raws = [*sysinfo.collect(measure, sleep=sleep), *_skipped_raws()]
    used.check()
    used.on_stage(STAGE_SUMMARY, None)
    result = build_result(
        raws, symptoms=request.symptoms,
        conditions=Conditions(request.input_declared, request.output_declared,
                              request.input_default_used, None, measure.silent_s,
                              measure.speech_s, measure.net_attempts),
        measured_at=measured_at, created_at=measured_at.astimezone(),
        thresholds=config.thresholds, rules=config.rules, advice=config.advice,
    )
    obj = result_to_obj(result)
    violations = check_result(obj)
    if violations:
        raise ResultShapeError(violations)
    used.check()
    return RunOutput(obj, dump_json(obj), render_markdown(result),
                     render_markdown(result, share=True))


def save_run(output: RunOutput, out_dir: Path, stem: str) -> list[Path]:
    """3ファイルを書く。同名があれば OutputExistsError、書けなければ OutputError。"""
    return write_outputs(out_dir, output.files(stem))
