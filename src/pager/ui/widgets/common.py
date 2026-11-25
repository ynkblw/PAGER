"""Kleine, wiederverwendbare UI-Bausteine."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)


def repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def set_prop(widget: QWidget, name: str, value) -> None:
    widget.setProperty(name, value)
    repolish(widget)


def label(text: str = "", name: str | None = None, wrap: bool = False, rich: bool = False) -> QLabel:
    lbl = QLabel(text)
    if name:
        lbl.setObjectName(name)
    if wrap:
        lbl.setWordWrap(True)
    lbl.setTextFormat(Qt.RichText if rich else Qt.PlainText)
    return lbl


def button(text: str, variant: str | None = None, tooltip: str = "") -> QPushButton:
    btn = QPushButton(text)
    if variant:
        btn.setProperty("variant", variant)
    btn.setCursor(Qt.PointingHandCursor)
    if tooltip:
        btn.setToolTip(tooltip)
    return btn


def clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if w := item.widget():
            w.setParent(None)
            w.deleteLater()
        elif child := item.layout():
            clear_layout(child)


def hline() -> QFrame:
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet("background: #E1E5EE;")
    return line


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None, margins: int = 24, spacing: int = 14):
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(margins, margins, margins, margins)
        self.body.setSpacing(spacing)


class Banner(QFrame):
    ICONS = {"info": "ℹ️", "warning": "⚠️", "error": "⛔", "success": "✅"}

    def __init__(self, kind: str = "info", text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Banner")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(10)
        self.icon = label()
        self.icon.setAlignment(Qt.AlignTop)
        self.text = label(wrap=True)
        self.text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        lay.addWidget(self.icon)
        lay.addWidget(self.text, 1)
        self.show_message(kind, text)

    def show_message(self, kind: str, text: str) -> None:
        set_prop(self, "kind", kind)
        self.icon.setText(self.ICONS.get(kind, ""))
        self.text.setText(text)
        self.setVisible(bool(text))
