# 測定を画面とは別のスレッドで動かす(AC-G6)。段階の通知・中止の合図・
# テスト音の聞こえ方の問い合わせを、実行層の RunHooks とシグナルの間でつなぐ。
# 結果は終わってから画面のスレッドが受け取る。ここでは保存も描画もしない。
from __future__ import annotations

import threading
import traceback

from PySide6.QtCore import QObject, QThread, Signal

from vccheck.gui.backend import Backend
from vccheck.runner import RunCancelledError, RunHooks, RunOutput, RunRequest

_ANSWER_POLL_S = 0.05


class RunThread(QThread):
    """1回の測定。終わったら finished が出るので、output を読む(止まったときは None)。"""

    stage_changed = Signal(str, object)
    tone_question = Signal()

    def __init__(self, backend: Backend, request: RunRequest,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend = backend
        self._request = request
        self._cancel = threading.Event()
        self._answered = threading.Event()
        self._answer: str | None = None
        self.output: RunOutput | None = None

    def cancel(self) -> None:
        """中止の合図を出す。測定部は次の待ちの切れ目で止まる。"""
        self._cancel.set()

    def answer_tone(self, answer: str | None) -> None:
        """DLG-02 の回答を渡す。回答なしで閉じたときは None。"""
        self._answer = answer
        self._answered.set()

    def _on_stage(self, stage: str, seconds: float | None) -> None:
        self.stage_changed.emit(stage, seconds)

    def _ask_tone(self) -> str | None:
        self._answer = None
        self._answered.clear()
        self.tone_question.emit()
        while not self._answered.wait(_ANSWER_POLL_S):
            if self._cancel.is_set():
                raise RunCancelledError
        return self._answer

    def run(self) -> None:
        hooks = RunHooks(on_stage=self._on_stage, cancelled=self._cancel.is_set,
                         ask_tone_heard=self._ask_tone)
        try:
            output = self._backend.run(self._request, hooks)
        except RunCancelledError:
            return
        except Exception:
            # 画面を巻き込んで落とさない。中身は標準エラーにだけ出す(画面設計書に出す場所が無い)。
            traceback.print_exc()
            return
        if not self._cancel.is_set():
            self.output = output
