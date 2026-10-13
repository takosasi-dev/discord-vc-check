# CLI(仕様書 §9.1): run・gui・report・check・stats・--selftest と終了コード。
# 測定そのものと config\ の読み込みは runner.py(実行層)にあり、ここは引数の解釈と表示だけ。
# 合成データだけの自己診断(FR-18)もここに置く。exe を引数なしで起動したときは GUI を出す。
from __future__ import annotations

import argparse
import io
import math
import os
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Final, NoReturn

from vccheck import analysis
from vccheck.io_safe import (
    Identity,
    JsonReadError,
    OutputError,
    OutputExistsError,
    dump_json,
    read_json,
    write_outputs,
)
from vccheck.judge import NOTE_UNCALIBRATED
from vccheck.model import (
    ITEM_IDS,
    ITEM_SPEC_BY_ID,
    MIC_ITEM_IDS,
    REASON_SKIPPED,
    SYMPTOMS,
    WORDS,
    Conditions,
    ConfigError,
    Measure,
    RawItem,
    Result,
    check_result,
    failed,
    fixed_unchecked_items,
    is_number,
    measured,
    result_from_obj,
    result_to_obj,
    unchecked,
)
from vccheck.report_md import render_markdown
from vccheck.rules import NO_CANDIDATE_TEXT
from vccheck.runner import (
    NOT_IMPLEMENTED,
    CannotMeasureError,
    Config,
    ResultShapeError,
    RunRequest,
    build_result,
    default_config_dir,
    is_frozen,
    load_config,
    load_measure,
    output_names,
    output_stem,
    perform_run,
    resolve_results_dir,
    unsupported_reason,
)

# 実行層へ移した名前のうち、テストと合成データが従来どおりここから取る物。
__all__ = ["Config", "build_result", "default_config_dir", "load_config", "main",
           "output_stem"]

EXIT_OK: Final = 0
EXIT_ERROR: Final = 1
EXIT_CANNOT_MEASURE: Final = 2
EXIT_CHECK: Final = 3
EXIT_EXISTS: Final = 4
EXIT_ABORTED: Final = 5


class CliError(Exception):
    """終了コードと、標準エラーに出す行を運ぶ。"""

    def __init__(self, code: int, *lines: str) -> None:
        super().__init__(*lines)
        self.code = code
        self.lines = lines


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        print(f"{self.prog}: {message}", file=sys.stderr)
        raise SystemExit(EXIT_ERROR)


def _read_result(path: Path) -> Result:
    try:
        obj = read_json(path)
    except JsonReadError as error:
        raise CliError(EXIT_CHECK, str(error)) from error
    except OSError as error:
        raise CliError(EXIT_ERROR, f"読めない: {path.name}({type(error).__name__})") from error
    violations = check_result(obj)
    if violations:
        raise CliError(EXIT_CHECK, *(f"{path.name}: {v}" for v in violations))
    if not isinstance(obj, dict):
        raise CliError(EXIT_CHECK, f"{path.name}: 最上位が object でない")
    return result_from_obj(obj)


def _write(out_dir: Path, files: dict[str, str]) -> list[Path]:
    try:
        return write_outputs(out_dir, files)
    except OutputExistsError as error:
        raise CliError(EXIT_EXISTS, str(error)) from error
    except OutputError as error:
        raise CliError(EXIT_ERROR, str(error)) from error


def _out_dir(out: str | None, config_dir: Path) -> Path:
    if out is not None:
        return Path(out)
    try:
        return resolve_results_dir(load_measure(config_dir).results_dir)
    except (ConfigError, OutputError) as error:
        raise CliError(EXIT_ERROR, str(error)) from error


def cmd_report(result_path: str, share: bool, out: str | None, config_dir: Path) -> int:
    source = Path(result_path)
    result = _read_result(source)
    name = f"{source.stem}.share.md" if share else f"{source.stem}.md"
    written = _write(_out_dir(out, config_dir), {name: render_markdown(result, share=share)})
    for path in written:
        print(path)
    return EXIT_OK


def cmd_check(result_path: str) -> int:
    try:
        _read_result(Path(result_path))
    except CliError as error:
        if error.code != EXIT_CHECK:
            raise
        for line in error.lines:
            print(line)
        return EXIT_CHECK
    print("違反なし")
    return EXIT_OK


