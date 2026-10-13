# CLI の run(この版は --skip-mic --skip-net だけ)のテスト(AC-16 の一部、FR-21・FR-22)。
# 収集は合成の値に差し替える。出力先は必ず一時フォルダ。
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.fixtures.synth import CONFIG_DIR
from vccheck import sysinfo
from vccheck.__main__ import main
from vccheck.model import ITEM_IDS, check_result, failed, measured

SKIP = ["--skip-mic", "--skip-net"]


def fake_collect(_measure, **_hooks):
    return [
        measured("env.discord_running", True, "確認した名前: Discord.exe"),
        measured("priv.mic_machine", "Allow"),
        measured("priv.mic_user", "Deny"),
        failed("priv.mic_discord", "Discord のエントリはあるが Value が無い"),
        measured("duck.pref", 1),
        measured("sys.cpu_avg_pct", 12.3),
        measured("sys.cpu_max_pct", 45.6),
        measured("sys.mem_load_pct", 78),
    ]


def forbidden_collect(_measure, **_hooks):
    raise AssertionError("測ってはならない場面で測定が始まった")


def names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


def test_run_writes_three_files_that_pass_check(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sysinfo, "collect", fake_collect)
    out = tmp_path / "out"
    assert main(["run", *SKIP, "--symptom", "duck", "--symptom", "quiet", "--out", str(out)]) == 0
    written = names(out)
    assert len(written) == 3
    stem = written[0][: -len(".json")]
    assert written == [f"{stem}.json", f"{stem}.md", f"{stem}.share.md"]
    data = (out / f"{stem}.json").read_bytes()
    assert data[:3] != b"\xef\xbb\xbf" and b"\r" not in data
    obj = json.loads(data.decode("utf-8"))
    assert check_result(obj) == []
    assert main(["check", str(out / f"{stem}.json")]) == 0
    assert [i["id"] for i in obj["items"]] == list(ITEM_IDS)
    by_id = {i["id"]: i for i in obj["items"]}
    for item_id, item in by_id.items():
        if item_id.startswith(("mic.", "net.")):
            assert (item["status"], item["value"]) == ("unchecked", None)
            assert item["reason"] == "実行時に指定されて飛ばした"
    for item_id in ("tone.heard", "echo.tone_in_mic_dbfs"):
        assert by_id[item_id]["status"] == "unchecked"
        assert by_id[item_id]["reason"] == "テスト音は確認なしでは再生しない"
    for item_id in ("dev.in.format", "dev.out.format"):
        assert (by_id[item_id]["status"], by_id[item_id]["value"]) == ("unchecked", None)
    assert by_id["sys.cpu_avg_pct"]["verdict"] == "none"
    assert by_id["priv.mic_user"]["verdict"] == "bad"
    assert obj["run"]["symptoms"] == ["duck", "quiet"]
    assert obj["run"]["conditions"] == {
        "input_declared": None, "output_declared": None, "input_default_used": None,
        "channels_used": None, "silent_s": 3, "speech_s": 7, "net_attempts": 20}
    assert obj["thresholds_calibrated"] == {"calibrated": 0, "total": 13}
    assert [s["status"] for s in obj["segments"]] == [
        "bad", "none", "unchecked", "none", "unchecked", "unchecked"]
    assert [c["rule_id"] for c in obj["candidates"]] == ["RL-4", "RL-8"]
    markdown = (out / f"{stem}.md").read_text(encoding="utf-8")
    assert "- 申告したデバイス: 申告なし(この回はデバイスを選んでいない)" in markdown
    assert "(この回はマイク・回線を測っていない)" in markdown
    assert "- 症状: 通話中に他の音が小さくなる / 自分の声が小さい・聞こえにくい" in markdown
    assert "問題なし" not in markdown
    printed = capsys.readouterr().out
    for name in written:
        assert name in printed


def test_run_without_skip_flags_is_not_implemented(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sysinfo, "collect", forbidden_collect)
    out = tmp_path / "out"
    for args in ([], ["--skip-mic"], ["--skip-net"], [*SKIP, "--confirm-tone"],
                 [*SKIP, "--input", "0"], [*SKIP, "--output", "Speakers"]):
        assert main(["run", *args, "--out", str(out)]) == 1
        assert "この版では未実装" in capsys.readouterr().err
    assert names(out) == []


