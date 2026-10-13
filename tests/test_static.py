# ソースに対する機械的な検査(AC-14・AC-15・AC-23 の grep と同じ式、INV-9、§0)。
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "vccheck"
SOURCES = sorted(PACKAGE.rglob("*.py"))
TRACKED_SUFFIXES = {".py", ".json", ".md", ".toml", ".txt", ""}
SKIP_DIRS = {".git", "spike", "results", "__pycache__", ".pytest_cache", ".mypy_cache",
             ".ruff_cache", ".venv"}


def hits(pattern: str, flags: int = 0) -> list[str]:
    found = []
    for path in SOURCES:
        for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1):
            if re.search(pattern, line, flags):
                found.append(f"{path.name}:{number}: {line.strip()}")
    return found


def repository_files() -> list[Path]:
    files = []
    for path in ROOT.rglob("*"):
        if path.is_file() and not SKIP_DIRS.intersection(path.relative_to(ROOT).parts):
            files.append(path)
    return files


def test_there_are_sources():
    assert len(SOURCES) >= 9


def test_ac14_no_registry_write_api():
    assert hits(r"SetValue|DeleteKey|DeleteValue|CreateKey|KEY_WRITE|KEY_ALL_ACCESS") == []


def test_ac15_no_audio_file_output():
    assert hits(r"import wave|\.wav|tobytes\(\)\.hex|print\(.*sample") == []


def test_ac23_no_discord_api_or_http():
    pattern = r"discord\.py|api/v[0-9]|Authorization|Bearer|websocket|requests|urllib|http\.client"
    assert hits(pattern, re.IGNORECASE) == []


def test_ac23_host_names_live_only_in_net_targets():
    """gateway を含むホスト名は config\\net_targets.json 以外に書かない(INV-3)。"""
    allowed = ROOT / "config" / "net_targets.json"
    host = re.compile(r"[A-Za-z0-9-]*gateway[A-Za-z0-9-]*\.[A-Za-z0-9.-]+", re.IGNORECASE)
    for path in repository_files():
        if path == allowed or path.suffix not in TRACKED_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8")
        assert host.search(text) is None, path.name
    assert hits(r"gateway", re.IGNORECASE) == []


def test_inv9_only_standard_library_imports():
    allowed = set(sys.stdlib_module_names) | {"vccheck", "PySide6"}
    for path in SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                assert module.split(".")[0] in allowed, f"{path.name}: {module}"


def test_inv9_no_external_commands():
    assert hits(r"subprocess|os\.system|os\.popen|tasklist|ping\.exe|netsh|powershell",
                re.IGNORECASE) == []


def test_phase_scope_no_audio_or_network_modules_yet():
    """この版は録音・再生・回線の測定を持たない(仕様書 §12 の Phase 3 以降)。"""
    assert hits(r"^\s*(import|from)\s+(socket|PySide6|ssl|http|ftplib|smtplib)\b") == []
    for name in ("capture.py", "tone.py", "net_probe.py"):
        assert not (PACKAGE / name).exists()


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_each_source_starts_with_a_short_japanese_comment(path):
    lines = path.read_text(encoding="utf-8").split("\n")
    head = []
    for line in lines:
        if not line.startswith("#"):
            break
        head.append(line)
    assert 1 <= len(head) <= 3
    assert re.search(r"[ぁ-んァ-ヶ一-龠]", head[0])


def test_no_personal_information_or_screen_labels_in_repository():
    """実名・メール・ユーザーのパス・ホスト名を入れない。Discord の画面の項目名は s5 の理由だけ。"""
    personal = re.compile(r"[A-Za-z]:\\Users\\(?!TestUser|Someone|<)[^\\\s\"']+|@gmail\.com",
                          re.IGNORECASE)
    for path in repository_files():
        if path.suffix not in TRACKED_SUFFIXES or path.name == "test_static.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert personal.search(text) is None, path.name
        assert "{{" not in text or path.suffix == ".py", path.name
    for config in (ROOT / "config").glob("*.json"):
        text = config.read_text(encoding="utf-8")
        for label in ("入力感度", "ノイズ抑制", "エコー除去"):
            assert label not in text, config.name


def test_text_files_are_utf8_without_bom_and_lf():
    for path in repository_files():
        if path.suffix not in TRACKED_SUFFIXES:
            continue
        data = path.read_bytes()
        assert not data.startswith(b"\xef\xbb\xbf"), path.name
        assert b"\r" not in data, path.name