def _stat_number(value: float) -> str:
    rounded = round(value, 3) + 0.0
    return str(int(rounded)) if rounded == int(rounded) else str(rounded)


def cmd_stats(result_paths: Sequence[str]) -> int:
    """数値の項目ごとに、件数・最小・中央値・最大を表で出す(閾値は出さない)。"""
    values: dict[str, list[float]] = {}
    labels: dict[str, tuple[str, str]] = {}
    for one in result_paths:
        for item in _read_result(Path(one)).items:
            value = item.value
            if item.status != "measured" or isinstance(value, (bool, str)) or value is None:
                continue
            if not is_number(value):
                continue
            values.setdefault(item.id, []).append(value)
            labels.setdefault(item.id, (item.name, item.unit or ""))
    order = [i for i in ITEM_IDS if i in values] + [i for i in values if i not in ITEM_IDS]
    print(f"結果 {len(result_paths)} 件")
    print("| ID | 項目 | 単位 | 件数 | 最小 | 中央値 | 最大 |")
    print("|---|---|---|---|---|---|---|")
    for item_id in order:
        summary = analysis.summarize(values[item_id])
        name, unit = labels[item_id]
        print(
            f"| {item_id} | {name} | {unit} | {summary.count} | {_stat_number(summary.minimum)} "
            f"| {_stat_number(summary.median)} | {_stat_number(summary.maximum)} |"
        )
    if not order:
        print("数値が測れた項目は無い")
    return EXIT_OK


# ---- 自己診断(合成データだけ。FR-18) ----

_SELFTEST_DEVICE: Final = "Headset (TestUser-PC)"
_SELFTEST_USER: Final = "TestUser"
_SELFTEST_RATE: Final = 8000


def _sine(amplitude: float, hz: float, rate: int, count: int) -> list[float]:
    return [amplitude * math.sin(2.0 * math.pi * hz * n / rate) for n in range(count)]


def _synthetic_recording(measure: Measure, rate: int, quiet: float, loud: float) -> list[float]:
    head = round(rate * measure.discard_head_ms / 1000.0)
    silent = round(rate * measure.silent_s)
    speech = round(rate * measure.speech_s)
    return [0.0] * head + _sine(quiet, 100.0, rate, silent) + _sine(loud, 200.0, rate, speech)


def _selftest_raws(kind: str, measure: Measure) -> list[RawItem]:
    others = [i for i in ITEM_IDS if not ITEM_SPEC_BY_ID[i].fixed_reason]
    if kind == "skipped":
        raws = [unchecked(i, REASON_SKIPPED) for i in others]
    elif kind == "failed":
        raws = [failed(i, "合成の失敗") for i in others]
    else:
        level = 0.0 if kind == "all_zero" else 0.1
        recording = _synthetic_recording(measure, _SELFTEST_RATE, level / 100.0, level)
        raws = analysis.measure_mic(recording, _SELFTEST_RATE, measure, None)
        raws += [
            measured("env.discord_running", True),
            measured("priv.mic_machine", "Allow"),
            measured("priv.mic_user", "Allow"),
            failed("priv.mic_discord", "合成の失敗"),
            measured("duck.pref", 1),
            measured("dev.in.format", "48000 Hz / 1 ch / Int16"),
            measured("dev.out.format", "48000 Hz / 2 ch / Float"),
            measured("sys.cpu_avg_pct", 12.5),
            measured("sys.cpu_max_pct", 40.0),
            measured("sys.mem_load_pct", 55),
            unchecked("tone.heard", REASON_SKIPPED),
            unchecked("echo.tone_in_mic_dbfs", REASON_SKIPPED),
        ]
        raws += [
            measured(i, 20.0) if i.startswith("net.gw.") else failed(i, "合成の失敗")
            for i in others
            if i.startswith("net.")
        ]
    return raws + fixed_unchecked_items()


def _heading_problems(markdown: str) -> list[str]:
    lines = markdown.split("\n")
    found = [line for line in lines if line.startswith("## [")]
    expected = [f"## [{label}] " for _, label in WORDS]
    if len(found) != len(expected) or any(
        not line.startswith(start) for line, start in zip(found, expected, strict=False)
    ):
        return ["Markdown の7見出しが §9.5 の順でちょうど1回ずつになっていない"]
    return []


