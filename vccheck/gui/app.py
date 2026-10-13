# GUI の入口(python -m vccheck gui、または exe を引数なしで起動)。
# config\ が壊れていれば DLG-03 を出して終了コード 1。測定を始められなければ SCR-05 で終了コード 2。
# smoke は、画面を作ってすぐ閉じるだけの動作確認(exe のビルドの後に使う)。
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from vccheck.gui.backend import Backend, DefaultBackend
from vccheck.gui.dialogs import ConfigErrorDialog
from vccheck.gui.window import MainWindow
from vccheck.runner import ConfigFileError

EXIT_OK = 0
EXIT_ERROR = 1
SMOKE_MS = 500


def _application() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication(sys.argv[:1])


def run_gui(*, config_dir: Path | None = None, smoke: bool = False,
            backend: Backend | None = None) -> int:
    """画面を出して、閉じられるまで待つ。戻り値は終了コード。"""
    app = _application()
    used = DefaultBackend(config_dir) if backend is None else backend
    try:
        settings = used.load()
    except ConfigFileError as error:
        dialog = ConfigErrorDialog(error.file_name)
        if smoke:
            QTimer.singleShot(SMOKE_MS, dialog.close)
            print(f"gui smoke: 設定ファイル {error.file_name} の形が壊れている")
        dialog.exec()
        return EXIT_ERROR
    window = MainWindow(used, settings)
    window.show()
    if smoke:
        QTimer.singleShot(SMOKE_MS, window.close)
    app.exec()
    window.shutdown()
    if smoke:
        # 名前は出さない。台数と、QtMultimedia を読み込めたかだけを出す。
        inputs, outputs = window.device_counts
        loaded = "読み込めた" if window.multimedia_loaded else "読み込めない"
        screen = window.current_screen().objectName()
        print(f"gui smoke: QtMultimedia {loaded} / 入力 {inputs} 台 / 出力 {outputs} 台"
              f" / 画面 {screen}")
        return EXIT_OK
    return window.exit_code
