"""Verlauf: frühere Läufe ansehen und erneut öffnen."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from pager.core.workspace import RunWorkspace, list_runs
from pager.ui.widgets.common import Card, button, label

STATUS = {
    "done": "✅ Fertig",
    "failed": "⛔ Fehler",
    "cancelled": "⏹ Abgebrochen",
    "running": "⏳ Unterbrochen",  # beim Laden kein Lauf aktiv → wurde nicht sauber beendet
    "waiting": "📋 Wartet auf Antwort",
    "agent": "💬 Agent-Fenster offen",  # PAGER wurde während eines Agent-Fensters beendet
}


class HistoryPage(QWidget):
    open_run = Signal(object, dict)  # RunWorkspace, meta

    def __init__(self, runs_dir_getter, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.runs_dir_getter = runs_dir_getter
        self.runs: list[tuple[RunWorkspace, dict]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(40, 36, 40, 36)
        root.setSpacing(18)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(4)
        titles.addWidget(label("Verlauf", "H1"))
        self.subtitle = label("", "Muted")
        titles.addWidget(self.subtitle)
        head.addLayout(titles, 1)
        refresh = button("⟳  Aktualisieren", "ghost")
        refresh.clicked.connect(self.refresh)
        head.addWidget(refresh, 0, Qt.AlignTop)
        root.addLayout(head)

        card = Card(margins=8)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Datum", "Walkthrough", "Betreff", "Status"])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setFocusPolicy(Qt.NoFocus)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.doubleClicked.connect(self._open_selected)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        card.body.addWidget(self.table)
        root.addWidget(card, 1)

        actions = QHBoxLayout()
        actions.addWidget(label("Doppelklick öffnet das Ergebnis.", "Faint"))
        actions.addStretch()
        self.folder_btn = button("📂  Ordner öffnen")
        self.folder_btn.clicked.connect(self._open_folder)
        self.open_btn = button("Ergebnis öffnen", "primary")
        self.open_btn.clicked.connect(self._open_selected)
        actions.addWidget(self.folder_btn)
        actions.addWidget(self.open_btn)
        root.addLayout(actions)

    def refresh(self):
        runs_dir: Path = self.runs_dir_getter()
        self.runs = list_runs(runs_dir)
        self.subtitle.setText(f"{len(self.runs)} Läufe · gespeichert in {runs_dir}")
        self.table.setRowCount(len(self.runs))
        for row, (ws, meta) in enumerate(self.runs):
            try:
                when = dt.datetime.fromisoformat(meta.get("created", "")).strftime("%d.%m.%Y  %H:%M")
            except ValueError:
                when = "?"
            cells = [
                when,
                f"{meta.get('icon', '🤖')}  {meta.get('workflow_name', '?')}",
                meta.get("title", ""),
                STATUS.get(meta.get("status", ""), meta.get("status", "")),
            ]
            for col, text in enumerate(cells):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self._update_buttons()

    def _selected(self):
        rows = self.table.selectionModel().selectedRows()
        return self.runs[rows[0].row()] if rows else None

    def _update_buttons(self):
        has = self._selected() is not None
        self.open_btn.setEnabled(has)
        self.folder_btn.setEnabled(has)

    def _open_selected(self, *_):
        if sel := self._selected():
            self.open_run.emit(*sel)

    def _open_folder(self):
        if sel := self._selected():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(sel[0].root)))
