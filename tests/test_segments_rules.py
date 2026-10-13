# segments.py と rules.py のテスト(§9.5、AC-9・AC-10 の一部)。
from __future__ import annotations

import copy

import pytest

from tests.fixtures.synth import CONFIG, CONFIG_DIR, RESULTS
from vccheck.io_safe import read_json
from vccheck.model import ITEM_SPEC_BY_ID, ConfigError, Item
from vccheck.rules import parse_advice, parse_rules, pick_candidates, rule_holds
from vccheck.segments import segment_status, summarize_segments


def item(item_id, status="measured", verdict="none", value=1):
    spec = ITEM_SPEC_BY_ID[item_id]
    if status != "measured":
        return Item(item_id, spec.segment, spec.name, spec.unit, status, None, None, None, None,
                    "理由", None)
    raw = None if verdict == "none" else "根拠"
    return Item(item_id, spec.segment, spec.name, spec.unit, status, value, spec.confidence,
                verdict, raw, None, None)


def test_segment_status_priority():
    ok, warn, bad = (item("mic.snr_db", verdict=v) for v in ("ok", "warn", "bad"))
    none = item("mic.peak_dbfs")
    fail = item("mic.clip_ms", "failed")
    skip = item("mic.clip_events", "unchecked")
    assert segment_status("S1", [ok, warn, bad, fail, skip]) == "bad"
    assert segment_status("S1", [ok, warn, fail, skip]) == "warn"
    assert segment_status("S1", [ok, none, fail, skip]) == "failed"
    assert segment_status("S1", [skip]) == "unchecked"
    assert segment_status("S1", []) == "unchecked"
    assert segment_status("S1", [ok, none, skip]) == "ok"
    assert segment_status("S1", [none, skip]) == "none"


def test_s5_s6_are_always_unchecked():
    running = item("env.discord_running", value=True)
    assert segment_status("S5", [running, item("s5.discord_internal", "unchecked")]) == "unchecked"
    assert segment_status("S6", [item("s6.remote", "unchecked")]) == "unchecked"
    assert segment_status("S5", [item("env.discord_running", verdict="bad")]) == "unchecked"


def test_summarize_segments_shape():
    segments = summarize_segments(RESULTS["mixed"].items)
    assert [s.id for s in segments] == ["S1", "S2", "S3", "S4", "S5", "S6"]
    by_id = {s.id: s for s in segments}
    assert by_id["S1"].status == "failed"
    assert by_id["S1"].unchecked_count == 1
    assert by_id["S2"].status == "bad"
    assert by_id["S5"].item_ids == ("env.discord_running", "s5.discord_internal")
    assert sum(len(s.item_ids) for s in segments) == len(RESULTS["mixed"].items)


# ---- rules.json(出荷する初期の規則) ----

RULES = {r.id: r for r in CONFIG.rules}


def test_shipped_rules_match_the_table():
    assert list(RULES) == [f"RL-{n}" for n in range(1, 13)]
    assert [r.id for r in CONFIG.rules if not r.enabled] == ["RL-9", "RL-12"]
    for rule in CONFIG.rules:
        assert rule.text.endswith("可能性")
        for cond in (*rule.when_all, *rule.when_any):
            assert all(i in ITEM_SPEC_BY_ID for i in cond.items)
        assert all(i in ITEM_SPEC_BY_ID for i in rule.evidence)
    assert RULES["RL-1"].symptoms == ("crackle",)
    assert RULES["RL-4"].symptoms == ("quiet", "muffled", "noise")
    assert RULES["RL-7"].symptoms == ("drop", "crackle")


def test_shipped_advice_has_no_placeholder_and_no_invented_text():
    """advice.json の文は §8.4 の実物の後に書く。それまでは空で、{{ も無い(AC-10)。"""
    text = (CONFIG_DIR / "advice.json").read_text(encoding="utf-8")
    assert "{{" not in text
    assert CONFIG.advice == {}


def holds(rule_id, *items):
    return rule_holds(RULES[rule_id], {i.id: i for i in items})


def test_rule_conditions():
    assert holds("RL-1", item("mic.clip_events", verdict="warn"))
    assert holds("RL-1", item("mic.clip_events", verdict="bad"))
    assert not holds("RL-1", item("mic.clip_events", verdict="ok"))
    assert not holds("RL-1", item("mic.clip_events"))
    assert not holds("RL-1", item("mic.clip_events", "failed"))
    assert not holds("RL-1")
    assert holds("RL-2", item("mic.speech_level_dbfs", verdict="bad"))
    assert holds("RL-3", item("mic.noise_floor_dbfs"), item("mic.snr_db", verdict="warn"))
    assert holds("RL-3", item("mic.noise_floor_dbfs", verdict="bad"), item("mic.snr_db"))
    assert not holds("RL-3", item("mic.noise_floor_dbfs", verdict="ok"), item("mic.snr_db"))
    assert holds("RL-4", item("mic.all_zero", value=True))
    assert not holds("RL-4", item("mic.all_zero", value=False))
    assert not holds("RL-4", item("mic.all_zero", value=1))
    assert holds("RL-4", item("mic.all_zero", value=False),
                 item("priv.mic_discord", verdict="bad", value="Deny"))
    assert not holds("RL-4", item("priv.mic_user", verdict="ok", value="Allow"))
    assert holds("RL-7", item("sys.mem_load_pct", verdict="warn"))
    assert holds("RL-10", item("tone.heard", verdict="bad", value="crackle"))
    assert not holds("RL-10", item("tone.heard", verdict="bad", value="none"))
    assert holds("RL-11", item("tone.heard", verdict="bad", value="none"))


