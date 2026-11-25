"""Automatische Ausführung (Copilot CLI / Demo): Prompt erstellen, senden, Antwort live anzeigen."""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QMarginsF, QObject, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QPageLayout, QPageSize, QTextDocument
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QMessageBox, QProgressBar, QTextBrowser, QVBoxLayout, QWidget,
)

from pager.config import Settings
from pager.copilot import BackendError, RunCancelled, RunRequest, create_backend
from pager.core.prompting import prepare_prompt
from pager.core.workflow import Workflow
from pager.core.workspace import PROMPT_FILE, RunWorkspace
from pager.ui.markdown_view import CSS as MARKDOWN_CSS
from pager.ui.markdown_view import to_html
from pager.ui.theme import shadow
from pager.ui.widgets.common import Banner, Card, button, label



class RunWorker(QObject):
    status = Signal(str)
    chunk = Signal(str)
    warning = Signal(str)
    done = Signal()
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, settings: Settings, wf: Workflow, answers: dict[str, Any], ws: RunWorkspace):
        super().__init__()
        self.settings, self.wf, self.answers, self.ws = settings, wf, answers, ws
        self.handle = None
        self._cancel_requested = False

    @Slot()
    def run(self):
        try:
            self.status.emit("Unterlagen werden aufbereitet …")
            prompt, attachments = prepare_prompt(self.wf, self.answers)
            for w in self.ws.prepare(self.wf, self.answers, prompt, attachments):
                self.warning.emit(w)
            if self._cancel_requested:
                raise RunCancelled()

            backend = create_backend(self.settings)
            request = RunRequest(
                workspace=self.ws.root,
                prompt_file=PROMPT_FILE,
                workflow_id=self.wf.id,
                workflow_name=self.wf.name,
                answers=self.answers,
                display=[(q.label, q.display(self.answers.get(q.id)))
                         for q in self.wf.questions if q.id in self.answers],
            )
            self.handle = backend.start(request)
            if self._cancel_requested:
                raise RunCancelled()
            self.status.emit("Copilot arbeitet …")
            for piece in self.handle.stream():
                self.chunk.emit(piece)
            self.done.emit()
        except RunCancelled:
            self.cancelled.emit()
        except BackendError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 – unerwartete Fehler sichtbar machen
            self.failed.emit(f"Unerwarteter Fehler: {type(exc).__name__}: {exc}")

    def cancel(self):
        self._cancel_requested = True
        if self.handle:
            self.handle.cancel()


