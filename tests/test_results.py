# 合成の結果10種に対する、結果・Markdown・check の性質のテスト(AC-7〜AC-9・AC-11)。
from __future__ import annotations

import copy
import json

import pytest

from tests.fixtures.synth import DEVICE_IN, DEVICE_OUT, IDENTITY, RESULTS, USER
from vccheck.io_safe import dump_json
from vccheck.model import (
    WORDS,
    check_result,
    result_from_obj,
    result_to_obj,
)
from vccheck.report_md import format_value, render_markdown
from vccheck.rules import NO_CANDIDATE_TEXT

NAMES = list(RESULTS)
HEADINGS = [f"## [{label}] " for _, label in WORDS]


def test_there_are_ten_fixtures_of_the_required_kinds():
    assert len(NAMES) == 10
    for required in ("normal", "all_failed", "all_skipped", "gate_not_met", "all_zero"):
        assert required in NAMES


@pytest.mark.parametrize("name", NAMES)
def test_ac7_no_substitute_values(name):
    obj = result_to_obj(RESULTS[name])
    assert len(obj["items"]) == 32
    for item in obj["items"]:
        if item["status"] != "measured":
            assert item["value"] is None, item["id"]
            assert item["confidence"] is None, item["id"]
            assert item["verdict"] is None, item["id"]
            assert item["reason"], item["id"]
        else:
            assert item["value"] is not None, item["id"]
            assert item["reason"] is None, item["id"]


def test_ac7_fixture_statuses_are_what_they_claim():
    def statuses(name):
        return {i.id: i.status for i in RESULTS[name].items}

    fixed = ("s5.discord_internal", "s6.remote")
    assert all(s == "measured" for i, s in statuses("normal").items() if i not in fixed)
    assert all(s == "failed" for i, s in statuses("all_failed").items() if i not in fixed)
    assert set(statuses("all_skipped").values()) == {"unchecked"}
    assert statuses("gate_not_met")["mic.speech_level_dbfs"] == "failed"
    assert statuses("all_zero")["mic.noise_floor_dbfs"] == "failed"


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("share", [False, True])
def test_ac8_seven_headings_in_order_and_counts(name, share):
    result = RESULTS[name]
    text = render_markdown(result, share=share, identity=IDENTITY)
    found = [line for line in text.split("\n") if line.startswith("## [")]
    assert len(found) == 7
    for line, start in zip(found, HEADINGS, strict=True):
        assert line.startswith(start)
    for key, label in WORDS:
        assert text.count(f"## [{label}]") == 1
        assert f"## [{label}] {result.counts[key]}件" in text
    lines = text.split("\n")
    header = "| " + " | ".join(label for _, label in WORDS) + " |"
    numbers = lines[lines.index(header) + 2].strip("|").split("|")
    assert sum(int(n) for n in numbers) == len(result.items) == 32
    assert sum(result.counts.values()) == 32


@pytest.mark.parametrize("name", NAMES)
def test_ac9_s5_s6_unchecked_and_no_ok_with_failed(name):
    result = RESULTS[name]
    by_id = {i.id: i for i in result.items}
    segments = {s.id: s for s in result.segments}
    assert segments["S5"].status == "unchecked"
    assert segments["S6"].status == "unchecked"
    assert by_id["s5.discord_internal"].status == "unchecked"
    assert by_id["s6.remote"].status == "unchecked"
    for seg in result.segments:
        if any(by_id[i].status == "failed" for i in seg.item_ids):
            assert seg.status != "ok", seg.id
    text = render_markdown(result) + render_markdown(result, share=True, identity=IDENTITY)
    assert "問題なし" not in text
    assert "問題なし" not in dump_json(result_to_obj(result))
    assert "| S5 Discord の内部 | 未チェック |" in text
    assert "| S6 通話相手 | 未チェック |" in text
    assert "通話相手のマイク・スピーカー・回線は測れない" in text
    assert "外から読む公式の方法を確認できていないため測らない" in text


def test_segment_states_of_fixtures():
    def states(name):
        return [s.status for s in RESULTS[name].segments]

    assert states("normal") == ["ok", "ok", "ok", "ok", "unchecked", "unchecked"]
    assert states("all_failed") == ["failed"] * 4 + ["unchecked"] * 2
    assert states("all_skipped") == ["unchecked"] * 6
    assert states("uncalibrated")[2:4] == ["none", "none"]
    assert states("clipping")[0] == "bad"
    # 無言の区間が大きい合成なので、測れなかった項目があっても「異常」が先に当たる。
    assert states("gate_not_met")[0] == "bad"
    assert states("mixed")[0] == "failed"


@pytest.mark.parametrize("name", NAMES)
def test_every_fixture_passes_check_and_round_trips(name):
    result = RESULTS[name]
    text = dump_json(result_to_obj(result))
    obj = json.loads(text)
    assert check_result(obj) == []
    assert result_from_obj(obj) == result
    assert render_markdown(result_from_obj(obj)) == render_markdown(result)


