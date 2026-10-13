# CLI の report・check・stats・--selftest と終了コードのテスト(AC-2・AC-16・AC-17)。
# 出力先は必ず一時フォルダ(tmp_path)を指定する。既定の保存先には書かない。
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.fixtures.synth import CONFIG_DIR, DEVICE_IN, RESULTS, USER
from vccheck.__main__ import default_config_dir, load_config, main, output_stem
from vccheck.io_safe import dump_json
from vccheck.model import ConfigError, check_result, result_to_obj

NO_HOST = '{"host": null, "port": 443}'


def write_result(folder: Path, name: str = "mixed", stem: str = "2026-10-01_140500") -> Path:
    path = folder / f"{stem}.json"
    path.write_text(dump_json(result_to_obj(RESULTS[name])), encoding="utf-8", newline="\n")
    return path


def names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


def test_default_config_dir_is_the_repository_config():
    assert default_config_dir() == CONFIG_DIR
    assert names(CONFIG_DIR) == ["advice.json", "measure.json", "net_targets.json", "rules.json",
                                 "thresholds.json"]


def test_ac2_selftest_exits_zero(capsys):
    assert main(["--selftest"]) == 0
    assert "selftest: OK" in capsys.readouterr().out


def test_selftest_fails_on_broken_config(tmp_path, capsys):
    shutil.copytree(CONFIG_DIR, tmp_path / "config")
    (tmp_path / "config" / "thresholds.json").write_text("{", encoding="utf-8")
    assert main(["--selftest"], config_dir=tmp_path / "config") == 1
    assert "thresholds.json" in capsys.readouterr().err


def test_check_accepts_good_result(tmp_path, capsys):
    assert main(["check", str(write_result(tmp_path))]) == 0
    assert capsys.readouterr().out.strip() == "違反なし"
    assert names(tmp_path) == ["2026-10-01_140500.json"]


def test_ac16_check_broken_json_exits_3(tmp_path, capsys):
    broken = tmp_path / "broken.json"
    broken.write_text("{ これは JSON ではない", encoding="utf-8")
    assert main(["check", str(broken)]) == 3
    assert "broken.json" in capsys.readouterr().out
    nan = tmp_path / "nan.json"
    nan.write_text('{"vccheck": NaN}', encoding="utf-8")
    assert main(["check", str(nan)]) == 3


def test_check_lists_violations_one_per_line(tmp_path, capsys):
    obj = result_to_obj(RESULTS["mixed"])
    obj["vccheck"] = 2
    obj["segments"][4]["status"] = "ok"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    assert main(["check", str(path)]) == 3
    lines = capsys.readouterr().out.strip().split("\n")
    assert lines == ["bad.json: vccheck が 1 でない",
                     "bad.json: segments: S5 の status が unchecked でない"]


def test_missing_file_exits_1(tmp_path, capsys):
    assert main(["check", str(tmp_path / "none.json")]) == 1
    assert main(["report", str(tmp_path / "none.json"), "--out", str(tmp_path)]) == 1
    assert main(["stats", str(tmp_path / "none.json")]) == 1
    assert names(tmp_path) == []


def test_report_writes_markdown(tmp_path, capsys):
    source = write_result(tmp_path)
    out = tmp_path / "out"
    assert main(["report", str(source), "--out", str(out)]) == 0
    assert names(out) == ["2026-10-01_140500.md"]
    data = (out / "2026-10-01_140500.md").read_bytes()
    assert data[:3] != b"\xef\xbb\xbf"
    assert b"\r" not in data
    assert "# 通話診断 2026-10-01 14:05" in data.decode("utf-8")
    assert DEVICE_IN in data.decode("utf-8")
    assert str(out / "2026-10-01_140500.md") in capsys.readouterr().out


def test_ac11_report_share(tmp_path, monkeypatch):
    monkeypatch.setenv("USERNAME", USER)
    monkeypatch.setenv("USERPROFILE", f"C:\\Users\\{USER}")
    monkeypatch.setenv("COMPUTERNAME", "TestUser-PC")
    source = write_result(tmp_path)
    out = tmp_path / "out"
    assert main(["report", str(source), "--share", "--out", str(out)]) == 0
    assert names(out) == ["2026-10-01_140500.share.md"]
    shared = (out / "2026-10-01_140500.share.md").read_text(encoding="utf-8")
    assert DEVICE_IN not in shared
    assert USER not in shared
    assert "入力デバイスA" in shared


def test_ac16_same_name_exits_4_and_adds_nothing(tmp_path, capsys):
    source = write_result(tmp_path)
    out = tmp_path / "out"
    assert main(["report", str(source), "--out", str(out)]) == 0
    before = (out / "2026-10-01_140500.md").read_bytes()
    assert main(["report", str(source), "--out", str(out)]) == 4
    assert "同名の出力が既にある" in capsys.readouterr().err
    assert names(out) == ["2026-10-01_140500.md"]
    assert (out / "2026-10-01_140500.md").read_bytes() == before
    assert main(["report", str(source), "--share", "--out", str(out)]) == 0
    assert main(["report", str(source), "--share", "--out", str(out)]) == 4
    assert names(out) == ["2026-10-01_140500.md", "2026-10-01_140500.share.md"]


