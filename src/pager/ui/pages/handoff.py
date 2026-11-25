"""Übergabe an Copilot Chat: Prompt kopieren → im Chat einfügen → Antwort zurückholen.

Für Umgebungen, in denen nur der Copilot Chat verfügbar ist (keine CLI, keine Agenten, keine
Repositories). PAGER kann den Chat nicht selbst bedienen – es führt den Nutzer durch die Übergabe.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget

from pager.config import Settings
from pager.core.prompting import Attachment
from pager.core.workflow import Workflow
from pager.core.workspace import PROMPT_FILE, RunWorkspace
from pager.ui.widgets.common import Banner, Card, button, label, set_prop


def _step_card(number: int, title: str, text: str) -> tuple[Card, QVBoxLayout]:
    card = Card(margins=24, spacing=12)
    head = QHBoxLayout()
    head.setSpacing(14)
    dot = label(str(number), "StepDot")
    dot.setFixedSize(28, 28)
    dot.setAlignment(Qt.AlignCenter)
    set_prop(dot, "state", "current")
    head.addWidget(dot, 0, Qt.AlignTop)
    titles = QVBoxLayout()
    titles.setSpacing(4)
    titles.addWidget(label(title, "H2"))
    titles.addWidget(label(text, "Muted", wrap=True))
    head.addLayout(titles, 1)
    card.body.addLayout(head)
    content = QVBoxLayout()
    content.setContentsMargins(42, 0, 0, 0)
    content.setSpacing(10)
    card.body.addLayout(content)
    return card, content


class HandoffPage(QWidget):
    finished = Signal(object, object, dict)  # RunWorkspace, Workflow | None, meta
    edit_answers = Signal(object, dict)  # Workflow, Antworten
    later = Signal()

    def __init__(self, settings: Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.settings = settings
        self.wf: Workflow | None = None
        self.ws: RunWorkspace | None = None
        self.answers: dict[str, Any] = {}
        self.prompt = ""

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
        titles.addWidget(self.title)
        titles.addWidget(label("Übergabe an Copilot Chat", "Muted"))
        head.addLayout(titles, 1)
        root.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        host = QWidget()
        body = QVBoxLayout(host)
        body.setContentsMargins(0, 0, 10, 0)
        body.setSpacing(14)
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

        # 1 – Prompt kopieren
        card, c = _step_card(1, "Prompt ist in der Zwischenablage",
                             "PAGER hat aus deinen Angaben den vollständigen Prompt erstellt und kopiert.")
        self.stats = label("", "Faint")
        c.addWidget(self.stats)
        row = QHBoxLayout()
        copy = button("📋  Erneut kopieren")
        copy.clicked.connect(self.copy_prompt)
        self.toggle = button("▸  Prompt anzeigen", "link")
        self.toggle.clicked.connect(self._toggle_preview)
        row.addWidget(copy)
        row.addWidget(self.toggle)
        row.addStretch()
        c.addLayout(row)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(240)
        self.preview.setStyleSheet("font-family: Consolas, Menlo, monospace; font-size: 9pt;")
        self.preview.hide()
        c.addWidget(self.preview)
        self.warn = Banner("warning")
        c.addWidget(self.warn)
        self.folder_btn = button("📂  Unterlagen-Ordner öffnen")
        self.folder_btn.clicked.connect(self._open_inputs)
        c.addWidget(self.folder_btn, 0, Qt.AlignLeft)
        body.addWidget(card)

        # 2 – Chat öffnen
        card, c = _step_card(2, "In Copilot Chat einfügen und absenden",
                             "Öffne Copilot Chat, füge den Prompt mit Strg+V ein und sende ihn ab.")
        self.chat_btn = button("↗  Copilot Chat öffnen", "primary")
        self.chat_btn.clicked.connect(self._open_chat)
        self.chat_hint = label("", "Faint")
        row = QHBoxLayout()
        row.addWidget(self.chat_btn)
        row.addWidget(self.chat_hint)
        row.addStretch()
        c.addLayout(row)
        body.addWidget(card)

        # 3 – Antwort zurückholen
        card, c = _step_card(3, "Antwort hier einfügen",
                             "Kopiere die Antwort von Copilot (Kopieren-Symbol unter der Antwort) und füge "
                             "sie hier ein. PAGER speichert sie im Verlauf; danach kannst du sie als PDF "
                             "oder Markdown exportieren.")
        self.answer = QPlainTextEdit()
        self.answer.setPlaceholderText("Antwort von Copilot hier einfügen (Strg+V) …")
        self.answer.setMinimumHeight(200)
        self.answer.textChanged.connect(self._update_buttons)
        c.addWidget(self.answer)
        row = QHBoxLayout()
        paste = button("📥  Aus Zwischenablage einfügen")
        paste.clicked.connect(self._paste)
        row.addWidget(paste)
        row.addStretch()
        self.done_btn = button("Ergebnis übernehmen  →", "primary")
        self.done_btn.clicked.connect(self._finish)
        row.addWidget(self.done_btn)
        c.addLayout(row)
        body.addWidget(card)
        body.addStretch()

        foot = QHBoxLayout()
        edit = button("✎  Angaben anpassen")
        edit.clicked.connect(lambda: self.wf and self.edit_answers.emit(self.wf, self.answers))
        self.edit_btn = edit
        foot.addWidget(edit)
        foot.addStretch()
        foot.addWidget(label("Der Lauf bleibt im Verlauf, bis eine Antwort eingefügt wurde.", "Faint"))
        later = button("Später fortsetzen")
        later.clicked.connect(self.later)
        foot.addWidget(later)
        root.addLayout(foot)

    # ------------------------------------------------------------------
    def start(self, wf: Workflow, answers: dict[str, Any], ws: RunWorkspace, prompt: str,
              attachments: list[Attachment], warnings: list[str]):
        self._load(wf, answers, ws, prompt)
        embedded = sum(1 for a in attachments if a.embedded)
        manual = len(attachments) - embedded
        self.stats.setText(self._stats_text(embedded))
        self.warn.show_message("warning", "\n".join(warnings))
        self.folder_btn.setVisible(manual > 0)
        self.copy_prompt()

    def resume(self, ws: RunWorkspace, wf: Workflow | None, meta: dict[str, Any]):
        prompt_file = ws.root / PROMPT_FILE
        prompt = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else ""
        self._load(wf, meta.get("answers", {}), ws, prompt)
        self.icon.setText(meta.get("icon", "🤖"))
        self.title.setText(meta.get("workflow_name", ws.root.name))
        self.stats.setText(self._stats_text(prompt.count("\n### Anhang ")))
        self.warn.show_message("info", "Fortgesetzter Lauf – der Prompt wurde erneut kopiert.")
        self.folder_btn.setVisible((ws.root / "eingaben").exists())
        self.copy_prompt()

    def _load(self, wf: Workflow | None, answers: dict[str, Any], ws: RunWorkspace, prompt: str):
        self.wf, self.answers, self.ws, self.prompt = wf, answers, ws, prompt
        if wf:
            self.icon.setText(wf.icon)
            self.title.setText(wf.name)
        self.edit_btn.setEnabled(wf is not None)
        self.preview.setPlainText(prompt)
        self.preview.hide()
        self.toggle.setText("▸  Prompt anzeigen")
        self.answer.clear()
        self.chat_hint.setText(self.settings.resolved_chat_url)
        self._update_buttons()

    def _stats_text(self, embedded: int) -> str:
        chars = f"{len(self.prompt):,}".replace(",", ".")
        att = f" · {embedded} Datei{'en' if embedded != 1 else ''} eingebettet" if embedded else ""
        return f"{chars} Zeichen{att}"

    # ---- Aktionen ------------------------------------------------------
    def copy_prompt(self):
        QGuiApplication.clipboard().setText(self.prompt)
        self.chat_btn.setFocus()

    def _toggle_preview(self):
        self.preview.setVisible(self.preview.isHidden())
        self.toggle.setText("▾  Prompt ausblenden" if self.preview.isVisible() else "▸  Prompt anzeigen")

    def _open_chat(self):
        QDesktopServices.openUrl(QUrl(self.settings.resolved_chat_url))
        QTimer.singleShot(300, self.answer.setFocus)

    def _open_inputs(self):
        if self.ws:
            folder = self.ws.root / "eingaben"
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder if folder.exists() else self.ws.root)))

    def _paste(self):
        text = QGuiApplication.clipboard().text()
        if text and text != self.prompt:
            self.answer.setPlainText(text)
        elif text == self.prompt:
            self.warn.show_message("warning", "In der Zwischenablage liegt noch der Prompt – bitte zuerst "
                                              "die Antwort in Copilot Chat kopieren.")

    def _update_buttons(self):
        self.done_btn.setEnabled(bool(self.answer.toPlainText().strip()))

    def _finish(self):
        text = self.answer.toPlainText().strip()
        if not text or not self.ws:
            return
        name = self.wf.output_name if self.wf else "ergebnis.md"
        self.ws.write_result(text + "\n", name)
        self.ws.write_meta(status="done", result_file=name,
                           answered=dt.datetime.now().isoformat(timespec="seconds"))
        self.finished.emit(self.ws, self.wf, self.ws.read_meta())
