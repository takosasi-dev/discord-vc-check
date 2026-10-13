# check の規則(§9.2)と io_safe(原子的な書き出し・読み込み・マスク)のテスト(AC-17 ほか)。
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from tests.fixtures.synth import RESULTS
from vccheck import io_safe
from vccheck.io_safe import (
    Identity,
    JsonReadError,
    OutputError,
    OutputExistsError,
    default_results_dir,
    dump_json,
    local_identity,
    mask_text,
    read_json,
    write_outputs,
)
from vccheck.model import check_result, result_to_obj

GOOD = result_to_obj(RESULTS["mixed"])
MEASURED = next(n for n, i in enumerate(GOOD["items"]) if i["verdict"] == "ok")
JUDGED_NONE = next(n for n, i in enumerate(GOOD["items"]) if i["verdict"] == "none")
FAILED = next(n for n, i in enumerate(GOOD["items"]) if i["status"] == "failed")


def changed(change):
    obj = copy.deepcopy(GOOD)
    change(obj)
    return check_result(obj)


def test_good_result_has_no_violation():
    assert check_result(copy.deepcopy(GOOD)) == []
    assert len(GOOD["candidates"]) == 2


@pytest.mark.parametrize(
    "change",
    [
        lambda o: o.update({"vccheck": 2}),
        lambda o: o.update({"vccheck": True}),
        lambda o: o.update({"vccheck": "1"}),
        lambda o: o.pop("vccheck"),
        lambda o: o.update({"extra": 1}),
        lambda o: o.update({"created_at": "きのう"}),
        lambda o: o["run"].update({"device_id": "x"}),
        lambda o: o["run"].update({"symptoms": ["hiccup"]}),
        lambda o: o["run"]["conditions"].update({"input_declared": ""}),
        lambda o: o["run"]["conditions"].pop("net_attempts"),
        lambda o: o["items"][MEASURED].update({"status": "lost"}),
        lambda o: o["items"][MEASURED].update({"value": None}),
        lambda o: o["items"][MEASURED].update({"confidence": "sure"}),
        lambda o: o["items"][MEASURED].update({"confidence": None}),
        lambda o: o["items"][MEASURED].update({"verdict": "good"}),
        lambda o: o["items"][MEASURED].update({"reason": "理由"}),
        lambda o: o["items"][MEASURED].update({"verdict_raw": None}),
        lambda o: o["items"][MEASURED].update({"segment": "S7"}),
        lambda o: o["items"][MEASURED].update({"value": [1]}),
        lambda o: o["items"][MEASURED].update({"value": float("nan")}),
        lambda o: o["items"][MEASURED].update({"value": float("inf")}),
        lambda o: o["items"][MEASURED].update({"device_id": "x"}),
        lambda o: o["items"][MEASURED].pop("note"),
        lambda o: o["items"][JUDGED_NONE].update({"verdict_raw": "1 > 0"}),
        lambda o: o["items"][FAILED].update({"value": 0}),
        lambda o: o["items"][FAILED].update({"value": ""}),
        lambda o: o["items"][FAILED].update({"confidence": "measured"}),
        lambda o: o["items"][FAILED].update({"verdict": "none"}),
        lambda o: o["items"][FAILED].update({"reason": ""}),
        lambda o: o["items"][FAILED].update({"reason": None}),
        lambda o: o["items"][1].update({"id": o["items"][0]["id"]}),
        lambda o: o["items"].pop(),
        lambda o: o["counts"].update({"ok": o["counts"]["ok"] + 1}),
        lambda o: o["counts"].pop("none"),
        lambda o: o["counts"].update({"good": 0}),
        lambda o: o["segments"][4].update({"status": "ok"}),
        lambda o: o["segments"][5].update({"status": "none"}),
        lambda o: o["segments"].pop(4),
        lambda o: o["segments"][0].update({"status": "fine"}),
        lambda o: o["candidates"].extend(copy.deepcopy(o["candidates"])),
        lambda o: o["candidates"][0].update({"text": "原因はマイクです"}),
        lambda o: o["candidates"][0].update({"confidence": "measured"}),
        lambda o: o["candidates"][0].update({"advice": ""}),
        lambda o: o["thresholds_calibrated"].update({"calibrated": 99}),
    ],
)
def test_each_violation_is_found(change):
    assert changed(change) != []


