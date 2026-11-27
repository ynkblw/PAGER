"""Gemeinsame Schnittstelle für automatische Backends (KI-Agent über ACP, Copilot CLI, Demo).

Der Standard-Modus „Copilot Chat“ ist bewusst kein Backend: Dort übergibt der Nutzer den
Prompt selbst per Zwischenablage (siehe ``ui/pages/handoff.py``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class BackendError(Exception):
    """Fehler beim Ausführen (Meldung wird dem Nutzer angezeigt)."""


class RunCancelled(Exception):
    pass


@dataclass
class RunRequest:
    workspace: Path
    prompt_file: str
    workflow_id: str
    workflow_name: str
    answers: dict[str, Any] = field(default_factory=dict)
    display: list[tuple[str, str]] = field(default_factory=list)  # (Frage, Antwort) für Demo


@dataclass
class BackendStatus:
    ok: bool
    message: str


class RunHandle(ABC):
    #: Wird bei Freigabe-Anfragen des Agenten aufgerufen (aus dem Hintergrund-Thread). Ohne
    #: Handler lehnt das ACP-Backend Werkzeuge ab.
    permission_handler = None

    @abstractmethod
    def stream(self) -> Iterator[str]:
        """Liefert die Antwort in Text-Stücken. Wirft ``BackendError`` oder ``RunCancelled``."""

    @abstractmethod
    def cancel(self) -> None:
        """Bricht den Lauf ab (thread-sicher)."""


class Backend(ABC):
    key: str = ""

    @abstractmethod
    def check(self) -> BackendStatus: ...

    @abstractmethod
    def start(self, request: RunRequest) -> RunHandle: ...
