# 開発用: README に載せる画面の画像(docs/images/gui-*.png)を作る。
# デバイスは合成の名前、結果は合成の検体(tests/fixtures)だけを使い、実機では何も測らない。
# ウィンドウは画面に出さず、QWidget.grab() で中身を画像にする。配布物には含めない。
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QApplication, QWidget  # noqa: E402

from tests.fixtures import synth  # noqa: E402
from tests.fixtures.gui_stub import StubBackend  # noqa: E402
from vccheck import runner  # noqa: E402
from vccheck.gui.backend import DeviceList  # noqa: E402
from vccheck.gui.window import MainWindow  # noqa: E402

OUT_DIR = ROOT / "docs" / "images"
# 画像に出す保存先。実在しない例のパスで、何も書き込まない。
SHOWN_FOLDER = Path(r"D:\VcCheck\results")
# この版の run が返す形(マイク・回線・テスト音は未チェック)の合成の検体。
FIXTURE = "this_version"
SYMPTOM = "SYM-05"


def settle(app: QApplication, seconds: float = 0.2) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def click(root: QWidget, name: str) -> None:
    button = root.findChild(QAbstractButton, name)
    if button is None:
        raise SystemExit(f"{name} が見つからない")
    button.click()


def save(window: MainWindow, name: str) -> Path:
    path = OUT_DIR / name
    if not window.grab().save(str(path), "PNG"):
        raise SystemExit(f"画像を書けない: {path.name}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="README 用の画面の画像を作る(合成データだけ)")
    parser.add_argument("--fixture", default=FIXTURE, help="SCR-04 に描く合成の検体の名前")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    # PC の配色(ダークモード)に左右されないように、画像は明るい配色で作る。
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    backend = StubBackend(
        # 測れる内容は、この版の実行層の値をそのまま使う(画像を実際の画面と同じにする)。
        capabilities=runner.capabilities(),
        devices=DeviceList((synth.DEVICE_IN,), (synth.DEVICE_OUT,), 0, 0),
        fixture=args.fixture,
        shown_folder=SHOWN_FOLDER,
    )
    window = MainWindow(backend, backend.load())
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    window.show()
    settle(app)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    click(window.symptom_screen, SYMPTOM)
    settle(app)
    written.append(save(window, "gui-symptoms.png"))
    click(window.symptom_screen, "SYM-11")
    settle(app)
    written.append(save(window, "gui-devices.png"))
    click(window.device_screen, "DEV-11")
    deadline = time.monotonic() + 10.0
    while window.current_screen() is not window.result_screen:
        if time.monotonic() > deadline:
            raise SystemExit("結果の画面に進まなかった")
        settle(app, 0.05)
    settle(app)
    written.append(save(window, "gui-result.png"))
    window.shutdown()
    window.close()
    style = app.style().objectName()
    for path in written:
        print(f"{path.relative_to(ROOT).as_posix()}  ({style})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
