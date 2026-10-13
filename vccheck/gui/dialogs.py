# DLG-01 テスト音の確認・DLG-02 テスト音の聞こえ方・DLG-03 設定ファイルの異常。
# DLG-01 の既定は「鳴らさない」で、Enter と Esc では鳴らさない(INV-8)。
# DLG-02 を回答なしで閉じたら申告なし(None)。値を推測して入れない。
from __future__ import annotations

from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from vccheck.gui import texts
from vccheck.gui.parts import make_label

_MIN_WIDTH = 480


class ToneConfirmDialog(QDialog):
    """DLG-01。「鳴らす」はクリックか、Tab で移った後の Space でだけ押せる。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DLG-01")
        self.setWindowTitle(texts.WINDOW_TITLE)
        self.setMinimumWidth(_MIN_WIDTH)
        layout = QVBoxLayout(self)
        layout.addWidget(make_label(texts.MSG_16, "DLG-01-1"))
        self._play = QPushButton(texts.BUTTON_PLAY)
        self._play.setObjectName("DLG-01-2")
        self._play.setAutoDefault(False)
        self._keep_quiet = QPushButton(texts.BUTTON_DO_NOT_PLAY)
        self._keep_quiet.setObjectName("DLG-01-3")
        self._keep_quiet.setDefault(True)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self._play)
        row.addWidget(self._keep_quiet)
        layout.addLayout(row)
        self._play.clicked.connect(self.accept)
        self._keep_quiet.clicked.connect(self.reject)
        self._keep_quiet.setFocus()


class ToneHeardDialog(QDialog):
    """DLG-02。4つのどれかを選ぶまで「回答」は押せない。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DLG-02")
        self.setWindowTitle(texts.WINDOW_TITLE)
        self.setMinimumWidth(_MIN_WIDTH)
        layout = QVBoxLayout(self)
        layout.addWidget(make_label(texts.MSG_17, "DLG-02-1"))
        choices = QWidget()
        choices.setObjectName("DLG-02-2")
        box = QVBoxLayout(choices)
        self._group = QButtonGroup(self)
        self._values: dict[int, str] = {}
        for number, (value, text) in enumerate(texts.MSG_30, start=1):
            radio = QRadioButton(text)
            radio.setObjectName(f"DLG-02-2-{value}")
            box.addWidget(radio)
            self._group.addButton(radio, number)
            self._values[number] = value
        layout.addWidget(choices)
        self._answer = QPushButton(texts.BUTTON_ANSWER)
        self._answer.setObjectName("DLG-02-3")
        self._answer.setEnabled(False)
        self._answer.setDefault(True)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self._answer)
        layout.addLayout(row)
        self._group.idToggled.connect(self._on_choice)
        self._answer.clicked.connect(self.accept)

    def _on_choice(self, _number: int, checked: bool) -> None:
        if checked:
            self._answer.setEnabled(True)

    def answer(self) -> str | None:
        """回答して閉じたときだけ ok / crackle / quiet / none。それ以外は None。"""
        if self.result() != QDialog.DialogCode.Accepted.value:
            return None
        return self._values.get(self._group.checkedId())


class ConfigErrorDialog(QDialog):
    """DLG-03。閉じるとアプリを終了する(終了コード 1)。"""

    def __init__(self, file_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DLG-03")
        self.setWindowTitle(texts.WINDOW_TITLE)
        self.setMinimumWidth(_MIN_WIDTH)
        layout = QVBoxLayout(self)
        layout.addWidget(make_label(texts.MSG_20.format(file=file_name), "DLG-03-1"))
        self._close = QPushButton(texts.BUTTON_CLOSE_BOTH)
        self._close.setObjectName("DLG-03-2")
        self._close.setDefault(True)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self._close)
        layout.addLayout(row)
        self._close.clicked.connect(self.accept)