def test_report_refuses_invalid_result(tmp_path):
    obj = result_to_obj(RESULTS["mixed"])
    obj["items"][0]["status"] = "lost"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out"
    assert main(["report", str(path), "--out", str(out)]) == 3
    assert not out.exists()


def test_report_default_out_comes_from_measure_json(tmp_path):
    """--out を省いたときは measure.json の results_dir。既定の場所には書かずに確かめる。"""
    config = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, config)
    target = tmp_path / "configured"
    measure = json.loads((config / "measure.json").read_text(encoding="utf-8"))
    measure["results_dir"] = str(target)
    (config / "measure.json").write_text(json.dumps(measure), encoding="utf-8")
    assert main(["report", str(write_result(tmp_path))], config_dir=config) == 0
    assert names(target) == ["2026-10-01_140500.md"]
    (config / "measure.json").write_text("[]", encoding="utf-8")
    assert main(["report", str(write_result(tmp_path, stem="x"))], config_dir=config) == 1


def test_stats_table(tmp_path, capsys):
    first = write_result(tmp_path, "normal", "a")
    second = write_result(tmp_path, "clipping", "b")
    third = write_result(tmp_path, "all_skipped", "c")
    assert main(["stats", str(first), str(second), str(third)]) == 0
    out = capsys.readouterr().out
    assert "結果 3 件" in out
    assert "| ID | 項目 | 単位 | 件数 | 最小 | 中央値 | 最大 |" in out
    assert "| mic.clip_events | クリップの回数 | 回 | 2 | 0 | 1400 | 2800 |" in out
    assert "| sys.mem_load_pct | メモリ使用率の最大 | % | 2 | 50 | 50 | 50 |" in out
    assert "mic.all_zero" not in out
    assert "priv.mic_user" not in out
    assert "warn" not in out and "bad" not in out


def test_stats_with_nothing_measured_and_with_broken_input(tmp_path, capsys):
    assert main(["stats", str(write_result(tmp_path, "all_skipped"))]) == 0
    assert "数値が測れた項目は無い" in capsys.readouterr().out
    broken = tmp_path / "broken.json"
    broken.write_text("[", encoding="utf-8")
    assert main(["stats", str(broken)]) == 3


def test_argument_errors_exit_1(capsys):
    assert main([]) == 1
    assert main(["unknown"]) == 1
    assert main(["check"]) == 1
    assert main(["run", "--symptom", "hiccup"]) == 1
    assert main(["stats"]) == 1
    capsys.readouterr()


def test_gui_is_not_implemented_in_this_version(capsys):
    assert main(["gui"]) == 1
    assert "この版では未実装" in capsys.readouterr().err


def test_output_stem():
    from datetime import datetime

    assert output_stem(datetime(2026, 10, 1, 14, 5, 0)) == "2026-10-01_140500"


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("measure.json", '{"silent_s": 3}'),
        ("measure.json", "not json"),
        ("thresholds.json", "{}"),
        ("net_targets.json", '{"gw": {"host": "", "port": 443}, "ref": ' + NO_HOST + "}"),
        ("net_targets.json", '{"gw": {"host": null, "port": 0}, "ref": ' + NO_HOST + "}"),
        ("net_targets.json", '{"gw": ' + NO_HOST + "}"),
        ("rules.json", "{}"),
        ("advice.json", '{"RL-1": "{{DISCORD_LABEL_INPUT}} を見る"}'),
        ("advice.json", '{"RL-99": "文"}'),
    ],
)
def test_fr22_broken_config_is_rejected(tmp_path, name, content):
    config = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, config)
    assert load_config(config) is not None
    (config / name).write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(config)
    (config / name).unlink()
    with pytest.raises(ConfigError):
        load_config(config)


def test_shipped_config_shape():
    config = load_config(CONFIG_DIR)
    assert config.measure.provisional is True
    assert (config.measure.silent_s, config.measure.speech_s) == (3, 7)
    assert config.measure.discord_process_names == ("Discord.exe",)
    assert config.measure.results_dir is None
    # 回線の測定先は、実物(仕様書 §8.5)の後に決める。それまでは未確定(null)。
    assert config.net_targets.gw.host is None
    assert config.net_targets.ref.host is None
    assert (config.net_targets.gw.port, config.net_targets.ref.port) == (443, 443)


def test_fixture_file_written_by_test_passes_check(tmp_path):
    path = write_result(tmp_path, "normal")
    assert check_result(json.loads(path.read_text(encoding="utf-8"))) == []
