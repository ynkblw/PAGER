"""Walkthrough: führt Schritt für Schritt durch die Fragen eines Workflows."""

from __future__ import annotations

import datetime as dt
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QMessageBox, QPlainTextEdit, QProgressBar, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from pager.agent.profiles import (
    AgentSetupError, agent_available, create_session, login_command, open_in_terminal,
)
from pager.agent.session import AgentSession
from pager.config import Settings
from pager.core.prompting import render_agent_prompt, render_prompt
from pager.core.workflow import Step, Workflow, agent_display
from pager.core.workspace import RunWorkspace
from pager.ui.widgets.agent_console import AgentConsole
from pager.ui.widgets.common import Banner, Card, button, clear_layout, hline, label
from pager.ui.widgets.fields import QuestionField, create_field
from pager.ui.widgets.stepper import Stepper

REVIEW_TITLE = "Überprüfen & starten"


class AgentContext:
    """Arbeitsordner und Agent-Sitzung eines Walkthrough-Laufs mit Agent-Fenstern.

    Wird beim ersten Agent-Fenster angelegt. Beim Start des abschließenden Auftrags übernimmt die
    Ausführungsseite Ordner und Sitzung, sodass der Agent das bisherige Gespräch kennt.
    """

    def __init__(self, settings: Settings, wf: Workflow, answers: dict[str, Any]):
        self.settings = settings
        self.wf = wf
        self.ws = RunWorkspace.create(settings.runs_path, wf)
        self.ws.write_meta(workflow_id=wf.id, workflow_name=wf.name, icon=wf.icon,
                           created=dt.datetime.now().isoformat(timespec="seconds"), status="agent",
                           backend=settings.backend, agent=settings.backend_label, title=wf.name)
        self.ws.copy_inputs(wf, answers)
        self.session: AgentSession | None = None
        self.consoles: dict[str, AgentConsole] = {}

    def ensure_session(self) -> AgentSession:
        if self.session is None or self.session.closed:
            self.session = create_session(self.settings, self.ws.root)
        return self.session

    def new_session(self) -> AgentSession:
        if self.session:
            self.session.close()
        self.session = None
        return self.ensure_session()

    @property
    def busy(self) -> bool:
        return bool(self.session and self.session.busy)

    def save_transcripts(self) -> None:
        for step_id, console in self.consoles.items():
            if console.transcript.turns:
                self.ws.write_transcript(step_id, console.transcript.markdown())

    def close(self, abandoned: bool = False) -> None:
        """Beendet die Sitzung. ``abandoned``: Walkthrough wurde ohne Auftrag verlassen."""
        for console in self.consoles.values():
            console.detach()
        self.save_transcripts()
        if abandoned:
            parts = [f"## {s.title}\n\n{self.consoles[s.id].transcript.markdown()}"
                     for s in self.wf.agent_steps
                     if s.id in self.consoles and self.consoles[s.id].transcript.turns]
            if parts:
                result = self.ws.write_result("\n\n".join(parts), "agent_verlauf.md")
                self.ws.write_meta(status="cancelled", result_file=result.name)
            else:
                self.ws.write_meta(status="cancelled")
        if self.session:
            self.session.close()
            self.session = None


