"""Arbeitsordner pro Lauf: Eingaben, Auftrag, Ergebnis und Metadaten.

Jeder Lauf ist nachvollziehbar abgelegt (z. B. für Rückfragen oder Audits)::

    runs/2026-11-25_093000_mein-walkthrough/
        AUFTRAG.md            – vollständiger Prompt (inkl. eingebetteter Dateiinhalte)
        eingaben/<frage>/…    – kopierte Unterlagen
        ergebnis.md           – Antwort von Copilot
        meta.json             – Workflow, Antworten, Status
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pager.core.prompting import LONG_PROMPT_CHARS, Attachment, file_targets
from pager.core.workflow import Workflow

PROMPT_FILE = "AUFTRAG.md"
META_FILE = "meta.json"


def _json_default(value: Any) -> Any:
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(type(value))


@dataclass
class RunWorkspace:
    root: Path

    @classmethod
    def create(cls, runs_dir: Path, workflow: Workflow) -> "RunWorkspace":
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        root = runs_dir / f"{stamp}_{workflow.id}"
        n = 2
        while root.exists():
            root = runs_dir / f"{stamp}_{workflow.id}_{n}"
            n += 1
        root.mkdir(parents=True)
        return cls(root)

    def prepare(self, workflow: Workflow, answers: dict[str, Any], prompt: str,
                attachments: list[Attachment]) -> list[str]:
        """Kopiert die Unterlagen (Nachvollziehbarkeit), schreibt den Prompt, liefert Hinweise."""
        for targets in file_targets(workflow, workflow.visible_answers(answers)).values():
            for src, rel in targets:
                dest = self.root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
        (self.root / PROMPT_FILE).write_text(prompt, encoding="utf-8")
        return attachment_warnings(attachments, len(prompt))

    # ---- Metadaten ---------------------------------------------------
    def write_meta(self, **values: Any) -> None:
        meta = self.read_meta()
        meta.update(values)
        (self.root / META_FILE).write_text(
            json.dumps(meta, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8"
        )

    def read_meta(self) -> dict[str, Any]:
        path = self.root / META_FILE
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def write_result(self, text: str, filename: str) -> Path:
        path = self.root / filename
        path.write_text(text, encoding="utf-8")
        return path

    def read_result(self) -> str:
        name = self.read_meta().get("result_file")
        if name and (self.root / name).exists():
            return (self.root / name).read_text(encoding="utf-8")
        return ""


def attachment_warnings(attachments: list[Attachment], prompt_chars: int) -> list[str]:
    warnings = []
    for a in attachments:
        if a.error:
            warnings.append(f"{a.name}: Inhalt konnte nicht gelesen werden ({a.error}) – bitte im Chat anhängen.")
        elif not a.embedded:
            warnings.append(f"{a.name}: kein Textformat – bitte im Chat manuell anhängen.")
        elif a.truncated:
            warnings.append(f"{a.name}: sehr lang, nur der Anfang wurde eingebettet.")
    if prompt_chars > LONG_PROMPT_CHARS:
        warnings.append(f"Der Prompt ist sehr lang ({prompt_chars:,} Zeichen). Copilot Chat kürzt evtl. – "
                        "ggf. weniger oder kleinere Dateien verwenden.".replace(",", "."))
    return warnings


def list_runs(runs_dir: Path) -> list[tuple[RunWorkspace, dict[str, Any]]]:
    if not runs_dir.is_dir():
        return []
    runs = []
    for folder in sorted(runs_dir.iterdir(), reverse=True):
        if folder.is_dir() and (folder / META_FILE).exists():
            ws = RunWorkspace(folder)
            runs.append((ws, ws.read_meta()))
    return runs
