"""Bausteine für den Walkthrough-Editor."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QGridLayout, QHBoxLayout, QHeaderView, QLayout, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem,
    QToolButton, QVBoxLayout, QWidget, QWidgetAction,
)

from pager.core.authoring import COLORS, ICONS, OPTION_TYPES, QUESTION_TYPES, slugify, unique_id
from pager.core.workflow import parse_number
from pager.ui.widgets.common import button, label


class FlowLayout(QLayout):
    """Layout, das Elemente wie Text umbricht (für Variablen-Chips)."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 6):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._layout(QRect(0, 0, width, 0), dry=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._layout(rect, dry=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _layout(self, rect: QRect, dry: bool) -> int:
        x, y, line_h = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > rect.right() and line_h > 0:
                x, y, line_h = rect.x(), y + line_h + self._spacing, 0
            if not dry:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


# ---------------------------------------------------------------------------


class EmojiButton(QToolButton):
    changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("EmojiButton")
        self.setPopupMode(QToolButton.InstantPopup)
        self.setFixedSize(56, 48)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Symbol wählen")
        menu = QMenu(self)
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(10, 10, 10, 10)
        grid = QGridLayout()
        grid.setSpacing(2)
        for i, emoji in enumerate(ICONS):
            b = QToolButton()
            b.setText(emoji)
            b.setObjectName("EmojiCell")
            b.setFixedSize(36, 36)
            b.clicked.connect(lambda _=False, e=emoji: self._pick(e))
            grid.addWidget(b, i // 8, i % 8)
        lay.addLayout(grid)
        self.custom = QLineEdit()
        self.custom.setPlaceholderText("Eigenes Emoji einfügen …")
        self.custom.returnPressed.connect(lambda: self._pick(self.custom.text().strip()))
        lay.addWidget(self.custom)
        action = QWidgetAction(menu)
        action.setDefaultWidget(host)
        menu.addAction(action)
        self.setMenu(menu)

    def _pick(self, emoji: str):
        if emoji:
            self.setText(emoji)
            self.menu().hide()
            self.changed.emit(emoji)


class ColorSwatches(QWidget):
    changed = Signal(str)

    def __init__(self):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.buttons: dict[str, QPushButton] = {}
        for color in COLORS:
            b = QPushButton()
            b.setCheckable(True)
            b.setFixedSize(28, 28)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                f"QPushButton {{ background: {color}; border: 2px solid white; border-radius: 14px; padding: 0; }}"
                f"QPushButton:checked {{ border: 3px solid #18202F; }}")
            b.clicked.connect(lambda _=False, c=color: self._pick(c))
            self.buttons[color] = b
            lay.addWidget(b)
        lay.addStretch()

    def set_color(self, color: str):
        for c, b in self.buttons.items():
            b.setChecked(c.lower() == (color or "").lower())

    def _pick(self, color: str):
        self.set_color(color)
        self.changed.emit(color)


# ---------------------------------------------------------------------------