def _invariant_problems(result: Result, config: Config) -> list[str]:
    """どの結果でも成り立つべき性質(INV-1・INV-5・INV-6・INV-7・INV-10・INV-12)。"""
    problems: list[str] = []
    obj = result_to_obj(result)
    text = dump_json(obj)
    problems += check_result(_read_back(text))
    if text.encode("utf-8").startswith(b"\xef\xbb\xbf") or "\r" in text:
        problems.append("JSON に BOM または CR がある")
    by_id = {i.id: i for i in result.items}
    for seg in result.segments:
        if seg.id in ("S5", "S6") and seg.status != "unchecked":
            problems.append(f"{seg.id} が未チェックでない")
        if seg.status == "ok" and any(by_id[i].status == "failed" for i in seg.item_ids):
            problems.append(f"{seg.id}: 測れなかった項目を含むのに正常")
    for item in result.items:
        if item.status != "measured" and item.value is not None:
            problems.append(f"{item.id}: 値が無い状態なのに value がある")
        spec = ITEM_SPEC_BY_ID[item.id]
        if item.status == "measured" and spec.judge == "number" and (
            item.verdict != "none" or NOTE_UNCALIBRATED not in (item.note or "")
        ):
            problems.append(f"{item.id}: 閾値が null なのに判定が付いた")
    if sum(result.counts.values()) != len(result.items):
        problems.append("件数の和が項目の数と一致しない")
    texts = {r.text for r in config.rules}
    if len(result.candidates) > 3 or any(c.text not in texts for c in result.candidates):
        problems.append("候補が3件を超えた、または rules.json に無い文が出た")
    identity = Identity((_SELFTEST_USER,), ("TestUser-PC",))
    markdown = render_markdown(result)
    shared = render_markdown(result, share=True, identity=identity)
    for label, body in (("Markdown", markdown), ("共有用 Markdown", shared)):
        problems += [f"{label}: {p}" for p in _heading_problems(body)]
        if "問題なし" in body:
            problems.append(f"{label}: 「問題なし」が出力にある")
        if not result.candidates and NO_CANDIDATE_TEXT not in body:
            problems.append(f"{label}: 候補が無いのに固定文が無い")
    if _SELFTEST_DEVICE in shared or _SELFTEST_USER in shared:
        problems.append("共有用 Markdown にデバイス名またはユーザー名が残った")
    return problems


def _read_back(text: str) -> object:
    """一時フォルダに書いて読み直す。同名を上書きしないことも確かめる。"""
    with tempfile.TemporaryDirectory(prefix="vccheck-selftest-") as folder:
        path = _write(Path(folder), {"selftest.json": text})[0]
        try:
            _write(Path(folder), {"selftest.json": text})
        except CliError as error:
            if error.code != EXIT_EXISTS:
                raise
        else:
            raise CliError(EXIT_ERROR, "同名の出力を上書きした")
        return read_json(path)


def _close(actual: float, expected: float, tolerance: float) -> bool:
    return abs(actual - expected) <= tolerance


def _analysis_problems() -> list[str]:
    checks: list[tuple[str, Callable[[], bool]]] = []
    wave = _sine(0.1, 440.0, 48000, 48000)
    checks.append(("正弦波の RMS", lambda: _close(analysis.to_dbfs(analysis.rms(wave))[0],
                                                 -23.0, 0.05)))
    checks.append(("正弦波の最大", lambda: _close(
        analysis.to_dbfs(max(abs(x) for x in wave))[0], -20.0, 0.05)))
    checks.append(("Goertzel", lambda: _close(
        analysis.to_dbfs(analysis.goertzel_amplitude(wave, 440.0, 48000))[0], -20.0, 0.1)))
    clipped = [0.0] * 10 + [1.0] * 5 + [0.0] * 10 + [1.0] * 2
    checks.append(("クリップ", lambda: analysis.clip_runs(clipped, 0.999, 3) == (1, 5)))
    checks.append(("パーセンタイル", lambda: analysis.nearest_rank(
        [float(n) for n in range(1, 11)], 90) == 9.0))
    checks.append(("DC", lambda: _close(analysis.dc_offset_pct([0.01] * 100), 1.0, 0.01)))
    checks.append(("全サンプル 0", lambda: analysis.is_all_zero([0.0] * 100)))
    checks.append(("RMS が 0 の下限丸め", lambda: analysis.to_dbfs(0.0) == (-120.0, True)))
    return [f"analysis: {name} が期待と違う" for name, check in checks if not check()]