@pytest.mark.parametrize("name", ["thresholds.json", "measure.json", "rules.json",
                                  "advice.json", "net_targets.json"])
def test_ac16_broken_config_exits_1_without_measuring(tmp_path, monkeypatch, capsys, name):
    monkeypatch.setattr(sysinfo, "collect", forbidden_collect)
    config = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, config)
    (config / name).write_text('{"broken": tru', encoding="utf-8")
    out = tmp_path / "out"
    assert main(["run", *SKIP, "--out", str(out)], config_dir=config) == 1
    assert name in capsys.readouterr().err
    assert names(out) == []


def test_ac16_existing_output_exits_4_and_adds_nothing(tmp_path, monkeypatch):
    from vccheck import __main__ as cli

    monkeypatch.setattr(cli, "output_stem", lambda _: "2026-10-01_140500")
    monkeypatch.setattr(sysinfo, "collect", fake_collect)
    out = tmp_path / "out"
    assert main(["run", *SKIP, "--out", str(out)]) == 0
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    assert len(before) == 3
    monkeypatch.setattr(sysinfo, "collect", forbidden_collect)
    assert main(["run", *SKIP, "--out", str(out)]) == 4
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before
    (out / "2026-10-01_140500.json").unlink()
    (out / "2026-10-01_140500.md").unlink()
    assert main(["run", *SKIP, "--out", str(out)]) == 4
    assert names(out) == ["2026-10-01_140500.share.md"]


def test_fr21_cancel_saves_nothing_and_exits_5(tmp_path, monkeypatch):
    def cancelled(_measure, **_hooks):
        raise KeyboardInterrupt

    monkeypatch.setattr(sysinfo, "collect", cancelled)
    out = tmp_path / "out"
    assert main(["run", *SKIP, "--out", str(out)]) == 5
    assert names(out) == []


def test_run_all_failed_still_passes_check(tmp_path, monkeypatch):
    def all_failed(_measure, **_hooks):
        return [failed(i, "PermissionError") for i in (
            "env.discord_running", "priv.mic_machine", "priv.mic_user", "priv.mic_discord",
            "duck.pref", "sys.cpu_avg_pct", "sys.cpu_max_pct", "sys.mem_load_pct")]

    monkeypatch.setattr(sysinfo, "collect", all_failed)
    out = tmp_path / "out"
    assert main(["run", *SKIP, "--out", str(out)]) == 0
    result = next(p for p in out.iterdir() if p.suffix == ".json")
    obj = json.loads(result.read_text(encoding="utf-8"))
    assert check_result(obj) == []
    assert [s["status"] for s in obj["segments"]] == [
        "failed", "failed", "unchecked", "failed", "unchecked", "unchecked"]
    assert obj["candidates"] == []
    assert all(i["value"] is None for i in obj["items"])


def test_run_with_the_real_collectors_passes_check(tmp_path):
    """実物の読み取り(レジストリ・プロセス名・負荷)を短い測定時間で通す。値は確かめない。"""
    config = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, config)
    measure = json.loads((config / "measure.json").read_text(encoding="utf-8"))
    measure.update({"silent_s": 0.1, "speech_s": 0.1, "sys_interval_ms": 100})
    (config / "measure.json").write_text(json.dumps(measure), encoding="utf-8")
    out = tmp_path / "out"
    assert main(["run", *SKIP, "--out", str(out)], config_dir=config) == 0
    written = names(out)
    assert len(written) == 3
    assert main(["check", str(out / written[0])]) == 0
    obj = json.loads((out / written[0]).read_text(encoding="utf-8"))
    assert {s["id"]: s["status"] for s in obj["segments"]}["S5"] == "unchecked"
    for item in obj["items"]:
        assert (item["value"] is None) == (item["status"] != "measured")
    assert sum(p.stat().st_size for p in out.iterdir()) <= 64 * 1024