class OptionsEditor(QWidget):
    """Tabelle für Antwortoptionen. Schreibt direkt in die übergebene Liste."""

    changed = Signal()
    COLS = ("Beschriftung", "Beschreibung", "Interner Wert")

    def __init__(self):
        super().__init__()
        self.options: list[dict] = []
        self._loading = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setObjectName("OptionsTable")
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.itemChanged.connect(self._on_item_changed)
        lay.addWidget(self.table)

        row = QHBoxLayout()
        add = button("＋ Option")
        add.clicked.connect(self._add)
        up = button("↑", tooltip="Nach oben")
        down = button("↓", tooltip="Nach unten")
        rm = button("Entfernen", "danger")
        up.clicked.connect(lambda: self._move(-1))
        down.clicked.connect(lambda: self._move(1))
        rm.clicked.connect(self._remove)
        for b in (add, up, down):
            row.addWidget(b)
        row.addStretch()
        row.addWidget(rm)
        lay.addLayout(row)
        lay.addWidget(label("Der interne Wert wird automatisch erzeugt und in Bedingungen/Prompt verwendet.",
                            "Faint", wrap=True))

    def load(self, options: list[dict]):
        self.options = options
        self._render()

    def _render(self, select: int | None = None):
        self._loading = True
        self.table.setRowCount(len(self.options))
        for r, o in enumerate(self.options):
            for c, key in enumerate(("label", "description", "value")):
                item = QTableWidgetItem(o.get(key, ""))
                if key == "value":
                    item.setForeground(Qt.gray)
                self.table.setItem(r, c, item)
        header = self.table.horizontalHeader().sizeHint().height()
        self.table.setFixedHeight(header + 36 * max(2, len(self.options)) + 6)
        self._loading = False
        if select is not None and 0 <= select < len(self.options):
            self.table.selectRow(select)

    def _on_item_changed(self, item: QTableWidgetItem):
        if self._loading:
            return
        o = self.options[item.row()]
        key = ("label", "description", "value")[item.column()]
        text = item.text().strip()
        if key == "label":
            others = [x["value"] for x in self.options if x is not o]
            if not o["value"] or o["value"] == slugify(o["label"]) or o["value"].startswith("option_"):
                o["value"] = unique_id(slugify(text) or "option", others)
                self._loading = True
                self.table.item(item.row(), 2).setText(o["value"])
                self._loading = False
        if key == "value":
            text = slugify(text) or o["value"]
        o[key] = text
        self.changed.emit()

    def _add(self):
        n = len(self.options) + 1
        taken = [o["value"] for o in self.options]
        self.options.append({"value": unique_id(f"option_{n}", taken), "label": f"Option {n}", "description": ""})
        self._render(select=len(self.options) - 1)
        self.table.editItem(self.table.item(len(self.options) - 1, 0))
        self.changed.emit()

    def _current(self) -> int:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def _move(self, delta: int):
        r = self._current()
        t = r + delta
        if r < 0 or not 0 <= t < len(self.options):
            return
        self.options[r], self.options[t] = self.options[t], self.options[r]
        self._render(select=t)
        self.changed.emit()

    def _remove(self):
        r = self._current()
        if r >= 0:
            del self.options[r]
            self._render(select=min(r, len(self.options) - 1))
            self.changed.emit()


# ---------------------------------------------------------------------------


