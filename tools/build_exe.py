# 開発用: PyInstaller で dist\VcCheck.exe を作る(コンソールなし。既定は1ファイル)。
# config\ を同梱し、使わない Qt の部品を外して小さくする。作業用のファイルは build\ に置く。
# exe と build\ はリポジトリに入れない(.gitignore)。署名はしない。
from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "VcCheck"
BUILD_DIR = ROOT / "build"
DIST_DIR = ROOT / "dist"
ENTRY = ROOT / "tools" / "exe_entry.py"

# 名前で読み込む物(PyInstaller が import 文から見つけられない)。
HIDDEN_IMPORTS = ["vccheck.gui.app", "PySide6.QtMultimedia"]
# 使わない Python 側のモジュール。
EXCLUDED_MODULES = [
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQuickControls2",
    "PySide6.QtQuick3D", "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtPrintSupport", "PySide6.QtSvg",
    "PySide6.QtSvgWidgets", "PySide6.QtXml", "PySide6.QtDBus", "PySide6.QtConcurrent",
    "PySide6.QtMultimediaWidgets", "PySide6.QtSpatialAudio", "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "tkinter", "unittest", "pydoc", "doctest", "pytest", "PIL",
]
# 同梱から外すファイル(名前の一部。小文字で比べる)。
# - opengl32sw: ソフトウェア描画の OpenGL。ウィジェットだけの画面では使わない。
# - qt6pdf / qpdf: PDF の画像形式プラグインと、その本体。
# - multimedia のプラグインと ffmpeg(av*・sw*): 動画・音声ファイルの再生用。
#   デバイスの一覧(QMediaDevices)は QtMultimedia 本体だけで動く。
# - translations: Qt 自身の訳。画面の文言はすべて自前。
DROPPED_FILES = [
    "opengl32sw.dll", "qt6pdf", "qpdf.dll", "qt6quick", "qt6qml", "qt6opengl", "qt6svg",
    "qsvg", "plugins\\multimedia\\", "plugins/multimedia/", "avcodec-", "avformat-", "avutil-",
    "swresample-", "swscale-", "translations\\", "translations/", "qt6virtualkeyboard",
]

SPEC_TEMPLATE = '''# -*- mode: python ; coding: utf-8 -*-
# tools/build_exe.py が毎回書き直す。手で直さない。
DROPPED = {dropped!r}


def kept(entries):
    return [e for e in entries
            if not any(part in e[0].lower() or part in str(e[1]).lower() for part in DROPPED)]


a = Analysis(
    [{entry!r}],
    pathex=[{root!r}],
    binaries=[],
    datas=[({config!r}, "config")],
    hiddenimports={hidden!r},
    excludes={excluded!r},
    noarchive=False,
)
a.binaries = kept(a.binaries)
a.datas = kept(a.datas)
pyz = PYZ(a.pure)
{tail}
'''
ONEFILE_TAIL = '''exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name={name!r}, console=False, upx=False, debug=False,
)
'''
ONEDIR_TAIL = '''exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name={name!r}, console=False, upx=False, debug=False,
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name={name!r})
'''


def write_spec(onedir: bool, hidden: list[str], dropped: list[str]) -> Path:
    tail = (ONEDIR_TAIL if onedir else ONEFILE_TAIL).format(name=NAME)
    text = SPEC_TEMPLATE.format(
        dropped=dropped, entry=str(ENTRY), root=str(ROOT), config=str(ROOT / "config"),
        hidden=hidden, excluded=EXCLUDED_MODULES, tail=tail)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    spec = BUILD_DIR / f"{NAME}.spec"
    spec.write_text(text, encoding="utf-8", newline="\n")
    return spec


def folder_size(folder: Path) -> int:
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())


def smoke(exe: Path) -> int:
    """作った exe で画面を作って閉じる。1回目は展開の時間を含む。"""
    code = 0
    for label in ("1回目", "2回目"):
        started = time.monotonic()
        done = subprocess.run([str(exe), "gui", "--smoke"], capture_output=True, timeout=120)
        seconds = time.monotonic() - started
        line = done.stdout.decode("utf-8", errors="replace").strip()
        print(f"smoke {label}: 終了コード {done.returncode} / {seconds:.1f} 秒 / {line}")
        code = code or done.returncode
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=f"dist\\{NAME}.exe を作る")
    parser.add_argument("--onedir", action="store_true",
                        help="1ファイルではなく、フォルダ(dist\\VcCheck\\)に作る")
    parser.add_argument("--smoke", action="store_true",
                        help="作った後に「VcCheck.exe gui --smoke」を2回動かして確かめる")
    parser.add_argument("--dist", type=Path, default=DIST_DIR,
                        help="出力先のフォルダ(既定はリポジトリの dist)")
    parser.add_argument("--keep-all", action="store_true",
                        help="Qt の部品を外さずに全部入れる(大きさの比較用)")
    parser.add_argument("--without-multimedia", action="store_true",
                        help="QtMultimedia が無くても作る(その exe の画面は SCR-05 になる)")
    args = parser.parse_args()
    for module in ("PyInstaller", "PySide6.QtWidgets"):
        if importlib.util.find_spec(module) is None:
            print(f"{module} が無い。pip で入れてから実行する", file=sys.stderr)
            return 1
    hidden = list(HIDDEN_IMPORTS)
    if importlib.util.find_spec("PySide6.QtMultimedia") is None:
        if not args.without_multimedia:
            print("PySide6.QtMultimedia が無い(PySide6-Addons に入っている)。\n"
                  "このまま作ると、exe の画面は「QtMultimedia を読み込めない」で止まる。\n"
                  "`pip install PySide6-Addons` の後で実行するか、--without-multimedia を付ける",
                  file=sys.stderr)
            return 1
        hidden.remove("PySide6.QtMultimedia")
    spec = write_spec(args.onedir, hidden, [] if args.keep_all else DROPPED_FILES)
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
               "--distpath", str(args.dist), "--workpath", str(BUILD_DIR / "work"), str(spec)]
    started = time.monotonic()
    done = subprocess.run(command, cwd=ROOT)
    if done.returncode != 0:
        return done.returncode
    exe = args.dist / NAME / f"{NAME}.exe" if args.onedir else args.dist / f"{NAME}.exe"
    size = folder_size(exe.parent) if args.onedir else exe.stat().st_size
    print(f"{exe}  {size / 1024 / 1024:.1f} MB"
          f"({'フォルダ全体' if args.onedir else '1ファイル'})"
          f"  ビルド {time.monotonic() - started:.0f} 秒")
    return smoke(exe) if args.smoke else 0


if __name__ == "__main__":
    raise SystemExit(main())
