"""Einstellungen → KI-Agent: grafische Einrichtung von Claude Code / Copilot CLI über ACP."""

from __future__ import annotations

import shlex
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
    QHeaderView, QLineEdit, QSizePolicy, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from pager.agent import profiles as P
from pager.agent.events import PERMISSION_POLICIES
from pager.config import Settings
from pager.ui.widgets.common import Banner, button, clear_layout, label

AGENT_HINTS = {
    "claude-code": "Claude Code wird über den offiziellen ACP-Adapter „claude-agent-acp“ angesprochen. "
                   "Der Adapter verwendet deine installierte Claude Code CLI und deren Anmeldung.",
    "copilot": "Die GitHub Copilot CLI spricht ACP direkt („copilot --acp“). Voraussetzung: Die CLI ist "
               "in eurer Organisation freigegeben und du bist angemeldet.",
    "custom": "Beliebiger Agent, der das Agent Client Protocol über stdin/stdout spricht.",
}
MODEL_SUGGESTIONS = {
    "claude-code": ["", "opus", "sonnet", "haiku"],
    "copilot": ["", "auto"],
    "custom": [""],
}
ICONS = {"ok": "✅", "warn": "⚠️", "error": "⛔", "info": "ℹ️", "pending": "⏳", "idle": "○"}


class _Signals(QObject):
    checks_done = Signal(list)
    test_done = Signal(bool, str)


