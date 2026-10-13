# 合成の結果を10種作る(AC-7〜AC-9)。音声は計算で作った配列だけ。
# 閾値の数値は、テストの中だけで与える仮の値(仕様書 §0・INV-5)。出荷する値ではない。
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from vccheck import analysis
from vccheck.__main__ import build_result, load_config
from vccheck.io_safe import Identity, read_json
from vccheck.judge import Thresholds, parse_thresholds
from vccheck.model import (
    ITEM_IDS,
    ITEM_SPEC_BY_ID,
    REASON_SKIPPED,
    Conditions,
    RawItem,
    Result,
    failed,
    fixed_unchecked_items,
    measured,
    unchecked,
)

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
CONFIG = load_config(CONFIG_DIR)
MEASURE = CONFIG.measure
RATE = 8000
DEVICE_IN = "Headset (TestUser-PC)"
DEVICE_OUT = "Speakers (TestUser-PC)"
USER = "TestUser"
IDENTITY = Identity((USER,), ("TestUser-PC",))
STAMP = datetime(2026, 10, 1, 14, 5, 0, tzinfo=timezone(timedelta(hours=9)))
CONDITIONS = Conditions(DEVICE_IN, DEVICE_OUT, False, 1, MEASURE.silent_s, MEASURE.speech_s,
                        MEASURE.net_attempts)

# テスト用の仮の閾値(warn, bad)。実機の較正とは無関係。
TEST_NUMBERS: dict[str, tuple[float, float]] = {
    "mic.noise_floor_dbfs": (-50, -40),
    "mic.speech_level_dbfs": (-30, -40),
    "mic.clip_events": (0, 2),
    "mic.clip_ms": (0, 10),
    "mic.snr_db": (20, 10),
    "mic.dc_offset_pct": (1, 5),
    "net.rtt_p95_ms": (100, 200),
    "net.jitter_ms": (30, 60),
    "net.fail_pct": (0, 10),
    "sys.cpu_avg_pct": (70, 90),
    "sys.cpu_max_pct": (90, 99),
    "sys.mem_load_pct": (80, 95),
    "echo.tone_in_mic_dbfs": (-60, -40),
}


def shipped_thresholds_obj() -> dict[str, object]:
    obj = read_json(CONFIG_DIR / "thresholds.json")
    assert isinstance(obj, dict)
    return obj


def thresholds_obj(numbers: dict[str, tuple[float | None, float | None]] | None = None,
                   gate: float | None = None) -> dict[str, object]:
    """出荷時の形に、テスト用の数値だけを差し込んだ thresholds.json の中身。"""
    obj = shipped_thresholds_obj()
    for key, (warn, bad) in (numbers or {}).items():
        entry = obj[key]
        assert isinstance(entry, dict)
        obj[key] = {**entry, "warn": warn, "bad": bad}
    obj["speech_gate_db"] = {"min": gate}
    return obj


NULL_THRESHOLDS = parse_thresholds(shipped_thresholds_obj())
TEST_THRESHOLDS = parse_thresholds(thresholds_obj(dict(TEST_NUMBERS), gate=10))


def sine(amplitude: float, hz: float, rate: int, count: int) -> list[float]:
    return [amplitude * math.sin(2.0 * math.pi * hz * n / rate) for n in range(count)]


def recording(quiet: float, loud: float, *, clip: bool = False) -> list[float]:
    """先頭の捨てる分 + 無言(小さい正弦波) + 発話(大きい正弦波)の合成の録音。"""
    head = round(RATE * MEASURE.discard_head_ms / 1000.0)
    silent = sine(quiet, 100.0, RATE, round(RATE * MEASURE.silent_s))
    speech = sine(loud, 200.0, RATE, round(RATE * MEASURE.speech_s))
    if clip:
        speech = [max(-1.0, min(1.0, x)) for x in speech]
    return [0.3] * head + silent + speech


def mic_raws(quiet: float, loud: float, gate: float | None, *, clip: bool = False) -> list[RawItem]:
    return analysis.measure_mic(recording(quiet, loud, clip=clip), RATE, MEASURE, gate)


