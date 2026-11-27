"""Farben, Schriften und Stylesheet."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QGraphicsDropShadowEffect, QWidget

RESOURCES = Path(__file__).resolve().parent.parent / "resources"

C = {
    "bg": "#F3F5F9",
    "surface": "#FFFFFF",
    "surface_alt": "#F7F8FB",
    "border": "#E1E5EE",
    "border_strong": "#C9D0DE",
    "text": "#18202F",
    "muted": "#5E6779",
    "faint": "#8C94A5",
    "accent": "#3A6FF8",
    "accent_hover": "#2F5FE0",
    "accent_pressed": "#2550C4",
    "accent_soft": "#E8EEFF",
    "sidebar": "#101B33",
    "sidebar_hover": "#1A2847",
    "sidebar_active": "#22355E",
    "sidebar_text": "#B9C4DB",
    "success": "#1F9D6B",
    "success_soft": "#E3F6EE",
    "warning": "#B7791F",
    "warning_soft": "#FFF5E0",
    "danger": "#D14343",
    "danger_soft": "#FDECEC",
}


def app_icon() -> QIcon:
    return QIcon(str(RESOURCES / "pager.svg"))


def brand_logo(width: int, ratio: float = 1.0) -> QPixmap:
    """Firmenlogo für die Seitenleiste, scharf auch auf hochauflösenden Bildschirmen."""
    pix = QPixmap(str(RESOURCES / "atlas-ai.png"))
    if pix.isNull():
        return pix
    scaled = pix.scaledToWidth(int(width * ratio), Qt.SmoothTransformation)
    scaled.setDevicePixelRatio(ratio)
    return scaled


def shadow(widget: QWidget, blur: int = 24, y: int = 4, alpha: int = 22) -> None:
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, y)
    effect.setColor(QColor(16, 27, 51, alpha))
    widget.setGraphicsEffect(effect)


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    font = QFont()
    font.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Inter", "Helvetica Neue", "Arial"])
    font.setPointSizeF(10 if sys.platform == "win32" else 13)
    app.setFont(font)

    pal = app.palette()
    pal.setColor(QPalette.Window, QColor(C["bg"]))
    pal.setColor(QPalette.Base, QColor(C["surface"]))
    pal.setColor(QPalette.Text, QColor(C["text"]))
    pal.setColor(QPalette.WindowText, QColor(C["text"]))
    pal.setColor(QPalette.Highlight, QColor(C["accent"]))
    pal.setColor(QPalette.HighlightedText, QColor("#FFFFFF"))
    pal.setColor(QPalette.PlaceholderText, QColor(C["faint"]))
    app.setPalette(pal)
    app.setStyleSheet(STYLESHEET)


STYLESHEET = f"""
* {{ outline: none; }}
QWidget {{ color: {C['text']}; }}
QMainWindow, #Page {{ background: {C['bg']}; }}
QToolTip {{ background: {C['sidebar']}; color: white; border: none; padding: 6px 8px; border-radius: 6px; }}

/* ---------- Sidebar ---------- */
#Sidebar {{ background: {C['sidebar']}; }}
#Sidebar QLabel {{ color: {C['sidebar_text']}; background: transparent; }}
#Logo {{ color: white; font-size: 22pt; font-weight: 700; letter-spacing: 3px; }}
#LogoSub {{ color: {C['sidebar_text']}; font-size: 8.5pt; }}
#NavButton {{
    text-align: left; padding: 10px 14px; border: none; border-radius: 8px;
    color: {C['sidebar_text']}; background: transparent; font-weight: 500;
}}
#NavButton:hover {{ background: {C['sidebar_hover']}; color: white; }}
#NavButton:checked {{ background: {C['sidebar_active']}; color: white; font-weight: 600; }}
#NavButton:disabled {{ color: #56627C; }}
#StatusBox {{ background: {C['sidebar_hover']}; border-radius: 10px; }}
#BrandBox {{ background: white; border-radius: 10px; }}
#BrandBox QLabel {{ background: transparent; }}
#StatusTitle {{ color: white; font-weight: 600; }}
#StatusText {{ font-size: 8.5pt; }}

