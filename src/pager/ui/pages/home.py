"""Startseite: Übersicht aller verfügbaren Walkthroughs."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QMenu, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from pager.core.workflow import Workflow, WorkflowLoadError
from pager.ui.theme import shadow
from pager.ui.widgets.common import Banner, Card, button, clear_layout, label


def _soft(hex_color: str, alpha: int = 28) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


class WorkflowCard(QFrame):
    start = Signal(object)
    edit = Signal(object)

    def __init__(self, wf: Workflow):
        super().__init__()
        self.wf = wf
        self.setObjectName("WorkflowCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(230)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        shadow(self, blur=18, y=2, alpha=14)
        if wf.background:
            self.setStyleSheet(f"#WorkflowCard {{ background: {wf.background}; }}")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 16)
        lay.setSpacing(10)

        top = QHBoxLayout()
        icon = label(wf.icon, "IconBubble")
        icon.setFixedSize(48, 48)
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet(f"background: {_soft(wf.color)};")
        top.addWidget(icon)
        top.addStretch()
        top.addWidget(label(wf.category, "ChipNeutral"), 0, Qt.AlignTop)
        lay.addLayout(top)

        lay.addWidget(label(wf.name, "H2"))
        desc = label(wf.description.strip(), "Muted", wrap=True)
        desc.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        desc.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        lay.addWidget(desc, 1)

        bottom = QHBoxLayout()
        n_steps, n_q = len(wf.steps), len(wf.questions)
        info = f"{n_steps} Schritte · {n_q} Fragen"
        if wf.has_agent_steps:
            info += " · 💬 Agent-Fenster"
        bottom.addWidget(label(info, "Faint"))
        bottom.addStretch()
        go = button("Starten  →", "primary")
        go.clicked.connect(lambda: self.start.emit(self.wf))
        bottom.addWidget(go)
        lay.addLayout(bottom)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.start.emit(self.wf)
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction("▶  Starten", lambda: self.start.emit(self.wf))
        menu.addAction("✎  Bearbeiten", lambda: self.edit.emit(self.wf))
        menu.exec(event.globalPos())


class HomePage(QWidget):
    start_workflow = Signal(object)
    edit_workflow = Signal(object)
    create_requested = Signal()
    import_requested = Signal()
    reload_requested = Signal()

    MIN_CARD_WIDTH = 300

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.workflows: list[Workflow] = []
        self.cards: list[WorkflowCard] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        lay = QVBoxLayout(content)
        lay.setContentsMargins(40, 36, 40, 36)
        lay.setSpacing(18)

        self.banner = Banner("warning")
        lay.addWidget(self.banner)

        self.sections = QVBoxLayout()
        self.sections.setSpacing(12)
        lay.addLayout(self.sections)
        lay.addStretch()

    # ------------------------------------------------------------------
    def set_workflows(self, workflows: list[Workflow], errors: list[WorkflowLoadError]):
        self.workflows = workflows
        if errors:
            self.banner.show_message(
                "warning",
                "Einige Workflow-Dateien konnten nicht geladen werden:\n"
                + "\n".join(f"• {e}" for e in errors),
            )
        else:
            self.banner.show_message("warning", "")
        self._render()

    def _columns(self) -> int:
        return max(1, min(3, (self.width() - 80) // self.MIN_CARD_WIDTH))

    def _render(self):
        clear_layout(self.sections)
        self.cards = []
        matches = self.workflows
        if not self.workflows:
            self.sections.addWidget(self._empty_state())
            return

        cols = self._columns()
        categories: dict[str, list[Workflow]] = {}
        for w in matches:
            categories.setdefault(w.category, []).append(w)
        for cat, wfs in categories.items():
            self.sections.addWidget(label(cat.upper(), "Faint"))
            grid_host = QWidget()
            grid = QGridLayout(grid_host)
            grid.setContentsMargins(0, 0, 0, 12)
            grid.setSpacing(16)
            for i, wf in enumerate(wfs):
                card = WorkflowCard(wf)
                card.start.connect(self.start_workflow)
                card.edit.connect(self.edit_workflow)
                self.cards.append(card)
                grid.addWidget(card, i // cols, i % cols)
            for c in range(cols):
                grid.setColumnStretch(c, 1)
            self.sections.addWidget(grid_host)
        self._cols = cols

    def _empty_state(self) -> QWidget:
        card = Card(margins=40, spacing=12)
        card.setMaximumWidth(640)
        shadow(card, blur=18, y=2, alpha=12)
        icon = label("🧭", "H1")
        icon.setStyleSheet("font-size: 40pt;")
        card.body.addWidget(icon, 0, Qt.AlignHCenter)
        title = label("Noch keine Walkthroughs", "H2")
        card.body.addWidget(title, 0, Qt.AlignHCenter)
        text = label("Ein Walkthrough ist eine Prompt-Vorlage mit Fragebogen: Er führt Schritt für Schritt "
                     "durch alle Angaben und baut daraus den fertigen Prompt für Copilot. Lege deinen ersten an – der Editor hilft dir dabei – oder importiere "
                     "eine vorhandene YAML-Datei. In den Einstellungen kannst du außerdem einen "
                     "gemeinsamen Ordner (z. B. ein Netzlaufwerk) einbinden.", "Muted", wrap=True)
        text.setAlignment(Qt.AlignCenter)
        card.body.addWidget(text)
        row = QHBoxLayout()
        row.addStretch()
        imp = button("Importieren …")
        imp.clicked.connect(self.import_requested)
        create = button("＋  Ersten Walkthrough anlegen", "primary")
        create.clicked.connect(self.create_requested)
        row.addWidget(imp)
        row.addWidget(create)
        row.addStretch()
        card.body.addSpacing(8)
        card.body.addLayout(row)
        wrap = QWidget()
        wl = QHBoxLayout(wrap)
        wl.setContentsMargins(0, 30, 0, 0)
        wl.addStretch()
        wl.addWidget(card, 1)
        wl.addStretch()
        return wrap

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.workflows and getattr(self, "_cols", None) != self._columns():
            self._render()
