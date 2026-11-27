"""Agent-Fenster: roher Output eines KI-Agenten (ACP) mit Eingabezeile.

Zeigt alles, was der Agent liefert, so wie es ankommt: Antworttext, Gedanken, Werkzeugaufrufe,
Freigabe-Anfragen und Rückfragen (Auswahl per Pfeiltasten). Auf Wunsch zusätzlich die
Protokoll-Rohdaten (JSON-RPC-Zeilen). Der Nutzer kann dem Agenten jederzeit schreiben. Welche
Bestandteile sichtbar sind, legt der Walkthrough fest (``AgentStepConfig``).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QSizePolicy, QSplitter, QVBoxLayout, QWidget,
)

from pager.agent.events import (
    AgentEvent, AutoDecision, Closed, ConfigChanged, Connected, Failed, ModeChanged, PermissionRequest,
    Plan, QuestionAnswered, QuestionRequest, Raw, Status, TextChunk, ToolCall, TurnEnded, TurnFailed,
    TurnStarted, question_fields,
)
from pager.agent.session import AgentSession, Transcript
from pager.core.workflow import AgentStepConfig
from pager.ui.widgets.common import button, clear_layout, label, set_prop

STOP_REASONS = {
    "end_turn": "", "cancelled": "abgebrochen", "max_tokens": "Antwortlänge erreicht",
    "max_turn_requests": "maximale Anzahl Schritte erreicht", "refusal": "vom Agenten abgelehnt",
}
TOOL_STATUS = {"pending": "wartet", "in_progress": "läuft", "completed": "fertig", "failed": "fehlgeschlagen"}

COLORS = {
    "text": "#E3E8F2", "user": "#8FB3FF", "agent_head": "#7CF2C4", "thought": "#8C94A5",
    "tool": "#F6C453", "system": "#6B7488", "error": "#FF8A8A", "ok": "#7CF2C4", "question": "#C9A7FF",
}


def _mono(size_factor: float = 0.95) -> QFont:
    font = QFont("Consolas")
    font.setStyleHint(QFont.Monospace)
    font.setFamilies(["Cascadia Mono", "Consolas", "Menlo", "SF Mono", "monospace"])
    font.setPointSizeF(max(8.0, QFont().pointSizeF() * size_factor))
    return font


class AgentBridge(QObject):
    """Reicht Ereignisse aus dem Sitzungs-Thread in den UI-Thread (queued Signal)."""

    event = Signal(object)


class PermissionPanel(QFrame):
    """Inline-Abfrage einer Werkzeug-Freigabe mit den Optionen, die der Agent anbietet."""

    answered = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("PermissionPanel")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)  # nie stauchen
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        self.title = label("", "H3", wrap=True)
        self.detail = label("", "Muted", wrap=True)
        self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.title)
        lay.addWidget(self.detail)
        self.buttons = QHBoxLayout()
        lay.addLayout(self.buttons)
        self.request: PermissionRequest | None = None
        self.queue: list[PermissionRequest] = []
        self.hide()

    def add(self, req: PermissionRequest) -> None:
        self.queue.append(req)
        if self.request is None:
            self._next()

    def _next(self) -> None:
        self.request = None
        while self.queue:
            req = self.queue.pop(0)
            if req.answered is False:
                self.request = req
                break
        if self.request is None:
            self.hide()
            return
        req = self.request
        kind = f" ({req.kind})" if req.kind else ""
        self.title.setText(f"🔐  Der Agent bittet um Freigabe: {req.title}{kind}")
        self.detail.setText(req.detail or "")
        self.detail.setVisible(bool(req.detail))
        clear_layout(self.buttons)
        for opt in req.options or [{"optionId": None, "name": "Abbrechen", "kind": "reject_once"}]:
            allow = str(opt.get("kind", "")).startswith("allow")
            text = opt.get("name") or opt.get("optionId") or "?"
            btn = button(("✓  " if allow else "✕  ") + text, "primary" if opt.get("kind") == "allow_once" else
                         ("danger" if not allow else None))
            btn.clicked.connect(lambda _=False, oid=opt.get("optionId"): self._answer(oid))
            self.buttons.addWidget(btn)
        self.buttons.addStretch()
        self.show()

    def _answer(self, option_id: str | None) -> None:
        if self.request:
            self.request.respond(option_id)
        self.answered.emit()
        self._next()

    def cancel_all(self) -> None:
        for req in [self.request, *self.queue]:
            if req is not None:
                req.respond(None)
        self.queue.clear()
        self._next()


class _KeyFilter(QObject):
    """Enter in einer Auswahlliste beantwortet die Rückfrage."""

    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.callback()
            return True
        return False


class QuestionPanel(QFrame):
    """Rückfrage des Agenten (ACP-Formular): Auswahl per Pfeiltasten, Enter antwortet.

    Claude Code schickt zu jeder Auswahlfrage ein optionales Freitextfeld („Other“), das hier als
    „Eigene Antwort“ direkt unter der Auswahl erscheint.
    """

    answered = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("QuestionPanel")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)  # nie stauchen
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        self.title = label("", "H3", wrap=True)
        lay.addWidget(self.title)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        lay.addLayout(self.body)
        row = QHBoxLayout()
        self.ok_btn = button("Antworten  ⏎", "primary")
        self.ok_btn.clicked.connect(self._accept)
        skip = button("Überspringen", tooltip="Ohne Antwort weiter – der Agent erfährt, dass du übersprungen hast")
        skip.clicked.connect(lambda: self._finish("decline"))
        cancel = button("Abbrechen", "danger", "Rückfrage und laufende Antwort abbrechen")
        cancel.clicked.connect(lambda: self._finish("cancel"))
        row.addWidget(self.ok_btn)
        row.addWidget(skip)
        row.addStretch()
        row.addWidget(cancel)
        lay.addLayout(row)
        lay.addWidget(label("↑ ↓ auswählen · Leertaste ankreuzen (Mehrfachauswahl) · Enter antworten", "Faint",
                            wrap=True))
        self._keys = _KeyFilter(self._accept)
        self.request: QuestionRequest | None = None
        self.queue: list[QuestionRequest] = []
        self.inputs: dict[str, tuple[str, QWidget]] = {}
        self.hide()

    def add(self, req: QuestionRequest) -> None:
        self.queue.append(req)
        if self.request is None:
            self._next()

    def _next(self) -> None:
        self.request = None
        while self.queue:
            req = self.queue.pop(0)
            if not req.answered:
                self.request = req
                break
        clear_layout(self.body)
        self.inputs = {}
        if self.request is None:
            self.hide()
            return
        self.title.setText(f"❓  {self.request.message}")
        first = None
        fields = question_fields(self.request.schema)
        for f in fields:
            if f["custom_for"]:
                continue  # wird unter der zugehörigen Auswahl angezeigt
            widget = self._field(f)
            first = first or widget
            for extra in fields:
                if extra["custom_for"] == f["key"]:
                    edit = QLineEdit()
                    edit.setPlaceholderText("Eigene Antwort oder Anmerkung (optional)")
                    edit.installEventFilter(self._keys)
                    self.body.addWidget(edit)
                    self.inputs[extra["key"]] = ("text", edit)
        self.show()
        if first is not None:
            first.setFocus()

    def _field(self, f: dict) -> QWidget:
        caption = f["title"] if f["title"] not in ("", "Other") else ""
        if f["description"] and f["description"] != self.request.message:
            caption = f"{caption}: {f['description']}" if caption else f["description"]
        if caption:
            self.body.addWidget(label(caption, "Muted", wrap=True))
        kind = f["kind"]
        if kind in ("choice", "multi"):
            lst = QListWidget()
            lst.setObjectName("QuestionList")
            for value, title, desc in f["options"]:
                item = QListWidgetItem(f"{title}   —   {desc}" if desc else title)
                item.setData(Qt.UserRole, value)
                item.setSizeHint(QSize(0, 32))
                if kind == "multi":
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(Qt.Checked if f["default"] and value in f["default"] else Qt.Unchecked)
                lst.addItem(item)
            lst.setSelectionMode(QAbstractItemView.SingleSelection)
            lst.setCurrentRow(0)
            lst.setFixedHeight(min(8, max(1, lst.count())) * 32 + 12)
            lst.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            lst.installEventFilter(self._keys)
            if kind == "choice":
                lst.itemDoubleClicked.connect(lambda _item: self._accept())
            else:
                lst.itemDoubleClicked.connect(
                    lambda it: it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked))
            self.body.addWidget(lst)
            self.inputs[f["key"]] = (kind, lst)
            return lst
        if kind == "bool":
            box = QCheckBox(f["title"] or f["key"])
            box.setChecked(bool(f["default"]))
            self.body.addWidget(box)
            self.inputs[f["key"]] = (kind, box)
            return box
        edit = QLineEdit()
        if f["default"] is not None:
            edit.setText(str(f["default"]))
        edit.installEventFilter(self._keys)
        self.body.addWidget(edit)
        self.inputs[f["key"]] = (kind, edit)
        return edit

    def content(self) -> dict:
        result: dict = {}
        for key, (kind, w) in self.inputs.items():
            if kind == "choice":
                item = w.currentItem()
                if item is not None:
                    result[key] = item.data(Qt.UserRole)
            elif kind == "multi":
                checked = [w.item(i).data(Qt.UserRole) for i in range(w.count())
                           if w.item(i).checkState() == Qt.Checked]
                if not checked and w.currentItem() is not None:
                    checked = [w.currentItem().data(Qt.UserRole)]  # nur markiert, nicht angekreuzt
                result[key] = checked
            elif kind == "bool":
                result[key] = w.isChecked()
            elif text := w.text().strip():
                if kind in ("number", "integer"):
                    try:
                        num = float(text.replace(",", "."))
                        result[key] = int(num) if kind == "integer" else num
                    except ValueError:
                        result[key] = text
                else:
                    result[key] = text
        return result

    def _accept(self) -> None:
        if self.request:
            self.request.accept(self.content())
        self._done()

    def _finish(self, action: str) -> None:
        if self.request:
            self.request.respond(action)
        self._done()

    def _done(self) -> None:
        self.answered.emit()
        self._next()

    def cancel_all(self) -> None:
        for req in [self.request, *self.queue]:
            if req is not None:
                req.cancel()
        self.queue.clear()
        self._next()


class _SendFilter(QObject):
    """Strg+Enter (bzw. ⌘+Enter) im Eingabefeld sendet."""

    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Enter) \
                and event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
            self.callback()
            return True
        return False


class AgentConsole(QWidget):
    reply_received = Signal()
    busy_changed = Signal(bool)
    attention = Signal(QWidget)  # Rückfrage/Freigabe erschienen → ins Bild scrollen
    reconnect_requested = Signal()
    login_requested = Signal()

    def __init__(self, config: AgentStepConfig | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("AgentConsole")
        self.cfg = config or AgentStepConfig()
        cfg = self.cfg
        self.session: AgentSession | None = None
        self.transcript = Transcript()
        self.start_prompt = ""
        self.auto_start = True
        self._start_sent = False
        self._unsubscribe = None
        self._role: str | None = None  # Rolle des zuletzt geschriebenen Abschnitts
        self._tools: dict[str, str] = {}
        self.bridge = AgentBridge()
        self.bridge.event.connect(self._on_event)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # Kopfzeile: Status links, Aktionen rechts
        head = QHBoxLayout()
        head.setSpacing(8)
        self.dot = label("●", "AgentDot")
        set_prop(self.dot, "state", "idle")
        head.addWidget(self.dot)
        self.status = label("Nicht verbunden", "H3")
        head.addWidget(self.status)
        self.agent_chip = label("", "ChipNeutral")
        self.agent_chip.hide()
        head.addWidget(self.agent_chip)
        head.addStretch()
        self.stop_btn = button("■  Abbrechen", "danger", "Laufende Antwort abbrechen")
        self.stop_btn.clicked.connect(self.cancel)
        self.stop_btn.hide()
        head.addWidget(self.stop_btn)
        self.reconnect_btn = button("↻  Neu verbinden")
        self.reconnect_btn.clicked.connect(self.reconnect_requested)
        self.reconnect_btn.hide()
        head.addWidget(self.reconnect_btn)
        self.login_btn = button("Anmelden …")
        self.login_btn.clicked.connect(self.login_requested)
        self.login_btn.hide()
        head.addWidget(self.login_btn)
        root.addLayout(head)

        # Werkzeugleiste: Modus/Modell (falls vom Agenten angeboten), Rohdaten
        tools = QHBoxLayout()
        tools.setSpacing(8)
        self.mode_label = label("Modus", "Faint")
        tools.addWidget(self.mode_label)
        self.mode = QComboBox()
        self.mode.setToolTip("Modus des Agenten (vom Agenten angeboten)")
        self.mode.activated.connect(self._on_mode)
        tools.addWidget(self.mode)
        self.model_label = label("Modell", "Faint")
        tools.addWidget(self.model_label)
        self.model = QComboBox()
        self.model.setToolTip("Modell (vom Agenten angeboten)")
        self.model.activated.connect(self._on_model)
        tools.addWidget(self.model)
        for combo in (self.mode, self.model):
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(12)
        for w in (self.mode_label, self.mode, self.model_label, self.model):
            w.hide()
        tools.addStretch()
        self.raw_btn = button("{ }  Rohdaten", "ghost", "ACP-Protokoll (JSON-RPC) ein-/ausblenden")
        self.raw_btn.setCheckable(True)
        self.raw_btn.toggled.connect(self._toggle_raw)
        self.raw_btn.setVisible(cfg.raw_toggle)
        tools.addWidget(self.raw_btn)
        self.tools_row = QWidget()
        self.tools_row.setLayout(tools)
        tools.setContentsMargins(0, 0, 0, 0)
        self.tools_row.setVisible(cfg.raw_toggle or cfg.show_controls)
        root.addWidget(self.tools_row)
        for w in (self.dot, self.status, self.agent_chip):
            w.setVisible(cfg.show_status and w is not self.agent_chip)

        # Ausgabe + Rohdaten
        self.split = QSplitter(Qt.Vertical)
        self.split.setChildrenCollapsible(False)
        self.output = QPlainTextEdit()
        self.output.setObjectName("AgentOutput")
        self.output.setReadOnly(True)
        self.output.setFont(_mono())
        self.output.setMaximumBlockCount(20000)
        self.output.setMinimumHeight(220)
        self.output.setPlaceholderText("Hier erscheint der rohe Output des Agenten …")
        self.split.addWidget(self.output)
        self.raw = QPlainTextEdit()
        self.raw.setObjectName("AgentRaw")
        self.raw.setReadOnly(True)
        self.raw.setFont(_mono(0.85))
        self.raw.setMaximumBlockCount(5000)
        self.raw.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.raw.setPlaceholderText("ACP-Rohdaten: → an den Agenten · ← vom Agenten · ! Fehlerausgabe")
        self.raw.hide()
        self.split.addWidget(self.raw)
        self.split.setSizes([420, 200])
        root.addWidget(self.split, 1)

        self.permission = PermissionPanel()
        root.addWidget(self.permission)
        self.question = QuestionPanel()
        self.question.answered.connect(lambda: self.input.setFocus())
        root.addWidget(self.question)

        # Eingabe
        row = QHBoxLayout()
        row.setSpacing(10)
        self.input = QPlainTextEdit()
        self.input.setObjectName("AgentInput")
        self.input.setFixedHeight(84)
        self.input.setPlaceholderText(cfg.placeholder or "Nachricht an den Agenten … (Strg+Enter sendet)")
        self._filter = _SendFilter(self.send)
        self.input.installEventFilter(self._filter)
        self.input.textChanged.connect(self._update_buttons)
        row.addWidget(self.input, 1)
        col = QVBoxLayout()
        col.setSpacing(6)
        self.send_btn = button("Senden  ➤", "primary")
        self.send_btn.setMinimumWidth(130)
        self.send_btn.clicked.connect(self.send)
        col.addWidget(self.send_btn)
        self.start_btn = button("Startnachricht", tooltip="Vorbereitete Startnachricht dieses Schritts senden")
        self.start_btn.clicked.connect(self._send_start)
        self.start_btn.hide()
        col.addWidget(self.start_btn)
        col.addStretch()
        row.addLayout(col)
        root.addLayout(row)
        self.hint = label("", "Error", wrap=True)
        self.hint.hide()
        root.addWidget(self.hint)

        self.input.setVisible(cfg.show_input)
        self.send_btn.setVisible(cfg.show_input)
        self._toggle_raw(False)
        self.raw_btn.setChecked(cfg.show_raw)
        self._toggle_raw(cfg.show_raw)
        self._update_buttons()

    # ==================================================================
    # Sitzung verbinden
    def attach(self, session: AgentSession, start_prompt: str = "", auto_start: bool = True) -> None:
        self.detach()
        same = session is self.session
        self.session = session
        self.start_prompt, self.auto_start = start_prompt, auto_start
        self._unsubscribe = session.subscribe(self.bridge.event.emit)
        self.reconnect_btn.hide()
        self.login_btn.hide()
        if session.connected and session.info:
            if not same:  # z. B. zweites Agent-Fenster derselben Sitzung
                self._show_connected(session.info, resumed=True)
            self._maybe_autostart()
        elif not session.started:
            session.start()
        self._update_buttons()

    def detach(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        self.permission.cancel_all()
        self.question.cancel_all()

    @property
    def busy(self) -> bool:
        return bool(self.session and self.session.busy)

    @property
    def ready(self) -> bool:
        return bool(self.session and self.session.ready)

    def result(self) -> dict:
        return self.transcript.as_answer()

    def show_hint(self, text: str) -> None:
        self.hint.setText(text)
        self.hint.setVisible(bool(text))

    def preview(self) -> None:
        """Füllt das Fenster mit Beispielinhalt (Vorschau im Editor, ohne Agent)."""
        model = {"id": "model", "category": "model", "type": "select", "currentValue": "a",
                 "options": [{"value": "a", "name": "Modell A"}, {"value": "b", "name": "Modell B"}]}
        events = [
            Raw("out", '{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1}}'),
            Raw("in", '{"jsonrpc": "2.0", "id": 1, "result": {"agentInfo": {"title": "Claude Agent"}}}'),
            Connected("Claude Code", "(Vorschau)", modes=[{"id": "default", "name": "Standard"},
                                                          {"id": "plan", "name": "Planen"}],
                      current_mode="default", config_options=[model]),
            TurnStarted("Startnachricht dieses Schritts …"),
            TextChunk("Ich lese zuerst die Unterlagen.", "thought"),
            Plan([{"content": "Unterlagen lesen", "status": "completed"},
                  {"content": "Rückfrage stellen", "status": "in_progress"}]),
            ToolCall("t1", "Datei lesen: eingaben/angebot.pdf", "read", "completed"),
            TextChunk("Hallo! Ich habe die Unterlagen gelesen. Welche Variante soll ich prüfen?"),
            TurnEnded("end_turn"),
        ]
        for ev in events:
            self._on_event(ev)
        self._set_status("Bereit", "ready")

    # ==================================================================
    # Aktionen
    def send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text or not self.ready:
            return
        self.input.clear()
        self._prompt(text)

    def _send_start(self) -> None:
        if self.start_prompt and self.ready:
            self._start_sent = True
            self._prompt(self.start_prompt)

    def _prompt(self, text: str) -> None:
        self.show_hint("")
        try:
            self.session.prompt(text)
        except RuntimeError as exc:
            self._write(f"\n[{exc}]\n", "error")

    def cancel(self) -> None:
        self.permission.cancel_all()
        self.question.cancel_all()
        if self.session:
            self.session.cancel()
            self._write("\n[Abbruch angefordert …]\n", "system")

    def _maybe_autostart(self) -> None:
        if self.start_prompt and self.auto_start and not self._start_sent and self.ready:
            self._send_start()

    def _on_mode(self, index: int) -> None:
        if self.session and (mode_id := self.mode.itemData(index)):
            self.session.set_mode(mode_id)

    def _on_model(self, index: int) -> None:
        if self.session and (value := self.model.itemData(index)) is not None:
            self.session.set_config_option(self.model.property("config_id"), value)

    def _toggle_raw(self, on: bool) -> None:
        self.raw.setVisible(on)
        self.raw_btn.setText("{ }  Rohdaten ausblenden" if on else "{ }  Rohdaten anzeigen")

    # ==================================================================
    # Ereignisse (UI-Thread)
    def _on_event(self, ev: AgentEvent) -> None:
        self.transcript.feed(ev)
        if isinstance(ev, Raw):
            prefix = {"out": "→ ", "in": "← ", "stderr": "! "}.get(ev.direction, "  ")
            self.raw.appendPlainText(prefix + ev.text)
            return
        if isinstance(ev, Status):
            self._set_status(ev.text, "connecting")
        elif isinstance(ev, Connected):
            self._show_connected(ev)
            self._maybe_autostart()
        elif isinstance(ev, TurnStarted):
            self._section("user")
            self._write(ev.text.strip() + "\n", "user_text")
            self._set_status("Arbeitet …", "busy")
            self.busy_changed.emit(True)
        elif isinstance(ev, TextChunk):
            if ev.role == "user":
                return  # Wiederholung eigener Nachrichten (z. B. beim Laden) nicht doppelt zeigen
            if ev.role == "thought" and not self.cfg.show_thoughts:
                return
            self._section("thought" if ev.role == "thought" else "agent")
            self._write(ev.text, "thought" if ev.role == "thought" else "text")
        elif isinstance(ev, ToolCall):
            if self.cfg.show_tools:
                self._on_tool(ev)
        elif isinstance(ev, Plan) and self.cfg.show_plan:
            self._section("tool")
            marks = {"completed": "☑", "in_progress": "▸"}
            lines = [f"  {marks.get(e.get('status'), '☐')} {e.get('content', '')}" for e in ev.entries]
            self._write("📋 Plan\n" + "\n".join(lines) + "\n", "tool")
        elif isinstance(ev, PermissionRequest):
            if self.cfg.show_tools:
                self._section("tool")
                self._write(f"🔐 Freigabe angefragt: {ev.title}\n", "tool")
            self.permission.add(ev)
            self.attention.emit(self.permission)
        elif isinstance(ev, QuestionRequest):
            self._section("question")
            self._write(f"❓ Rückfrage: {ev.message}\n", "question")
            self.question.add(ev)
            self.attention.emit(self.question)
        elif isinstance(ev, QuestionAnswered):
            self._section("question")
            self._write(f"   → {ev.summary}\n", "question")
        elif isinstance(ev, AutoDecision) and self.cfg.show_tools:
            self._section("tool")
            self._write(f"{'✓ automatisch erlaubt' if ev.allowed else '✕ automatisch abgelehnt'}: {ev.title}\n",
                        "tool")
        elif isinstance(ev, TurnEnded):
            reason = STOP_REASONS.get(ev.stop_reason, ev.stop_reason)
            self._write(f"\n— Ende der Antwort{f' ({reason})' if reason else ''} —\n", "system")
            self._role = None
            self._turn_done()
            if self.transcript.replies:
                self.reply_received.emit()
        elif isinstance(ev, TurnFailed):
            self._write(f"\n[Fehler: {ev.message}]\n", "error")
            self._role = None
            self._turn_done()
        elif isinstance(ev, ModeChanged):
            idx = self.mode.findData(ev.mode_id)
            if idx >= 0:
                self.mode.setCurrentIndex(idx)
        elif isinstance(ev, ConfigChanged):
            self._fill_model(ev.config_options)
        elif isinstance(ev, Failed):
            self._write(f"\n[{ev.message}]\n", "error")
            self._set_status("Keine Verbindung", "error")
            self.reconnect_btn.show()
            self.login_btn.setVisible(ev.login_required)
            self.permission.cancel_all()
            self.question.cancel_all()
            self.busy_changed.emit(False)
        elif isinstance(ev, Closed):
            if self.status.property("state") != "error":
                self._set_status("Sitzung beendet", "idle")
            self.reconnect_btn.show()
            self.permission.cancel_all()
            self.question.cancel_all()
        self._update_buttons()

    def _turn_done(self) -> None:
        self.permission.cancel_all()
        self.question.cancel_all()
        self._set_status("Bereit", "ready")
        self.busy_changed.emit(False)

    def _on_tool(self, ev: ToolCall) -> None:
        title = ev.title or self._tools.get(ev.tool_call_id) or "Werkzeug"
        self._tools[ev.tool_call_id] = title
        status = TOOL_STATUS.get(ev.status, ev.status)
        if ev.update and not ev.status and not ev.detail:
            return
        self._section("tool")
        kind = f" ({ev.kind})" if ev.kind and not ev.update else ""
        line = f"⚙ {title}{kind}" + (f" [{status}]" if status else "")
        if ev.detail:
            line += f"\n    {ev.detail}"
        self._write(line + "\n", "error" if ev.status == "failed" else "tool")

    def _show_connected(self, info: Connected, resumed: bool = False) -> None:
        name = f"{info.agent_name} {info.agent_version}".strip()
        self.agent_chip.setText(name)
        self.agent_chip.setVisible(self.cfg.show_status)
        self._write(f"— {'Sitzung fortgesetzt' if resumed else 'Verbunden'}: {name} —\n", "system")
        self._set_status("Bereit" if not self.busy else "Arbeitet …",
                         "ready" if not self.busy else "busy")
        self.mode.clear()
        for m in info.modes:
            self.mode.addItem(m.get("name") or m.get("id"), m.get("id"))
        if info.modes:
            self.mode.setCurrentIndex(max(0, self.mode.findData(info.current_mode)))
        show = len(info.modes) > 1 and self.cfg.show_controls
        self.mode.setVisible(show)
        self.mode_label.setVisible(show)
        self._fill_model(info.config_options)

    def _fill_model(self, config_options: list[dict]) -> None:
        opt = next((o for o in config_options if o.get("category") == "model" and o.get("type") == "select"), None)
        if not opt or not opt.get("options"):
            self.model.hide()
            self.model_label.hide()
            return
        self.model.blockSignals(True)
        self.model.clear()
        values = []
        for o in opt["options"]:
            # Optionen können gruppiert sein ({group, options: [...]})
            values.extend(o.get("options", [o]) if isinstance(o, dict) else [])
        for o in values:
            self.model.addItem(str(o.get("name") or o.get("value")), o.get("value"))
        self.model.setCurrentIndex(max(0, self.model.findData(opt.get("currentValue"))))
        self.model.setProperty("config_id", opt.get("id"))
        self.model.blockSignals(False)
        show = self.model.count() > 1 and self.cfg.show_controls
        self.model.setVisible(show)
        self.model_label.setVisible(show)

    # ==================================================================
    # Darstellung
    def _set_status(self, text: str, state: str) -> None:
        self.status.setText(text)
        self.status.setProperty("state", state)
        set_prop(self.dot, "state", state)

    def _update_buttons(self) -> None:
        ready = self.ready
        busy = self.busy
        self.send_btn.setEnabled(ready and bool(self.input.toPlainText().strip()))
        self.stop_btn.setVisible(busy)
        self.start_btn.setVisible(bool(self.start_prompt) and not self._start_sent)
        self.start_btn.setEnabled(ready)
        self.mode.setEnabled(ready)
        self.model.setEnabled(ready)

    def _section(self, role: str) -> None:
        """Beginnt einen neuen Abschnitt, wenn die Rolle wechselt (Du / Agent / Gedanken / Werkzeug)."""
        if role == self._role:
            return
        heads = {"user": ("\n› Du\n", "user"), "agent": ("\n◆ Agent\n", "agent_head")}
        if role in heads:
            text, style = heads[role]
            self._write(text, style)
        elif self._role in ("agent", "thought", "user", "question") and not self.output.toPlainText().endswith("\n"):
            self._write("\n", "text")
        self._role = role

    def _write(self, text: str, style: str) -> None:
        fmt = QTextCharFormat()
        if style == "system" and not self.cfg.show_system:
            return
        color = {"text": "text", "user_text": "text", "user": "user", "agent_head": "agent_head",
                 "thought": "thought", "tool": "tool", "system": "system", "error": "error",
                 "question": "question"}[style]
        fmt.setForeground(QColor(COLORS[color]))
        if style in ("user", "agent_head"):
            fmt.setFontWeight(QFont.Bold)
        if style == "thought":
            fmt.setFontItalic(True)
        bar = self.output.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 20
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text, fmt)
        if at_bottom:
            bar.setValue(bar.maximum())