def other_raws() -> dict[str, RawItem]:
    """マイク以外の項目の、何も引っかからない合成の値。"""
    raws = [
        measured("env.discord_running", True, "確認した名前: Discord.exe"),
        measured("priv.mic_machine", "Allow"),
        measured("priv.mic_user", "Allow"),
        measured("priv.mic_discord", "Allow"),
        measured("duck.pref", 3),
        measured("dev.in.format", "48000 Hz / 1 ch / Int16"),
        measured("dev.out.format", "48000 Hz / 2 ch / Float"),
        measured("sys.cpu_avg_pct", 10.0),
        measured("sys.cpu_max_pct", 30.0),
        measured("sys.mem_load_pct", 50),
        measured("tone.heard", "ok"),
        measured("echo.tone_in_mic_dbfs", -80.0),
    ]
    for side in ("gw", "ref"):
        raws += [
            measured(f"net.{side}.rtt_p50_ms", 20.0),
            measured(f"net.{side}.rtt_p95_ms", 30.0),
            measured(f"net.{side}.rtt_max_ms", 40.0),
            measured(f"net.{side}.jitter_ms", 5.0),
            measured(f"net.{side}.fail_pct", 0.0),
        ]
    return {r.id: r for r in raws}


def compose(mic: list[RawItem], changes: list[RawItem] | None = None) -> list[RawItem]:
    by_id = other_raws()
    for raw in [*mic, *(changes or [])]:
        by_id[raw.id] = raw
    return [*by_id.values(), *fixed_unchecked_items()]


def every(make: str) -> list[RawItem]:
    ids = [i for i in ITEM_IDS if not ITEM_SPEC_BY_ID[i].fixed_reason]
    if make == "failed":
        return [*(failed(i, "合成の失敗") for i in ids), *fixed_unchecked_items()]
    return [*(unchecked(i, REASON_SKIPPED) for i in ids), *fixed_unchecked_items()]


@dataclass(frozen=True)
class Fixture:
    name: str
    raws: list[RawItem]
    thresholds: Thresholds
    symptoms: tuple[str, ...] = ()


def fixtures() -> list[Fixture]:
    healthy = mic_raws(0.001, 0.1, 10)
    return [
        Fixture("normal", compose(healthy), TEST_THRESHOLDS),
        Fixture("all_failed", every("failed"), TEST_THRESHOLDS, ("quiet",)),
        Fixture("all_skipped", every("unchecked"), TEST_THRESHOLDS),
        Fixture("gate_not_met", compose(mic_raws(0.05, 0.05, 10)), TEST_THRESHOLDS, ("quiet",)),
        Fixture("all_zero", compose(mic_raws(0.0, 0.0, 10)), TEST_THRESHOLDS, ("quiet",)),
        Fixture("clipping", compose(mic_raws(0.001, 2.0, 10, clip=True)), TEST_THRESHOLDS,
                ("crackle",)),
        Fixture("uncalibrated", compose(mic_raws(0.001, 0.1, None)), NULL_THRESHOLDS,
                ("noise",)),
        Fixture(
            "many_candidates",
            compose(healthy, [
                measured("net.gw.rtt_p95_ms", 500.0),
                measured("net.ref.jitter_ms", 45.0),
                measured("sys.cpu_max_pct", 99.5),
                measured("priv.mic_user", "Deny"),
                measured("duck.pref", 1),
                measured("tone.heard", "crackle"),
            ]),
            TEST_THRESHOLDS,
            ("drop",),
        ),
        Fixture("silent_zero", compose(mic_raws(0.0, 0.1, 10)), TEST_THRESHOLDS),
        Fixture(
            "mixed",
            compose(healthy, [
                failed("priv.mic_discord", "キーが無い | C:\\Users\\TestUser\\x を\n読めない"),
                failed("duck.pref", "値が無い"),
                failed("dev.out.format", "合成の失敗"),
                measured("tone.heard", "none"),
                unchecked("echo.tone_in_mic_dbfs", "テスト音は確認なしでは再生しない"),
                measured("net.gw.fail_pct", 100.0),
                failed("net.gw.rtt_p95_ms", "成功した接続が少ない"),
            ]),
            TEST_THRESHOLDS,
            ("other_side", "quiet"),
        ),
    ]


def build(fixture: Fixture) -> Result:
    return build_result(
        fixture.raws, symptoms=fixture.symptoms, conditions=CONDITIONS, measured_at=STAMP,
        created_at=STAMP, thresholds=fixture.thresholds, rules=CONFIG.rules, advice=CONFIG.advice,
    )


RESULTS: dict[str, Result] = {f.name: build(f) for f in fixtures()}