/* ---------- Typografie ---------- */
#H1 {{ font-size: 20pt; font-weight: 700; }}
#H2 {{ font-size: 14pt; font-weight: 650; }}
#H3 {{ font-size: 11pt; font-weight: 650; }}
#Muted {{ color: {C['muted']}; }}
#Faint {{ color: {C['faint']}; font-size: 9pt; }}
#Error {{ color: {C['danger']}; font-size: 9pt; }}
#Required {{ color: {C['danger']}; }}

/* ---------- Karten ---------- */
#Card {{ background: {C['surface']}; border: 1px solid {C['border']}; border-radius: 14px; }}
#WorkflowCard {{ background: {C['surface']}; border: 1px solid {C['border']}; border-radius: 14px; }}
#WorkflowCard:hover {{ border: 1px solid {C['accent']}; }}
#IconBubble {{ border-radius: 12px; font-size: 20pt; }}
#Chip {{
    background: {C['accent_soft']}; color: {C['accent']}; border-radius: 9px;
    padding: 2px 9px; font-size: 8.5pt; font-weight: 600;
}}
#ChipNeutral {{
    background: {C['surface_alt']}; color: {C['muted']}; border: 1px solid {C['border']};
    border-radius: 9px; padding: 2px 9px; font-size: 8.5pt;
}}
#Banner[kind="warning"] {{ background: {C['warning_soft']}; border: 1px solid #F1D9A6; border-radius: 10px; }}
#Banner[kind="error"] {{ background: {C['danger_soft']}; border: 1px solid #F3C1C1; border-radius: 10px; }}
#Banner[kind="success"] {{ background: {C['success_soft']}; border: 1px solid #B7E4CF; border-radius: 10px; }}
#Banner[kind="info"] {{ background: {C['accent_soft']}; border: 1px solid #C9D6FF; border-radius: 10px; }}
#Banner QLabel {{ background: transparent; }}

