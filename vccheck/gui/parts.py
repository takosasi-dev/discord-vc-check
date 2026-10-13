# 画面で共通に使う小さな部品: 文字のラベル、等幅と大きい文字のフォント、
# 中身の高さに合わせて自分ではスクロールしない表。
# 色は付けない。強調は太字と文字の印だけ(画面設計書 §10)。
from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

MONO_FAMILY = "Consolas"
BIG_SCALE = 1.6
HUGE_SCALE = 3.0


def mono_font(base: QFont) -> QFont:
    """数値用の等幅フォント。日本語の字は元のフォントで出る。"""
    font = QFont(base)
    font.setFamilies([MONO_FAMILY, base.family()])
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


def scaled_font(base: QFont, scale: float) -> QFont:
    font = QFont(base)
    font.setPointSizeF(base.pointSizeF() * scale)
    return font


def bold_font(base: QFont) -> QFont:
    font = QFont(base)
    font.setBold(True)
    return font


def make_label(text: str, name: str | None = None, *, bold: bool = False,
               scale: float | None = None, mono: bool = False,
               parent: QWidget | None = None) -> QLabel:
    """折り返す文字のラベル。name は画面設計書の要素 ID(objectName)。"""
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    if name is not None:
        label.setObjectName(name)
    font = label.font()
    if mono:
        font = mono_font(font)
    if scale is not None:
        font = scaled_font(font, scale)
    if bold:
        font = bold_font(font)
    label.setFont(font)
    return label


class FitTable(QTableWidget):
    """行の数だけの高さになる表。選択も編集もフォーカスも受けない(見るだけ)。"""

    def __init__(self, columns: Sequence[str], rows: Sequence[Sequence[str]],
                 mono_columns: Sequence[int] = (), parent: QWidget | None = None) -> None:
        super().__init__(len(rows), len(columns), parent)
        self.setHorizontalHeaderLabels(list(columns))
        self.verticalHeader().hide()
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWordWrap(True)
        header = self.horizontalHeader()
        header.setSectionsClickable(False)
        header.setHighlightSections(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        mono = mono_font(self.font())
        for row, cells in enumerate(rows):
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                if column in mono_columns:
                    item.setFont(mono)
                self.setItem(row, column, item)
        self._fit()

    def _fit(self) -> None:
        self.resizeRowsToContents()
        height = self.horizontalHeader().height() + 2 * self.frameWidth()
        height += sum(self.rowHeight(row) for row in range(self.rowCount()))
        if height != self.height():
            self.setFixedHeight(height)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._fit()