def cmd_selftest(config_dir: Path) -> int:
    try:
        config = load_config(config_dir)
    except ConfigError as error:
        print(f"selftest: 失敗: {error}", file=sys.stderr)
        return EXIT_ERROR
    # 判定の数値を持ち込まない。較正済みの値があっても、ここでは null に戻して確かめる。
    blank = replace(
        config.thresholds,
        numbers={k: replace(t, warn=None, bad=None) for k, t in config.thresholds.numbers.items()},
        speech_gate_db=None,
    )
    conditions = Conditions(_SELFTEST_DEVICE, "Speakers (TestUser-PC)", False, 1,
                            config.measure.silent_s, config.measure.speech_s,
                            config.measure.net_attempts)
    stamp = datetime(2026, 10, 1, 14, 5, 0).astimezone()
    problems = _analysis_problems()
    expected_s1 = {"speech": None, "all_zero": "failed", "skipped": "unchecked",
                   "failed": "failed"}
    for kind, s1 in expected_s1.items():
        try:
            result = build_result(
                _selftest_raws(kind, config.measure), symptoms=list(SYMPTOMS)[:1],
                conditions=conditions, measured_at=stamp, created_at=stamp,
                thresholds=blank, rules=config.rules, advice=config.advice,
            )
            found = _invariant_problems(result, config)
            by_id = {i.id: i for i in result.items}
            if s1 is not None and result.segments[0].status not in (s1, "bad"):
                found.append("S1 の状態が期待と違う")
            if kind == "all_zero" and (
                by_id["mic.all_zero"].value is not True
                or any(by_id[i].status != "failed" for i in MIC_ITEM_IDS if i != "mic.all_zero")
            ):
                found.append("全サンプル 0 の扱いが期待と違う")
            if kind == "speech" and any(by_id[i].status != "measured" for i in MIC_ITEM_IDS):
                found.append("合成の発話でマイクの項目が測れなかった")
        except (ValueError, CliError, OSError) as error:
            found = [f"例外 {type(error).__name__}: {error}"]
        problems += [f"{kind}: {p}" for p in found]
    if problems:
        for line in problems:
            print(f"selftest: 失敗: {line}", file=sys.stderr)
        return EXIT_ERROR
    print(f"selftest: OK(合成の結果 {len(expected_s1)} 種)")
    return EXIT_OK


def cmd_run(args: argparse.Namespace, config_dir: Path) -> int:
    """この版は Windows の設定の読み取りと PC の負荷だけを測る(--skip-mic --skip-net)。"""
    try:
        config = load_config(config_dir)
    except ConfigError as error:
        raise CliError(EXIT_ERROR, f"設定ファイルが壊れている。何も測っていない: {error}"
                       ) from error
    request = RunRequest(symptoms=tuple(dict.fromkeys(args.symptom)), skip_mic=args.skip_mic,
                         skip_net=args.skip_net, confirm_tone=args.confirm_tone)
    reason = unsupported_reason(request)
    if reason is not None:
        raise CliError(EXIT_ERROR, reason)
    if args.input is not None or args.output is not None:
        raise CliError(
            EXIT_ERROR,
            f"run: デバイスの一覧の取得は{NOT_IMPLEMENTED}。--input と --output は使えない",
        )
    out_dir = _out_dir(args.out, config_dir)
    measured_at = datetime.now()
    stem = output_stem(measured_at)
    for name in output_names(stem):
        if (out_dir / name).exists():
            raise CliError(EXIT_EXISTS, f"同名の出力が既にある: {name}")
    seconds = config.measure.silent_s + config.measure.speech_s
    print(f"Windows の設定を読み、PC の負荷を {seconds:g} 秒測る(マイクと回線は測らない)")
    try:
        output = perform_run(request, config, measured_at=measured_at)
    except CannotMeasureError as error:
        raise CliError(EXIT_CANNOT_MEASURE, str(error)) from error
    except ResultShapeError as error:
        raise CliError(EXIT_ERROR, "結果が形の規則に合わない。何も保存していない",
                       *error.violations) from error
    for path in _write(out_dir, output.files(stem)):
        print(path)
    return EXIT_OK


