# SCR-01 症状・SCR-02 デバイスと測定内容・SCR-03 測定中・SCR-05 開始できない。
# 要素 ID(SYM-01 など)を objectName にする。Enter と Esc は各画面が受ける(画面設計書 §4〜§8)。
# どの測定ができるかは実行層の capabilities から受け取り、画面の中では決めない。
from __future__ import annotations

import math

from PySide6.QtCore import QElapsedTimer, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from vccheck.gui import texts
from vccheck.gui.backend import DeviceList, Settings
from vccheck.gui.parts import BIG_SCALE, HUGE_SCALE, make_label
from vccheck.runner import (
    STAGE_MIC_SILENT,
    STAGE_MIC_SPEECH,
    STAGE_NET,
    STAGE_TONE,
    STAGES,
    RunRequest,
)

_ENTER_KEYS = (Qt.Key.Key_Return, Qt.Key.Key_Enter)
_TICK_MS = 200


def _button_row(*buttons: QPushButton) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addStretch(1)
    for button in buttons:
        row.addWidget(button)
    return row


def _chain_tab_order(widgets: list[QWidget]) -> None:
    for first, second in zip(widgets, widgets[1:], strict=False):
        QWidget.setTabOrder(first, second)


class Screen(QWidget):
    """MainWindow の中で切り替える1画面。表示したときに、最初の部品へフォーカスを置く。"""

    def focus_first(self) -> None:
        raise NotImplementedError


class SymptomScreen(Screen):
    """SCR-01。症状は0個でも次へ進める(AC-G2)。"""

    next_requested = Signal()
    close_requested = Signal()

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SCR-01")
        layout = QVBoxLayout(self)
        layout.addWidget(make_label(texts.STEP_SYMPTOMS, "STEP-01", bold=True))
        self._cancelled = make_label(texts.MSG_09, "SYM-14", bold=True)
        self._cancelled.setVisible(False)
        layout.addWidget(self._cancelled)
        layout.addWidget(make_label(texts.MSG_01, "SYM-09"))
        self._checks: list[tuple[str, QCheckBox]] = []
        for number, (symptom, text) in enumerate(texts.SYMPTOM_LABELS, start=1):
            check = QCheckBox(text)
            check.setObjectName(f"SYM-{number:02d}")
            layout.addWidget(check)
            self._checks.append((symptom, check))
        layout.addStretch(1)
        layout.addWidget(make_label(texts.MSG_02, "SYM-10"))
        layout.addWidget(make_label(
            texts.MSG_03.format(total=settings.calibrated_total, n=settings.calibrated),
            "SYM-12"))
        self._close = QPushButton(texts.BUTTON_CLOSE)
        self._close.setObjectName("SYM-13")
        self._next = QPushButton(texts.BUTTON_NEXT)
        self._next.setObjectName("SYM-11")
        layout.addLayout(_button_row(self._close, self._next))
        self._close.clicked.connect(self.close_requested)
        self._next.clicked.connect(self.next_requested)
        _chain_tab_order([*(check for _, check in self._checks), self._close, self._next])

    def selected(self) -> tuple[str, ...]:
        return tuple(symptom for symptom, check in self._checks if check.isChecked())

    def reset(self) -> None:
        """もう一度測るの後は、前回の選択を引き継がない。"""
        for _, check in self._checks:
            check.setChecked(False)

    def show_cancelled(self, shown: bool) -> None:
        self._cancelled.setVisible(shown)

    def focus_first(self) -> None:
        self._checks[0][1].setFocus()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in _ENTER_KEYS:
            self.next_requested.emit()
        elif event.key() == Qt.Key.Key_Escape:
            self.close_requested.emit()
        else:
            super().keyPressEvent(event)


def estimate_seconds(settings: Settings, *, mic: bool, net: bool, tone: bool) -> int:
    """所要時間の目安(MSG-05)。measure.json の値から数える(画面設計書 §5 の式)。"""
    measure = settings.measure
    total_ms = 0.0
    if mic:
        total_ms += measure.discard_head_ms + (measure.silent_s + measure.speech_s) * 1000.0
    if net:
        total_ms += measure.net_attempts * measure.net_interval_ms
    if tone:
        total_ms += measure.tone_s * 1000.0
    return math.ceil(total_ms / 1000.0)