class CheckRow(QFrame):
    action = Signal(str)

    def __init__(self, check: P.Check):
        super().__init__()
        self.setObjectName("CheckRow")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(12)
        icon = label(ICONS.get(check.state, "•"))
        icon.setFixedWidth(24)
        lay.addWidget(icon, 0, Qt.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        texts.addWidget(label(check.title, "H3"))
        detail = label(check.detail, "Faint", wrap=True)
        detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        texts.addWidget(detail)
        lay.addLayout(texts, 1)
        labels = {"install": "Installieren …", "login": "Anmelden …", "node": "Node.js laden ↗",
                  "docs": "Anleitung ↗"}
        if check.action in labels:
            btn = button(labels[check.action])
            btn.clicked.connect(lambda: self.action.emit(check.action))
            lay.addWidget(btn, 0, Qt.AlignVCenter)


class AgentSetupPanel(QWidget):
    """Wird in der Einstellungsseite angezeigt, wenn „KI-Agent über ACP“ gewählt ist."""

    def __init__(self, settings: Settings, apply_callback, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings
        self._apply_callback = apply_callback  # übernimmt die Formularwerte in ``settings``
        self.signals = _Signals()
        self.signals.checks_done.connect(self._show_checks)
        self.signals.test_done.connect(self._show_test)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(14)
        root.addWidget(label("KI-Agent einrichten", "H3"))

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(12)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.form = form

        self.agent = QComboBox()
        for key, text in P.AGENTS.items():
            self.agent.addItem(text, key)
        self.agent.currentIndexChanged.connect(self._on_agent)
        self.agent_hint = label("", "Faint", wrap=True)
        self._row("Agent", self._stack(self.agent, self.agent_hint))
        root.addLayout(form)

        # ---- Prüfliste --------------------------------------------------------
        box = QFrame()
        box.setObjectName("SetupBox")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(14, 12, 14, 12)
        bl.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(label("Einrichtung prüfen", "H3"), 1)
        self.recheck_btn = button("↻  Erneut prüfen", "ghost")
        self.recheck_btn.clicked.connect(self.run_checks)
        head.addWidget(self.recheck_btn)
        bl.addLayout(head)
        self.checks_host = QVBoxLayout()
        self.checks_host.setSpacing(6)
        bl.addLayout(self.checks_host)
        test_row = QHBoxLayout()
        self.test_btn = button("🔌  Verbindung testen (ACP)", "primary")
        self.test_btn.setToolTip("Startet den Agenten, öffnet eine Sitzung und beendet sie wieder – "
                                 "es wird keine Nachricht an die KI gesendet.")
        self.test_btn.clicked.connect(self.test_connection)
        test_row.addWidget(self.test_btn)
        self.test_state = label("", "Faint", wrap=True)
        test_row.addWidget(self.test_state, 1)
        bl.addLayout(test_row)
        self.test_banner = Banner()
        bl.addWidget(self.test_banner)
        root.addWidget(box)

        # ---- Details ----------------------------------------------------------
        form2 = QFormLayout()
        form2.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form2.setHorizontalSpacing(24)
        form2.setVerticalSpacing(12)
        form2.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.form = form2
        self.claude_path = QLineEdit()
        self.claude_path.setPlaceholderText("leer = automatisch suchen (claude im PATH)")
        self.use_installed = QCheckBox("Diese Claude Code CLI für den Adapter verwenden (empfohlen)")
        self.use_installed.setToolTip("Setzt CLAUDE_CODE_EXECUTABLE. Ohne Haken nutzt der Adapter seine "
                                      "eigene, mitgelieferte Claude-Version.")
        self.claude_row = self._row("Claude Code CLI", self._stack(self._browse(self.claude_path), self.use_installed))
        self.adapter = QLineEdit()
        self.adapter.setPlaceholderText(f"leer = automatisch (claude-agent-acp oder npx -y {P.ADAPTER_PACKAGE})")
        self.adapter_row = self._row("ACP-Adapter", self._hint(
            self.adapter, "Startbefehl des Adapters, z. B. Pfad zu claude-agent-acp(.exe)."))
        self.copilot_path = QLineEdit()
        self.copilot_path.setPlaceholderText("leer = automatisch suchen (copilot im PATH)")
        self.copilot_row = self._row("Copilot CLI", self._hint(
            self._browse(self.copilot_path), "Wird mit „--acp“ gestartet. Anmeldung: copilot login."))
        self.command = QLineEdit()
        self.command.setPlaceholderText("z. B. /pfad/zu/mein-agent --acp")
        self.command_row = self._row("Startbefehl", self.command)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.lineEdit().setPlaceholderText("leer = Standard des Agenten")
        self.model.setMinimumWidth(260)
        self._row("Modell", self._hint(self.model, "Lässt sich im Agent-Fenster auch während der Sitzung wechseln, "
                                                   "falls der Agent Modelle anbietet."))
        self.policy = QComboBox()
        for key, text in PERMISSION_POLICIES.items():
            self.policy.addItem(text, key)
        self.policy.currentIndexChanged.connect(self._on_policy)
        self.policy_hint = label("", "Faint", wrap=True)
        self._row("Freigaben", self._stack(self.policy, self.policy_hint))
        self.args = QLineEdit()
        self.args.setPlaceholderText("optional, z. B. --add-dir=C:\\Projekte")
        self._row("Parameter", self.args)
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 240)
        self.timeout.setSuffix(" min")
        self.timeout.setMaximumWidth(140)
        self._row("Zeitlimit", self._hint(self.timeout, "Höchstdauer des abschließenden Auftrags."))

        self.mcp = QTableWidget(0, 3)
        self.mcp.setObjectName("OptionsTable")
        self.mcp.setHorizontalHeaderLabels(["Name", "Befehl", "Argumente"])
        self.mcp.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.mcp.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.mcp.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.mcp.verticalHeader().hide()
        self.mcp.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.mcp.setFixedHeight(130)
        mcp_box = QWidget()
        ml = QVBoxLayout(mcp_box)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(6)
        ml.addWidget(self.mcp)
        mr = QHBoxLayout()
        add = button("＋ MCP-Server", "ghost")
        add.clicked.connect(lambda: self._add_mcp({"name": "", "command": "", "args": []}))
        rem = button("Entfernen", "ghost")
        rem.clicked.connect(lambda: self.mcp.removeRow(self.mcp.currentRow()) if self.mcp.currentRow() >= 0 else None)
        mr.addWidget(add)
        mr.addWidget(rem)
        mr.addStretch()
        ml.addLayout(mr)
        ml.addWidget(label("Optional: MCP-Server, die PAGER jeder Agent-Sitzung mitgibt (stdio), z. B. später der "
                           "TIA-MCP-Server. Argumente durch Leerzeichen getrennt, Pfade mit Leerzeichen in "
                           "Anführungszeichen.", "Faint", wrap=True))
        self._row("MCP-Server", mcp_box)
        root.addLayout(form2)
        self._checks_running = False

    # ---- Aufbau-Helfer ----------------------------------------------------
    def _row(self, text: str, widget: QWidget) -> QWidget:
        lbl = label(text)
        lbl.setFixedWidth(150)
        self.form.addRow(lbl, widget)
        return widget

    @staticmethod
    def _stack(*widgets: QWidget) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        for w in widgets:
            lay.addWidget(w)
        box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        return box

    def _hint(self, widget: QWidget, text: str) -> QWidget:
        return self._stack(widget, label(text, "Faint", wrap=True))

    def _browse(self, edit: QLineEdit) -> QWidget:
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(edit, 1)
        btn = button("…", tooltip="Durchsuchen")
        btn.setFixedWidth(44)
        btn.setStyleSheet("padding: 6px 0;")

        def pick():
            path, _ = QFileDialog.getOpenFileName(self, "Programm wählen", "",
                                                  "Programme (*.exe *.cmd *.bat);;Alle Dateien (*)")
            if path:
                edit.setText(path)

        btn.clicked.connect(pick)
        lay.addWidget(btn)
        return box

    # ---- Laden / Übernehmen ---------------------------------------------
    def load(self) -> None:
        s = self.settings
        self.agent.blockSignals(True)
        self.agent.setCurrentIndex(max(0, self.agent.findData(s.agent)))
        self.agent.blockSignals(False)
        self.claude_path.setText(s.claude_path)
        self.use_installed.setChecked(s.use_installed_claude)
        self.adapter.setText(s.acp_adapter)
        self.copilot_path.setText(s.copilot_path)
        self.command.setText(s.agent_command)
        self.args.setText(s.agent_args)
        self.policy.setCurrentIndex(max(0, self.policy.findData(s.permission_policy)))
        self.timeout.setValue(s.timeout_minutes)
        self.mcp.setRowCount(0)
        for server in s.mcp_servers:
            self._add_mcp(server)
        self._on_agent(reset_model=False)
        self.model.setCurrentText(s.agent_model)
        self._on_policy()
        self.test_banner.show_message("info", "")
        self.test_state.setText("")

    def apply(self, s: Settings) -> str | None:
        """Übernimmt die Werte. Gibt eine Fehlermeldung zurück oder ``None``."""
        servers = []
        for row in range(self.mcp.rowCount()):
            name, cmd, args = (self._cell(row, c) for c in range(3))
            if not cmd:
                continue
            try:
                arg_list = P.split_command(args)
            except ValueError as exc:
                return f"MCP-Server „{name or cmd}“: Argumente ungültig ({exc})."
            servers.append({"name": name or Path(cmd).stem, "command": cmd, "args": arg_list})
        for text, what in ((self.adapter.text(), "ACP-Adapter"), (self.command.text(), "Startbefehl"),
                           (self.args.text(), "Parameter")):
            try:
                P.split_command(text)
            except ValueError as exc:
                return f"{what} ungültig: {exc}"
        s.agent = self.agent.currentData()
        s.claude_path = self.claude_path.text().strip()
        s.use_installed_claude = self.use_installed.isChecked()
        s.acp_adapter = self.adapter.text().strip()
        s.copilot_path = self.copilot_path.text().strip()
        s.agent_command = self.command.text().strip()
        s.agent_args = self.args.text().strip()
        s.agent_model = self.model.currentText().strip()
        s.permission_policy = self.policy.currentData()
        s.timeout_minutes = self.timeout.value()
        s.mcp_servers = servers
        return None

    def _cell(self, row: int, col: int) -> str:
        item = self.mcp.item(row, col)
        return item.text().strip() if item else ""

    def _add_mcp(self, server: dict) -> None:
        row = self.mcp.rowCount()
        self.mcp.insertRow(row)
        values = [server.get("name", ""), server.get("command", ""), shlex.join(server.get("args") or [])]
        for col, value in enumerate(values):
            self.mcp.setItem(row, col, QTableWidgetItem(value))

    # ---- Reaktionen -------------------------------------------------------
    def _on_agent(self, *_args, reset_model: bool = True) -> None:
        key = self.agent.currentData()
        self.agent_hint.setText(AGENT_HINTS.get(key, ""))
        self.form.setRowVisible(self.claude_row, key == "claude-code")
        self.form.setRowVisible(self.adapter_row, key == "claude-code")
        self.form.setRowVisible(self.copilot_row, key == "copilot")
        self.form.setRowVisible(self.command_row, key == "custom")
        current = self.model.currentText()
        self.model.clear()
        self.model.addItems(MODEL_SUGGESTIONS.get(key, [""]))
        self.model.setCurrentText("" if reset_model else current)
        self.test_banner.show_message("info", "")
        self.test_state.setText("")
        if self.isVisible():
            self.run_checks()

    def _on_policy(self) -> None:
        texts = {
            "ask": "PAGER zeigt jede Werkzeug-Anfrage des Agenten (Datei schreiben, Befehl ausführen …) zur Freigabe an.",
            "read": "Lesen, Suchen und Nachdenken laufen ohne Rückfrage, alles andere fragt PAGER nach.",
            "deny": "Der Agent darf keine Werkzeuge benutzen und antwortet nur mit Text.",
            "allow": "Achtung: Der Agent darf ohne Rückfrage Dateien ändern und Befehle ausführen.",
        }
        self.policy_hint.setText(texts.get(self.policy.currentData(), ""))

    def showEvent(self, event):
        super().showEvent(event)
        if not self._checks_running and not self.checks_host.count():
            self.run_checks()

    # ---- Prüfen / Testen --------------------------------------------------
    def _snapshot(self) -> Settings | None:
        from dataclasses import replace
        snap = replace(self.settings)
        if error := self.apply(snap):
            self.test_banner.show_message("error", error)
            return None
        self._apply_callback(snap)
        return snap

    def run_checks(self) -> None:
        snap = self._snapshot()
        if snap is None or self._checks_running:
            return
        self._checks_running = True
        clear_layout(self.checks_host)
        self.checks_host.addWidget(label(f"{ICONS['pending']}  Prüfe …", "Muted"))
        self.recheck_btn.setEnabled(False)
        threading.Thread(target=lambda: self.signals.checks_done.emit(P.run_checks(snap)), daemon=True).start()

    def _show_checks(self, checks: list) -> None:
        self._checks_running = False
        self.recheck_btn.setEnabled(True)
        clear_layout(self.checks_host)
        for check in checks:
            row = CheckRow(check)
            row.action.connect(self._action)
            self.checks_host.addWidget(row)

    def test_connection(self) -> None:
        snap = self._snapshot()
        if snap is None:
            return
        self.test_btn.setEnabled(False)
        self.test_state.setText("⏳  Agent wird gestartet … (beim ersten Start über npx kann das dauern)")
        self.test_banner.show_message("info", "")
        cwd = Path(tempfile.mkdtemp(prefix="pager-test-"))
        threading.Thread(
            target=lambda: self.signals.test_done.emit(*P.test_connection(snap, cwd, timeout=240)),
            daemon=True).start()

    def _show_test(self, ok: bool, message: str) -> None:
        self.test_btn.setEnabled(True)
        self.test_state.setText("")
        self.test_banner.show_message("success" if ok else "error", message)

    def _action(self, action: str) -> None:
        snap = self._snapshot() or self.settings
        if action == "docs":
            url = P.COPILOT_INSTALL_URL if snap.agent == "copilot" else P.CLAUDE_INSTALL_URL
            QDesktopServices.openUrl(QUrl(url))
            return
        if action == "node":
            QDesktopServices.openUrl(QUrl(P.NODE_URL))
            return
        cmd = P.login_command(snap) if action == "login" else P.install_command(snap)
        text = shlex.join(cmd)
        if P.open_in_terminal(cmd):
            self.test_banner.show_message(
                "info", f"Im Terminalfenster abschließen, danach „Erneut prüfen“.\n{text}")
        else:
            QGuiApplication.clipboard().setText(text)
            self.test_banner.show_message("info", f"Bitte im Terminal ausführen (in Zwischenablage kopiert):\n{text}")