def test_rule_net_conditions():
    gw_bad = item("net.gw.jitter_ms", verdict="bad")
    ref_ok = [item(f"net.ref.{leaf}", verdict="ok")
              for leaf in ("rtt_p95_ms", "jitter_ms", "fail_pct")]
    assert holds("RL-5", gw_bad, *ref_ok)
    assert not holds("RL-6", gw_bad, *ref_ok)
    ref_mixed = [ref_ok[0], ref_ok[1], item("net.ref.fail_pct", verdict="warn")]
    assert not holds("RL-5", gw_bad, *ref_mixed)
    assert holds("RL-6", gw_bad, *ref_mixed)
    # 比較先が測れなかったときは、どちらの規則も成立しない(測っていないものを根拠にしない)。
    ref_failed = [ref_ok[0], ref_ok[1], item("net.ref.fail_pct", "failed")]
    assert not holds("RL-5", gw_bad, *ref_failed)
    assert not holds("RL-6", gw_bad, *ref_failed)


def test_rule_duck_values_are_type_strict():
    for value in (0, 1, 2):
        assert holds("RL-8", item("duck.pref", value=value))
    assert not holds("RL-8", item("duck.pref", value=3))
    assert not holds("RL-8", item("duck.pref", value=True))
    assert not holds("RL-8", item("duck.pref", "failed"))


def test_disabled_rules_never_become_candidates():
    items = [item("echo.tone_in_mic_dbfs", verdict="bad")]
    assert pick_candidates(items, ["echo"], CONFIG.rules, {}) == []


def test_candidate_order_and_limit():
    result = RESULTS["many_candidates"]
    assert [c.rule_id for c in result.candidates] == ["RL-6", "RL-7", "RL-4"]
    no_symptom = pick_candidates(result.items, [], CONFIG.rules, {})
    assert [c.rule_id for c in no_symptom] == ["RL-4", "RL-6", "RL-7"]
    duck = pick_candidates(result.items, ["duck"], CONFIG.rules, {"RL-8": "次の文"})
    assert duck[0].rule_id == "RL-8"
    assert duck[0].advice == "次の文"
    assert len(duck) == 3
    for cand in duck:
        assert cand.confidence == "estimated"
        assert cand.text == RULES[cand.rule_id].text


def test_rule_id_order_is_numeric():
    items = [item("mic.speech_level_dbfs", verdict="bad"),
             item("tone.heard", verdict="bad", value="crackle")]
    picked = pick_candidates(items, [], CONFIG.rules, {})
    assert [c.rule_id for c in picked] == ["RL-2", "RL-10"]


def test_ac10_candidates_of_every_fixture():
    for name, result in RESULTS.items():
        assert len(result.candidates) <= 3, name
        for cand in result.candidates:
            assert cand.text.endswith("可能性"), name
            assert cand.confidence == "estimated", name
            assert cand.advice is None, name
    assert RESULTS["normal"].candidates == ()
    assert [c.rule_id for c in RESULTS["clipping"].candidates] == ["RL-1"]
    assert [c.rule_id for c in RESULTS["all_zero"].candidates] == ["RL-4"]
    assert [c.rule_id for c in RESULTS["mixed"].candidates] == ["RL-11", "RL-5"]
    assert RESULTS["uncalibrated"].candidates == ()


def shipped_rules():
    obj = read_json(CONFIG_DIR / "rules.json")
    assert isinstance(obj, list)
    return obj


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r[0].update({"text": "原因はマイクです"}),
        lambda r: r[0]["when_all"][0].update({"item": "mic.unknown"}),
        lambda r: r[0].update({"evidence": ["mic.unknown"]}),
        lambda r: r[0]["when_all"][0].update({"warn_over": 3}),
        lambda r: r[0]["when_all"][0].update({"verdict_in": ["terrible"]}),
        lambda r: r[0]["when_all"][0].pop("verdict_in"),
        lambda r: r[0].update({"when_all": [{"item": "duck.pref", "value_in": [None]}]}),
        lambda r: r[0].update({"when_all": [{"item_any": [], "status_in": ["failed"]}]}),
        lambda r: r[0].update({"when_all": [{"item": "duck.pref", "status_in": ["lost"]}]}),
        lambda r: r[0].update({"when_all": []}),
        lambda r: r[0].update({"symptoms": ["hiccup"]}),
        lambda r: r[0].update({"enabled": "yes"}),
        lambda r: r[0].update({"threshold": 5}),
        lambda r: r[0].pop("when_any"),
        lambda r: r[1].update({"id": "RL-1"}),
    ],
)
def test_broken_rules_are_rejected(change):
    rules = copy.deepcopy(shipped_rules())
    change(rules)
    with pytest.raises(ConfigError):
        parse_rules(rules)


def test_parse_rules_accepts_status_in_and_item_all():
    rules = parse_rules([{
        "id": "RL-90", "enabled": True, "symptoms": [],
        "when_all": [{"item_all": ["mic.snr_db", "mic.clip_ms"], "status_in": ["failed"]}],
        "when_any": [], "text": "合成の規則が成立した可能性", "evidence": [],
    }])
    both = {i.id: i for i in (item("mic.snr_db", "failed"), item("mic.clip_ms", "failed"))}
    assert rule_holds(rules[0], both)
    assert not rule_holds(rules[0], {"mic.snr_db": item("mic.snr_db", "failed")})


def test_advice_rules():
    assert parse_advice({"RL-1": "次に見る場所"}, CONFIG.rules) == {"RL-1": "次に見る場所"}
    for broken in ({"RL-1": "{{DISCORD_LABEL_X}} を見る"}, {"RL-99": "文"}, {"RL-1": ""},
                   {"RL-1": 3}, []):
        with pytest.raises(ConfigError):
            parse_advice(broken, CONFIG.rules)
