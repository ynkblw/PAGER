"""Demo-Backend: simuliert Copilot, damit die Oberfläche ohne Copilot erprobt werden kann."""

from __future__ import annotations

import random
import re
import threading
from collections.abc import Iterator

from pager.copilot.base import Backend, BackendStatus, RunCancelled, RunHandle, RunRequest


class DemoBackend(Backend):
    key = "demo"

    def check(self) -> BackendStatus:
        return BackendStatus(True, "Demo-Modus – es wird kein Copilot aufgerufen")

    def start(self, request: RunRequest) -> RunHandle:
        return _DemoRun(_report(request))


class _DemoRun(RunHandle):
    def __init__(self, text: str):
        self.text = text
        self._cancel = threading.Event()

    def stream(self) -> Iterator[str]:
        if self._cancel.wait(1.2):  # "Copilot denkt nach"
            raise RunCancelled()
        for token in re.findall(r"\S+\s*|\s+", self.text):
            if self._cancel.wait(random.uniform(0.004, 0.02)):
                raise RunCancelled()
            yield token

    def cancel(self) -> None:
        self._cancel.set()


def _report(request: RunRequest) -> str:
    rows = "\n".join(f"| {q} | {a.replace('|', '/')} |" for q, a in request.display)
    filled = sum(1 for _, a in request.display if a != "–")
    return f"""\
> ⚠️ **Demo-Ergebnis** – erzeugt ohne Copilot. Hier erscheint später die Antwort von Copilot.

# {request.workflow_name}

## Zusammenfassung
Copilot hätte den Prompt aus `{request.prompt_file}` mit **{filled} ausgefüllten Angaben**
bearbeitet.

## Übergebene Angaben

| Angabe | Wert |
|---|---|
{rows}

## So geht es weiter
1. In den **Einstellungen** die Übergabe auf **Copilot Chat** umstellen.
2. GHE-Adresse eintragen.
3. Walkthrough erneut starten – PAGER kopiert den Prompt und öffnet Copilot Chat.
"""