def test_markdown_shape_of_clipping_fixture():
    text = render_markdown(RESULTS["clipping"])
    assert text.startswith(
        "---\ntitle: 通話診断 2026-10-01 14:05\ncreated: 2026-10-01 14:05\n"
        "measured: 2026-10-01 14:05:00\ntags:\n  - 通話診断\n  - VcCheck\nvccheck: 1\n---\n"
        "# 通話診断 2026-10-01 14:05\n\n- 症状: 自分の声が割れる\n"
    )
    assert f"- 申告したデバイス: 入力 {DEVICE_IN} / 出力 {DEVICE_OUT}(申告)" in text
    assert "- 条件: 無言 3 秒 → 発話 7 秒、回線は TCP 20 回 × 2 か所\n" in text
    assert "- 閾値の較正: 13 項目中 13 項目(未較正の項目は判定なし)" in text
    assert "## 切り分け\n| 区間 | 状態 | 未チェックの項目 |\n|---|---|---|\n" in text
    assert "| S1 自分のマイク(Windows の入力) | 異常 | 0件 |" in text
    assert (
        "## 原因の可能性(最大3つ)\n1. マイクの入力が大きすぎて、波形が割れている可能性"
        "(根拠: クリップの回数 2800 回、最大の振幅 0.0 dBFS)\n"
    ) in text
    assert "| 項目 | 値 | 確度 | 判定 | 根拠 |" in text
    assert "| クリップの回数 | 2800 回 | 実測 | 2800 > 2 | — |" in text
    assert text.endswith(
        "\n---\n- 判定はすべて、私が thresholds.json に置いた値による。"
        "Discord の内部と通話相手は測っていない。\n"
    )
    assert "\r" not in text


def test_markdown_when_no_rule_holds():
    for name in ("normal", "all_skipped", "all_failed", "uncalibrated"):
        text = render_markdown(RESULTS[name])
        assert f"## 原因の可能性(最大3つ)\n{NO_CANDIDATE_TEXT}\n" in text
        assert "次に見る場所" not in text
    assert "(この回はマイク・回線を測っていない)" in render_markdown(RESULTS["all_skipped"])


def test_markdown_uncalibrated_and_reasons():
    text = render_markdown(RESULTS["uncalibrated"])
    assert "- 閾値の較正: 13 項目中 0 項目(未較正の項目は判定なし)" in text
    assert "閾値が未較正" in text
    assert "発話の検出は未較正のため確認していない" in text
    assert "確認した名前: Discord.exe" in text
    gate = render_markdown(RESULTS["gate_not_met"])
    assert "| 発話の大きさ | 声が検出できなかった(または無言の区間に声が入った) |" in gate
    assert "| 項目 | 理由 |" in gate
    zero = render_markdown(RESULTS["silent_zero"])
    assert "下限丸め / ドライバーのノイズゲートの可能性(未確認)" in zero


def test_markdown_escapes_pipes_and_newlines_in_tables():
    text = render_markdown(RESULTS["mixed"])
    row = "| マイクの許可(Discord) | キーが無い \\| C:\\Users\\TestUser\\x を<br>読めない |"
    assert row in text
    assert "次に見る場所: 未記入(advice.json に文が無い)" in text


def test_format_value():
    assert format_value(3, "回") == "3 回"
    assert format_value(-23.0, "dBFS") == "-23.0 dBFS"
    assert format_value(True, None) == "はい"
    assert format_value(False, None) == "いいえ"
    assert format_value("Allow", None) == "Allow"
    assert format_value(None, "ms") == "—"


@pytest.mark.parametrize("name", NAMES)
def test_ac11_share_output_has_no_device_or_user_name(name):
    shared = render_markdown(RESULTS[name], share=True, identity=IDENTITY)
    assert DEVICE_IN not in shared
    assert DEVICE_OUT not in shared
    assert USER not in shared
    assert USER.lower() not in shared.lower()
    assert "TestUser-PC" not in shared
    assert "C:\\" not in shared
    assert "- 申告したデバイス: 入力 入力デバイスA / 出力 出力デバイスA(申告)" in shared
    plain = render_markdown(RESULTS[name])
    assert DEVICE_IN in plain


def test_share_masks_names_inside_reasons():
    obj = copy.deepcopy(result_to_obj(RESULTS["mixed"]))
    obj["items"][1]["status"] = "failed"
    for key in ("value", "confidence", "verdict", "verdict_raw"):
        obj["items"][1][key] = None
    obj["items"][1]["reason"] = (
        f"{DEVICE_IN} を開けない D:\\work\\x.txt 192.168.1.23 fe80::1 ::1 TESTUSER "
        "\\\\TestUser-PC\\share end"
    )
    assert check_result(obj) == []
    shared = render_markdown(result_from_obj(obj), share=True, identity=IDENTITY)
    for secret in (DEVICE_IN, "D:\\work", "192.168.1.23", "fe80", "::1", "TESTUSER",
                   "TestUser-PC"):
        assert secret not in shared
    masked = "(パス) (IPアドレス) (IPアドレス) (IPアドレス) (ユーザー名) (パス)"
    assert f"入力デバイスA を開けない {masked} end" in shared
    # 日時は IP アドレスと取り違えない。
    assert "2026-10-01 14:05:00" in shared
