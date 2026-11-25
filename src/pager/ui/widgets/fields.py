"""Eingabe-Widgets je Fragetyp. Neue Typen: Klasse ableiten und in ``FIELD_TYPES`` eintragen."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDateEdit, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QLineEdit, QPlainTextEdit, QRadioButton, QSizePolicy, QVBoxLayout, QWidget,
)

from pager.core.workflow import Question, as_list, parse_number
from pager.ui.widgets.common import button, clear_layout, label, set_prop


class QuestionField(QWidget):
    changed = Signal()

    def __init__(self, question: Question, parent: QWidget | None = None):
        super().__init__(parent)
        self.q = question
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        title = label(
            f"{question.label}" + (' <span style="color:#D14343">*</span>' if question.required else ""),
            "H3", wrap=True, rich=True,
        )
        lay.addWidget(title)
        if question.help:
            lay.addWidget(label(question.help, "Faint", wrap=True))
        self.input = self.build()
        lay.addWidget(self.input)
        self.error = label("", "Error", wrap=True)
        self.error.hide()
        lay.addWidget(self.error)

    # -- von Unterklassen zu implementieren --------------------------------
    def build(self) -> QWidget:
        raise NotImplementedError

    def value(self) -> Any:
        raise NotImplementedError

    def set_value(self, value: Any) -> None:
        raise NotImplementedError

    # ---------------------------------------------------------------------
    def set_error(self, message: str | None) -> None:
        self.error.setText(message or "")
        self.error.setVisible(bool(message))


class TextField(QuestionField):
    def build(self):
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(self.q.placeholder)
        self.edit.textChanged.connect(self.changed)
        return self.edit

    def value(self):
        return self.edit.text().strip() or None

    def set_value(self, value):
        self.edit.setText("" if value is None else str(value))


class TextAreaField(QuestionField):
    def build(self):
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText(self.q.placeholder)
        self.edit.setMinimumHeight(110)
        self.edit.setTabChangesFocus(True)
        self.edit.textChanged.connect(self.changed)
        return self.edit

    def value(self):
        return self.edit.toPlainText().strip() or None

    def set_value(self, value):
        self.edit.setPlainText("" if value is None else str(value))


class NumberField(QuestionField):
    def build(self):
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        self.edit.setMaximumWidth(220)
        self.edit.setAlignment(Qt.AlignRight)
        self.edit.setPlaceholderText(self.q.placeholder or ("0" if self.q.type == "integer" else "0,00"))
        self.edit.textChanged.connect(self.changed)
        row.addWidget(self.edit)
        if self.q.unit:
            row.addWidget(label(self.q.unit, "Muted"))
        row.addStretch()
        return box

    def value(self):
        text = self.edit.text().strip()
        if not text:
            return None
        try:
            num = parse_number(text)
        except ValueError:
            return text  # Validierung meldet den Fehler
        return int(num) if self.q.type == "integer" and num == int(num) else num

    def set_value(self, value):
        if value is None or value == "":
            self.edit.clear()
        else:
            try:
                num = parse_number(value)
                self.edit.setText(str(int(num)) if num == int(num) else str(num).replace(".", ","))
            except ValueError:
                self.edit.setText(str(value))


class DateField(QuestionField):
    _UNSET = QDate(2000, 1, 1)

    def build(self):
        self.edit = QDateEdit()
        self.edit.setCalendarPopup(True)
        self.edit.setDisplayFormat("dd.MM.yyyy")
        self.edit.setMinimumDate(self._UNSET)
        self.edit.setSpecialValueText("Kein Datum")
        self.edit.setDate(self._UNSET)
        self.edit.setMaximumWidth(220)
        self.edit.dateChanged.connect(self.changed)
        return self.edit

    def value(self):
        d = self.edit.date()
        return None if d == self._UNSET else dt.date(d.year(), d.month(), d.day())

    def set_value(self, value):
        if isinstance(value, str):
            try:
                value = dt.date.fromisoformat(value)
            except ValueError:
                value = None
        self.edit.setDate(QDate(value.year, value.month, value.day) if value else self._UNSET)


class _OptionCard(QFrame):
    def __init__(self, radio: QRadioButton, description: str):
        super().__init__()
        self.setObjectName("OptionCard")
        self.setCursor(Qt.PointingHandCursor)
        self.radio = radio
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(2)
        lay.addWidget(radio)
        if description:
            desc = label(description, "Faint", wrap=True)
            desc.setContentsMargins(26, 0, 0, 0)
            lay.addWidget(desc)
        radio.toggled.connect(lambda on: set_prop(self, "selected", on))

    def mouseReleaseEvent(self, event):
        self.radio.setChecked(True)
        super().mouseReleaseEvent(event)


class ChoiceField(QuestionField):
    CARD_LIMIT = 4

    def build(self):
        self.use_cards = len(self.q.options) <= self.CARD_LIMIT or any(o.description for o in self.q.options)
        if not self.use_cards:
            self.combo = QComboBox()
            self.combo.addItem("Bitte wählen …", None)
            for opt in self.q.options:
                self.combo.addItem(opt.label, opt.value)
            self.combo.setMaximumWidth(420)
            self.combo.currentIndexChanged.connect(self.changed)
            return self.combo

        box = QWidget()
        grid = QGridLayout(box)
        grid.setContentsMargins(0, 2, 0, 0)
        grid.setSpacing(8)
        self.group = QButtonGroup(box)
        self.radios: dict[str, QRadioButton] = {}
        cols = 3 if len(self.q.options) in (3, 6) else 2
        for i, opt in enumerate(self.q.options):
            radio = QRadioButton(opt.label)
            self.group.addButton(radio)
            self.radios[opt.value] = radio
            radio.toggled.connect(lambda on: on and self.changed.emit())
            grid.addWidget(_OptionCard(radio, opt.description), i // cols, i % cols)
        return box

    def value(self):
        if not self.use_cards:
            return self.combo.currentData()
        for val, radio in self.radios.items():
            if radio.isChecked():
                return val
        return None

    def set_value(self, value):
        if not self.use_cards:
            idx = self.combo.findData(value)
            self.combo.setCurrentIndex(max(idx, 0))
        elif value in self.radios:
            self.radios[value].setChecked(True)


class MultiChoiceField(QuestionField):
    def build(self):
        box = QWidget()
        grid = QGridLayout(box)
        grid.setContentsMargins(0, 2, 0, 0)
        grid.setHorizontalSpacing(24)
        self.checks: dict[str, QCheckBox] = {}
        for i, opt in enumerate(self.q.options):
            cb = QCheckBox(opt.label)
            cb.setToolTip(opt.description)
            cb.toggled.connect(self.changed)
            self.checks[opt.value] = cb
            grid.addWidget(cb, i // 3, i % 3)
        grid.setColumnStretch(3, 1)
        return box

    def value(self):
        return [v for v, cb in self.checks.items() if cb.isChecked()]

    def set_value(self, value):
        selected = set(as_list(value))
        for v, cb in self.checks.items():
            cb.setChecked(v in selected)


class _SegmentField(QuestionField):
    """Basis für Ja/Nein und Skalen: exklusive Segment-Buttons."""

    def segments(self) -> list[tuple[Any, str]]:
        raise NotImplementedError

    def build(self):
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 2, 0, 0)
        row.setSpacing(6)
        self.group = QButtonGroup(box)
        self.buttons: dict[Any, Any] = {}
        self._add_prefix(row)
        for val, text in self.segments():
            btn = button(text, "segment")
            btn.setCheckable(True)
            self.group.addButton(btn)
            self.buttons[val] = btn
            btn.toggled.connect(lambda on: on and self.changed.emit())
            row.addWidget(btn)
        self._add_suffix(row)
        row.addStretch()
        return box

    def _add_prefix(self, row):
        pass

    def _add_suffix(self, row):
        pass

    def value(self):
        for val, btn in self.buttons.items():
            if btn.isChecked():
                return val
        return None

    def set_value(self, value):
        if value in self.buttons:
            self.buttons[value].setChecked(True)


class BoolField(_SegmentField):
    def segments(self):
        return [(True, "Ja"), (False, "Nein")]

    def set_value(self, value):
        if value is not None:
            super().set_value(bool(value))


class ScaleField(_SegmentField):
    def segments(self):
        lo, hi = int(self.q.min or 1), int(self.q.max or 5)
        return [(n, str(n)) for n in range(lo, hi + 1)]

    def _add_prefix(self, row):
        if self.q.scale_labels:
            row.addWidget(label(self.q.scale_labels[0], "Muted"))

    def _add_suffix(self, row):
        if len(self.q.scale_labels) > 1:
            row.addWidget(label(self.q.scale_labels[-1], "Muted"))

    def set_value(self, value):
        if value is not None:
            try:
                super().set_value(int(parse_number(value)))
            except ValueError:
                pass


class _DropZone(QFrame):
    dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            set_prop(self, "active", True)

    def dragLeaveEvent(self, event):
        set_prop(self, "active", False)

    def dropEvent(self, event):
        set_prop(self, "active", False)
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        self.dropped.emit([p for p in paths if Path(p).is_file()])


class FileField(QuestionField):
    def build(self):
        self.multiple = self.q.type == "files"
        self.paths: list[str] = []
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 2, 0, 0)
        lay.setSpacing(6)

        zone = _DropZone()
        zone.dropped.connect(self.add_paths)
        z = QHBoxLayout(zone)
        z.setContentsMargins(18, 16, 18, 16)
        z.addWidget(label("📎", "H2"))
        txt = QVBoxLayout()
        txt.setSpacing(0)
        txt.addWidget(label("Dateien hierher ziehen" if self.multiple else "Datei hierher ziehen", "H3"))
        txt.addWidget(label(self._filter_hint(), "Faint"))
        z.addLayout(txt, 1)
        pick = button("Durchsuchen …")
        pick.clicked.connect(self._pick)
        z.addWidget(pick)
        lay.addWidget(zone)

        self.list_box = QVBoxLayout()
        self.list_box.setSpacing(4)
        lay.addLayout(self.list_box)
        return box

    def _filter_hint(self) -> str:
        first = self.q.file_filter.split(";;")[0]
        return first if "(*)" not in first else "Alle Dateitypen"

    def _pick(self):
        if self.multiple:
            files, _ = QFileDialog.getOpenFileNames(self, self.q.label, "", self.q.file_filter)
        else:
            file, _ = QFileDialog.getOpenFileName(self, self.q.label, "", self.q.file_filter)
            files = [file] if file else []
        self.add_paths(files)

    def add_paths(self, files: list[str]):
        if not files:
            return
        if self.multiple:
            self.paths += [f for f in files if f not in self.paths]
        else:
            self.paths = [files[0]]
        self._render()
        self.changed.emit()

    def _remove(self, path: str):
        self.paths.remove(path)
        self._render()
        self.changed.emit()

    def _render(self):
        clear_layout(self.list_box)
        for p in self.paths:
            row = QFrame()
            row.setObjectName("FileRow")
            row.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            h = QHBoxLayout(row)
            h.setContentsMargins(12, 6, 6, 6)
            h.addWidget(label("📄"))
            name = label(Path(p).name)
            name.setToolTip(p)
            h.addWidget(name, 1)
            size = Path(p).stat().st_size if Path(p).exists() else 0
            h.addWidget(label(_human_size(size), "Faint"))
            rm = button("✕", "ghost", "Entfernen")
            rm.clicked.connect(lambda _=False, p=p: self._remove(p))
            h.addWidget(rm)
            self.list_box.addWidget(row)

    def value(self):
        if self.multiple:
            return list(self.paths)
        return self.paths[0] if self.paths else None

    def set_value(self, value):
        self.paths = [str(p) for p in as_list(value)]
        self._render()


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


FIELD_TYPES: dict[str, type[QuestionField]] = {
    "text": TextField,
    "textarea": TextAreaField,
    "number": NumberField,
    "integer": NumberField,
    "date": DateField,
    "choice": ChoiceField,
    "multichoice": MultiChoiceField,
    "bool": BoolField,
    "scale": ScaleField,
    "file": FileField,
    "files": FileField,
}


def create_field(question: Question) -> QuestionField:
    field = FIELD_TYPES[question.type](question)
    if question.default is not None:
        field.set_value(question.default)
    return field