class DeviceScreen(Screen):
    """SCR-02。実行層が測れない内容のチェックは、オフのまま無効にする。"""

    start_requested = Signal()
    back_requested = Signal()

    def __init__(self, settings: Settings, devices: DeviceList,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SCR-02")
        self._settings = settings
        self._devices = devices
        able = settings.capabilities
        layout = QVBoxLayout(self)
        layout.addWidget(make_label(texts.STEP_DEVICES, "STEP-01", bold=True))
        layout.addWidget(make_label(texts.MSG_04, "DEV-03"))
        self._input = self._combo("DEV-01", devices.inputs, devices.default_input)
        self._output = self._combo("DEV-02", devices.outputs, devices.default_output)
        form = QFormLayout()
        form.addRow(texts.LABEL_INPUT, self._input)
        form.addRow(texts.LABEL_OUTPUT, self._output)
        layout.addLayout(form)
        layout.addWidget(make_label(texts.LABEL_CONTENTS))
        measure = settings.measure
        self._mic = self._check(
            "DEV-04",
            texts.CHECK_MIC.format(silent=f"{measure.silent_s:g}", speech=f"{measure.speech_s:g}"),
            enabled=able.mic, checked=able.mic)
        self._net = self._check("DEV-05", texts.CHECK_NET, enabled=able.net, checked=able.net)
        self._tone = self._check("DEV-06", texts.CHECK_TONE,
                                 enabled=able.tone and bool(devices.outputs), checked=False)
        for check in (self._mic, self._net, self._tone):
            layout.addWidget(check)
            check.toggled.connect(self._refresh)
        self._estimate = make_label("", "DEV-07")
        layout.addWidget(self._estimate)
        layout.addWidget(make_label(texts.MSG_06, "DEV-08"))
        # DEV-09 は、仕様書 Q-13 が確認されるまで表示しない(画面設計書 §14 G-4)。
        self._muted_note = make_label(texts.MSG_08, "DEV-09")
        self._muted_note.setVisible(False)
        layout.addWidget(self._muted_note)
        self._choose_one = make_label(texts.MSG_07, "DEV-12", bold=True)
        layout.addWidget(self._choose_one)
        # DEV-90 は画面設計書に無い。測定部がマイク・回線・テスト音のどれも測れない版でだけ出す。
        self._limited = make_label(texts.DEV_90, "DEV-90")
        layout.addWidget(self._limited)
        layout.addStretch(1)
        self._back = QPushButton(texts.BUTTON_BACK)
        self._back.setObjectName("DEV-10")
        self._start = QPushButton(texts.BUTTON_START)
        self._start.setObjectName("DEV-11")
        layout.addLayout(_button_row(self._back, self._start))
        self._back.clicked.connect(self.back_requested)
        self._start.clicked.connect(self.start_requested)
        _chain_tab_order([self._input, self._output, self._mic, self._net, self._tone,
                          self._back, self._start])
        self._refresh()

    @staticmethod
    def _combo(name: str, names: tuple[str, ...], default: int | None) -> QComboBox:
        combo = QComboBox()
        combo.setObjectName(name)
        for index, device in enumerate(names):
            combo.addItem(device + texts.DEFAULT_SUFFIX if index == default else device)
        if default is not None:
            combo.setCurrentIndex(default)
        return combo

    @staticmethod
    def _check(name: str, text: str, *, enabled: bool, checked: bool) -> QCheckBox:
        check = QCheckBox(text)
        check.setObjectName(name)
        check.setChecked(checked)
        check.setEnabled(enabled)
        return check

    def _refresh(self) -> None:
        mic, net, tone = self._mic.isChecked(), self._net.isChecked(), self._tone.isChecked()
        self._estimate.setText(texts.MSG_05.format(
            n=estimate_seconds(self._settings, mic=mic, net=net, tone=tone)))
        if self._settings.capabilities.any:
            nothing = not (mic or net or tone)
            self._start.setEnabled(not nothing)
            self._choose_one.setVisible(nothing)
            self._limited.setVisible(False)
        else:
            # どれも測れない版では、Windows の設定と PC の負荷だけの測定を始められるようにする。
            self._start.setEnabled(True)
            self._choose_one.setVisible(False)
            self._limited.setVisible(True)

    def tone_wanted(self) -> bool:
        return self._tone.isChecked()

    def can_start(self) -> bool:
        return self._start.isEnabled()

    def _declared(self, combo: QComboBox, names: tuple[str, ...]) -> str | None:
        index = combo.currentIndex()
        if 0 <= index < len(names) and names[index]:
            return names[index]
        return None

    def make_request(self, symptoms: tuple[str, ...], *, confirm_tone: bool) -> RunRequest:
        """画面の選択を、CLI の引数と同じ形の指定にする。"""
        devices = self._devices
        return RunRequest(
            symptoms=symptoms,
            skip_mic=not self._mic.isChecked(),
            skip_net=not self._net.isChecked(),
            confirm_tone=confirm_tone,
            input_declared=self._declared(self._input, devices.inputs),
            output_declared=self._declared(self._output, devices.outputs),
            input_default_used=(devices.default_input is not None
                                and self._input.currentIndex() == devices.default_input),
        )

    def focus_first(self) -> None:
        self._input.setFocus()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in _ENTER_KEYS:
            if self._start.isEnabled():
                self.start_requested.emit()
        elif event.key() == Qt.Key.Key_Escape:
            self.back_requested.emit()
        else:
            super().keyPressEvent(event)


def skipped_stages(request: RunRequest) -> frozenset[str]:
    """指定から、飛ばす段階を決める(画面設計書 §6)。"""
    skipped: set[str] = set()
    if request.skip_mic:
        skipped.update((STAGE_MIC_SILENT, STAGE_MIC_SPEECH))
    if request.skip_net:
        skipped.add(STAGE_NET)
    if not request.confirm_tone:
        skipped.add(STAGE_TONE)
    return frozenset(skipped)


class RunScreen(Screen):
    """SCR-03。指示と残り秒と段階の一覧だけを出す。音の大きさや波形は出さない(INV-2)。"""

    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SCR-03")
        layout = QVBoxLayout(self)
        layout.addWidget(make_label(texts.STEP_RUN, "STEP-01", bold=True))
        layout.addStretch(1)
        self._instruction = make_label("", "RUN-01", scale=BIG_SCALE)
        self._instruction.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._instruction)
        self._remaining = make_label("", "RUN-02", scale=HUGE_SCALE, mono=True)
        self._remaining.setAlignment(Qt.AlignmentFlag.AlignCenter)
        policy = self._remaining.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self._remaining.setSizePolicy(policy)
        layout.addWidget(self._remaining)
        self._stages = QListWidget()
        self._stages.setObjectName("RUN-03")
        self._stages.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._stages.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._stages.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._stages.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for _ in STAGES:
            self._stages.addItem("")
        self._stages.setFixedHeight(
            self._stages.sizeHintForRow(0) * len(STAGES) + 2 * self._stages.frameWidth() + 4)
        layout.addWidget(self._stages)
        layout.addStretch(1)
        self._cancel = QPushButton(texts.BUTTON_CANCEL)
        self._cancel.setObjectName("RUN-04")
        layout.addLayout(_button_row(self._cancel))
        self._cancel.clicked.connect(self.cancel_requested)
        self._skipped: frozenset[str] = frozenset()
        self._current: str | None = None
        self._seconds: float | None = None
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._tick)
        self.begin(RunRequest())

    def begin(self, request: RunRequest) -> None:
        """測定の開始。まだどの段階も始まっていない状態にする。"""
        self._skipped = skipped_stages(request)
        self._current = None
        self._seconds = None
        self._timer.stop()
        self._cancel.setEnabled(True)
        self._instruction.setText(texts.MSG_10)
        self._remaining.setVisible(False)
        self._draw_stages()

    def set_stage(self, stage: str, seconds: float | None) -> None:
        """実行層が知らせた段階に切り替える。秒数がある段階だけ残り秒を出す。"""
        if stage not in STAGES:
            return
        self._current = stage
        self._instruction.setText(texts.STAGE_MESSAGES[stage])
        self._seconds = seconds
        if seconds is None:
            self._timer.stop()
            self._remaining.setVisible(False)
        else:
            self._clock.start()
            self._remaining.setVisible(True)
            self._tick()
            self._timer.start()
        self._draw_stages()

    def stop(self) -> None:
        self._timer.stop()

    def set_cancelling(self) -> None:
        """中止を受け付けた後は、止まるまで中止のボタンを押せなくする。"""
        self._cancel.setEnabled(False)

    def _tick(self) -> None:
        if self._seconds is None:
            return
        left = self._seconds - self._clock.elapsed() / 1000.0
        self._remaining.setText(str(max(0, math.ceil(left))))

    def _draw_stages(self) -> None:
        position = STAGES.index(self._current) if self._current in STAGES else -1
        for index, stage in enumerate(STAGES):
            if stage in self._skipped:
                sign = texts.MARK_SKIPPED
            elif index < position:
                sign = texts.MARK_DONE
            elif index == position:
                sign = texts.MARK_RUNNING
            else:
                sign = texts.MARK_WAITING
            self._stages.item(index).setText(f"{sign} {texts.STAGE_NAMES[stage]}")

    def focus_first(self) -> None:
        self._cancel.setFocus()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        # Esc だけを受ける。ほかのキーは無視する(画面設計書 §6)。
        if event.key() == Qt.Key.Key_Escape and self._cancel.isEnabled():
            self.cancel_requested.emit()


class BlockedScreen(Screen):
    """SCR-05。入力デバイスが0台か、QtMultimedia を読み込めないとき。"""

    close_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SCR-05")
        layout = QVBoxLayout(self)
        layout.addStretch(1)
        self._message = make_label(texts.MSG_18, "ERR-01")
        layout.addWidget(self._message)
        layout.addStretch(1)
        self._close = QPushButton(texts.BUTTON_CLOSE_BOTH)
        self._close.setObjectName("ERR-02")
        layout.addLayout(_button_row(self._close))
        self._close.clicked.connect(self.close_requested)

    def set_message(self, text: str) -> None:
        self._message.setText(text)

    def focus_first(self) -> None:
        self._close.setFocus()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (*_ENTER_KEYS, Qt.Key.Key_Escape):
            self.close_requested.emit()
        else:
            super().keyPressEvent(event)