class ConditionEditor(QWidget):
    """„Nur anzeigen, wenn …“ – verständlicher Baukasten für ``show_if``."""

    changed = Signal()
    ALWAYS = "Immer anzeigen"

    def __init__(self, noun: str = "Frage"):
        super().__init__()
        self.candidates: list[dict] = []
        self._loading = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self.mode = QComboBox()
        self.mode.addItems([self.ALWAYS, f"Nur anzeigen, wenn …"])
        self.mode.setMaximumWidth(240)
        self.mode.currentIndexChanged.connect(self._on_mode)
        lay.addWidget(self.mode)

        self.row = QWidget()
        rows = QVBoxLayout(self.row)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(6)
        self.question = QComboBox()
        self.question.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.question.setMinimumContentsLength(18)
        self.op = QComboBox()
        self.op.setMinimumWidth(170)
        self.value = QComboBox()
        self.value.setEditable(True)
        self.value.setMinimumWidth(150)
        self.values_list = QListWidget()
        self.values_list.setMaximumHeight(110)
        rows.addWidget(self.question)
        r = QHBoxLayout()
        r.addWidget(self.op)
        r.addWidget(self.value, 1)
        r.addWidget(self.values_list, 1)
        rows.addLayout(r)
        lay.addWidget(self.row)
        self.hint = label("", "Faint", wrap=True)
        lay.addWidget(self.hint)

        self.question.currentIndexChanged.connect(self._on_question)
        self.op.currentIndexChanged.connect(self._on_op)
        self.value.currentTextChanged.connect(self._emit)
        self.values_list.itemChanged.connect(self._emit)

    # ---- API ---------------------------------------------------------
    def load(self, condition: dict | None, candidates: list[dict]):
        self._loading = True
        self.candidates = candidates
        self.question.clear()
        for q in candidates:
            icon = QUESTION_TYPES.get(q["type"], ("•",))[0]
            self.question.addItem(f"{icon}  {q['label']}", q["id"])
        enabled = bool(candidates)
        self.mode.setEnabled(enabled or bool(condition))
        self.hint.setText("" if enabled else f"Bedingungen können sich nur auf vorherige Fragen beziehen.")
        if condition and condition.get("question"):
            self.mode.setCurrentIndex(1)
            idx = self.question.findData(condition["question"])
            if idx < 0:
                self.question.addItem(f"⚠️  {condition['question']} (nicht gefunden)", condition["question"])
                idx = self.question.count() - 1
            self.question.setCurrentIndex(idx)
            self._fill_ops()
            self._select_op(condition)
            self._fill_value(condition)
        else:
            self.mode.setCurrentIndex(0)
            self._fill_ops()
            self._fill_value(None)
        self.row.setVisible(self.mode.currentIndex() == 1)
        self._loading = False

    def condition(self) -> dict | None:
        if self.mode.currentIndex() == 0 or self.question.currentData() is None:
            return None
        qid = self.question.currentData()
        op = self.op.currentData()
        q = self._question()
        if op == "filled":
            return {"question": qid}
        if op in ("yes", "no"):
            return {"question": qid, "equals": op == "yes"}
        if op == "in":
            vals = [self.values_list.item(i).data(Qt.UserRole) for i in range(self.values_list.count())
                    if self.values_list.item(i).checkState() == Qt.Checked]
            return {"question": qid, "in": vals}
        value: Any = self.value.currentData() if self.value.currentData() is not None else self.value.currentText()
        if q and q["type"] in ("number", "integer", "scale"):
            try:
                num = parse_number(value)
                value = int(num) if num.is_integer() else num
            except ValueError:
                pass
        return {"question": qid, ("equals" if op == "equals" else "not_equals"): value}

    # ---- intern ------------------------------------------------------
    def _question(self) -> dict | None:
        qid = self.question.currentData()
        return next((q for q in self.candidates if q["id"] == qid), None)

    def _fill_ops(self):
        self.op.blockSignals(True)
        self.op.clear()
        q = self._question()
        t = q["type"] if q else "text"
        if t == "bool":
            self.op.addItem("mit Ja beantwortet", "yes")
            self.op.addItem("mit Nein beantwortet", "no")
        elif t in OPTION_TYPES:
            self.op.addItem("ist" if t == "choice" else "enthält", "equals")
            if t == "choice":
                self.op.addItem("ist nicht", "not_equals")
            self.op.addItem("ist eine von …" if t == "choice" else "enthält eine von …", "in")
            self.op.addItem("ist beantwortet", "filled")
        else:
            self.op.addItem("ist ausgefüllt", "filled")
            self.op.addItem("ist gleich", "equals")
            self.op.addItem("ist ungleich", "not_equals")
        self.op.blockSignals(False)

    def _select_op(self, c: dict):
        if "in" in c and c["in"] is not None:
            key = "in"
        elif c.get("equals") is True:
            key = "yes"
        elif c.get("equals") is False:
            key = "no"
        elif "equals" in c and c["equals"] is not None:
            key = "equals"
        elif "not_equals" in c and c["not_equals"] is not None:
            key = "not_equals"
        else:
            key = "filled"
        idx = self.op.findData(key)
        self.op.setCurrentIndex(max(idx, 0))

    def _fill_value(self, c: dict | None):
        q = self._question()
        op = self.op.currentData()
        self.value.blockSignals(True)
        self.values_list.blockSignals(True)
        self.value.clear()
        self.values_list.clear()
        options = q["options"] if q and q["type"] in OPTION_TYPES else []
        for o in options:
            self.value.addItem(o["label"], o["value"])
            item = QListWidgetItem(o["label"])
            item.setData(Qt.UserRole, o["value"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            selected = c and o["value"] in (c.get("in") or [])
            item.setCheckState(Qt.Checked if selected else Qt.Unchecked)
            self.values_list.addItem(item)
        self.value.setEditable(not options)
        if c:
            current = c.get("equals", c.get("not_equals"))
            if current is not None and not isinstance(current, bool):
                idx = self.value.findData(current)
                if idx >= 0:
                    self.value.setCurrentIndex(idx)
                else:
                    self.value.setEditText(str(current))
        self.value.setVisible(op in ("equals", "not_equals"))
        self.values_list.setVisible(op == "in")
        self.value.blockSignals(False)
        self.values_list.blockSignals(False)

    def _on_mode(self):
        self.row.setVisible(self.mode.currentIndex() == 1)
        if self.mode.currentIndex() == 1 and self.question.currentIndex() < 0 and self.question.count():
            self.question.setCurrentIndex(0)
        self._emit()

    def _on_question(self):
        if self._loading:
            return
        self._fill_ops()
        self._fill_value(None)
        self._emit()

    def _on_op(self):
        if self._loading:
            return
        self._fill_value(None)
        self._emit()

    def _emit(self, *_):
        if not self._loading:
            self.changed.emit()


# ---------------------------------------------------------------------------


class DefaultEditor(QWidget):
    """Standardwert passend zum Fragetyp."""

    changed = Signal()

    def __init__(self):
        super().__init__()
        self.q: dict | None = None
        self._loading = False
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.combo = QComboBox()
        self.combo.setMinimumWidth(220)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("kein Standardwert")
        self.edit.setMaximumWidth(320)
        self.lay.addWidget(self.combo)
        self.lay.addWidget(self.edit)
        self.lay.addStretch()
        self.combo.currentIndexChanged.connect(self._on_change)
        self.edit.textChanged.connect(self._on_change)

    @staticmethod
    def supports(qtype: str) -> bool:
        return qtype in ("text", "textarea", "number", "integer", "scale", "bool", "choice")

    def load(self, q: dict):
        self._loading = True
        self.q = q
        t, default = q["type"], q.get("default")
        use_combo = t in ("bool", "choice")
        self.combo.setVisible(use_combo)
        self.edit.setVisible(not use_combo)
        self.combo.clear()
        self.combo.addItem("– kein Standardwert –", None)
        if t == "bool":
            self.combo.addItem("Ja", True)
            self.combo.addItem("Nein", False)
        elif t == "choice":
            for o in q["options"]:
                self.combo.addItem(o["label"], o["value"])
        if use_combo:
            idx = next((i for i in range(self.combo.count()) if self.combo.itemData(i) == default
                        and type(self.combo.itemData(i)) is type(default)), 0)
            self.combo.setCurrentIndex(idx)
        else:
            if default is None:
                text = ""
            elif isinstance(default, float):
                text = str(default).replace(".", ",")
            else:
                text = str(default)
            self.edit.setText(text)
        self._loading = False

    def _on_change(self, *_):
        if self._loading or not self.q:
            return
        t = self.q["type"]
        if t in ("bool", "choice"):
            self.q["default"] = self.combo.currentData()
        else:
            text = self.edit.text().strip()
            if not text:
                self.q["default"] = None
            elif t in ("number", "integer", "scale"):
                try:
                    num = parse_number(text)
                    self.q["default"] = int(num) if t != "number" or num.is_integer() else num
                except ValueError:
                    return
            else:
                self.q["default"] = text
        self.changed.emit()


def type_combo() -> QComboBox:
    combo = QComboBox()
    for key, (icon, name, desc) in QUESTION_TYPES.items():
        combo.addItem(f"{icon}  {name}", key)
        combo.setItemData(combo.count() - 1, desc, Qt.ToolTipRole)
    combo.setMinimumWidth(240)
    combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    return combo
