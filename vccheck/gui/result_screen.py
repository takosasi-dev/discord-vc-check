# SCR-04 結果。結果の JSON(形の版 1)を読んで描くだけで、判定・区間・候補をここで計算しない。
# 状態は [異常] のような文字の印で示し、値が無い所は「—」にする(代用値を出さない。INV-1)。
# この画面だけ縦にスクロールする。ボタンの列はスクロールの外に置く。
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from vccheck.gui import texts
from vccheck.gui.backend import SaveReport
from vccheck.gui.parts import BIG_SCALE, FitTable, make_label
from vccheck.gui.screens import Screen
from vccheck.model import CONFIDENCES, WORD_LABELS, WORDS, Item, Result, result_from_obj

_ENTER_KEYS = (Qt.Key.Key_Return, Qt.Key.Key_Enter)
_VALUE_WORDS = ("bad", "warn", "ok", "none")
_OPEN_WORDS = ("bad", "warn", "failed")
_SYMPTOM_TEXT = dict(texts.SYMPTOM_LABELS)


def format_value(item: Item) -> str:
    """値の表示(画面設計書 §7)。回は整数、ほかの単位つきの数は小数第1位。null は「—」。"""
    value = item.value
    if value is None:
        return texts.DASH
    if isinstance(value, bool):
        return texts.YES if value else texts.NO
    if isinstance(value, str):
        return texts.TONE_HEARD_VALUES.get(value, value) if item.id == "tone.heard" else value
    if item.unit == texts.UNIT_COUNT:
        return f"{value:.0f} {item.unit}"
    if item.unit:
        return f"{value:.1f} {item.unit}"
    return str(value)


def _evidence(item_id: str, items: Mapping[str, Item]) -> str:
    item = items.get(item_id)
    if item is None:
        return item_id
    if item.status == "measured":
        return f"{item.name} {format_value(item)}"
    return f"{item.name} {texts.mark(WORD_LABELS[item.word])}"


def _verdict(item: Item) -> str:
    if item.verdict in ("ok", "warn", "bad") and item.verdict_raw:
        return f"{WORD_LABELS[item.verdict]} ({item.verdict_raw})"
    return texts.DASH


def _reason(item: Item) -> str:
    reason = item.reason or texts.DASH
    return f"{reason}({item.note})" if item.note else reason


def _when(stamp: str) -> str:
    return datetime.fromisoformat(stamp).strftime("%Y-%m-%d %H:%M")


class SectionHeader(QToolButton):
    """詳細の節の見出し。クリック・Space・Enter で開閉する。

    開いているかどうかは矢印の向きだけで示す(押し込んだ状態にすると、色が付いて見える)。
    """

    opened_changed = Signal(bool)

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._opened = False
        self.setText(text)
        self.setAutoRaise(True)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setArrowType(Qt.ArrowType.RightArrow)
        self.clicked.connect(self._toggle)

    def is_open(self) -> bool:
        return self._opened

    def set_open(self, opened: bool) -> None:
        self._opened = opened
        self.setArrowType(Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow)
        self.opened_changed.emit(opened)

    def _toggle(self) -> None:
        self.set_open(not self._opened)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in _ENTER_KEYS:
            self.click()
        else:
            super().keyPressEvent(event)


