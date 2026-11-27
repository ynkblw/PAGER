"""Walkthrough-Editor: Walkthroughs ohne YAML-Kenntnisse anlegen und bearbeiten."""

from __future__ import annotations

import copy
from pathlib import Path

from pydantic import ValidationError
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QGridLayout, QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLineEdit, QMenu, QMessageBox, QPlainTextEdit,
    QScrollArea, QSizePolicy, QSpinBox, QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

from pager.config import Settings, user_workflow_dir
from pager.core import authoring as A
from pager.core.workflow import AgentStepConfig, Question
from pager.ui.widgets.agent_console import AgentConsole
from pager.ui.theme import shadow
from pager.ui.widgets.common import Banner, Card, button, clear_layout, hline, label
from pager.ui.widgets.editor_widgets import (
    ColorSwatches, ConditionEditor, DefaultEditor, EmojiButton, FlowLayout, OptionsEditor, type_combo,
)
from pager.ui.widgets.fields import create_field

LABEL_WIDTH = 140


def _form() -> QFormLayout:
    form = QFormLayout()
    form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
    form.setHorizontalSpacing(20)
    form.setVerticalSpacing(14)
    form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
    return form


def _row(form: QFormLayout, text: str, widget: QWidget, hint: str = "") -> QWidget:
    """Fügt eine Zeile hinzu und gibt das Feld-Widget zurück (für ``setRowVisible``)."""
    if hint:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(widget)
        lay.addWidget(label(hint, "Faint", wrap=True))
        box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        widget = box
    lbl = label(text)
    lbl.setFixedWidth(LABEL_WIDTH)
    lbl.setContentsMargins(0, 8, 0, 0)
    form.addRow(lbl, widget)
    return widget


class _Collapsible(QWidget):
    def __init__(self, title: str):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.title = title
        self.toggle = button(f"▸  {title}", "link")
        self.toggle.clicked.connect(self._flip)
        lay.addWidget(self.toggle, 0, Qt.AlignLeft)
        self.body = QWidget()
        self.form = _form()
        self.body.setLayout(self.form)
        self.body.hide()
        lay.addWidget(self.body)

    def _flip(self):
        self.body.setVisible(self.body.isHidden())
        self.toggle.setText(f"{'▾' if self.body.isVisible() else '▸'}  {self.title}")


class _FormPage(QWidget):
    changed = Signal()

    def __init__(self, title: str, subtitle: str):
        super().__init__()
        self._loading = False
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(16)
        self.card = Card(spacing=16, margins=26)
        self.title = label(title, "H1")
        self.subtitle = label(subtitle, "Muted", wrap=True)
        self.card.body.addWidget(self.title)
        self.card.body.addWidget(self.subtitle)
        self.card.body.addWidget(hline())
        self.form = _form()
        self.card.body.addLayout(self.form)
        self.lay.addWidget(self.card)

    def _emit(self, *_):
        if not self._loading:
            self.changed.emit()


# ---------------------------------------------------------------------------
# Allgemein


class GeneralForm(_FormPage):
    location_changed = Signal(object)  # Path

    def __init__(self):
        super().__init__("Allgemein", "Name und Aussehen des Walkthroughs auf der Startseite.")
        self.draft: dict = {}
        self.is_new = True

        self.name = QLineEdit()
        self.name.setPlaceholderText("z. B. Lieferantenbewertung")
        _row(self.form, "Name *", self.name)

        self.description = QPlainTextEdit()
        self.description.setPlaceholderText("Kurz beschreiben, wobei der Walkthrough hilft – erscheint auf der Kachel.")
        self.description.setFixedHeight(76)
        _row(self.form, "Beschreibung", self.description)

        look = QWidget()
        lr = QHBoxLayout(look)
        lr.setContentsMargins(0, 0, 0, 0)
        lr.setSpacing(16)
        self.icon = EmojiButton()
        lr.addWidget(self.icon)
        self.colors = ColorSwatches()
        lr.addWidget(self.colors, 1)
        _row(self.form, "Symbol & Farbe", look)
        self.background = ColorSwatches(A.BACKGROUNDS)
        _row(self.form, "Hintergrund", self.background,
             "Hinterlegt die Kachel auf der Startseite farbig. Weiß = keine Hinterlegung.")

        self.category = QComboBox()
        self.category.setEditable(True)
        self.category.setMinimumWidth(280)
        _row(self.form, "Kategorie", self.category, "Gruppiert die Kacheln auf der Startseite.")

        self.location = QComboBox()
        self.location_row = _row(self.form, "Speicherort", self.location)
        self.path_label = label("", "Faint", wrap=True)
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.path_row = _row(self.form, "Datei", self.path_label)

        adv = _Collapsible("Erweitert")
        self.card.body.addWidget(adv)
        self.wf_id = QLineEdit()
        _row(adv.form, "ID", self.wf_id, "Eindeutiger Kurzname, wird auch als Dateiname verwendet.")
        self.output = QLineEdit()
        self.output.setPlaceholderText("ergebnis.md")
        _row(adv.form, "Ergebnis-Datei", self.output)
        self.order = QSpinBox()
        self.order.setRange(0, 999)
        self.order.setMaximumWidth(120)
        _row(adv.form, "Reihenfolge", self.order, "Kleinere Zahl = weiter vorne auf der Startseite.")
        self.lay.addStretch()

        self.name.textChanged.connect(self._on_name)
        self.description.textChanged.connect(lambda: self._set("description", self.description.toPlainText()))
        self.icon.changed.connect(lambda v: self._set("icon", v))
        self.colors.changed.connect(lambda v: self._set("color", v))
        self.background.changed.connect(lambda v: self._set("background", v))
        self.category.currentTextChanged.connect(lambda v: self._set("category", v.strip()))
        self.wf_id.textChanged.connect(lambda v: self._set("id", v.strip()))
        self.output.textChanged.connect(lambda v: self._set("output_name", v.strip()))
        self.order.valueChanged.connect(lambda v: self._set("order", v))
        self.location.currentIndexChanged.connect(
            lambda: not self._loading and self.location_changed.emit(self.location.currentData()))

    def load(self, draft: dict, categories: list[str], source: Path | None,
             locations: list[tuple[str, Path]], target: Path | None):
        self._loading = True
        self.draft = draft
        self.name.setText(draft["name"])
        self.description.setPlainText(draft.get("description") or "")
        self.icon.setText(draft.get("icon") or "🤖")
        self.colors.set_color(draft.get("color") or "")
        self.background.set_color(draft.get("background") or "")
        self.category.clear()
        self.category.addItems(sorted(set(categories) | {draft.get("category") or "Allgemein"}))
        self.category.setCurrentText(draft.get("category") or "Allgemein")
        self.wf_id.setText(draft["id"])
        self.output.setText(draft.get("output_name") or "")
        self.order.setValue(int(draft.get("order") or 100))

        self.location.clear()
        for text, path in locations:
            self.location.addItem(text, path)
        if target:
            self.location.setCurrentIndex(max(0, self.location.findData(target)))
        self.is_new = source is None
        self.form.setRowVisible(self.location_row, source is None)
        self.form.setRowVisible(self.path_row, source is not None)
        self.path_label.setText(str(source or ""))
        self._loading = False

    def _set(self, key: str, value):
        if self._loading:
            return
        self.draft[key] = value
        self.changed.emit()

    def _on_name(self, text: str):
        if self._loading:
            return
        old = self.draft["name"]
        self.draft["name"] = text
        # ID folgt dem Namen, solange sie automatisch erzeugt wurde und die Datei neu ist
        if self.is_new:
            if A.is_auto_id(self.draft["id"], old, "-") or not self.draft["id"]:
                self.draft["id"] = A.slugify(text, "-") or "walkthrough"
                self._loading = True
                self.wf_id.setText(self.draft["id"])
                self._loading = False
        self.changed.emit()