/* ---------- Buttons ---------- */
QPushButton {{
    background: {C['surface']}; border: 1px solid {C['border_strong']}; border-radius: 8px;
    padding: 8px 16px; font-weight: 500;
}}
QPushButton:hover {{ background: {C['surface_alt']}; border-color: {C['faint']}; }}
QPushButton:pressed {{ background: {C['border']}; }}
QPushButton:disabled {{ color: {C['faint']}; background: {C['surface_alt']}; border-color: {C['border']}; }}
QPushButton[variant="primary"] {{
    background: {C['accent']}; color: white; border: 1px solid {C['accent']}; font-weight: 600;
}}
QPushButton[variant="primary"]:hover {{ background: {C['accent_hover']}; border-color: {C['accent_hover']}; }}
QPushButton[variant="primary"]:pressed {{ background: {C['accent_pressed']}; }}
QPushButton[variant="primary"]:disabled {{ background: #A9BDF7; border-color: #A9BDF7; color: white; }}
QPushButton[variant="danger"] {{ color: {C['danger']}; border-color: #EDB4B4; }}
QPushButton[variant="danger"]:hover {{ background: {C['danger_soft']}; }}
QPushButton[variant="danger"]:disabled {{ color: {C['faint']}; border-color: {C['border']}; background: {C['surface_alt']}; }}
QPushButton#MenuButton::menu-indicator {{ image: none; width: 0; }}
QPushButton[variant="ghost"] {{ background: transparent; border: none; color: {C['muted']}; padding: 6px 10px; }}
QPushButton[variant="ghost"]:hover {{ color: {C['accent']}; background: {C['accent_soft']}; }}
QPushButton[variant="link"] {{ background: transparent; border: none; color: {C['accent']}; padding: 2px 4px; }}
QPushButton[variant="link"]:hover {{ text-decoration: underline; }}
QPushButton[variant="segment"] {{ padding: 8px 18px; min-width: 44px; }}
QPushButton[variant="segment"]:checked {{
    background: {C['accent_soft']}; border: 1px solid {C['accent']}; color: {C['accent']}; font-weight: 600;
}}

/* ---------- Eingaben ---------- */
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QDateEdit, QSpinBox {{
    background: {C['surface']}; border: 1px solid {C['border_strong']}; border-radius: 8px;
    padding: 7px 10px; selection-background-color: {C['accent']};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QDateEdit:focus, QSpinBox:focus {{
    border: 1px solid {C['accent']};
}}
QLineEdit[invalid="true"], QPlainTextEdit[invalid="true"] {{ border: 1px solid {C['danger']}; }}
QLineEdit:read-only {{ background: {C['surface_alt']}; color: {C['muted']}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView {{
    background: white; border: 1px solid {C['border']}; selection-background-color: {C['accent_soft']};
    selection-color: {C['text']}; padding: 4px;
}}
QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 18px; height: 18px; }}
QCheckBox::indicator {{ border: 1px solid {C['border_strong']}; border-radius: 5px; background: white; }}
QCheckBox::indicator:checked {{ background: {C['accent']}; border-color: {C['accent']}; image: url({(RESOURCES / 'check.svg').as_posix()}); }}
QRadioButton::indicator {{ width: 16px; height: 16px; border: 1px solid {C['border_strong']}; border-radius: 9px; background: white; }}
QRadioButton::indicator:checked {{ width: 18px; height: 18px; border: none; background: transparent; image: url({(RESOURCES / 'radio.svg').as_posix()}); }}

#OptionCard {{ background: {C['surface']}; border: 1px solid {C['border']}; border-radius: 10px; }}
#OptionCard[selected="true"] {{ background: {C['accent_soft']}; border: 1px solid {C['accent']}; }}
#OptionCard QLabel {{ background: transparent; }}

#DropZone {{
    background: {C['surface_alt']}; border: 2px dashed {C['border_strong']}; border-radius: 12px;
}}
#DropZone[active="true"] {{ background: {C['accent_soft']}; border-color: {C['accent']}; }}
#DropZone QLabel {{ background: transparent; }}
#FileRow {{ background: {C['surface']}; border: 1px solid {C['border']}; border-radius: 8px; }}
#FileRow QLabel {{ background: transparent; }}

/* ---------- Stepper ---------- */
#Stepper {{ background: transparent; }}
#StepItem {{ border-radius: 10px; background: transparent; }}
#StepItem:hover {{ background: {C['surface']}; }}
#StepItem QLabel {{ background: transparent; }}
#StepDot {{
    border-radius: 14px; font-weight: 700; font-size: 9pt;
    background: {C['surface']}; border: 2px solid {C['border_strong']}; color: {C['faint']};
}}
#StepDot[state="current"] {{ background: {C['accent']}; border-color: {C['accent']}; color: white; }}
#StepDot[state="done"] {{ background: {C['success_soft']}; border-color: {C['success']}; color: {C['success']}; }}
#StepTitle[state="current"] {{ font-weight: 650; color: {C['text']}; }}
#StepTitle[state="upcoming"] {{ color: {C['faint']}; }}
#StepTitle[state="done"] {{ color: {C['muted']}; }}

/* ---------- Diverses ---------- */
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: #CBD2DF; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #AAB3C5; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QProgressBar {{ background: {C['border']}; border: none; border-radius: 3px; max-height: 6px; }}
QProgressBar::chunk {{ background: {C['accent']}; border-radius: 3px; }}
QTextBrowser {{ background: {C['surface']}; border: none; padding: 8px; }}
QTableWidget {{
    background: {C['surface']}; border: none; gridline-color: transparent;
    selection-background-color: {C['accent_soft']}; selection-color: {C['text']};
}}
QTableWidget::item {{ padding: 8px; border-bottom: 1px solid {C['border']}; }}
QHeaderView::section {{
    background: {C['surface']}; color: {C['muted']}; border: none; border-bottom: 1px solid {C['border']};
    padding: 10px 8px; font-weight: 600;
}}
QSlider::groove:horizontal {{ height: 6px; background: {C['border']}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {C['accent']}; width: 18px; margin: -6px 0; border-radius: 9px; }}
QMessageBox QLabel {{ min-width: 320px; }}

/* ---------- Menüs ---------- */
QMenu {{ background: white; border: 1px solid {C['border']}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 8px 22px 8px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {C['accent_soft']}; color: {C['text']}; }}
QMenu::separator {{ height: 1px; background: {C['border']}; margin: 4px 8px; }}

/* ---------- Editor ---------- */
QTreeWidget#Outline {{ background: transparent; border: none; show-decoration-selected: 0; }}
QTreeWidget#Outline::item {{ padding: 7px 6px; border-radius: 7px; margin: 1px 0; }}
QTreeWidget#Outline::item:hover {{ background: {C['surface_alt']}; }}
QTreeWidget#Outline::item:selected {{ background: {C['accent_soft']}; color: {C['text']}; }}
QTreeWidget#Outline::branch, QTreeWidget#Outline::branch:selected {{ background: transparent; }}
#EmojiButton {{
    font-size: 22pt; background: {C['surface']}; border: 1px solid {C['border_strong']}; border-radius: 10px;
}}
#EmojiButton:hover {{ border-color: {C['accent']}; }}
#EmojiButton::menu-indicator {{ image: none; width: 0; }}
#EmojiCell {{ font-size: 16pt; border: none; border-radius: 6px; background: transparent; }}
#EmojiCell:hover {{ background: {C['accent_soft']}; }}
QPushButton#VarChip {{
    background: {C['accent_soft']}; color: {C['accent']}; border: 1px solid #C9D6FF;
    border-radius: 13px; padding: 4px 12px; font-size: 9pt; font-weight: 500;
}}
QPushButton#VarChip:hover {{ background: #DCE5FF; border-color: {C['accent']}; }}
QTableWidget#OptionsTable {{ border: 1px solid {C['border']}; border-radius: 8px; }}
QTableWidget#OptionsTable::item {{ padding: 4px 8px; }}
QListWidget#WorkflowList {{
    background: {C['surface']}; border: 1px solid {C['border']}; border-radius: 10px; padding: 4px;
}}
QListWidget#WorkflowList::item {{ padding: 10px 8px; border-radius: 7px; }}
QListWidget#WorkflowList::item:hover {{ background: {C['surface_alt']}; }}
QListWidget#WorkflowList::item:selected {{ background: {C['accent_soft']}; color: {C['text']}; }}
QListWidget {{ background: {C['surface']}; border: 1px solid {C['border']}; border-radius: 8px; }}

