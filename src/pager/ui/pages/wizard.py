"""Walkthrough: führt Schritt für Schritt durch die Fragen eines Workflows."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QPlainTextEdit, QProgressBar, QScrollArea, QSizePolicy, QVBoxLayout,
    QWidget,
)

from pager.core.prompting import render_prompt
from pager.core.workflow import Step, Workflow
from pager.ui.widgets.common import Card, button, clear_layout, hline, label
from pager.ui.widgets.fields import QuestionField, create_field
from pager.ui.widgets.stepper import Stepper

REVIEW_TITLE = "Überprüfen & starten"


class WizardPage(QWidget):
    back_home = Signal()
    launch = Signal(object, dict)  # Workflow, Antworten

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.wf: Workflow | None = None
        self.answers: dict[str, Any] = {}
        self.index = 0
        self.max_reached = 0
        self.fields: dict[str, QuestionField] = {}
        self.backend_label = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 22, 32, 22)
        root.setSpacing(14)

        # Kopfzeile
        head = QHBoxLayout()
        head.setSpacing(12)
        self.back_link = button("←  Übersicht", "ghost")
        self.back_link.clicked.connect(self.back_home)
        head.addWidget(self.back_link)
        self.icon = label("", "H1")
        head.addWidget(self.icon)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.title = label("", "H2")
        self.subtitle = label("", "Faint")
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        head.addLayout(titles, 1)
        self.mode_chip = label("", "Chip")
        head.addWidget(self.mode_chip)
        root.addLayout(head)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        root.addWidget(self.progress)

        # Hauptbereich
        body = QHBoxLayout()
        body.setSpacing(24)
        self.stepper = Stepper()
        self.stepper.step_clicked.connect(self._jump)
        body.addWidget(self.stepper, 0, Qt.AlignTop)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        host = QWidget()
        host_lay = QVBoxLayout(host)
        host_lay.setContentsMargins(0, 0, 8, 0)
        self.card = Card(spacing=20, margins=28)
        self.card.setMaximumWidth(820)
        # Mindesthöhe = Wunschhöhe → ScrollArea scrollt, statt Inhalte zu stauchen
        self.card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        host_lay.addWidget(self.card)
        host_lay.addStretch()
        self.scroll.setWidget(host)
        body.addWidget(self.scroll, 1)
        root.addLayout(body, 1)

        # Fußzeile
        foot = QHBoxLayout()
        self.hint = label("", "Faint")
        foot.addWidget(self.hint)
        foot.addStretch()
        self.back_btn = button("Zurück")
        self.back_btn.clicked.connect(self._back)
        self.next_btn = button("Weiter  →", "primary")
        self.next_btn.setMinimumWidth(150)
        self.next_btn.clicked.connect(self._next)
        foot.addWidget(self.back_btn)
        foot.addWidget(self.next_btn)
        root.addLayout(foot)

    # ------------------------------------------------------------------
    def start(self, wf: Workflow, answers: dict[str, Any] | None = None, back_text: str = "←  Übersicht"):
        self.back_link.setText(back_text)
        self.wf = wf
        self.answers = {**wf.default_answers(), **(answers or {})}
        self.icon.setText(wf.icon)
        self.title.setText(wf.name)
        self.mode_chip.setText(self.backend_label)
        self.mode_chip.setToolTip("So wird der fertige Prompt an Copilot übergeben (Einstellungen)")
        self.index = 0
        # Beim Bearbeiten eines früheren Laufs sind alle Schritte direkt anspringbar
        self.max_reached = len(self._steps()) - 1 if answers else 0
        self._show(self.max_reached if answers else 0)

    def _steps(self) -> list[Step | None]:
        """Sichtbare Schritte + ``None`` als abschließender Überprüfungsschritt."""
        return [*self.wf.visible_steps(self.answers), None]

    # ---- Navigation ----------------------------------------------------
    def _show(self, index: int):
        steps = self._steps()
        self.index = max(0, min(index, len(steps) - 1))
        self.max_reached = max(self.max_reached, self.index)
        step = steps[self.index]

        self.stepper.set_steps([s.title if s else REVIEW_TITLE for s in steps])
        self.stepper.set_current(self.index, self.max_reached)
        self.progress.setMaximum(len(steps) - 1)
        self.progress.setValue(self.index)
        self.subtitle.setText(f"Schritt {self.index + 1} von {len(steps)}")

        clear_layout(self.card.body)
        self.fields = {}
        if step is None:
            self._build_review()
        else:
            self._build_step(step)

        last = step is None
        self.back_btn.setEnabled(self.index > 0)
        self.next_btn.setText(self._launch_text() if last else "Weiter  →")
        self.hint.setText("" if last else "Pflichtfelder sind mit * markiert.")
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(0))

    def _launch_text(self) -> str:
        return "📋  Prompt erstellen" if self.backend_label == "Copilot Chat" else "▶  An Copilot senden"

    def _jump(self, index: int):
        if index > self.index and not self._validate():
            return
        self._collect()
        self._show(index)

    def _back(self):
        self._collect()
        self._show(self.index - 1)

    def _next(self):
        if self._steps()[self.index] is None:
            self._launch()
            return
        if self._validate():
            self._collect()
            self._show(self.index + 1)

    def _launch(self):
        # Gesamtprüfung über alle sichtbaren Fragen
        for i, step in enumerate(self._steps()[:-1]):
            for q in step.questions:
                if q.is_visible(self.answers) and q.validate_answer(self.answers.get(q.id)):
                    self._show(i)
                    self._validate()
                    return
        self.launch.emit(self.wf, self.wf.visible_answers(self.answers))

    # ---- Formular ------------------------------------------------------
    def _build_step(self, step: Step):
        body = self.card.body
        body.addWidget(label(step.title, "H1"))
        if step.description:
            body.addWidget(label(step.description.strip(), "Muted", wrap=True))
        body.addWidget(hline())
        for q in step.questions:
            field = create_field(q)
            if q.id in self.answers:
                field.set_value(self.answers[q.id])
            field.changed.connect(lambda qid=q.id: self._on_changed(qid))
            self.fields[q.id] = field
            body.addWidget(field)
        body.addStretch()
        self._update_visibility()
        first = next(iter(self.fields.values()), None)
        if first:
            QTimer.singleShot(0, first.input.setFocus)

    def _on_changed(self, qid: str):
        self.answers[qid] = self.fields[qid].value()
        self.fields[qid].set_error(None)
        self._update_visibility()
        # Schritt-Liste kann sich durch Bedingungen ändern
        titles = [s.title if s else REVIEW_TITLE for s in self._steps()]
        if titles != [i.title.text() for i in self.stepper.items]:
            self.stepper.set_steps(titles)
            self.stepper.set_current(self.index, self.max_reached)

    def _update_visibility(self):
        for qid, field in self.fields.items():
            field.setVisible(field.q.is_visible(self.answers))

    def _collect(self):
        for qid, field in self.fields.items():
            self.answers[qid] = field.value()

    def _validate(self) -> bool:
        self._collect()
        first_error = None
        for field in self.fields.values():
            if not field.isVisible():
                field.set_error(None)
                continue
            err = field.q.validate_answer(field.value())
            field.set_error(err)
            if err and first_error is None:
                first_error = field
        if first_error:
            self.scroll.ensureWidgetVisible(first_error, 0, 60)
            first_error.input.setFocus()
        return first_error is None

    # ---- Überprüfung ---------------------------------------------------
    def _build_review(self):
        body = self.card.body
        body.addWidget(label("Alles bereit?", "H1"))
        body.addWidget(label(
            "Prüfe deine Angaben. Danach erstellt PAGER den fertigen Prompt – Inhalte hochgeladener "
            "Dateien werden automatisch eingebettet – und übergibt ihn an Copilot.", "Muted", wrap=True))

        meta = QHBoxLayout()
        meta.addWidget(label(f"Übergabe: {self.backend_label}", "Chip"))
        meta.addStretch()
        body.addLayout(meta)

        for i, step in enumerate(self._steps()[:-1]):
            body.addWidget(hline())
            head = QHBoxLayout()
            head.addWidget(label(step.title, "H3"), 1)
            edit = button("Bearbeiten", "link")
            edit.clicked.connect(lambda _=False, i=i: self._show(i))
            head.addWidget(edit)
            body.addLayout(head)

            grid = QGridLayout()
            grid.setHorizontalSpacing(24)
            grid.setVerticalSpacing(6)
            grid.setColumnStretch(1, 1)
            row = 0
            for q in step.questions:
                if not q.is_visible(self.answers):
                    continue
                grid.addWidget(label(q.label, "Muted", wrap=True), row, 0, Qt.AlignTop)
                val = label(q.display(self.answers.get(q.id)), wrap=True)
                val.setTextInteractionFlags(Qt.TextSelectableByMouse)
                grid.addWidget(val, row, 1)
                row += 1
            grid.setColumnMinimumWidth(0, 240)
            body.addLayout(grid)

        body.addWidget(hline())
        toggle = button("▸  Prompt-Vorschau anzeigen", "link")
        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        preview.setMinimumHeight(260)
        preview.setStyleSheet("font-family: Consolas, Menlo, monospace; font-size: 9pt;")
        preview.hide()

        def flip():
            if preview.isHidden():
                try:
                    preview.setPlainText(render_prompt(self.wf, self.answers))
                except Exception as exc:  # noqa: BLE001 – Vorlagenfehler anzeigen statt abstürzen
                    preview.setPlainText(f"Fehler in der Prompt-Vorlage:\n{exc}")
            preview.setVisible(preview.isHidden())
            toggle.setText(("▾  Prompt-Vorschau ausblenden" if preview.isVisible()
                            else "▸  Prompt-Vorschau anzeigen"))

        toggle.clicked.connect(flip)
        body.addWidget(toggle, 0, Qt.AlignLeft)
        body.addWidget(preview)
        body.addStretch()
