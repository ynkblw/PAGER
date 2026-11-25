"""Hauptfenster: Seitenleiste + Seitenstapel."""

from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QMainWindow, QMessageBox, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from pager import __version__
from pager.config import Settings, user_workflow_dir
from pager.core import authoring
from pager.core.workflow import Workflow, WorkflowLoadError, load_workflow, load_workflows
from pager.core.prompting import prepare_prompt
from pager.core.workspace import RunWorkspace
from pager.ui.pages.editor import WorkflowEditorPage
from pager.ui.pages.handoff import HandoffPage
from pager.ui.pages.history import HistoryPage
from pager.ui.pages.home import HomePage
from pager.ui.pages.run import RunPage
from pager.ui.pages.settings import SettingsPage
from pager.ui.pages.wizard import WizardPage
from pager.ui.theme import app_icon
from pager.ui.widgets.common import label


class Sidebar(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("Sidebar")
        self.setFixedWidth(236)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 26, 16, 18)
        lay.setSpacing(4)

        lay.addWidget(label("PAGER", "Logo"))
        lay.addWidget(label("Geführte Prompts für Copilot", "LogoSub"))
        lay.addSpacing(26)

        self.group = QButtonGroup(self)
        self.buttons: dict[str, QPushButton] = {}
        for key, text in (("home", "🧭   Walkthroughs"), ("history", "🕘   Verlauf"), ("settings", "⚙️   Einstellungen")):
            btn = QPushButton(text)
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            self.group.addButton(btn)
            self.buttons[key] = btn
            lay.addWidget(btn)
        lay.addStretch()

        box = QFrame()
        box.setObjectName("StatusBox")
        b = QVBoxLayout(box)
        b.setContentsMargins(12, 10, 12, 10)
        b.setSpacing(2)
        self.status_title = label("", "StatusTitle")
        self.status_text = label("", "StatusText", wrap=True)
        b.addWidget(self.status_title)
        b.addWidget(self.status_text)
        lay.addWidget(box)
        lay.addSpacing(8)
        lay.addWidget(label(f"Version {__version__}", "LogoSub"))

    def set_status(self, settings: Settings):
        if settings.backend == "chat":
            self.status_title.setText("● Copilot Chat")
            host = settings.resolved_chat_url.split("//")[-1].split("/")[0]
            self.status_text.setText(f"Übergabe per Zwischenablage · {host}")
            self.status_title.setStyleSheet("color: #7CF2C4;")
        elif settings.backend == "demo":
            self.status_title.setText("● Demo-Modus")
            self.status_text.setText("Antworten werden simuliert.")
            self.status_title.setStyleSheet("color: #F6C453;")
        else:
            self.status_title.setText("● Copilot CLI")
            self.status_text.setText(settings.ghe_host or "github.com")
            self.status_title.setStyleSheet("color: #7CF2C4;")


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.workflows: dict[str, Workflow] = {}
        self._editor_origin: tuple[QWidget, str] | None = None
        self._wizard_from_editor = False
        self.setWindowTitle("PAGER")
        self.setWindowIcon(app_icon())
        self.resize(1320, 860)
        self.setMinimumSize(1180, 720)
        user_workflow_dir().mkdir(parents=True, exist_ok=True)

        central = QWidget()
        self.setCentralWidget(central)
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self.sidebar = Sidebar()
        row.addWidget(self.sidebar)
        self.stack = QStackedWidget()
        row.addWidget(self.stack, 1)

        self.home = HomePage()
        self.wizard = WizardPage()
        self.run = RunPage(settings)
        self.history = HistoryPage(lambda: self.settings.runs_path)
        self.settings_page = SettingsPage(settings)
        self.editor = WorkflowEditorPage(settings)
        self.handoff = HandoffPage(settings)
        for page in (self.home, self.wizard, self.run, self.history, self.settings_page, self.editor,
                     self.handoff):
            self.stack.addWidget(page)

        # Navigation
        nav = self.sidebar.buttons
        nav["home"].clicked.connect(self.show_home)
        nav["history"].clicked.connect(self.show_history)
        nav["settings"].clicked.connect(self.show_settings)

        # Ablauf
        self.home.start_workflow.connect(self.start_workflow)
        self.home.edit_workflow.connect(self.edit_workflow)
        self.home.create_requested.connect(self.new_workflow)
        self.home.import_requested.connect(self.import_workflow)
        self.home.reload_requested.connect(self.reload_workflows)
        self.wizard.back_home.connect(self._wizard_back)
        self.wizard.launch.connect(self.launch)
        self.run.new_run.connect(self.show_home)
        self.run.edit_answers.connect(lambda wf, a: self.start_workflow(wf, a))
        self.run.running_changed.connect(self._on_running)
        self.history.open_run.connect(self.open_run)
        self.handoff.finished.connect(self._handoff_finished)
        self.handoff.edit_answers.connect(lambda wf, a: self.start_workflow(wf, a))
        self.handoff.later.connect(self.show_home)
        self.settings_page.saved.connect(self._on_settings_saved)

        # Walkthrough-Verwaltung
        self.settings_page.new_workflow.connect(self.new_workflow)
        self.settings_page.edit_workflow.connect(self.edit_workflow)
        self.settings_page.duplicate_workflow.connect(self.duplicate_workflow)
        self.settings_page.delete_workflow.connect(self.delete_workflow)
        self.settings_page.import_workflow.connect(self.import_workflow)
        self.editor.saved.connect(lambda _: self.reload_workflows())
        self.editor.leave.connect(self._leave_editor)
        self.editor.test_requested.connect(self._test_workflow)

        self._on_settings_saved()
        self.show_home()

    # ---- Navigation ----------------------------------------------------
    def _show(self, page: QWidget, nav_key: str) -> bool:
        current = self.stack.currentWidget()
        if current is self.editor and page is not self.editor and page is not self.wizard:
            if not self.editor.confirm_leave():
                self._sync_nav()
                return False
        self.stack.setCurrentWidget(page)
        self.sidebar.buttons[nav_key].setChecked(True)
        self._nav_key = nav_key
        return True

    def _sync_nav(self):
        self.sidebar.buttons[getattr(self, "_nav_key", "home")].setChecked(True)

    def show_home(self):
        self._show(self.home, "home")

    def show_history(self):
        if self._show(self.history, "history"):
            self.history.refresh()

    def show_settings(self):
        self._show(self.settings_page, "settings")

    # ---- Walkthroughs laden / ausführen ---------------------------------
    def reload_workflows(self):
        workflows, errors = load_workflows(self.settings.workflow_paths)
        self.workflows = {w.id: w for w in workflows}
        self.home.set_workflows(workflows, errors)
        self.settings_page.set_workflows(workflows)
        self.editor.set_categories(sorted({w.category for w in workflows}))

    def start_workflow(self, wf: Workflow, answers: dict | None = None):
        self._wizard_from_editor = False
        self.wizard.backend_label = self.settings.backend_label
        self.wizard.start(wf, answers)
        self._show(self.wizard, "home")

    def _wizard_back(self):
        if self._wizard_from_editor:
            self._wizard_from_editor = False
            self.stack.setCurrentWidget(self.editor)
        else:
            self.show_home()

    def launch(self, wf: Workflow, answers: dict):
        self._wizard_from_editor = False
        if self.settings.backend == "chat":
            self._launch_chat(wf, answers)
            return
        self._show(self.run, "home")
        self.run.start(wf, answers)

    def _launch_chat(self, wf: Workflow, answers: dict):
        """Copilot-Chat-Modus: Prompt bauen, Lauf anlegen, Übergabe-Seite zeigen."""
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            prompt, attachments = prepare_prompt(wf, answers)
            ws = RunWorkspace.create(self.settings.runs_path, wf)
            warnings = ws.prepare(wf, answers, prompt, attachments)
            ws.write_meta(
                workflow_id=wf.id, workflow_name=wf.name, icon=wf.icon,
                created=dt.datetime.now().isoformat(timespec="seconds"), status="waiting",
                backend="chat", answers=answers, title=RunPage._run_title(wf, answers))
        except Exception as exc:  # noqa: BLE001 – z. B. Fehler in der Prompt-Vorlage
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Prompt konnte nicht erstellt werden", str(exc))
            return
        QApplication.restoreOverrideCursor()
        self.handoff.start(wf, answers, ws, prompt, attachments, warnings)
        self._show(self.handoff, "home")

    def _handoff_finished(self, ws, wf, meta: dict):
        self.run.show_existing(ws, wf, meta)
        self.run.banner.show_message("success", "Antwort gespeichert – sie ist jetzt auch im Verlauf.")
        self._show(self.run, "home")

    def open_run(self, ws, meta: dict):
        wf = self.workflows.get(meta.get("workflow_id"))
        if meta.get("status") == "waiting":
            self.handoff.resume(ws, wf, meta)
            self._show(self.handoff, "history")
            return
        self.run.show_existing(ws, wf, meta)
        self._show(self.run, "history")

    # ---- Walkthrough-Editor ------------------------------------------------
    def _open_editor(self):
        origin = self.stack.currentWidget()
        if origin is not self.editor:
            self._editor_origin = (origin, getattr(self, "_nav_key", "home"))
        self._show(self.editor, "settings")

    def _leave_editor(self):
        page, key = self._editor_origin or (self.settings_page, "settings")
        if page in (self.wizard, self.run):
            page, key = self.settings_page, "settings"
        self._show(page, key)

    def new_workflow(self):
        if not self.editor.confirm_leave():
            return
        self.editor.open_new()
        self._open_editor()

    def edit_workflow(self, wf: Workflow):
        if not wf.source or not self.editor.confirm_leave():
            return
        try:
            self.editor.open_file(wf.source)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Öffnen fehlgeschlagen", str(exc))
            return
        self._open_editor()

    def duplicate_workflow(self, wf: Workflow):
        if not self.editor.confirm_leave():
            return
        draft = authoring.load_draft(wf.source)
        draft["name"] = f"{draft['name']} (Kopie)"
        draft["id"] = authoring.unique_id(f"{draft['id']}-kopie", list(self.workflows))
        self.editor.open_new(draft, target_dir=wf.source.parent)
        self._open_editor()

    def delete_workflow(self, wf: Workflow):
        answer = QMessageBox.question(
            self, "Walkthrough löschen",
            f"„{wf.name}“ endgültig löschen?\n\nDatei: {wf.source}\n\nBisherige Läufe im Verlauf bleiben erhalten.")
        if answer != QMessageBox.Yes:
            return
        try:
            wf.source.unlink()
        except OSError as exc:
            QMessageBox.warning(self, "Löschen fehlgeschlagen", str(exc))
            return
        self.reload_workflows()

    def import_workflow(self):
        path, _ = QFileDialog.getOpenFileName(self, "Walkthrough importieren", "", "Walkthrough (*.yaml *.yml)")
        if not path:
            return
        try:
            wf = load_workflow(Path(path))
        except WorkflowLoadError as exc:
            QMessageBox.warning(self, "Import fehlgeschlagen", f"Die Datei ist kein gültiger Walkthrough:\n\n{exc.message}")
            return
        target = user_workflow_dir() / f"{wf.id}.yaml"
        if target.exists() and QMessageBox.question(
                self, "Überschreiben?", f"Ein Walkthrough mit der ID „{wf.id}“ existiert bereits. Ersetzen?") \
                != QMessageBox.Yes:
            return
        shutil.copyfile(path, target)
        self.reload_workflows()
        QMessageBox.information(self, "Importiert", f"„{wf.name}“ wurde importiert.")

    def _test_workflow(self, wf: Workflow):
        self._wizard_from_editor = True
        self.wizard.backend_label = self.settings.backend_label
        self.wizard.start(wf, back_text="←  Zurück zum Editor")
        self.stack.setCurrentWidget(self.wizard)

    # ---- Sonstiges -------------------------------------------------------
    def _on_running(self, running: bool):
        for btn in self.sidebar.buttons.values():
            btn.setEnabled(not running)

    def _on_settings_saved(self):
        self.sidebar.set_status(self.settings)
        self.reload_workflows()

    def closeEvent(self, event):
        if self.stack.currentWidget() is self.editor and not self.editor.confirm_leave():
            event.ignore()
            return
        if self.run.is_running:
            answer = QMessageBox.question(
                self, "Copilot arbeitet noch", "Ein Lauf ist aktiv. Abbrechen und PAGER beenden?")
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.run.cancel()
            if self.run.thread:
                self.run.thread.quit()
                self.run.thread.wait(5000)
        super().closeEvent(event)
