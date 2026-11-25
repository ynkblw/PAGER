"""Vertikale Schrittanzeige für den Walkthrough."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from pager.ui.widgets.common import clear_layout, label, set_prop


class _StepItem(QFrame):
    clicked = Signal(int)

    def __init__(self, index: int, title: str):
        super().__init__()
        self.setObjectName("StepItem")
        self.index = index
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(12)
        self.dot = label(str(index + 1), "StepDot")
        self.dot.setFixedSize(28, 28)
        self.dot.setAlignment(Qt.AlignCenter)
        self.title = label(title, "StepTitle", wrap=True)
        lay.addWidget(self.dot)
        lay.addWidget(self.title, 1)
        self.enabled = False

    def set_state(self, state: str, clickable: bool):
        self.dot.setText("✓" if state == "done" else str(self.index + 1))
        set_prop(self.dot, "state", state)
        set_prop(self.title, "state", state)
        self.enabled = clickable
        self.setCursor(Qt.PointingHandCursor if clickable else Qt.ArrowCursor)

    def mouseReleaseEvent(self, event):
        if self.enabled:
            self.clicked.emit(self.index)
        super().mouseReleaseEvent(event)


class Stepper(QWidget):
    step_clicked = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Stepper")
        self.setFixedWidth(230)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(2)
        self.items: list[_StepItem] = []

    def set_steps(self, titles: list[str]):
        clear_layout(self.lay)
        self.items = []
        for i, title in enumerate(titles):
            item = _StepItem(i, title)
            item.clicked.connect(self.step_clicked)
            self.items.append(item)
            self.lay.addWidget(item)
        self.lay.addStretch()

    def set_current(self, current: int, max_reached: int):
        for item in self.items:
            if item.index == current:
                state = "current"
            elif item.index < max_reached or item.index < current:
                state = "done"
            else:
                state = "upcoming"
            item.set_state(state, clickable=item.index <= max_reached and item.index != current)