class ResultScreen(Screen):
    """SCR-04。show_result に渡された結果の JSON だけから描く。"""

    copy_requested = Signal()
    open_folder_requested = Signal()
    again_requested = Signal()
    close_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SCR-04")
        outer = QVBoxLayout(self)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        outer.addWidget(self._scroll, 1)
        body = QWidget()
        self._scroll.setWidget(body)
        layout = QVBoxLayout(body)

        self._title = make_label("", "RES-01", scale=BIG_SCALE)
        self._header = make_label("", "RES-02")
        self._calibrated = make_label("", "RES-03")
        self._nothing = make_label(texts.MSG_29, "RES-14", bold=True)
        for widget in (self._title, self._header, self._calibrated, self._nothing):
            layout.addWidget(widget)

        layout.addWidget(make_label(texts.HEADING_SEGMENTS, bold=True))
        self._segments_box = QVBoxLayout()
        layout.addLayout(self._segments_box)
        self._segments: FitTable | None = None
        layout.addWidget(make_label(texts.MSG_26, "MSG-26"))
        layout.addWidget(make_label(texts.MSG_27, "MSG-27"))

        layout.addWidget(make_label(texts.HEADING_CANDIDATES, bold=True))
        self._candidates = make_label("", "RES-05")
        layout.addWidget(self._candidates)

        self._counts = make_label("", "RES-06", mono=True)
        layout.addWidget(self._counts)

        layout.addWidget(make_label(texts.HEADING_DETAILS, bold=True))
        self._details = QWidget()
        self._details.setObjectName("RES-07")
        self._details_box = QVBoxLayout(self._details)
        self._details_box.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._details)
        self._sections: list[SectionHeader] = []

        layout.addWidget(make_label(texts.MSG_28, "RES-08"))
        self._saved = make_label("", "RES-13")
        layout.addWidget(self._saved)
        layout.addStretch(1)

        self._copy = self._button(texts.BUTTON_COPY, "RES-09")
        self._open = self._button(texts.BUTTON_OPEN_FOLDER, "RES-10")
        self._again = self._button(texts.BUTTON_AGAIN, "RES-11")
        self._close = self._button(texts.BUTTON_CLOSE, "RES-12")
        row = QHBoxLayout()
        row.addStretch(1)
        for button in (self._copy, self._open, self._again, self._close):
            row.addWidget(button)
        outer.addLayout(row)
        self._copy.clicked.connect(self.copy_requested)
        self._open.clicked.connect(self.open_folder_requested)
        self._again.clicked.connect(self.again_requested)
        self._close.clicked.connect(self.close_requested)

    @staticmethod
    def _button(text: str, name: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(name)
        return button

    def show_result(self, obj: Mapping[str, object], report: SaveReport) -> None:
        """結果の JSON と保存の結果を描く。形の規則に合わない JSON は受け取らない(ValueError)。"""
        result = result_from_obj(obj)
        cond = result.run.conditions
        by_id = {item.id: item for item in result.items}
        self._title.setText(texts.RESULT_TITLE.format(when=_when(result.run.measured_at)))
        names = [_SYMPTOM_TEXT.get(s, s) for s in result.run.symptoms]
        symptoms = texts.SYMPTOM_SEPARATOR.join(names) or texts.SYMPTOMS_NONE
        device_in = self._device(cond.input_declared, cond.input_default_used is True)
        device_out = self._device(cond.output_declared, False)
        self._header.setText(
            texts.SYMPTOMS_LINE.format(names=symptoms) + "\n"
            + texts.DEVICES_LINE.format(input=device_in, output=device_out))
        self._calibrated.setText(
            texts.MSG_03.format(total=result.calibrated_total, n=result.calibrated))
        self._nothing.setVisible(all(item.status != "measured" for item in result.items))
        self._draw_segments(result)
        self._candidates.setText(self._candidate_text(result, by_id))
        self._counts.setText(self._count_text(result))
        self._draw_details(result)
        self._draw_saved(report)
        self._scroll.verticalScrollBar().setValue(0)

    @staticmethod
    def _device(name: str | None, default_used: bool) -> str:
        if not name:
            return texts.DASH
        shown = " ".join(name.split())
        return shown + (texts.DEFAULT_USED if default_used else "") + texts.DECLARED

    def _draw_segments(self, result: Result) -> None:
        if self._segments is not None:
            self._segments_box.removeWidget(self._segments)
            self._segments.deleteLater()
        rows = [
            [f"{seg.id} {seg.name}", texts.mark(WORD_LABELS[seg.status]),
             texts.COUNT_ITEMS.format(n=seg.unchecked_count)]
            for seg in result.segments
        ]
        self._segments = FitTable(texts.SEGMENT_COLUMNS, rows, mono_columns=(2,))
        self._segments.setObjectName("RES-04")
        self._segments_box.addWidget(self._segments)

    @staticmethod
    def _candidate_text(result: Result, by_id: Mapping[str, Item]) -> str:
        if not result.candidates:
            return texts.MSG_25
        lines: list[str] = []
        for number, cand in enumerate(result.candidates, start=1):
            evidence = texts.EVIDENCE_SEPARATOR.join(_evidence(e, by_id) for e in cand.evidence)
            lines.append(f"{number}. {cand.text}")
            lines.append(f"    {texts.EVIDENCE}{evidence or texts.DASH}")
            lines.append(f"    {texts.ADVICE}{cand.advice or texts.DASH}")
        return "\n".join(lines)

    @staticmethod
    def _count_text(result: Result) -> str:
        parts = [f"{texts.mark(label)} {result.counts[key]}" for key, label in WORDS]
        return "  ".join(parts[:5]) + "\n" + "  ".join(parts[5:])

    def _draw_details(self, result: Result) -> None:
        while self._details_box.count():
            taken = self._details_box.takeAt(0)
            widget = taken.widget() if taken is not None else None
            if widget is not None:
                widget.deleteLater()
        self._sections = []
        for key, label in WORDS:
            items = [item for item in result.items if item.word == key]
            header = SectionHeader(texts.SECTION_TITLE.format(word=label, n=len(items)))
            header.setObjectName(f"RES-07-{key}")
            content: QWidget
            if not items:
                content = make_label(texts.SECTION_EMPTY)
            elif key in _VALUE_WORDS:
                content = FitTable(
                    texts.VALUE_COLUMNS,
                    [[item.segment, item.name, format_value(item),
                      CONFIDENCES.get(item.confidence or "", texts.DASH), _verdict(item),
                      item.note or item.reason or texts.DASH] for item in items],
                    mono_columns=(2,))
            else:
                content = FitTable(
                    texts.REASON_COLUMNS,
                    [[item.segment, item.name, _reason(item)] for item in items])
            content.setObjectName(f"RES-07-{key}-body")
            # 先に画面へ入れる。親の無いまま表示すると、別のウィンドウとして出てしまう。
            self._details_box.addWidget(header)
            self._details_box.addWidget(content)
            opened = key in _OPEN_WORDS and bool(items)
            header.set_open(opened)
            content.setVisible(opened)
            header.opened_changed.connect(content.setVisible)
            self._sections.append(header)
        order: list[QWidget] = [*self._sections, self._copy, self._open, self._again,
                                self._close]
        for first, second in zip(order, order[1:], strict=False):
            QWidget.setTabOrder(first, second)

    def _draw_saved(self, report: SaveReport) -> None:
        self._open.setEnabled(report.folder is not None)
        if report.problem is None:
            path = None if report.folder is None else report.folder / f"{report.stem}.json"
            self._saved.setText("" if path is None else texts.SAVED_TO.format(path=path))
        elif report.exists:
            self._saved.setText(texts.MSG_23)
        else:
            self._saved.setText(texts.MSG_22.format(reason=report.problem))

    def section_headers(self) -> list[SectionHeader]:
        return list(self._sections)

    def saved_label(self) -> QLabel:
        return self._saved

    def focus_first(self) -> None:
        if self._sections:
            self._sections[0].setFocus()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close_requested.emit()
        else:
            super().keyPressEvent(event)