def test_non_object_is_a_violation():
    assert check_result([]) == ["最上位が object でない"]
    assert check_result(None) != []


def test_violations_are_one_line_each():
    lines = changed(lambda o: (o.update({"vccheck": 2}), o["items"][FAILED].update({"value": 0})))
    assert len(lines) == 2
    assert all("\n" not in line for line in lines)


# ---- io_safe ----


def test_ac17_json_is_utf8_without_bom_and_lf(tmp_path):
    text = dump_json(GOOD)
    path = write_outputs(tmp_path, {"r.json": text})[0]
    data = path.read_bytes()
    assert data[:3] != b"\xef\xbb\xbf"
    assert b"\r" not in data
    assert data.endswith(b"\n")
    assert "通話相手".encode() in data
    assert read_json(path) == GOOD
    assert [p.name for p in tmp_path.iterdir()] == ["r.json"]


def test_markdown_file_is_lf_only(tmp_path):
    path = write_outputs(tmp_path, {"r.md": "a\nb\n"})[0]
    assert path.read_bytes() == b"a\nb\n"


def test_write_does_not_overwrite(tmp_path):
    write_outputs(tmp_path, {"a.json": "1\n"})
    with pytest.raises(OutputExistsError):
        write_outputs(tmp_path, {"b.md": "new\n", "a.json": "2\n"})
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.json"]
    assert (tmp_path / "a.json").read_text(encoding="utf-8") == "1\n"


def test_write_creates_missing_folder(tmp_path):
    out = tmp_path / "x" / "y"
    assert [p.name for p in write_outputs(out, {"a.md": "a\n", "a.json": "{}\n"})] == [
        "a.md", "a.json"]


def test_write_failure_leaves_nothing(tmp_path, monkeypatch):
    calls = []

    def failing_rename(source, target):
        calls.append(target)
        if len(calls) == 2:
            raise PermissionError("合成の失敗")
        return original(source, target)

    original = io_safe.os.rename
    monkeypatch.setattr(io_safe.os, "rename", failing_rename)
    with pytest.raises(OutputError):
        write_outputs(tmp_path, {"a.json": "1\n", "a.md": "2\n", "a.share.md": "3\n"})
    assert list(tmp_path.iterdir()) == []


def test_write_into_a_file_path_fails(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(OutputError):
        write_outputs(blocker / "sub", {"a.json": "1\n"})


def test_read_json_rejects_broken_input(tmp_path):
    for name, data in (("a.json", b"{"), ("b.json", b'{"x": NaN}'), ("c.json", b'{"x": Infinity}'),
                       ("d.json", b"\xff\xfe"), ("e.json", b"\xef\xbb\xbf{}")):
        path = tmp_path / name
        path.write_bytes(data)
        with pytest.raises(JsonReadError):
            read_json(path)
    with pytest.raises(ValueError):
        dump_json({"x": float("nan")})


def test_default_results_dir():
    assert default_results_dir("X:/somewhere") == Path("X:/somewhere")
    assert default_results_dir(None, {"LOCALAPPDATA": "X:/local"}) == Path(
        "X:/local") / "VcCheck" / "results"
    with pytest.raises(OutputError):
        default_results_dir(None, {})


def test_local_identity_from_environment():
    env = {"USERNAME": "TestUser", "USERPROFILE": "C:\\Users\\TestUser",
           "COMPUTERNAME": "TESTUSER-PC"}
    identity = local_identity(env)
    assert identity.usernames == ("TestUser",)
    assert identity.hostnames == ("TESTUSER-PC",)
    assert local_identity({}) == Identity((), ())


def test_mask_text_finds_user_names_in_paths():
    nobody = Identity((), ())
    text = "C:\\Users\\Someone\\a.txt を読めない。Someone の設定"
    masked = mask_text(text, input_name=None, output_name=None, identity=nobody)
    assert "Someone" not in masked
    assert masked == "(パス) を読めない。(ユーザー名) の設定"
