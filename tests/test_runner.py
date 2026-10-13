# 実行層(runner.py)のテスト: capabilities、進み具合と中止の合図、壊れた設定ファイルの名前、
# exe のときの config\ と保存先の場所。読み取りは合成の値に差し替え、出力先は一時フォルダ。
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

import pytest

from tests.fixtures.synth import CONFIG, CONFIG_DIR
from vccheck import runner, sysinfo
from vccheck.__main__ import main
from vccheck.model import ConfigError, check_result, measured
from vccheck.runner import (
    Capabilities,
    ConfigFileError,
    RunCancelledError,
    RunHooks,
    RunNotSupportedError,
    RunRequest,
    perform_run,
)

STAMP = datetime(2026, 10, 1, 14, 5, 0)


def fake_collect(_measure, **_hooks):
    return [
        measured("env.discord_running", False, "確認した名前: Discord.exe"),
        measured("priv.mic_machine", "Allow"),
        measured("priv.mic_user", "Allow"),
        measured("priv.mic_discord", "Allow"),
        measured("duck.pref", 3),
        measured("sys.cpu_avg_pct", 1.0),
        measured("sys.cpu_max_pct", 2.0),
        measured("sys.mem_load_pct", 3),
    ]


def test_this_version_can_measure_neither_mic_nor_net_nor_tone():
    able = runner.capabilities()
    assert able == Capabilities(mic=False, net=False, tone=False)
    assert able.any is False
    assert Capabilities(mic=False, net=True, tone=False).any is True


@pytest.mark.parametrize("request_", [
    RunRequest(skip_mic=False), RunRequest(skip_net=False), RunRequest(confirm_tone=True)])
def test_unsupported_measurements_are_refused_before_measuring(monkeypatch, request_):
    def forbidden(_measure, **_hooks):
        raise AssertionError("測ってはならない場面で測定が始まった")

    monkeypatch.setattr(sysinfo, "collect", forbidden)
    assert "この版では未実装" in (runner.unsupported_reason(request_) or "")
    with pytest.raises(RunNotSupportedError, match="この版では未実装"):
        perform_run(request_, CONFIG, measured_at=STAMP)


def test_perform_run_reports_stages_and_keeps_declared_devices(monkeypatch):
    monkeypatch.setattr(sysinfo, "collect", fake_collect)
    stages: list[tuple[str, float | None]] = []
    request = RunRequest(symptoms=("duck",), input_declared="Headset (TestUser-PC)",
                         output_declared="Speakers (TestUser-PC)", input_default_used=True)
    output = perform_run(request, CONFIG, measured_at=STAMP,
                         hooks=RunHooks(on_stage=lambda s, d: stages.append((s, d))))
    assert stages == [("prepare", None), ("summary", None)]
    assert check_result(output.obj) == []
    run = output.obj["run"]
    assert isinstance(run, dict)
    assert run["conditions"] == {
        "input_declared": "Headset (TestUser-PC)", "output_declared": "Speakers (TestUser-PC)",
        "input_default_used": True, "channels_used": None, "silent_s": 3, "speech_s": 7,
        "net_attempts": 20}
    assert list(output.files("2026-10-01_140500")) == [
        "2026-10-01_140500.json", "2026-10-01_140500.md", "2026-10-01_140500.share.md"]
    assert "Headset (TestUser-PC)" in output.markdown
    assert "Headset (TestUser-PC)" not in output.share_markdown
    assert "入力デバイスA" in output.share_markdown


def test_cancel_stops_the_wait_inside_the_load_sampling(monkeypatch):
    """本物の sample_load が、実行層の渡した待ち方の中で中止の合図を受け取る。"""
    monkeypatch.setattr(sysinfo, "read_discord_running", lambda _names: measured(
        "env.discord_running", False))
    for name in ("read_mic_consent", "read_mic_consent_discord", "read_ducking"):
        monkeypatch.setattr(sysinfo, name, lambda *_args: measured("duck.pref", 3))
    calls = {"count": 0}

    def cancelled() -> bool:
        calls["count"] += 1
        return calls["count"] > 3

    with pytest.raises(RunCancelledError):
        perform_run(RunRequest(), CONFIG, measured_at=STAMP, hooks=RunHooks(cancelled=cancelled))
    assert calls["count"] == 4


def test_hooks_sleep_waits_when_not_cancelled():
    RunHooks().sleep(0.01)
    with pytest.raises(RunCancelledError):
        RunHooks(cancelled=lambda: True).sleep(60.0)
    assert RunHooks().ask_tone_heard() is None


@pytest.mark.parametrize("name", ["thresholds.json", "measure.json", "rules.json",
                                  "advice.json", "net_targets.json"])
