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
GUI_SOURCES = sorted((PACKAGE / "gui").rglob("*.py"))
TRACKED_SUFFIXES = {".py", ".json", ".md", ".toml", ".txt", ""}
SKIP_DIRS = {".git", "spike", "results", "build", "dist", "__pycache__", ".pytest_cache",
             ".mypy_cache", ".ruff_cache", ".venv"}


def hits(pattern: str, flags: int = 0, sources: list[Path] | None = None) -> list[str]:
    found = []
    for path in SOURCES if sources is None else sources:
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
    assert len(SOURCES) >= 19
    assert len(GUI_SOURCES) >= 9


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
    assert hits(r"^\s*(import|from)\s+(socket|ssl|http|ftplib|smtplib)\b") == []
    for name in ("capture.py", "tone.py", "net_probe.py"):
        assert not (PACKAGE / name).exists()
    # PySide6 を使うのは画面(vccheck/gui/)だけ。CLI は PySide6 が無くても動く。
    outside = [path for path in SOURCES if path not in GUI_SOURCES]
    assert hits(r"^\s*(import|from)\s+PySide6\b", sources=outside) == []
    # 録音・再生の部品は、画面からも使わない(デバイスの名前の一覧だけを読む)。
    assert hits(r"QAudioSource|QAudioSink|QMediaPlayer|QSoundEffect|QMediaRecorder") == []


def test_ac_g8_gui_uses_no_colors_or_style_sheets():
    """画面設計書 AC-G8 の grep と同じ式。"""
    assert hits(r"setStyleSheet|QPalette|QColor", sources=GUI_SOURCES) == []


def test_ac_g10_gui_does_not_import_measuring_or_judging_modules():
    """画面設計書 AC-G10 の grep と同じ式。画面は実行層(runner.py)だけを呼ぶ。"""
    names = r"(analysis|judge|segments|rules|sysinfo|capture|net_probe|tone)"
    assert hits(r"(import|from) +vccheck\." + names, sources=GUI_SOURCES) == []
    # 「from vccheck import sysinfo」の形でも持ち込まない。
    assert hits(r"from +vccheck +import +.*\b" + names + r"\b", sources=GUI_SOURCES) == []


def test_gui_wording_lives_only_in_texts():
    """画面に出す文は texts.py に集める(画面設計書に無い文言を、ほかのファイルで足さない)。"""
    japanese = re.compile(r"[ぁ-んァ-ヶ一-龠]")
    for path in GUI_SOURCES:
        # app.py の日本語は、動作確認(smoke)が標準出力に出す行だけ。
        if path.name in ("texts.py", "app.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skipped: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    skipped.add(id(first.value))  # docstring
            elif isinstance(node, ast.Raise) and node.exc is not None:
                skipped.update(id(part) for part in ast.walk(node.exc))  # 例外の文
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) not in skipped:
                    assert not japanese.search(node.value), f"{path.name}:{node.lineno}"


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
