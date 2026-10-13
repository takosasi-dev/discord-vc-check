# judge.py のテスト(AC-6)。閾値の数値は、このテストの中だけで与える仮の値。
from __future__ import annotations

import pytest

from tests.fixtures.synth import (
    NULL_THRESHOLDS,
    RESULTS,
    TEST_THRESHOLDS,
    compose,
    mic_raws,
    shipped_thresholds_obj,
    thresholds_obj,
)
from vccheck.judge import judge_item, judge_items, parse_thresholds
from vccheck.model import (
    ITEM_IDS,
    ITEM_SPEC_BY_ID,
    THRESHOLD_KEYS,
    ConfigError,
    RawItem,
    failed,
    measured,
)


def test_shipped_thresholds_are_all_null():
    """出荷する thresholds.json に数値が1つも無い(INV-5・D-5)。"""
    obj = shipped_thresholds_obj()
    assert len(THRESHOLD_KEYS) == 13
    for key in THRESHOLD_KEYS:
        assert obj[key]["warn"] is None, key
        assert obj[key]["bad"] is None, key
    assert obj["speech_gate_db"] == {"min": None}
    assert NULL_THRESHOLDS.calibrated_counts() == (0, 13)
    assert NULL_THRESHOLDS.speech_gate_db is None


@pytest.mark.parametrize("name", list(RESULTS))
def test_ac6_null_thresholds_give_no_verdict(name):
    """閾値が初期値なら、どの合成の結果でも数値の項目は判定なしで「閾値が未較正」。"""
    measured_numbers = 0
    for item in RESULTS[name].items:
        if ITEM_SPEC_BY_ID[item.id].judge != "number" or item.status != "measured":
            continue
        again = judge_item(RawItem(item.id, "measured", item.value), NULL_THRESHOLDS)
        assert again.verdict == "none"
        assert again.verdict_raw is None
        assert "閾値が未較正" in (again.note or "")
        measured_numbers += 1
    if name in ("normal", "uncalibrated"):
        assert measured_numbers == 16


def one(item_id, value, numbers):
    return judge_item(measured(item_id, value), parse_thresholds(thresholds_obj(numbers)))


def test_ac6_high_boundaries():
    numbers = {"sys.cpu_avg_pct": (70, 90)}
    assert one("sys.cpu_avg_pct", 70, numbers).verdict == "ok"
    assert one("sys.cpu_avg_pct", 70.1, numbers).verdict == "warn"
    assert one("sys.cpu_avg_pct", 90, numbers).verdict == "warn"
    assert one("sys.cpu_avg_pct", 90.1, numbers).verdict == "bad"
    assert one("sys.cpu_avg_pct", 0, numbers).verdict == "ok"


def test_ac6_low_boundaries():
    numbers = {"mic.speech_level_dbfs": (-30, -40)}
    assert one("mic.speech_level_dbfs", -30.0, numbers).verdict == "ok"
    assert one("mic.speech_level_dbfs", -30.1, numbers).verdict == "warn"
    assert one("mic.speech_level_dbfs", -40.0, numbers).verdict == "warn"
    assert one("mic.speech_level_dbfs", -40.1, numbers).verdict == "bad"
    assert one("mic.speech_level_dbfs", -10.0, numbers).verdict == "ok"


@pytest.mark.parametrize("pair", [(5, None), (None, 5), (None, None)])
def test_ac6_one_null_means_no_verdict(pair):
    item = one("mic.clip_events", 100, {"mic.clip_events": pair})
    assert item.verdict == "none"
    assert item.verdict_raw is None
    assert "閾値が未較正" in (item.note or "")


def test_verdict_raw_matches_spec_example():
    item = one("mic.clip_events", 3, {"mic.clip_events": (0, 5)})
    assert (item.verdict, item.verdict_raw) == ("warn", "3 > 0")
    assert item.confidence == "measured"
    assert item.reason is None


def test_abs_is_used_for_dc_offset():
    numbers = {"mic.dc_offset_pct": (1, 5)}
    assert one("mic.dc_offset_pct", -3.0, numbers).verdict == "warn"
    assert one("mic.dc_offset_pct", -6.0, numbers).verdict == "bad"
    assert one("mic.dc_offset_pct", -1.0, numbers).verdict == "ok"
    assert one("mic.dc_offset_pct", -3.0, numbers).verdict_raw == "|-3.0| > 1"


def test_net_thresholds_apply_to_both_targets():
    numbers = {"net.rtt_p95_ms": (100, 200)}
    assert one("net.gw.rtt_p95_ms", 150.0, numbers).verdict == "warn"
    assert one("net.ref.rtt_p95_ms", 250.0, numbers).verdict == "bad"
    assert one("net.gw.rtt_p50_ms", 9999.0, numbers).verdict == "none"