/* ---------- Agent-Fenster ---------- */
QPlainTextEdit#AgentOutput {{
    background: #0F1729; color: #E3E8F2; border: 1px solid #1E2A44; border-radius: 10px; padding: 10px 12px;
    selection-background-color: #2F5FE0;
}}
QPlainTextEdit#AgentRaw {{
    background: #0B1120; color: #9AA6BF; border: 1px solid #1E2A44; border-radius: 10px; padding: 8px 10px;
}}
QPlainTextEdit#AgentInput {{ border-radius: 10px; }}
QSplitter::handle {{ background: transparent; height: 8px; }}
#AgentDot {{ font-size: 12pt; color: {C['faint']}; }}
#AgentDot[state="connecting"] {{ color: {C['warning']}; }}
#AgentDot[state="ready"] {{ color: {C['success']}; }}
#AgentDot[state="busy"] {{ color: {C['accent']}; }}
#AgentDot[state="error"] {{ color: {C['danger']}; }}
#PermissionPanel {{ background: {C['warning_soft']}; border: 1px solid #F1D9A6; border-radius: 10px; }}
#PermissionPanel QLabel {{ background: transparent; }}
#QuestionPanel {{ background: #F3EEFF; border: 1px solid #D6C6FF; border-radius: 10px; }}
#QuestionPanel QLabel {{ background: transparent; }}
QListWidget#QuestionList {{ background: white; border: 1px solid #D6C6FF; border-radius: 8px; padding: 3px; }}
QListWidget#QuestionList::item {{ padding: 6px 8px; border-radius: 6px; }}
QListWidget#QuestionList::item:selected {{ background: #8A5CF6; color: white; }}
#SetupBox {{ background: {C['surface_alt']}; border: 1px solid {C['border']}; border-radius: 12px; }}
#SetupBox QLabel {{ background: transparent; }}
#CheckRow {{ background: {C['surface']}; border: 1px solid {C['border']}; border-radius: 9px; }}
#CheckRow QLabel {{ background: transparent; }}
"""