def test_broken_config_names_the_file(tmp_path, name):
    config = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, config)
    (config / name).write_text("[1, 2", encoding="utf-8")
    with pytest.raises(ConfigFileError) as caught:
        runner.load_config(config)
    assert caught.value.file_name == name
    assert isinstance(caught.value, ConfigError)
    (config / name).write_text('"形が違う"', encoding="utf-8")
    with pytest.raises(ConfigFileError) as caught:
        runner.load_config(config)
    assert caught.value.file_name == name
    (config / name).unlink()
    with pytest.raises(ConfigFileError) as caught:
        runner.load_config(config)
    assert caught.value.file_name == name


# ---- exe(PyInstaller)のときの場所 ----


def freeze(monkeypatch, exe_dir: Path, unpacked: Path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(unpacked), raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "VcCheck.exe"))


def test_from_source_the_config_is_the_repository_config():
    assert runner.is_frozen() is False
    assert runner.default_config_dir() == CONFIG_DIR
    assert runner.app_dir() == CONFIG_DIR.parent
    assert runner.resolve_results_dir("results") == Path("results")


def test_frozen_config_comes_from_the_bundle_unless_one_sits_beside_the_exe(tmp_path,
                                                                           monkeypatch):
    exe_dir, unpacked = tmp_path / "app", tmp_path / "_MEI123"
    exe_dir.mkdir()
    shutil.copytree(CONFIG_DIR, unpacked / "config")
    freeze(monkeypatch, exe_dir, unpacked)
    assert runner.is_frozen() is True
    assert runner.default_config_dir() == unpacked / "config"
    assert runner.load_config(runner.default_config_dir()).measure.silent_s == 3
    # exe の隣に config\ を置くと、フォルダごとそちらを使う(同梱の物と混ぜない)。
    shutil.copytree(CONFIG_DIR, exe_dir / "config")
    assert runner.default_config_dir() == exe_dir.resolve() / "config"
    (exe_dir / "config" / "advice.json").unlink()
    with pytest.raises(ConfigFileError) as caught:
        runner.load_config(runner.default_config_dir())
    assert caught.value.file_name == "advice.json"


def test_frozen_results_never_go_into_the_unpack_folder(tmp_path, monkeypatch):
    exe_dir, unpacked = tmp_path / "app", tmp_path / "_MEI123"
    exe_dir.mkdir()
    unpacked.mkdir()
    freeze(monkeypatch, exe_dir, unpacked)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.chdir(unpacked)
    # 既定(results_dir が null)は、ソースから動かすときと同じ場所(仕様書 §9.1)。
    assert runner.resolve_results_dir(None) == tmp_path / "local" / "VcCheck" / "results"
    # 相対パスは exe のフォルダから数える。作業フォルダや展開先にはならない。
    assert runner.resolve_results_dir("results") == exe_dir.resolve() / "results"
    assert runner.resolve_results_dir(str(tmp_path / "abs")) == tmp_path / "abs"
    for chosen in (runner.resolve_results_dir(None), runner.resolve_results_dir("results")):
        assert unpacked not in chosen.parents


def test_frozen_exe_without_arguments_opens_the_gui(tmp_path, monkeypatch):
    from vccheck import __main__ as cli

    seen: list[tuple[bool, Path]] = []
    monkeypatch.setattr(cli, "cmd_gui", lambda smoke, folder: seen.append((smoke, folder)) or 0)
    freeze(monkeypatch, tmp_path, tmp_path)
    monkeypatch.setattr(sys, "argv", ["VcCheck.exe"])
    assert main(config_dir=CONFIG_DIR) == 0
    assert seen == [(False, CONFIG_DIR)]
    # 引数があれば CLI として動く。
    monkeypatch.setattr(sys, "argv", ["VcCheck.exe", "--selftest"])
    assert main(config_dir=CONFIG_DIR) == 0
    monkeypatch.setattr(sys, "argv", ["VcCheck.exe", "gui", "--smoke"])
    assert main(config_dir=CONFIG_DIR) == 0
    assert seen == [(False, CONFIG_DIR), (True, CONFIG_DIR)]
    # 引数を明示して呼んだとき(テストなど)は、空でも GUI にしない。
    assert main([], config_dir=CONFIG_DIR) == 1
    assert len(seen) == 2


def test_cli_survives_missing_standard_streams(tmp_path, monkeypatch):
    """コンソールの無い exe では sys.stdout と sys.stderr が None になる。"""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    try:
        assert main(["check", str(tmp_path / "none.json")]) == 1
        assert main(["unknown"]) == 1
        assert main(["--selftest"]) == 0
        assert sys.stdout is not None and sys.stderr is not None
    finally:
        for stream in (sys.stdout, sys.stderr):
            if stream is not None:
                stream.close()
