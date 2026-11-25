"""Baut aus Walkthrough + Antworten einen vollständigen, in sich geschlossenen Prompt.

Der Prompt besteht aus
1. der gerenderten Prompt-Vorlage (Jinja2) und
2. automatisch angehängten Dateiinhalten („Anhänge“),
sodass er 1:1 in Copilot Chat eingefügt werden kann – ohne Zugriff auf lokale Dateien.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import ChainableUndefined, Environment

from pager.core.extract import extract_text
from pager.core.workflow import Workflow, as_list

INPUT_DIR = "eingaben"
MAX_ATTACHMENT_CHARS = 40_000  # je Datei
LONG_PROMPT_CHARS = 100_000  # ab hier warnen (Chat-Eingaben sind begrenzt)

_env = Environment(undefined=ChainableUndefined, trim_blocks=True, lstrip_blocks=True)

DEFAULT_PROMPT = """\
# Aufgabe: {{ workflow.name }}

{{ workflow.description }}

## Angaben
{{ antworten_tabelle }}
{% if dateien_liste %}

## Unterlagen
{{ dateien_liste }}
Die Inhalte der Unterlagen findest du unten im Abschnitt „Anhänge“.
{% endif %}

## Antwort
Antworte auf Deutsch als gut strukturierter Bericht in Markdown.
Kennzeichne Annahmen und offene Punkte deutlich.
"""


@dataclass
class Attachment:
    number: int
    question: str  # Beschriftung der Datei-Frage
    name: str
    rel: str  # Pfad im Arbeitsordner
    source: Path
    text: str | None = None  # None = nicht einbettbar → im Chat manuell anhängen
    truncated: bool = False
    error: str = ""

    @property
    def embedded(self) -> bool:
        return self.text is not None


def file_targets(workflow: Workflow, answers: dict[str, Any]) -> dict[str, list[tuple[Path, str]]]:
    """Zielpfade der hochgeladenen Dateien im Arbeitsordner: ``{frage_id: [(quelle, rel_pfad)]}``."""
    result: dict[str, list[tuple[Path, str]]] = {}
    for q in workflow.questions:
        if q.type not in ("file", "files"):
            continue
        used: set[str] = set()
        targets = []
        for src in as_list(answers.get(q.id)):
            src = Path(src)
            name, n = src.name, 2
            while name.lower() in used:
                name = f"{src.stem}_{n}{src.suffix}"
                n += 1
            used.add(name.lower())
            targets.append((src, f"{INPUT_DIR}/{q.id}/{name}"))
        if targets:
            result[q.id] = targets
    return result


def collect_attachments(workflow: Workflow, answers: dict[str, Any], read: bool = True) -> list[Attachment]:
    """Sammelt alle Dateien der sichtbaren Antworten; mit ``read`` wird der Inhalt extrahiert."""
    attachments = []
    for qid, targets in file_targets(workflow, workflow.visible_answers(answers)).items():
        label = workflow.question(qid).label
        for src, rel in targets:
            att = Attachment(len(attachments) + 1, label, src.name, rel, src)
            if not read:
                att.text = f"‹Inhalt von {src.name}›"
            else:
                try:
                    text = extract_text(src)
                except Exception as exc:  # noqa: BLE001 – defekte Datei soll den Lauf nicht stoppen
                    text, att.error = None, str(exc)
                if text is not None and len(text) > MAX_ATTACHMENT_CHARS:
                    text, att.truncated = text[:MAX_ATTACHMENT_CHARS], True
                att.text = text
            attachments.append(att)
    return attachments


def build_context(workflow: Workflow, answers: dict[str, Any], attachments: list[Attachment]) -> dict[str, Any]:
    visible = workflow.visible_answers(answers)
    rows = ["| Frage | Antwort |", "|---|---|"]
    for q in workflow.questions:
        if q.id in visible and q.type not in ("file", "files"):
            rows.append(f"| {q.label} | {q.display(visible[q.id]).replace('|', '/')} |")

    lines, files = [], {}
    for att in attachments:
        where = f"Anhang {att.number}" if att.embedded else "wird separat im Chat angehängt"
        lines.append(f"- {att.question}: {att.name} ({where})")
        qid = att.rel.split("/")[1]
        files.setdefault(qid, []).append({"name": att.name, "anhang": att.number if att.embedded else None})

    return {
        "workflow": workflow,
        "answers": visible,
        "anzeige": {q.id: q.display(visible.get(q.id)) for q in workflow.questions},
        "dateien": files,
        "antworten_tabelle": "\n".join(rows),
        "dateien_liste": "\n".join(lines),
        "heute": dt.date.today().strftime("%d.%m.%Y"),
    }


def _fence(text: str) -> str:
    fence = "````"
    while fence in text:
        fence += "`"
    return fence


def render_attachments(attachments: list[Attachment]) -> str:
    embedded = [a for a in attachments if a.embedded]
    manual = [a for a in attachments if not a.embedded]
    if not attachments:
        return ""
    parts = ["\n---\n\n## Anhänge"]
    for a in embedded:
        fence = _fence(a.text)
        note = f"\n_(gekürzt auf {MAX_ATTACHMENT_CHARS:,} Zeichen)_".replace(",", ".") if a.truncated else ""
        parts.append(f"\n### Anhang {a.number}: {a.name} ({a.question})\n{fence}\n{a.text.strip()}\n{fence}{note}")
    if manual:
        names = ", ".join(a.name for a in manual)
        parts.append(f"\nFolgende Dateien werden separat im Chat angehängt: {names}")
    return "\n".join(parts) + "\n"


def prepare_prompt(workflow: Workflow, answers: dict[str, Any], read_files: bool = True
                   ) -> tuple[str, list[Attachment]]:
    """Vollständiger Prompt (Vorlage + Anhänge) und die Liste der Anhänge."""
    attachments = collect_attachments(workflow, answers, read=read_files)
    template = workflow.prompt.strip() or DEFAULT_PROMPT
    body = _env.from_string(template).render(**build_context(workflow, answers, attachments)).strip()
    return body + "\n" + render_attachments(attachments), attachments


def render_prompt(workflow: Workflow, answers: dict[str, Any], read_files: bool = True) -> str:
    return prepare_prompt(workflow, answers, read_files)[0]
