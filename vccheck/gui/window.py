# MainWindow。5つの画面を1枚のウィンドウの中で切り替え、画面設計書 §2 の遷移を持つ。
# 測定は別のスレッド(worker.py)で動かし、中止できる。保存とクリップボードはここで扱う。
# ステータスバーには SCR-04 の保存結果とコピー結果だけを出す。
from __future__ import annotations

from PySide6.QtGui import QCloseEvent, QGuiApplication
from PySide6.QtWidgets import QDialog, QMainWindow, QStackedWidget, QWidget

from vccheck.gui import texts
from vccheck.gui.backend import (
    Backend,
    DeviceList,
    MultimediaUnavailableError,
    SaveReport,
    Settings,
)
from vccheck.gui.dialogs import ToneConfirmDialog, ToneHeardDialog
from vccheck.gui.result_screen import ResultScreen
from vccheck.gui.screens import (
    BlockedScreen,
    DeviceScreen,
    RunScreen,
    Screen,
    SymptomScreen,
)
from vccheck.gui.worker import RunThread
from vccheck.runner import RunOutput, RunRequest

MIN_SIZE = (720, 560)
DEFAULT_SIZE = (880, 640)
EXIT_OK = 0
EXIT_CANNOT_MEASURE = 2


class MainWindow(QMainWindow):
    """ウィザードの本体。exit_code は、閉じたときにアプリが返す終了コード。"""

    def __init__(self, backend: Backend, settings: Settings,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend = backend
        self._thread: RunThread | None = None
        self._confirming = False
        self._cancelling = False
        self._share_text = ""
        self._report: SaveReport | None = None
        self.exit_code = EXIT_OK
        self.setWindowTitle(texts.WINDOW_TITLE)
        self.setMinimumSize(*MIN_SIZE)
        self.resize(*DEFAULT_SIZE)

        blocked: str | None = None
        try:
            devices = backend.devices()
        except MultimediaUnavailableError:
            devices = DeviceList((), ())
            blocked = texts.MSG_19
        else:
            if not devices.inputs:
                blocked = texts.MSG_18
        self.device_counts = (len(devices.inputs), len(devices.outputs))
        self.multimedia_loaded = blocked != texts.MSG_19

        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)
        self.symptom_screen = SymptomScreen(settings)
        self.device_screen = DeviceScreen(settings, devices)
        self.run_screen = RunScreen()
        self.result_screen = ResultScreen()
        self.blocked_screen = BlockedScreen()
        for screen in (self.symptom_screen, self.device_screen, self.run_screen,
                       self.result_screen, self.blocked_screen):
            self._stack.addWidget(screen)
        self.statusBar()

        self.symptom_screen.next_requested.connect(self._to_devices)
        self.symptom_screen.close_requested.connect(self.close)
        self.device_screen.back_requested.connect(self._to_symptoms)
        self.device_screen.start_requested.connect(self._on_start)
        self.run_screen.cancel_requested.connect(self._on_cancel)
        self.result_screen.copy_requested.connect(self._on_copy)
        self.result_screen.open_folder_requested.connect(self._on_open_folder)
        self.result_screen.again_requested.connect(self._on_again)
        self.result_screen.close_requested.connect(self.close)
        self.blocked_screen.close_requested.connect(self.close)

        if blocked is None:
            self._show(self.symptom_screen)
        else:
            self.blocked_screen.set_message(blocked)
            self.exit_code = EXIT_CANNOT_MEASURE
            self._show(self.blocked_screen)

    # ---- 画面の切り替え ----

    def current_screen(self) -> QWidget:
        screen = self._stack.currentWidget()
        if screen is None:
            raise RuntimeError("画面が1枚も無い")
        return screen

    def _show(self, screen: Screen) -> None:
        self._stack.setCurrentWidget(screen)
        if screen is not self.result_screen:
            self.statusBar().clearMessage()
        screen.focus_first()

    def _to_symptoms(self) -> None:
        self._show(self.symptom_screen)

    def _to_devices(self) -> None:
        self.symptom_screen.show_cancelled(False)
        self._show(self.device_screen)

    # ---- 測定 ----

    def _on_start(self) -> None:
        if not self.device_screen.can_start() or self._thread is not None or self._confirming:
            return
        if not self.device_screen.tone_wanted():
            self._start(confirm_tone=False)
            return
        # テスト音は、確認の後にしか鳴らさない(INV-8)。既定は「鳴らさない」。
        self._confirming = True
        dialog = ToneConfirmDialog(self)
        dialog.finished.connect(self._on_tone_confirmed)
        dialog.finished.connect(dialog.deleteLater)
        dialog.open()

    def _on_tone_confirmed(self, code: int) -> None:
        self._confirming = False
        if code == QDialog.DialogCode.Accepted.value:
            self._start(confirm_tone=True)

    def _start(self, *, confirm_tone: bool) -> None:
        request: RunRequest = self.device_screen.make_request(
            self.symptom_screen.selected(), confirm_tone=confirm_tone)
        self._cancelling = False
        self.run_screen.begin(request)
        self._show(self.run_screen)
        thread = RunThread(self._backend, request, self)
        thread.stage_changed.connect(self.run_screen.set_stage)
        thread.tone_question.connect(self._on_tone_question)
        thread.finished.connect(self._on_run_finished)
        self._thread = thread
        thread.start()

    def _on_tone_question(self) -> None:
        dialog = ToneHeardDialog(self)
        dialog.finished.connect(lambda _code: self._on_tone_answered(dialog))
        dialog.open()

    def _on_tone_answered(self, dialog: ToneHeardDialog) -> None:
        if self._thread is not None:
            self._thread.answer_tone(dialog.answer())
        dialog.deleteLater()

    def _on_cancel(self) -> None:
        """中止。測定部が止まるのを待ってから SCR-01 に戻る(_on_run_finished)。"""
        if self._thread is not None:
            self._cancelling = True
            self.run_screen.set_cancelling()
            self._thread.cancel()

    def _on_run_finished(self) -> None:
        thread = self._thread
        if thread is None:
            return
        self._thread = None
        thread.wait()
        self.run_screen.stop()
        output = thread.output
        thread.deleteLater()
        if output is None or self._cancelling:
            # 中止したとき(結果が出た直後の中止も含む)と、測定部が途中で止まったとき。
            # どちらも何も保存しない。
            self.symptom_screen.show_cancelled(True)
            self._show(self.symptom_screen)
            return
        self._finish(output)

    def _finish(self, output: RunOutput) -> None:
        report = self._backend.save(output)
        self._share_text = output.share_markdown
        self._report = report
        self.result_screen.show_result(output.obj, report)
        self._show(self.result_screen)
        if report.problem is None:
            self.statusBar().showMessage(texts.MSG_21.format(stem=report.stem))
        elif report.exists:
            self.statusBar().showMessage(texts.MSG_23)
        else:
            self.statusBar().showMessage(texts.MSG_22.format(reason=report.problem))

    def is_measuring(self) -> bool:
        return self._thread is not None

    # ---- SCR-04 のボタン ----

    def _on_copy(self) -> None:
        QGuiApplication.clipboard().setText(self._share_text)
        self.statusBar().showMessage(texts.MSG_24)

    def _on_open_folder(self) -> None:
        if self._report is not None and self._report.folder is not None:
            self._backend.open_folder(self._report.folder)

    def _on_again(self) -> None:
        self.symptom_screen.reset()
        self.symptom_screen.show_cancelled(False)
        self._show(self.symptom_screen)

    # ---- ウィンドウを閉じる ----

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._thread is not None:
            # 測定中の閉じるボタンは、中止と同じ(画面設計書 §6)。ウィンドウは閉じない。
            event.ignore()
            self._on_cancel()
            return
        event.accept()

    def shutdown(self) -> None:
        """アプリを終える前に、測定のスレッドを止めて待つ。"""
        thread = self._thread
        if thread is not None:
            thread.cancel()
            thread.wait()
