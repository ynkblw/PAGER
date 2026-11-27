"""Einstellungen: Walkthroughs verwalten, Übergabe (Copilot Chat / KI-Agent über ACP / CLI), Ablage."""

from __future__ import annotations

import shlex
import sys

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit, QListWidget,
    QListWidgetItem, QPlainTextEdit, QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from pager.config import BACKENDS, Settings, builtin_workflow_dir, user_workflow_dir
from pager.core.workflow import Workflow
from pager.copilot import CopilotCliBackend
from pager.ui.widgets.agent_setup import AgentSetupPanel
from pager.ui.widgets.common import Banner, Card, button, label

MODEL_SUGGESTIONS = ["", "auto"]  # weitere Modell-IDs je nach Freigabe in eurer Copilot-Policy


class SettingsPage(QWidget):
    saved = Signal()
    new_workflow = Signal()
    edit_workflow = Signal(object)  # Workflow
    duplicate_workflow = Signal(object)
    delete_workflow = Signal(object)
    import_workflow = Signal()

    def __init__(self, settings: Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.settings = settings
        self._workflows: dict[str, Workflow] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        content = QWidget()
        scroll.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(40, 36, 40, 36)
        root.setSpacing(18)

        root.addWidget(label("Einstellungen", "H1"))
        root.addWidget(label(f"Gespeichert in {Settings.file()}", "Faint"))

        # ---- Walkthroughs -------------------------------------------------
        wfc = Card()
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label("Walkthroughs", "H2"))
        titles.addWidget(label("Eigene Walkthroughs anlegen und pflegen – ganz ohne YAML-Kenntnisse.",
                               "Muted", wrap=True))
        head.addLayout(titles, 1)
        new_btn = button("＋  Neuer Walkthrough", "primary")
        new_btn.clicked.connect(self.new_workflow)
        head.addWidget(new_btn, 0, Qt.AlignTop)
        wfc.body.addLayout(head)
        self.wf_list = QListWidget()
        self.wf_list.setObjectName("WorkflowList")
        self.wf_list.setMinimumHeight(190)
        self.wf_list.itemDoubleClicked.connect(lambda _: self._emit_selected(self.edit_workflow))
        self.wf_list.itemSelectionChanged.connect(self._update_wf_buttons)
        wfc.body.addWidget(self.wf_list)
        self.wf_empty = label("Noch keine Walkthroughs vorhanden. Lege mit „Neuer Walkthrough“ den ersten an "
                              "oder importiere eine YAML-Datei.", "Muted", wrap=True)
        wfc.body.addWidget(self.wf_empty)
        row = QHBoxLayout()
        self.wf_edit = button("✎  Bearbeiten")
        self.wf_dup = button("⧉  Duplizieren")
        self.wf_del = button("Löschen", "danger")
        imp = button("Importieren …")
        folder = button("📂  Ordner öffnen", "ghost")
        self.wf_edit.clicked.connect(lambda: self._emit_selected(self.edit_workflow))
        self.wf_dup.clicked.connect(lambda: self._emit_selected(self.duplicate_workflow))
        self.wf_del.clicked.connect(lambda: self._emit_selected(self.delete_workflow))
        imp.clicked.connect(self.import_workflow)
        folder.clicked.connect(self._open_wf_folder)
        for b in (self.wf_edit, self.wf_dup, imp):
            row.addWidget(b)
        row.addStretch()
        row.addWidget(folder)
        row.addWidget(self.wf_del)
        wfc.body.addLayout(row)
        root.addWidget(wfc)

        # ---- Verbindung ---------------------------------------------------
        conn = Card()
        conn.body.addWidget(label("Verbindung zur KI", "H2"))
        form = self._form(conn)
        self.backend = QComboBox()
        for key, text in BACKENDS.items():
            self.backend.addItem(text, key)
        self.backend.currentIndexChanged.connect(self._update_mode)
        self.mode_hint = label("", "Faint", wrap=True)
        mode_box = QWidget()
        ml = QVBoxLayout(mode_box)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(4)
        ml.addWidget(self.backend)
        ml.addWidget(self.mode_hint)
        mode_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        form.addRow("Übergabe", mode_box)

        self.host = QLineEdit()
        self.host.setPlaceholderText("https://<instanz>.ghe.com")
        self.host.textChanged.connect(self._update_chat_placeholder)
        form.addRow("GHE-Adresse", self._with_hint(
            self.host, "Adresse eurer GitHub-Enterprise-Instanz. Leer = github.com."))

        chat_row = QWidget()
        cr = QHBoxLayout(chat_row)
        cr.setContentsMargins(0, 0, 0, 0)
        self.chat_url = QLineEdit()
        cr.addWidget(self.chat_url, 1)
        open_chat = button("↗ Öffnen", tooltip="Chat-Adresse im Browser öffnen")
        open_chat.clicked.connect(self._open_chat)
        cr.addWidget(open_chat)
        form.addRow("Copilot Chat", self._with_hint(
            chat_row, "Seite, die nach dem Kopieren des Prompts geöffnet wird. Leer = <GHE-Adresse>/copilot."))

        # Copilot CLI – nur sichtbar, wenn ausgewählt
        self.cli_box = QWidget()
        cli = QVBoxLayout(self.cli_box)
        cli.setContentsMargins(0, 8, 0, 0)
        cli.setSpacing(12)
        cli.addWidget(label("Copilot CLI", "H3"))
        cli_form = QFormLayout()
        cli_form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        cli_form.setHorizontalSpacing(24)
        cli_form.setVerticalSpacing(12)
        cli.addLayout(cli_form)
        self.exe = QLineEdit()
        self.exe.setPlaceholderText("leer = automatisch suchen (copilot im PATH)")
        self._add_row(cli_form, "Programm", self._with_browse(self.exe, file=True))
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItems(MODEL_SUGGESTIONS)
        self.model.lineEdit().setPlaceholderText("leer = Copilot-Standard")
        self.model.setMinimumWidth(280)
        self._add_row(cli_form, "Modell", self.model)
        self.extra_args = QLineEdit()
        self._add_row(cli_form, "Parameter", self._with_hint(
            self.extra_args, "Zusätzliche Parameter für copilot, z. B. --deny-tool=shell"))
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 240)
        self.timeout.setSuffix(" min")
        self.timeout.setMaximumWidth(140)
        self._add_row(cli_form, "Zeitlimit", self.timeout)
        row = QHBoxLayout()
        self.test_btn = button("Verbindung testen")
        self.test_btn.clicked.connect(self._test)
        self.login_btn = button("Bei Copilot anmelden …")
        self.login_btn.clicked.connect(self._login)
        row.addWidget(self.test_btn)
        row.addWidget(self.login_btn)
        row.addStretch()
        cli.addLayout(row)
        conn.body.addWidget(self.cli_box)

        # KI-Agent über ACP – nur sichtbar, wenn ausgewählt
        self.agent_panel = AgentSetupPanel(settings, self._apply_connection)
        conn.body.addWidget(self.agent_panel)
        self.conn_banner = Banner()
        conn.body.addWidget(self.conn_banner)
        root.addWidget(conn)

        # ---- Ablage -----------------------------------------------------------
        store = Card()
        store.body.addWidget(label("Ablage", "H2"))
        form = self._form(store)
        self.wf_dirs = QPlainTextEdit()
        self.wf_dirs.setPlaceholderText(r"Ein Ordner pro Zeile, z. B. \\server\freigabe\PAGER")
        self.wf_dirs.setFixedHeight(80)
        form.addRow("Geteilte Ordner", self._with_hint(
            self.wf_dirs, "Zusätzliche Ordner mit Walkthroughs (*.yaml), z. B. ein Netzlaufwerk des Teams."))
        self.runs_dir = QLineEdit()
        self.runs_dir.setPlaceholderText(str(Settings().runs_path))
        form.addRow("Verlauf", self._with_hint(
            self._with_browse(self.runs_dir), "Ordner für Prompts, Unterlagen und Ergebnisse der Läufe."))
        self.show_examples = QCheckBox("Mitgelieferte Beispiel-Walkthroughs anzeigen")
        form.addRow("Beispiele", self._with_hint(
            self.show_examples, f"z. B. „Agent-Direktkontakt (Test)“ · {builtin_workflow_dir()}"))
        root.addWidget(store)

        foot = QHBoxLayout()
        self.save_banner = Banner()
        foot.addWidget(self.save_banner, 1)
        save = button("Speichern", "primary")
        save.setMinimumWidth(140)
        save.clicked.connect(self.save)
        foot.addWidget(save, 0, Qt.AlignTop)
        root.addLayout(foot)
        root.addStretch()

        self.load()

    # ---- Walkthroughs ---------------------------------------------------
    def set_workflows(self, workflows: list[Workflow]):
        self.wf_list.clear()
        for wf in workflows:
            n = len(wf.questions)
            extra = f"  ·  💬 {len(wf.agent_steps)} Agent-Fenster" if wf.has_agent_steps else ""
            builtin = wf.source and wf.source.parent.resolve() == builtin_workflow_dir().resolve()
            item = QListWidgetItem(f"{wf.icon}   {wf.name}      ·  {wf.category}  ·  {n} Fragen{extra}"
                                   + ("  ·  Beispiel" if builtin else ""))
            item.setData(Qt.UserRole, wf.id)
            item.setToolTip(str(wf.source))
            self.wf_list.addItem(item)
        self._workflows = {wf.id: wf for wf in workflows}
        self.wf_list.setVisible(bool(workflows))
        self.wf_empty.setVisible(not workflows)
        self._update_wf_buttons()

    def _selected_workflow(self) -> Workflow | None:
        item = self.wf_list.currentItem()
        return self._workflows.get(item.data(Qt.UserRole)) if item and item.isSelected() else None

    def _emit_selected(self, signal):
        if wf := self._selected_workflow():
            signal.emit(wf)

    def _update_wf_buttons(self):
        has = self._selected_workflow() is not None
        for b in (self.wf_edit, self.wf_dup, self.wf_del):
            b.setEnabled(has)

    def _open_wf_folder(self):
        wf = self._selected_workflow()
        folder = wf.source.parent if wf and wf.source else user_workflow_dir()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # ---- Helfer --------------------------------------------------------
    @staticmethod
    def _form(card: Card) -> QFormLayout:
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(12)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        card.body.addLayout(form)
        add_row = form.addRow

        def fixed_width_row(text, widget):
            lbl = label(text)
            lbl.setFixedWidth(150)
            add_row(lbl, widget)

        form.addRow = fixed_width_row
        return form

    @staticmethod
    def _with_hint(widget: QWidget, hint: str) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(widget)
        lay.addWidget(label(hint, "Faint", wrap=True))
        return box

    def _with_browse(self, edit: QLineEdit, file: bool = False) -> QWidget:
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(edit, 1)
        btn = button("…", tooltip="Durchsuchen")
        btn.setFixedWidth(44)
        btn.setStyleSheet("padding: 6px 0;")

        def pick():
            if file:
                path, _ = QFileDialog.getOpenFileName(self, "Programm wählen", "",
                                                      "Programme (*.exe *.cmd *.bat);;Alle Dateien (*)")
            else:
                path = QFileDialog.getExistingDirectory(self, "Ordner wählen")
            if path:
                edit.setText(path)

        btn.clicked.connect(pick)
        lay.addWidget(btn)
        return box

    # ---- Laden / Speichern -------------------------------------------
    def load(self):
        s = self.settings
        self.backend.setCurrentIndex(max(0, self.backend.findData(s.backend)))
        self.host.setText(s.ghe_host)
        self.exe.setText(s.copilot_path)
        self.model.setCurrentText(s.model)
        self.chat_url.setText(s.chat_url)
        self.wf_dirs.setPlainText("\n".join(s.workflow_dirs))
        self.extra_args.setText(shlex.join(s.cli_extra_args))
        self.timeout.setValue(s.timeout_minutes)
        self.runs_dir.setText(s.runs_dir)
        self.show_examples.setChecked(s.show_examples)
        self.agent_panel.load()
        self.timeout.setValue(s.timeout_minutes)
        self.conn_banner.show_message("info", "")
        self.save_banner.show_message("info", "")
        self._update_mode()
        self._update_chat_placeholder()

    def _apply(self) -> bool:
        try:
            extra = shlex.split(self.extra_args.text(), posix=sys.platform != "win32")
        except ValueError as exc:
            self.save_banner.show_message("error", f"CLI-Parameter ungültig: {exc}")
            return False
        s = self.settings
        if error := self.agent_panel.apply(s):
            self.save_banner.show_message("error", error)
            return False
        self._apply_connection(s)
        if s.backend == "copilot-cli":
            s.copilot_path = self.exe.text().strip()
        s.model = self.model.currentText().strip()
        s.chat_url = self.chat_url.text().strip()
        s.workflow_dirs = [l.strip() for l in self.wf_dirs.toPlainText().splitlines() if l.strip()]
        s.cli_extra_args = extra
        if s.backend == "copilot-cli":
            s.timeout_minutes = self.timeout.value()
        s.runs_dir = self.runs_dir.text().strip()
        s.show_examples = self.show_examples.isChecked()
        return True

    def _apply_connection(self, s: Settings) -> None:
        """Übergabe-Modus und GHE-Adresse (auch für Prüfungen des Agent-Panels)."""
        s.backend = self.backend.currentData()
        s.ghe_host = self.host.text().strip().rstrip("/")

    def save(self):
        if not self._apply():
            return
        try:
            self.settings.save()
        except OSError as exc:
            self.save_banner.show_message("error", f"Speichern fehlgeschlagen: {exc}")
            return
        self.save_banner.show_message("success", "Einstellungen gespeichert.")
        self.saved.emit()

    # ---- Übergabe ------------------------------------------------------
    MODE_HINTS = {
        "chat": "PAGER kopiert den fertigen Prompt in die Zwischenablage und öffnet Copilot Chat. "
                "Die Antwort fügst du anschließend wieder in PAGER ein. Funktioniert überall, wo der "
                "Copilot Chat verfügbar ist.",
        "acp": "PAGER spricht über das Agent Client Protocol (ACP) direkt mit einem KI-Agenten: heute "
               "Claude Code, später die GitHub Copilot CLI. Antworten erscheinen live; Agent-Fenster in "
               "Walkthroughs zeigen den rohen Output und erlauben direkte Nachrichten.",
        "copilot-cli": "Älterer Einzelaufruf „copilot -p …“ ohne ACP: kein Agent-Fenster, keine Rückfragen. "
                       "Für neue Einrichtungen „KI-Agent über ACP“ verwenden.",
        "demo": "Zum Ausprobieren: Antworten und Agent-Fenster werden simuliert, keine KI wird aufgerufen.",
    }

    def _update_mode(self):
        key = self.backend.currentData()
        self.mode_hint.setText(self.MODE_HINTS.get(key, ""))
        self.cli_box.setVisible(key == "copilot-cli")
        self.agent_panel.setVisible(key == "acp")

    def _update_chat_placeholder(self):
        host = self.host.text().strip().rstrip("/") or "https://github.com"
        self.chat_url.setPlaceholderText(f"{host}/copilot")

    def _open_chat(self):
        url = self.chat_url.text().strip() or self.chat_url.placeholderText()
        QDesktopServices.openUrl(QUrl(url))

    @staticmethod
    def _add_row(form: QFormLayout, text: str, widget: QWidget):
        lbl = label(text)
        lbl.setFixedWidth(150)
        form.addRow(lbl, widget)

    # ---- Aktionen ------------------------------------------------------
    def _test(self):
        if not self._apply():
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            status = CopilotCliBackend(self.settings).check()
        finally:
            QApplication.restoreOverrideCursor()
        self.conn_banner.show_message("success" if status.ok else "error", status.message)

    def _login(self):
        if not self._apply():
            return
        cmd = CopilotCliBackend(self.settings).open_login()
        text = shlex.join(cmd)
        if sys.platform == "win32":
            self.conn_banner.show_message(
                "info", "Anmeldung im Konsolenfenster abschließen (Gerätecode im Browser bestätigen).")
        else:
            QGuiApplication.clipboard().setText(text)
            self.conn_banner.show_message(
                "info", f"Bitte im Terminal ausführen (in Zwischenablage kopiert):\n{text}")