# ---------------------------------------------------------------------------
# Schritt


class StepForm(_FormPage):
    SUBTITLES = {
        False: "Ein Schritt fasst zusammengehörige Fragen auf einer Seite zusammen.",
        True: "Ein Agent-Fenster zeigt den rohen Output des KI-Agenten und lässt den Nutzer direkt mit ihm "
              "schreiben. Das Ergebnis steht danach in der Prompt-Vorlage zur Verfügung. Voraussetzung: "
              "Übergabe „KI-Agent über ACP“ oder Demo-Modus.",
    }

    def __init__(self):
        super().__init__("Schritt", self.SUBTITLES[False])
        self.step: dict = {}
        self.draft: dict = {}
        self.step_title = QLineEdit()
        _row(self.form, "Titel *", self.step_title)
        self.description = QPlainTextEdit()
        self.description.setFixedHeight(70)
        self.description.setPlaceholderText("Optionaler Erklärungstext oberhalb der Fragen")
        _row(self.form, "Beschreibung", self.description)

        # Agent-Fenster
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self.agent_prompt = QPlainTextEdit()
        self.agent_prompt.setFont(mono)
        self.agent_prompt.setMinimumHeight(150)
        self.agent_prompt.setPlaceholderText(
            "Erste Nachricht an den Agenten, z. B.\nPrüfe die Angaben zu {{ anzeige.<id> }} und stelle Rückfragen.\n\n"
            "Leer lassen, wenn der Nutzer das Gespräch selbst beginnt.")
        self.agent_prompt_row = _row(
            self.form, "Startnachricht", self.agent_prompt,
            "Bausteine wie in der Prompt-Vorlage: {{ anzeige.<id> }}, {{ answers.<id> }}, "
            "{{ agent.<fenster>.antwort }}. Hochgeladene Dateien liegen im Arbeitsordner: {{ dateien_pfade }}.")
        opts = QWidget()
        ol = QVBoxLayout(opts)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.setSpacing(6)
        self.auto_start = QCheckBox("Startnachricht beim Öffnen automatisch senden")
        self.require_reply = QCheckBox("„Weiter“ erst nach einer Antwort des Agenten")
        for w in (self.auto_start, self.require_reply):
            ol.addWidget(w)
        self.agent_opts_row = _row(self.form, "Verhalten", opts)

        # Anzeige: Bestandteile des Agent-Fensters ein-/ausblenden
        disp = QWidget()
        grid = QGridLayout(disp)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        self.display_checks: dict[str, QCheckBox] = {}
        for i, (key, text, hint) in enumerate(A.AGENT_DISPLAY):
            box = QCheckBox(text)
            box.setToolTip(hint)
            box.toggled.connect(lambda v, k=key: self._set_agent(k, v))
            grid.addWidget(box, i // 2, i % 2)
            self.display_checks[key] = box
        self.agent_display_row = _row(self.form, "Anzeige", disp,
                                      "Welche Bestandteile das Agent-Fenster zeigt. Antworttext, Rückfragen und "
                                      "Freigabe-Anfragen sind immer sichtbar. Tooltips erklären die Punkte.")
        self.agent_placeholder = QLineEdit()
        self.agent_placeholder.setPlaceholderText("Nachricht an den Agenten … (Strg+Enter sendet)")
        self.agent_placeholder_row = _row(self.form, "Eingabehinweis", self.agent_placeholder)

        self.condition = ConditionEditor("Schritt")
        _row(self.form, "Sichtbarkeit", self.condition)
        adv = _Collapsible("Erweitert")
        self.card.body.addWidget(adv)
        self.step_id = QLineEdit()
        self.step_id_row = _row(adv.form, "ID", self.step_id)

        # Live-Vorschau des Agent-Fensters
        self.preview_card = Card(spacing=12, margins=22)
        self.preview_card.body.addWidget(label("👁  So sieht das Agent-Fenster aus (Beispielinhalt)", "H3"))
        self.preview_host = QVBoxLayout()
        self.preview_card.body.addLayout(self.preview_host)
        self.lay.addWidget(self.preview_card)
        self.preview_timer = QTimer(self, singleShot=True, interval=200)
        self.preview_timer.timeout.connect(self._render_preview)
        self.lay.addStretch()

        self.step_title.textChanged.connect(self._on_title)
        self.description.textChanged.connect(self._on_desc)
        self.condition.changed.connect(self._on_condition)
        self.step_id.textChanged.connect(self._on_id)
        self.agent_prompt.textChanged.connect(lambda: self._set_agent("prompt", self.agent_prompt.toPlainText()))
        self.auto_start.toggled.connect(lambda v: self._set_agent("auto_start", v))
        self.require_reply.toggled.connect(lambda v: self._set_agent("require_reply", v))
        self.agent_placeholder.textChanged.connect(lambda v: self._set_agent("placeholder", v))

    @property
    def is_agent(self) -> bool:
        return A.is_agent_step(self.step)

    def load(self, step: dict, draft: dict, number: int):
        self._loading = True
        self.step, self.draft = step, draft
        agent = self.is_agent
        self.title.setText(f"{'💬 Agent-Fenster' if agent else 'Schritt'} {number}")
        self.subtitle.setText(self.SUBTITLES[agent])
        self.step_title.setText(step["title"])
        self.description.setPlainText(step.get("description") or "")
        self.description.setPlaceholderText("Optionaler Erklärungstext oberhalb des Agent-Fensters" if agent
                                            else "Optionaler Erklärungstext oberhalb der Fragen")
        for row in (self.agent_prompt_row, self.agent_opts_row, self.agent_placeholder_row,
                    self.agent_display_row):
            self.form.setRowVisible(row, agent)
        cfg = {**A.AGENT_DEFAULTS, **(step.get("agent") or {})}
        self.agent_prompt.setPlainText(cfg["prompt"] or "")
        self.auto_start.setChecked(bool(cfg["auto_start"]))
        self.require_reply.setChecked(bool(cfg["require_reply"]))
        for key, box in self.display_checks.items():
            box.setChecked(bool(cfg[key]))
        self.agent_placeholder.setText(cfg["placeholder"] or "")
        self.preview_card.setVisible(agent)
        self.condition.load(step.get("show_if"), A.earlier_questions(draft, step))
        self.step_id.setText(step["id"])
        self._loading = False
        if agent:
            self._render_preview()

    def _set_agent(self, key: str, value):
        if self._loading or not self.is_agent:
            return
        self.step.setdefault("agent", dict(A.AGENT_DEFAULTS))[key] = value
        if key in self.display_checks or key == "placeholder":
            self.preview_timer.start()
        self.changed.emit()

    def _render_preview(self):
        clear_layout(self.preview_host)
        if not self.is_agent:
            return
        cfg = {**A.AGENT_DEFAULTS, **(self.step.get("agent") or {})}
        try:
            config = AgentStepConfig.model_validate(cfg)
        except ValidationError:
            return
        console = AgentConsole(config)
        console.setMinimumHeight(420)
        console.preview()
        self.preview_host.addWidget(console)

    def _taken_ids(self) -> list[str]:
        taken = [s["id"] for s in self.draft["steps"] if s is not self.step]
        return taken + (A.question_ids(self.draft) if self.is_agent else [])

    def _on_title(self, text: str):
        if self._loading:
            return
        if A.is_auto_id(self.step["id"], self.step["title"]):
            old = self.step["id"]
            self.step["id"] = A.unique_id(A.slugify(text) or "schritt", self._taken_ids())
            if self.is_agent:
                A.rename_agent_step(self.draft, old, self.step["id"])
            self._loading = True
            self.step_id.setText(self.step["id"])
            self._loading = False
        self.step["title"] = text
        self.changed.emit()

    def _on_desc(self):
        if not self._loading:
            self.step["description"] = self.description.toPlainText()
            self.changed.emit()

    def _on_condition(self):
        self.step["show_if"] = self.condition.condition()
        self._emit()

    def _on_id(self, text: str):
        if not self._loading:
            if self.is_agent:
                A.rename_agent_step(self.draft, self.step["id"], text.strip())
            self.step["id"] = text.strip()
            self.changed.emit()


# ---------------------------------------------------------------------------
# Frage


class QuestionForm(_FormPage):
    def __init__(self):
        super().__init__("Frage", "Was soll der Nutzer an dieser Stelle angeben?")
        self.q: dict = {}
        self.draft: dict = {}

        self.qtype = type_combo()
        self.type_hint = label("", "Faint")
        type_box = QWidget()
        tl = QVBoxLayout(type_box)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(4)
        tl.addWidget(self.qtype)
        tl.addWidget(self.type_hint)
        _row(self.form, "Art der Eingabe", type_box)

        self.qlabel = QLineEdit()
        self.qlabel.setPlaceholderText("z. B. Wie viele Stück werden pro Jahr benötigt?")
        _row(self.form, "Frage *", self.qlabel)
        self.required = QCheckBox("Pflichtfeld – muss ausgefüllt werden")
        _row(self.form, "", self.required)
        self.help = QLineEdit()
        self.help.setPlaceholderText("Optionaler Hinweis unter der Frage")
        _row(self.form, "Hilfetext", self.help)
        self.placeholder = QLineEdit()
        self.placeholder.setPlaceholderText("Grauer Beispieltext im leeren Feld")
        self.placeholder_row = _row(self.form, "Platzhalter", self.placeholder)

        self.options = OptionsEditor()
        self.options_row = _row(self.form, "Antwortoptionen", self.options)

        rng = QWidget()
        rl = QHBoxLayout(rng)
        rl.setContentsMargins(0, 0, 0, 0)
        self.min = QLineEdit()
        self.min.setPlaceholderText("min.")
        self.max = QLineEdit()
        self.max.setPlaceholderText("max.")
        for w in (self.min, self.max):
            w.setMaximumWidth(110)
        rl.addWidget(self.min)
        rl.addWidget(label("bis"))
        rl.addWidget(self.max)
        rl.addStretch()
        self.range_row = _row(self.form, "Wertebereich", rng)
        self.unit = QLineEdit()
        self.unit.setPlaceholderText("z. B. Stk., €, kg")
        self.unit.setMaximumWidth(200)
        self.unit_row = _row(self.form, "Einheit", self.unit)

        sl = QWidget()
        sll = QHBoxLayout(sl)
        sll.setContentsMargins(0, 0, 0, 0)
        self.scale_lo = QLineEdit()
        self.scale_lo.setPlaceholderText("links, z. B. gering")
        self.scale_hi = QLineEdit()
        self.scale_hi.setPlaceholderText("rechts, z. B. hoch")
        sll.addWidget(self.scale_lo)
        sll.addWidget(self.scale_hi)
        self.scale_row = _row(self.form, "Skalen-Beschriftung", sl)

        self.filter = QComboBox()
        self.filter.setEditable(True)
        for name, flt in A.FILE_FILTER_PRESETS.items():
            self.filter.addItem(name, flt)
        self.filter_row = _row(self.form, "Erlaubte Dateien", self.filter,
                               "Vorlage wählen oder eigenen Filter eintragen, z. B. „Zeichnungen (*.pdf *.dxf)“.")

        self.default = DefaultEditor()
        self.default_row = _row(self.form, "Standardwert", self.default)
        self.condition = ConditionEditor()
        _row(self.form, "Sichtbarkeit", self.condition)

        adv = _Collapsible("Erweitert")
        self.card.body.addWidget(adv)
        self.qid = QLineEdit()
        self.qid_row = _row(adv.form, "ID", self.qid,
                            "Name der Antwort in der Prompt-Vorlage, z. B. {{ anzeige.<id> }}. "
                            "Umbenennen passt Bedingungen und Prompt automatisch an.")

        # Live-Vorschau
        self.preview_card = Card(spacing=12, margins=22)
        head = QHBoxLayout()
        head.addWidget(label("👁  So sieht die Frage im Walkthrough aus", "H3"), 1)
        self.preview_card.body.addLayout(head)
        self.preview_host = QVBoxLayout()
        self.preview_card.body.addLayout(self.preview_host)
        self.lay.addWidget(self.preview_card)
        self.lay.addStretch()
        self.preview_timer = QTimer(self, singleShot=True, interval=150)
        self.preview_timer.timeout.connect(self._render_preview)

        self.qtype.currentIndexChanged.connect(self._on_type)
        self.qlabel.textChanged.connect(self._on_label)
        self.required.toggled.connect(lambda v: self._set("required", v))
        self.help.textChanged.connect(lambda v: self._set("help", v))
        self.placeholder.textChanged.connect(lambda v: self._set("placeholder", v))
        self.options.changed.connect(self._on_options)
        self.min.textChanged.connect(lambda v: self._set_num("min", v))
        self.max.textChanged.connect(lambda v: self._set_num("max", v))
        self.unit.textChanged.connect(lambda v: self._set("unit", v.strip()))
        self.scale_lo.textChanged.connect(self._on_scale_labels)
        self.scale_hi.textChanged.connect(self._on_scale_labels)
        self.filter.currentTextChanged.connect(self._on_filter)
        self.default.changed.connect(self._after_change)
        self.condition.changed.connect(self._on_condition)
        self.qid.editingFinished.connect(self._on_id)

    def load(self, q: dict, draft: dict):
        self._loading = True
        self.q, self.draft = q, draft
        self.qtype.setCurrentIndex(max(0, self.qtype.findData(q["type"])))
        self.qlabel.setText(q["label"])
        self.required.setChecked(bool(q.get("required")))
        self.help.setText(q.get("help") or "")
        self.placeholder.setText(q.get("placeholder") or "")
        self.options.load(q["options"])
        self.min.setText("" if q.get("min") is None else A.fmt_plain(q["min"]))
        self.max.setText("" if q.get("max") is None else A.fmt_plain(q["max"]))
        self.unit.setText(q.get("unit") or "")
        labels = list(q.get("scale_labels") or []) + ["", ""]
        self.scale_lo.setText(labels[0])
        self.scale_hi.setText(labels[1] if len(q.get("scale_labels") or []) > 1 else "")
        preset = next((n for n, f in A.FILE_FILTER_PRESETS.items() if f == q.get("filter")), None)
        self.filter.setCurrentText(preset or q.get("filter") or "Alle Dateien")
        self.default.load(q)
        self.condition.load(q.get("show_if"), A.earlier_questions(draft, q))
        self.qid.setText(q["id"])
        self._update_rows()
        self._loading = False
        self._render_preview()

    # ---- Änderungen ----------------------------------------------------
    def _set(self, key: str, value):
        if self._loading:
            return
        self.q[key] = value
        self._after_change()

    def _set_num(self, key: str, text: str):
        if self._loading:
            return
        text = text.strip()
        if not text:
            self.q[key] = None
        else:
            try:
                self.q[key] = A.parse_number(text)
            except ValueError:
                return
        self._after_change()

    def _after_change(self):
        if self._loading:
            return
        self.preview_timer.start()
        self.changed.emit()

    def _on_type(self):
        if self._loading:
            return
        self.q["type"] = self.qtype.currentData()
        A.ensure_type_defaults(self.q)
        if self.q.get("default") is not None and not DefaultEditor.supports(self.q["type"]):
            self.q["default"] = None
        self.load(self.q, self.draft)  # Felder je nach Typ neu aufbauen
        self.changed.emit()

    def _on_label(self, text: str):
        if self._loading:
            return
        if A.is_auto_id(self.q["id"], self.q["label"]):
            new_id = A.unique_id(A.slugify(text) or "frage", A.question_ids(self.draft, exclude=self.q))
            A.rename_question(self.draft, self.q["id"], new_id)
            self.q["id"] = new_id
            self._loading = True
            self.qid.setText(new_id)
            self._loading = False
        self.q["label"] = text
        self._after_change()

    def _on_id(self):
        new_id = A.slugify(self.qid.text()) or self.q["id"]
        new_id = A.unique_id(new_id, A.question_ids(self.draft, exclude=self.q)) if new_id != self.q["id"] else new_id
        if new_id != self.q["id"]:
            A.rename_question(self.draft, self.q["id"], new_id)
            self.q["id"] = new_id
            self.changed.emit()
        self.qid.setText(new_id)

    def _on_options(self):
        self.default.load(self.q)
        self._after_change()

    def _on_scale_labels(self):
        if self._loading:
            return
        lo, hi = self.scale_lo.text().strip(), self.scale_hi.text().strip()
        self.q["scale_labels"] = [lo, hi] if (lo or hi) else []
        self._after_change()

    def _on_filter(self, text: str):
        if self._loading:
            return
        self.q["filter"] = A.FILE_FILTER_PRESETS.get(text, text.strip())
        self._after_change()

    def _on_condition(self):
        self.q["show_if"] = self.condition.condition()
        self._after_change()

    # ---- Darstellung -----------------------------------------------------
    def _update_rows(self):
        t = self.q["type"]
        icon, name, desc = A.QUESTION_TYPES[t]
        self.type_hint.setText(desc)
        self.form.setRowVisible(self.placeholder_row, t in A.PLACEHOLDER_TYPES)
        self.form.setRowVisible(self.options_row, t in A.OPTION_TYPES)
        self.form.setRowVisible(self.range_row, t in A.RANGE_TYPES)
        self.form.setRowVisible(self.unit_row, t in A.UNIT_TYPES)
        self.form.setRowVisible(self.scale_row, t == "scale")
        self.form.setRowVisible(self.filter_row, t in A.FILE_TYPES)
        self.form.setRowVisible(self.default_row, DefaultEditor.supports(t))

    def _render_preview(self):
        clear_layout(self.preview_host)
        try:
            question = Question.model_validate(A.clean_question(self.q))
        except (ValidationError, ValueError, TypeError) as exc:
            msg = exc.errors()[0]["msg"].removeprefix("Value error, ") if isinstance(exc, ValidationError) else exc
            self.preview_host.addWidget(label(f"Vorschau nicht möglich: {msg}", "Muted", wrap=True))
            return
        field = create_field(question)
        self.preview_host.addWidget(field)


# ---------------------------------------------------------------------------
# Prompt


class PromptForm(_FormPage):
    def __init__(self):
        super().__init__(
            "Prompt-Vorlage",
            "Dieser Text wird mit den Antworten gefüllt und an Copilot bzw. den KI-Agenten übergeben. Klicke auf einen "
            "Baustein, um ihn an der Cursorposition einzufügen. Inhalte hochgeladener Dateien werden "
            "automatisch als „Anhänge“ ans Ende gesetzt.")
        self.draft: dict = {}
        self.chips_host = QWidget()
        self.chips = FlowLayout(self.chips_host)
        self.card.body.addWidget(label("Bausteine", "H3"))
        self.card.body.addWidget(self.chips_host)

        self.editor = QPlainTextEdit()
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSizeF(self.font().pointSizeF() * 0.95)
        self.editor.setFont(mono)
        self.editor.setMinimumHeight(280)
        self.editor.setPlaceholderText("Leer lassen, um die Standardvorlage zu verwenden "
                                       "(alle Antworten als Tabelle + Dateiliste).")
        self.card.body.addWidget(self.editor)
        row = QHBoxLayout()
        self.std_btn = button("Standardvorlage als Startpunkt einfügen")
        self.std_btn.clicked.connect(self._insert_default)
        row.addWidget(self.std_btn)
        row.addStretch()
        self.card.body.addLayout(row)
        self.card.body.addWidget(label(
            "Für Fortgeschrittene: Die Vorlage nutzt Jinja2, z. B. {% if answers.<id> %} … {% endif %} "
            "für Abschnitte, die nur bei bestimmten Antworten erscheinen.", "Faint", wrap=True))

        self.preview_card = Card(spacing=10, margins=22)
        self.preview_card.body.addWidget(label("👁  Vorschau mit Beispielantworten", "H3"))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(mono)
        self.preview.setMinimumHeight(220)
        self.preview_card.body.addWidget(self.preview)
        self.lay.addWidget(self.preview_card)
        self.lay.addStretch()

        self.timer = QTimer(self, singleShot=True, interval=250)
        self.timer.timeout.connect(self._render_preview)
        self.editor.textChanged.connect(self._on_text)

    def load(self, draft: dict):
        self._loading = True
        self.draft = draft
        self.editor.setPlainText(draft.get("prompt") or "")
        self.editor.moveCursor(QTextCursor.End)
        clear_layout(self.chips)
        chips = [("📋 Alle Antworten (Tabelle)", "{{ antworten_tabelle }}"),
                 ("📎 Liste der Unterlagen", "{{ dateien_liste }}"),
                 ("📅 Heutiges Datum", "{{ heute }}")]
        for s in draft["steps"]:
            if A.is_agent_step(s):
                chips.append((f"💬 {s['title']}: Antwort", f"{{{{ agent.{s['id']}.antwort }}}}"))
                chips.append((f"💬 {s['title']}: Verlauf", f"{{{{ agent.{s['id']}.verlauf }}}}"))
            for q in s["questions"]:
                if q["type"] in A.FILE_TYPES:
                    continue
                icon = A.QUESTION_TYPES[q["type"]][0]
                chips.append((f"{icon} {q['label']}", f"{{{{ anzeige.{q['id']} }}}}"))
        for text, snippet in chips:
            chip = button(text)
            chip.setObjectName("VarChip")
            chip.setToolTip(f"Fügt {snippet} ein")
            chip.clicked.connect(lambda _=False, s=snippet: self._insert(s))
            self.chips.addWidget(chip)
        self._loading = False
        self._render_preview()

    def _insert(self, snippet: str):
        self.editor.insertPlainText(snippet)
        self.editor.setFocus()

    def _insert_default(self):
        if self.editor.toPlainText().strip() and QMessageBox.question(
                self, "Vorlage ersetzen?", "Der aktuelle Text wird durch die Standardvorlage ersetzt.") \
                != QMessageBox.Yes:
            return
        self.editor.setPlainText(A.default_prompt())

    def _on_text(self):
        if self._loading:
            return
        self.draft["prompt"] = self.editor.toPlainText()
        self.timer.start()
        self.changed.emit()

    def _render_preview(self):
        self.preview.setPlainText(A.preview_prompt(self.draft))


# ---------------------------------------------------------------------------
# Editor-Seite


class WorkflowEditorPage(QWidget):
    saved = Signal(object)  # Path
    test_requested = Signal(object)  # Workflow
    leave = Signal()

    def __init__(self, settings: Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.settings = settings
        self.draft: dict = A.new_draft()
        self.source: Path | None = None
        self.target_dir: Path = user_workflow_dir()
        self.categories: list[str] = []
        self.dirty = False
        self._current_key = "general"

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 22, 32, 22)
        root.setSpacing(14)

        # Kopfzeile
        head = QHBoxLayout()
        head.setSpacing(12)
        back = button("←  Zurück", "ghost")
        back.clicked.connect(self.leave)
        head.addWidget(back)
        self.head_icon = label("", "H1")
        head.addWidget(self.head_icon)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.head_title = label("", "H2")
        self.head_sub = label("", "Faint")
        titles.addWidget(self.head_title)
        titles.addWidget(self.head_sub)
        head.addLayout(titles, 1)
        self.status = label("", "ChipNeutral")
        head.addWidget(self.status)
        test = button("▶  Testen", tooltip="Walkthrough mit dem aktuellen Stand durchspielen")
        test.clicked.connect(self._test)
        head.addWidget(test)
        self.save_btn = button("Speichern", "primary")
        self.save_btn.setMinimumWidth(130)
        self.save_btn.clicked.connect(self.save)
        head.addWidget(self.save_btn)
        root.addLayout(head)

        self.banner = Banner("warning")
        root.addWidget(self.banner)

        body = QHBoxLayout()
        body.setSpacing(20)

        # Gliederung
        outline = Card(margins=14, spacing=10)
        outline.setFixedWidth(300)
        shadow(outline, blur=16, y=2, alpha=10)
        outline.body.addWidget(label("Aufbau", "H3"))
        self.tree = QTreeWidget()
        self.tree.setObjectName("Outline")
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(18)
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.currentItemChanged.connect(self._on_select)
        outline.body.addWidget(self.tree, 1)

        add_row = QHBoxLayout()
        add_step = button("＋ Schritt  ▾")
        add_step.setObjectName("MenuButton")
        step_menu = QMenu(add_step)
        step_menu.setToolTipsVisible(True)
        act = step_menu.addAction("📁   Schritt mit Fragen")
        act.setToolTip("Eine Seite mit Fragen im Walkthrough")
        act.triggered.connect(self._add_step)
        act = step_menu.addAction("💬   Agent-Fenster")
        act.setToolTip("Roher Output des KI-Agenten mit Eingabefeld – direkter Dialog mit dem Agenten")
        act.triggered.connect(self._add_agent_step)
        add_step.setMenu(step_menu)
        self.add_q = button("＋ Frage  ▾", "primary")
        self.add_q.setObjectName("MenuButton")
        menu = QMenu(self.add_q)
        menu.setToolTipsVisible(True)
        for key, (icon, name, desc) in A.QUESTION_TYPES.items():
            act = menu.addAction(f"{icon}   {name}")
            act.setToolTip(desc)
            act.triggered.connect(lambda _=False, k=key: self._add_question(k))
        self.add_q.setMenu(menu)
        add_row.addWidget(add_step)
        add_row.addWidget(self.add_q, 1)
        outline.body.addLayout(add_row)

        tools = QHBoxLayout()
        tools.setSpacing(4)
        self.up_btn = button("↑", "ghost", "Nach oben")
        self.down_btn = button("↓", "ghost", "Nach unten")
        self.dup_btn = button("⧉", "ghost", "Duplizieren")
        self.del_btn = button("🗑", "ghost", "Löschen")
        self.up_btn.clicked.connect(lambda: self._move(-1))
        self.down_btn.clicked.connect(lambda: self._move(1))
        self.dup_btn.clicked.connect(self._duplicate)
        self.del_btn.clicked.connect(self._delete)
        tools.addWidget(label("Auswahl:", "Faint"))
        for b in (self.up_btn, self.down_btn, self.dup_btn, self.del_btn):
            b.setFixedWidth(40)
            tools.addWidget(b)
        tools.addStretch()
        outline.body.addLayout(tools)
        body.addWidget(outline)

        # Formulare
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.stack = QStackedWidget()
        self.general = GeneralForm()
        self.step_form = StepForm()
        self.question_form = QuestionForm()
        self.prompt_form = PromptForm()
        for f in (self.general, self.step_form, self.question_form, self.prompt_form):
            self.stack.addWidget(f)
            f.changed.connect(self._on_changed)
        self.general.location_changed.connect(self._on_location)
        self.stack.currentChanged.connect(self._fit_stack)
        host = QWidget()
        hl = QVBoxLayout(host)
        hl.setContentsMargins(0, 0, 10, 0)
        hl.addWidget(self.stack)
        hl.addStretch()
        self.scroll.setWidget(host)
        body.addWidget(self.scroll, 1)
        root.addLayout(body, 1)

        self.validate_timer = QTimer(self, singleShot=True, interval=400)
        self.validate_timer.timeout.connect(self._validate)

    def _fit_stack(self, index: int):
        for i in range(self.stack.count()):
            policy = QSizePolicy.Preferred if i == index else QSizePolicy.Ignored
            self.stack.widget(i).setSizePolicy(policy, policy)
        self.stack.adjustSize()

    # ---- Öffnen -------------------------------------------------------
    def set_categories(self, categories: list[str]):
        self.categories = categories

    def open_new(self, draft: dict | None = None, target_dir: Path | None = None):
        self.draft = draft or A.new_draft()
        self.source = None
        self.target_dir = target_dir or user_workflow_dir()
        self._open()
        self.dirty = draft is not None  # Duplikat ist noch nicht gespeichert
        self._update_header()

    def open_file(self, path: Path):
        self.draft = A.load_draft(path)
        self.source = path
        self.target_dir = path.parent
        self._open()

    def _open(self):
        self.dirty = False
        self._current_key = "general"
        self._rebuild_tree("general")
        self._update_header()
        self._validate()

    # ---- Gliederung -----------------------------------------------------
    def _rebuild_tree(self, select: str | None = None):
        self.tree.blockSignals(True)
        self.tree.clear()
        bold = QFont(self.font())
        bold.setWeight(QFont.DemiBold)
        general = QTreeWidgetItem([f"⚙️   Allgemein"])
        general.setData(0, Qt.UserRole, "general")
        self.tree.addTopLevelItem(general)
        for si, s in enumerate(self.draft["steps"]):
            item = QTreeWidgetItem([self._step_text(si, s)])
            item.setData(0, Qt.UserRole, f"step:{si}")
            item.setFont(0, bold)
            for qi, q in enumerate(s["questions"]):
                child = QTreeWidgetItem([self._question_text(q)])
                child.setData(0, Qt.UserRole, f"q:{si}:{qi}")
                item.addChild(child)
            self.tree.addTopLevelItem(item)
        prompt = QTreeWidgetItem(["🧩   Prompt-Vorlage"])
        prompt.setData(0, Qt.UserRole, "prompt")
        self.tree.addTopLevelItem(prompt)
        self.tree.expandAll()
        self.tree.blockSignals(False)
        self._select(select or self._current_key)

    @staticmethod
    def _step_text(si: int, s: dict) -> str:
        cond = "  ⑂" if s.get("show_if") else ""
        icon = "💬 " if A.is_agent_step(s) else ""
        return f"{si + 1}.  {icon}{s['title'] or '(ohne Titel)'}{cond}"

    @staticmethod
    def _question_text(q: dict) -> str:
        icon = A.QUESTION_TYPES.get(q["type"], ("•",))[0]
        req = " *" if q.get("required") else ""
        cond = "  ⑂" if q.get("show_if") else ""
        return f"{icon}  {q['label'] or '(ohne Text)'}{req}{cond}"

    def _find_item(self, key: str) -> QTreeWidgetItem | None:
        it = self._iter_items()
        return next((i for i in it if i.data(0, Qt.UserRole) == key), None)

    def _iter_items(self):
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            yield top
            for c in range(top.childCount()):
                yield top.child(c)

    def _select(self, key: str):
        item = self._find_item(key) or self.tree.topLevelItem(0)
        self.tree.setCurrentItem(item)
        self._on_select(item, None)

    def _parse(self, key: str):
        parts = key.split(":")
        if parts[0] == "step":
            return "step", int(parts[1]), None
        if parts[0] == "q":
            return "q", int(parts[1]), int(parts[2])
        return parts[0], None, None

    def _on_select(self, item, _prev):
        if item is None:
            return
        key = item.data(0, Qt.UserRole)
        self._current_key = key
        kind, si, qi = self._parse(key)
        if kind == "general":
            self.general.load(self.draft, self.categories, self.source,
                              self._locations(), self.target_dir)
            self.stack.setCurrentWidget(self.general)
        elif kind == "step":
            self.step_form.load(self.draft["steps"][si], self.draft, si + 1)
            self.stack.setCurrentWidget(self.step_form)
        elif kind == "q":
            self.question_form.load(self.draft["steps"][si]["questions"][qi], self.draft)
            self.stack.setCurrentWidget(self.question_form)
        else:
            self.prompt_form.load(self.draft)
            self.stack.setCurrentWidget(self.prompt_form)
        movable = kind in ("step", "q")
        for b in (self.up_btn, self.down_btn, self.dup_btn, self.del_btn):
            b.setEnabled(movable)
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(0))

    # ---- Struktur ändern ------------------------------------------------
    def _current_step_index(self) -> int:
        kind, si, _ = self._parse(self._current_key)
        if kind in ("step", "q"):
            return si
        return len(self.draft["steps"]) - 1

    def _add_step(self):
        step = A.new_step(self.draft)
        idx = self._current_step_index() + 1
        self.draft["steps"].insert(idx, step)
        self._structure_changed(f"step:{idx}")

    def _add_agent_step(self):
        step = A.new_agent_step(self.draft)
        idx = self._current_step_index() + 1
        self.draft["steps"].insert(idx, step)
        self._structure_changed(f"step:{idx}")

    def _add_question(self, qtype: str):
        if not self.draft["steps"]:
            self.draft["steps"].append(A.new_step(self.draft))
        si = max(0, self._current_step_index())
        step = self.draft["steps"][si]
        if A.is_agent_step(step):
            QMessageBox.information(self, "Agent-Fenster", "Ein Agent-Fenster enthält keine Fragen. Füge über "
                                    "„＋ Schritt“ einen Schritt mit Fragen hinzu.")
            return
        q = A.new_question(self.draft, qtype, step)
        kind, _, qi = self._parse(self._current_key)
        pos = qi + 1 if kind == "q" else len(step["questions"])
        step["questions"].insert(pos, q)
        self._structure_changed(f"q:{si}:{pos}")

    def _move(self, delta: int):
        kind, si, qi = self._parse(self._current_key)
        steps = self.draft["steps"]
        if kind == "step":
            t = si + delta
            if 0 <= t < len(steps):
                steps[si], steps[t] = steps[t], steps[si]
                self._structure_changed(f"step:{t}")
        elif kind == "q":
            qs = steps[si]["questions"]
            t = qi + delta
            if 0 <= t < len(qs):
                qs[qi], qs[t] = qs[t], qs[qi]
                self._structure_changed(f"q:{si}:{t}")
            elif t < 0 and si > 0 and not A.is_agent_step(steps[si - 1]):  # in den vorherigen Schritt
                steps[si - 1]["questions"].append(qs.pop(qi))
                self._structure_changed(f"q:{si - 1}:{len(steps[si - 1]['questions']) - 1}")
            elif t >= len(qs) and si < len(steps) - 1 and not A.is_agent_step(steps[si + 1]):  # nächster Schritt
                steps[si + 1]["questions"].insert(0, qs.pop(qi))
                self._structure_changed(f"q:{si + 1}:0")

    def _duplicate(self):
        kind, si, qi = self._parse(self._current_key)
        if kind == "step":
            step = copy.deepcopy(self.draft["steps"][si])
            step["title"] += " (Kopie)"
            step["id"] = A.unique_id(step["id"], [s["id"] for s in self.draft["steps"]])
            taken = A.question_ids(self.draft)
            for q in step["questions"]:
                q["id"] = A.unique_id(q["id"], taken)
                taken.append(q["id"])
            self.draft["steps"].insert(si + 1, step)
            self._structure_changed(f"step:{si + 1}")
        elif kind == "q":
            q = copy.deepcopy(self.draft["steps"][si]["questions"][qi])
            q["label"] += " (Kopie)"
            q["id"] = A.unique_id(q["id"], A.question_ids(self.draft))
            self.draft["steps"][si]["questions"].insert(qi + 1, q)
            self._structure_changed(f"q:{si}:{qi + 1}")

    def _delete(self):
        kind, si, qi = self._parse(self._current_key)
        if kind == "step":
            step = self.draft["steps"][si]
            ids = [q["id"] for q in step["questions"]]
            refs = [r for qid in ids for r in A.references(self.draft, qid)]
            text = f"Schritt „{step['title']}“ mit {len(ids)} Frage(n) löschen?"
            if refs:
                text += "\n\nDie Fragen werden noch verwendet in:\n• " + "\n• ".join(dict.fromkeys(refs))
            if QMessageBox.question(self, "Schritt löschen", text) != QMessageBox.Yes:
                return
            del self.draft["steps"][si]
            for qid in ids:
                A.remove_condition_references(self.draft, qid)
            self._structure_changed(f"step:{max(0, si - 1)}" if self.draft["steps"] else "general")
        elif kind == "q":
            q = self.draft["steps"][si]["questions"][qi]
            refs = A.references(self.draft, q["id"])
            text = f"Frage „{q['label']}“ löschen?"
            if refs:
                text += ("\n\nSie wird noch verwendet in:\n• " + "\n• ".join(refs)
                         + "\n\nBedingungen, die sich darauf beziehen, werden entfernt.")
            if QMessageBox.question(self, "Frage löschen", text) != QMessageBox.Yes:
                return
            del self.draft["steps"][si]["questions"][qi]
            A.remove_condition_references(self.draft, q["id"])
            remaining = len(self.draft["steps"][si]["questions"])
            self._structure_changed(f"q:{si}:{min(qi, remaining - 1)}" if remaining else f"step:{si}")

    def _structure_changed(self, select: str):
        self._mark_dirty()
        self._rebuild_tree(select)

    # ---- Änderungen ----------------------------------------------------
    def _on_changed(self):
        self._mark_dirty()
        item = self.tree.currentItem()
        kind, si, qi = self._parse(self._current_key)
        if item is not None:
            if kind == "step":
                item.setText(0, self._step_text(si, self.draft["steps"][si]))
            elif kind == "q":
                item.setText(0, self._question_text(self.draft["steps"][si]["questions"][qi]))
        self._update_header()

    def _on_location(self, path: Path):
        if path:
            self.target_dir = path
            self._update_header()

    def _mark_dirty(self):
        self.dirty = True
        self._update_header()
        self.validate_timer.start()

    def _update_header(self):
        self.head_icon.setText(self.draft.get("icon") or "🤖")
        self.head_title.setText(self.draft.get("name") or "(ohne Namen)")
        where = self.source or (self.target_dir / f"{self.draft.get('id') or '…'}.yaml")
        folder = "Meine Walkthroughs" if where.parent == user_workflow_dir() else where.parent.name
        state = "Neu – noch nicht gespeichert" if self.source is None else "Gespeichert"
        if self.dirty and self.source is not None:
            state = "● Ungespeicherte Änderungen"
        self.head_sub.setText(f"{state}  ·  {folder} › {where.name}")
        self.head_sub.setToolTip(str(where))

    def _validate(self) -> list[str]:
        _, errors = A.validate_draft(self.draft)
        if errors:
            shown = errors[:6] + ([f"… und {len(errors) - 6} weitere"] if len(errors) > 6 else [])
            self.banner.show_message("warning", "Noch nicht fertig:\n" + "\n".join(f"• {e}" for e in shown))
            self.status.setText(f"⚠️  {len(errors)} Hinweis{'e' if len(errors) > 1 else ''}")
        else:
            self.banner.show_message("warning", "")
            self.status.setText("✅  Bereit")
        return errors

    # ---- Speichern / Testen --------------------------------------------
    def save(self) -> bool:
        errors = self._validate()
        if errors:
            self.banner.show_message("error", "Speichern nicht möglich:\n" + "\n".join(f"• {e}" for e in errors))
            return False
        path = self.source or (self.target_dir / f"{self.draft['id']}.yaml")
        if self.source is None and path.exists():
            if QMessageBox.question(self, "Datei existiert bereits",
                                    f"{path.name} gibt es schon. Überschreiben?") != QMessageBox.Yes:
                return False
        try:
            A.save_draft(self.draft, path)
        except OSError as exc:
            self.banner.show_message("error", f"Speichern fehlgeschlagen: {exc}")
            return False
        self.source = path
        self.dirty = False
        self._update_header()
        self.banner.show_message("success", f"Gespeichert: {path}")
        if self._current_key == "general":
            self._select("general")
        self.saved.emit(path)
        return True

    def _test(self):
        wf, errors = A.validate_draft(self.draft)
        if errors:
            self._validate()
            return
        self.test_requested.emit(wf)

    def confirm_leave(self) -> bool:
        if not self.dirty:
            return True
        box = QMessageBox(self)
        box.setWindowTitle("Ungespeicherte Änderungen")
        box.setText(f"Änderungen an „{self.draft.get('name')}“ speichern?")
        box.setStandardButtons(QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        box.setButtonText(QMessageBox.Save, "Speichern")
        box.setButtonText(QMessageBox.Discard, "Verwerfen")
        box.setButtonText(QMessageBox.Cancel, "Abbrechen")
        answer = box.exec()
        if answer == QMessageBox.Save:
            return self.save()
        if answer == QMessageBox.Discard:
            self.dirty = False
            return True
        return False

    # ---- Hilfen ------------------------------------------------------------
    def _locations(self) -> list[tuple[str, Path]]:
        locs = [("Meine Walkthroughs (lokal)", user_workflow_dir())]
        locs += [(f"📁 {p}", Path(p)) for p in self.settings.workflow_dirs if p.strip()]
        return locs