def test_enum_verdicts():
    assert judge_item(measured("priv.mic_user", "Deny"), NULL_THRESHOLDS).verdict == "bad"
    allow = judge_item(measured("priv.mic_user", "Allow"), NULL_THRESHOLDS)
    assert (allow.verdict, allow.verdict_raw) == ("ok", "Allow = ok")
    unknown = judge_item(measured("priv.mic_user", "Prompt"), NULL_THRESHOLDS)
    assert (unknown.verdict, unknown.verdict_raw) == ("none", None)
    heard = {v: judge_item(measured("tone.heard", v), NULL_THRESHOLDS).verdict
             for v in ("ok", "quiet", "crackle", "none")}
    assert heard == {"ok": "ok", "quiet": "warn", "crackle": "bad", "none": "bad"}
    assert judge_item(measured("tone.heard", "ok"), NULL_THRESHOLDS).confidence == "reported"


@pytest.mark.parametrize(
    "item_id",
    ["duck.pref", "dev.in.format", "dev.out.format", "env.discord_running", "mic.peak_dbfs",
     "mic.all_zero", "net.gw.rtt_p50_ms", "net.ref.rtt_max_ms"],
)
def test_never_judged_items(item_id):
    value = "x" if item_id.startswith("dev.") else 1
    item = judge_item(measured(item_id, value), TEST_THRESHOLDS)
    assert (item.verdict, item.verdict_raw) == ("none", None)


def test_item_without_value_has_no_confidence_or_verdict():
    item = judge_item(failed("mic.snr_db", "合成の失敗"), TEST_THRESHOLDS)
    assert (item.value, item.confidence, item.verdict, item.verdict_raw) == (None,) * 4
    assert item.reason == "合成の失敗"
    assert judge_item(measured("net.gw.jitter_ms", 1.0), TEST_THRESHOLDS).confidence == "reference"


def test_substitute_values_are_rejected():
    with pytest.raises(ValueError):
        judge_item(RawItem("mic.snr_db", "failed", 0, "理由"), TEST_THRESHOLDS)
    with pytest.raises(ValueError):
        judge_item(RawItem("mic.snr_db", "failed", None, ""), TEST_THRESHOLDS)
    with pytest.raises(ValueError):
        judge_item(RawItem("mic.snr_db", "measured", None), TEST_THRESHOLDS)
    with pytest.raises(ValueError):
        judge_item(measured("mic.snr_db", float("nan")), TEST_THRESHOLDS)
    with pytest.raises(ValueError):
        judge_item(measured("mic.snr_db", float("inf")), TEST_THRESHOLDS)


def test_judge_items_requires_every_item_once():
    raws = compose(mic_raws(0.001, 0.1, 10))
    assert [i.id for i in judge_items(raws, TEST_THRESHOLDS)] == list(ITEM_IDS)
    with pytest.raises(ValueError, match="足りない"):
        judge_items(raws[1:], TEST_THRESHOLDS)
    with pytest.raises(ValueError, match="重複"):
        judge_items([*raws, raws[0]], TEST_THRESHOLDS)


def test_calibrated_counts():
    th = parse_thresholds(thresholds_obj({"mic.clip_events": (0, 2), "mic.snr_db": (20, None)}))
    assert th.calibrated_counts() == (1, 13)
    assert TEST_THRESHOLDS.calibrated_counts() == (13, 13)


def broken(change):
    obj = shipped_thresholds_obj()
    change(obj)
    return obj


@pytest.mark.parametrize(
    "change",
    [
        lambda o: o.pop("mic.snr_db"),
        lambda o: o.update({"extra": 1}),
        lambda o: o.update({"mic.snr_db": {"direction": "up", "warn": None, "bad": None}}),
        lambda o: o.update({"mic.snr_db": {"direction": "low", "warn": "1", "bad": None}}),
        lambda o: o.update({"mic.snr_db": {"direction": "low", "warn": True, "bad": None}}),
        lambda o: o.update({"mic.snr_db": {"direction": "low", "warn": None}}),
        lambda o: o.update({"mic.snr_db": {"direction": "low", "warn": None, "bad": None,
                                           "use": "square"}}),
        lambda o: o.update({"speech_gate_db": None}),
        lambda o: o.update({"speech_gate_db": {"min": "x"}}),
        lambda o: o["enum_verdicts"].update({"tone.heard": {"ok": "fine"}}),
        lambda o: o["enum_verdicts"].pop("tone.heard"),
        lambda o: o.update({"enum_verdicts": []}),
    ],
)
def test_broken_thresholds_are_rejected(change):
    with pytest.raises(ConfigError):
        parse_thresholds(broken(change))
    with pytest.raises(ConfigError):
        parse_thresholds([])