class RunPage(QWidget):
    running_changed = Signal(bool)
    new_run = Signal()
    edit_answers = Signal(object, dict)  # Workflow, Antworten

    def __init__(self, settings: Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.settings = settings
        self.wf: Workflow | None = None
        self.answers: dict[str, Any] = {}
        self.ws: RunWorkspace | None = None
        self.text = ""
        self.thread: QThread | None = None
        self.worker: RunWorker | None = None
        self.started_at = 0.0
        self.warnings: list[str] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 22, 32, 22)
        root.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(12)
        self.icon = label("", "H1")
        head.addWidget(self.icon)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.title = label("", "H2")
        self.status = label("", "Muted")
        titles.addWidget(self.title)
        titles.addWidget(self.status)
        head.addLayout(titles, 1)
        self.elapsed = label("", "Faint")
        head.addWidget(self.elapsed)
        self.cancel_btn = button("Abbrechen", "danger")
        self.cancel_btn.clicked.connect(self.cancel)
        head.addWidget(self.cancel_btn)
        root.addLayout(head)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 0)
        root.addWidget(self.progress)

        self.banner = Banner()
        root.addWidget(self.banner)

        card = Card(margins=6)
        shadow(card, blur=20, y=2, alpha=12)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.view.document().setDefaultStyleSheet(MARKDOWN_CSS)
        self.view.document().setDocumentMargin(18)
        card.body.addWidget(self.view)
        root.addWidget(card, 1)

        actions = QHBoxLayout()
        self.folder_btn = button("📂  Arbeitsordner")
        self.folder_btn.clicked.connect(self.open_folder)
        self.edit_btn = button("✎  Angaben anpassen")
        self.edit_btn.setToolTip("Walkthrough mit denselben Antworten erneut öffnen")
        self.edit_btn.clicked.connect(lambda: self.edit_answers.emit(self.wf, self.answers))
        actions.addWidget(self.folder_btn)
        actions.addWidget(self.edit_btn)
        actions.addStretch()
        self.md_btn = button("Markdown speichern …")
        self.md_btn.clicked.connect(self.save_markdown)
        self.pdf_btn = button("PDF exportieren …")
        self.pdf_btn.clicked.connect(self.save_pdf)
        self.new_btn = button("Neuer Durchlauf", "primary")
        self.new_btn.clicked.connect(self.new_run)
        for b in (self.md_btn, self.pdf_btn, self.new_btn):
            actions.addWidget(b)
        root.addLayout(actions)

        self.render_timer = QTimer(self, interval=120, singleShot=True)
        self.render_timer.timeout.connect(self._render)
        self.clock = QTimer(self, interval=1000)
        self.clock.timeout.connect(self._tick)

    # ------------------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self.thread is not None

    def start(self, wf: Workflow, answers: dict[str, Any]):
        self.wf, self.answers = wf, answers
        self.text = ""
        self.warnings = []
        self.icon.setText(wf.icon)
        self.title.setText(wf.name)
        self.banner.show_message("info", "")
        self._render()
        self.ws = RunWorkspace.create(self.settings.runs_path, wf)
        self.ws.write_meta(
            workflow_id=wf.id, workflow_name=wf.name, icon=wf.icon,
            created=dt.datetime.now().isoformat(timespec="seconds"),
            status="running", backend=self.settings.backend,
            answers=answers,
            title=self._run_title(wf, answers),
        )

        self.worker = RunWorker(self.settings, wf, answers, self.ws)
        self.thread = QThread(self)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.status.connect(self.status.setText)
        self.worker.chunk.connect(self._on_chunk)
        self.worker.warning.connect(self._on_warning)
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.cancelled.connect(self._on_cancelled)
        self.thread.start()

        self.started_at = time.monotonic()
        self._tick()
        self.clock.start()
        self._set_running(True)

    def show_existing(self, ws: RunWorkspace, wf: Workflow | None, meta: dict[str, Any]):
        """Zeigt einen früheren Lauf aus dem Verlauf an."""
        self.ws, self.wf, self.answers = ws, wf, meta.get("answers", {})
        self.text = ws.read_result()
        self.icon.setText(meta.get("icon", "🤖"))
        self.title.setText(meta.get("workflow_name", ws.root.name))
        self.status.setText(f"Lauf vom {_fmt_ts(meta.get('created'))} · {meta.get('title', '')}")
        self.elapsed.setText("")
        if meta.get("status") == "failed":
            self.banner.show_message("error", meta.get("error", "Lauf fehlgeschlagen."))
        elif not self.text:
            self.banner.show_message("warning", "Für diesen Lauf liegt kein Ergebnis vor.")
        else:
            self.banner.show_message("info", "")
        self._render()
        self._set_running(False)
        self.edit_btn.setEnabled(wf is not None)

    # ---- Worker-Signale ------------------------------------------------
    def _on_chunk(self, piece: str):
        self.text += piece
        if not self.render_timer.isActive():
            self.render_timer.start()

    def _on_warning(self, message: str):
        self.warnings.append(message)
        self.banner.show_message("warning", "\n".join(self.warnings))

    def _on_done(self):
        self._render()
        result = self.ws.write_result(self.text, self.wf.output_name)
        self.ws.write_meta(status="done", result_file=result.name,
                           duration_s=round(time.monotonic() - self.started_at))
        self.status.setText(f"Fertig · Ergebnis gespeichert unter {result.name}")
        if not self.warnings:
            self.banner.show_message("success", "Copilot hat geantwortet.")
        self._finish()

    def _on_failed(self, message: str):
        self._render()
        self.ws.write_meta(status="failed", error=message)
        self.status.setText("Fehlgeschlagen")
        self.banner.show_message("error", message)
        self._finish()

    def _on_cancelled(self):
        self._render()
        self.ws.write_meta(status="cancelled")
        self.status.setText("Abgebrochen")
        self.banner.show_message("warning", "Der Lauf wurde abgebrochen.")
        self._finish()

    def _finish(self):
        self.clock.stop()
        self._tick()
        if self.thread:
            self.thread.quit()
            self.thread.wait(3000)
        self.thread = None
        self.worker = None
        self._set_running(False)

    # ------------------------------------------------------------------
    def cancel(self):
        if self.worker:
            self.status.setText("Wird abgebrochen …")
            self.cancel_btn.setEnabled(False)
            self.worker.cancel()

    def _set_running(self, running: bool):
        self.progress.setVisible(running)
        self.cancel_btn.setVisible(running)
        self.cancel_btn.setEnabled(running)
        has_text = bool(self.text) and not running
        for b in (self.md_btn, self.pdf_btn):
            b.setEnabled(has_text)
        for b in (self.new_btn, self.edit_btn, self.folder_btn):
            b.setEnabled(not running)
        self.running_changed.emit(running)

    def _render(self):
        bar = self.view.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 30
        self.view.setHtml(to_html(self.text or "_Warte auf Antwort von Copilot …_"))
        if at_bottom and self.is_running:
            bar.setValue(bar.maximum())

    def _tick(self):
        if self.started_at:
            secs = int(time.monotonic() - self.started_at)
            self.elapsed.setText(f"⏱ {secs // 60:02d}:{secs % 60:02d}")

    @staticmethod
    def _run_title(wf: Workflow, answers: dict[str, Any]) -> str:
        for q in wf.questions:
            if q.type in ("text", "textarea") and answers.get(q.id):
                return str(answers[q.id])[:80]
        return wf.name

    # ---- Aktionen ------------------------------------------------------
    def open_folder(self):
        if self.ws:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.ws.root)))

    def _suggest(self, suffix: str) -> str:
        name = Path(self.wf.output_name if self.wf else "ergebnis.md").with_suffix(suffix).name
        return str(self.ws.root / name) if self.ws else name

    def save_markdown(self):
        path, _ = QFileDialog.getSaveFileName(self, "Ergebnis speichern", self._suggest(".md"),
                                              "Markdown (*.md)")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.text)

    def save_pdf(self):
        path, _ = QFileDialog.getSaveFileName(self, "Als PDF exportieren", self._suggest(".pdf"),
                                              "PDF (*.pdf)")
        if not path:
            return
        printer = QPrinter(QPrinter.HighResolution)
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setOutputFileName(path)
        printer.setPageLayout(QPageLayout(QPageSize(QPageSize.A4), QPageLayout.Portrait,
                                          QMarginsF(18, 18, 18, 18), QPageLayout.Millimeter))
        doc = QTextDocument()
        doc.setDefaultStyleSheet(MARKDOWN_CSS)
        doc.setHtml(to_html(self.text))
        doc.print_(printer)
        QMessageBox.information(self, "PDF exportiert", f"Gespeichert unter:\n{path}")


def _fmt_ts(value: str | None) -> str:
    try:
        return dt.datetime.fromisoformat(value).strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError):
        return "?"