class WizardPage(QWidget):
    back_home = Signal()
    launch = Signal(object, dict)  # Workflow, Antworten

    open_settings = Signal()

    def __init__(self, settings: Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.settings = settings
        self.agent_ctx: AgentContext | None = None
        self.wf: Workflow | None = None
        self.answers: dict[str, Any] = {}
        self.index = 0
        self.max_reached = 0
        self.fields: dict[str, QuestionField] = {}
        self.console: AgentConsole | None = None
        self.backend_label = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 22, 32, 22)
        root.setSpacing(14)

        # Kopfzeile
        head = QHBoxLayout()
        head.setSpacing(12)
        self.back_link = button("←  Start", "ghost")
        self.back_link.clicked.connect(self._leave)
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
    def start(self, wf: Workflow, answers: dict[str, Any] | None = None, back_text: str = "←  Start"):
        self.discard_agent_context()
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

        self._release_consoles()
        clear_layout(self.card.body)
        self.fields = {}
        self.console = None
        if step is None:
            self._build_review()
        elif step.is_agent:
            self._build_agent_step(step)
        else:
            self._build_step(step)

        last = step is None
        self.back_btn.setEnabled(self.index > 0)
        self.next_btn.setText(self._launch_text() if last else "Weiter  →")
        self.hint.setText("" if last or (step and step.is_agent) else "Pflichtfelder sind mit * markiert.")
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(0))

    def _launch_text(self) -> str:
        if self.backend_label == "Copilot Chat":
            return "📋  Prompt erstellen"
        return f"▶  An {self.backend_label} senden" if self.settings.backend == "acp" else "▶  An Copilot senden"

    def _jump(self, index: int):
        if self._agent_busy():
            return
        if index > self.index and not self._validate():
            return
        self._collect()
        self._show(index)

    def _back(self):
        if self._agent_busy():
            return
        self._collect()
        self._show(self.index - 1)

    def _leave(self):
        if self.agent_ctx and self.agent_ctx.busy and QMessageBox.question(
                self, "Agent arbeitet noch", "Der Agent antwortet gerade. Abbrechen und den Walkthrough verlassen?") \
                != QMessageBox.Yes:
            return
        self.discard_agent_context()
        self.back_home.emit()

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
        if self._agent_busy():
            return
        if self.agent_ctx:
            self.agent_ctx.save_transcripts()
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
        step = self._current_step()
        if step is not None and step.is_agent and self.console is not None and self.console.transcript.turns:
            self.answers[step.id] = self.console.result()

    def _current_step(self) -> Step | None:
        steps = self._steps()
        return steps[self.index] if 0 <= self.index < len(steps) else None

    def _validate(self) -> bool:
        step = self._current_step()
        if step is not None and step.is_agent:
            return self._validate_agent(step)
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
            if step.is_agent:
                grid.addWidget(label("Agent-Fenster", "Muted"), row, 0, Qt.AlignTop)
                val = label(agent_display(self.answers.get(step.id)), wrap=True)
                val.setTextInteractionFlags(Qt.TextSelectableByMouse)
                grid.addWidget(val, row, 1)
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

    # ---- Agent-Fenster ---------------------------------------------------
    def _build_agent_step(self, step: Step):
        body = self.card.body
        head = QHBoxLayout()
        head.addWidget(label(step.title, "H1"), 1)
        head.addWidget(label("💬 Agent-Fenster", "Chip"), 0, Qt.AlignVCenter)
        body.addLayout(head)
        if step.description:
            body.addWidget(label(step.description.strip(), "Muted", wrap=True))

        if not agent_available(self.settings):
            banner = Banner("warning", (
                "Dieser Schritt braucht eine direkte Verbindung zu einem KI-Agenten. Stelle unter "
                "Einstellungen → Übergabe „KI-Agent über ACP“ (Claude Code / Copilot CLI) oder den "
                "Demo-Modus ein. Du kannst den Schritt auch überspringen."))
            body.addWidget(banner)
            go = button("⚙️  Zu den Einstellungen")
            go.clicked.connect(self.open_settings)
            body.addWidget(go, 0, Qt.AlignLeft)
            body.addStretch()
            return

        try:
            if self.agent_ctx is None:
                self.agent_ctx = AgentContext(self.settings, self.wf, self.wf.visible_answers(self.answers))
            session = self.agent_ctx.ensure_session()
        except (AgentSetupError, OSError) as exc:
            body.addWidget(Banner("error", f"Der Agent kann nicht gestartet werden:\n{exc}"))
            go = button("⚙️  Einrichtung öffnen")
            go.clicked.connect(self.open_settings)
            body.addWidget(go, 0, Qt.AlignLeft)
            body.addStretch()
            return

        console = self.agent_ctx.consoles.get(step.id)
        if console is None:
            console = AgentConsole(step.agent)
            console.reply_received.connect(lambda sid=step.id: self._on_agent_reply(sid))
            console.busy_changed.connect(self._on_agent_busy)
            console.reconnect_requested.connect(lambda sid=step.id: self._reconnect(sid))
            console.login_requested.connect(self._login)
            console.attention.connect(
                lambda w: QTimer.singleShot(50, lambda: self.scroll.ensureWidgetVisible(w, 0, 40)))
            self.agent_ctx.consoles[step.id] = console
        try:
            start = render_agent_prompt(self.wf, step, self.answers)
        except Exception as exc:  # noqa: BLE001 – Vorlagenfehler anzeigen
            start = ""
            body.addWidget(Banner("error", f"Fehler in der Startnachricht: {exc}"))
        console.setMinimumHeight(500)
        body.addWidget(console, 1)
        console.show()
        console.attach(session, start, step.agent.auto_start)
        self.console = console
        where = label(f"Arbeitsordner des Agenten: {self.agent_ctx.ws.root}", "Faint", wrap=True)
        where.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.addWidget(where)
        QTimer.singleShot(0, console.input.setFocus)

    def _release_consoles(self):
        """Agent-Fenster vor dem Neuaufbau der Seite herauslösen (sie bleiben samt Verlauf erhalten)."""
        if not self.agent_ctx:
            return
        for console in self.agent_ctx.consoles.values():
            if console.parent() is not None:
                console.detach()
                self.card.body.removeWidget(console)
                console.setParent(None)
                console.hide()

    def _on_agent_reply(self, step_id: str):
        console = self.agent_ctx.consoles.get(step_id) if self.agent_ctx else None
        if console:
            self.answers[step_id] = console.result()
            console.show_hint("")
            self.agent_ctx.ws.write_transcript(step_id, console.transcript.markdown())

    def _on_agent_busy(self, busy: bool):
        self.next_btn.setEnabled(not busy)
        self.back_btn.setEnabled(not busy and self.index > 0)

    def _agent_busy(self) -> bool:
        if self.agent_ctx and self.agent_ctx.busy:
            if self.console:
                self.console.show_hint("Der Agent antwortet noch. Bitte warten oder „Abbrechen“ wählen.")
            return True
        return False

    def _validate_agent(self, step: Step) -> bool:
        if not agent_available(self.settings) or self.console is None:
            return True  # Schritt ohne Agent überspringbar
        if self._agent_busy():
            return False
        earlier = (self.answers.get(step.id) or {}).get("nachrichten") if isinstance(
            self.answers.get(step.id), dict) else 0  # z. B. „Angaben anpassen“ eines früheren Laufs
        if step.agent.require_reply and not self.console.transcript.replies and not earlier:
            self.console.show_hint("Bitte zuerst eine Antwort des Agenten abwarten – "
                                   "schreibe ihm eine Nachricht oder sende die Startnachricht.")
            return False
        self._collect()
        return True

    def _reconnect(self, step_id: str):
        if not self.agent_ctx:
            return
        try:
            session = self.agent_ctx.new_session()
        except AgentSetupError as exc:
            QMessageBox.warning(self, "Agent nicht verfügbar", str(exc))
            return
        for sid, console in self.agent_ctx.consoles.items():
            if sid == step_id:
                console.attach(session, console.start_prompt, console.auto_start)

    def _login(self):
        cmd = login_command(self.settings)
        if not open_in_terminal(cmd):
            QMessageBox.information(self, "Anmelden", "Bitte im Terminal ausführen:\n\n" + " ".join(cmd))

    def take_agent_context(self) -> AgentContext | None:
        """Übergibt Ordner und Sitzung an die Ausführung (der Assistent gibt sie damit ab)."""
        self._release_consoles()  # vor dem Abgeben aus dem Layout lösen (sonst löscht der Neuaufbau sie)
        ctx, self.agent_ctx = self.agent_ctx, None
        self.console = None
        return ctx

    def discard_agent_context(self):
        if self.agent_ctx:
            self._release_consoles()
            self.agent_ctx.close(abandoned=True)
            self.agent_ctx = None