def cmd_gui(smoke: bool, config_dir: Path) -> int:
    """ウィザードの画面を出す。PySide6 が無い環境でも、CLI のほかの機能は動く。"""
    try:
        from vccheck.gui.app import run_gui
    except ImportError as error:
        if not (error.name or "").startswith(("PySide6", "shiboken6")):
            raise
        raise CliError(
            EXIT_CANNOT_MEASURE,
            "gui: PySide6 を読み込めないため、画面を出せない",
            "`pip install PySide6-Essentials PySide6-Addons` の後で、もう一度実行する",
        ) from error
    return run_gui(config_dir=config_dir, smoke=smoke)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="vccheck", description="Discord(VcCheck): 通話の音の切り分け")
    parser.add_argument("--selftest", action="store_true", help="合成データだけの自己診断")
    sub = parser.add_subparsers(dest="command", parser_class=_Parser)
    run = sub.add_parser("run", help="測って結果を書き出す")
    run.add_argument("--symptom", action="append", default=[], choices=list(SYMPTOMS),
                     metavar="ID", help="症状(複数可): " + " ".join(SYMPTOMS))
    run.add_argument("--input", help="入力デバイスの名前または番号")
    run.add_argument("--output", help="出力デバイスの名前または番号")
    run.add_argument("--skip-mic", action="store_true", help="マイクの測定を飛ばす")
    run.add_argument("--skip-net", action="store_true", help="回線の測定を飛ばす")
    run.add_argument("--confirm-tone", action="store_true", help="テスト音の再生を確認済みにする")
    run.add_argument("--out", help="保存先のフォルダ")
    gui = sub.add_parser("gui", help="ウィザードの画面(PySide6 が要る)")
    # 画面を作ってすぐ閉じる動作確認用(exe のビルドの後の確認に使う)。
    gui.add_argument("--smoke", action="store_true", help=argparse.SUPPRESS)
    report = sub.add_parser("report", help="結果の JSON から Markdown を書き出す")
    report.add_argument("result")
    report.add_argument("--share", action="store_true", help="共有用(名前をマスク)")
    report.add_argument("--out", help="保存先のフォルダ")
    check = sub.add_parser("check", help="結果の JSON の形を検査する")
    check.add_argument("result")
    stats = sub.add_parser("stats", help="複数の結果の数値項目の統計(較正の補助)")
    stats.add_argument("results", nargs="+")
    return parser


def _ensure_streams() -> None:
    """コンソールの無い exe では標準出力・標準エラーが None になる。捨て先につないでおく。"""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _relax_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        if not isinstance(stream, io.TextIOWrapper):
            continue
        if is_frozen():
            # exe は環境変数(PYTHONUTF8)に頼れないので、出力の文字コードをここで決める。
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        else:
            stream.reconfigure(errors="backslashreplace")


def main(argv: Sequence[str] | None = None, *, config_dir: Path | None = None) -> int:
    _ensure_streams()
    _relax_streams()
    parser = build_parser()
    given = list(sys.argv[1:] if argv is None else argv)
    if argv is None and is_frozen() and not given:
        # exe を引数なしで起動したとき(ダブルクリック)は、画面を出す。
        given = ["gui"]
    try:
        args = parser.parse_args(given)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else EXIT_ERROR
    folder = default_config_dir() if config_dir is None else config_dir
    try:
        if args.selftest:
            return cmd_selftest(folder)
        if args.command == "report":
            return cmd_report(args.result, args.share, args.out, folder)
        if args.command == "check":
            return cmd_check(args.result)
        if args.command == "stats":
            return cmd_stats(args.results)
        if args.command == "run":
            return cmd_run(args, folder)
        if args.command == "gui":
            return cmd_gui(args.smoke, folder)
    except CliError as error:
        for line in error.lines:
            print(line, file=sys.stderr)
        return error.code
    except KeyboardInterrupt:
        print("中止した。何も保存していない", file=sys.stderr)
        return EXIT_ABORTED
    parser.print_help(sys.stderr)
    return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
