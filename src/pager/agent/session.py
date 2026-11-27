"""Gemeinsame Schnittstelle einer laufenden Agent-Sitzung (ACP oder Demo).

Alle Methoden kehren sofort zurück; Ergebnisse kommen als Ereignisse (``pager.agent.events``) an
die Abonnenten. So kann die Oberfläche eine Sitzung über mehrere Schritte eines Walkthroughs
hinweg weiterverwenden (z. B. Agent-Fenster → abschließender Auftrag).
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Callable

from pager.agent.events import (
    AgentEvent, Closed, Connected, Failed, Listener, TextChunk, TurnEnded, TurnFailed, TurnStarted,
)


class AgentSession(ABC):
    name: str = "Agent"

    def __init__(self) -> None:
        self._listeners: list[Listener] = []
        self._listeners_lock = threading.Lock()
        self.info: Connected | None = None
        self.connected = False
        self.busy = False
        self.closed = False
        self.started = False
        self.error: str = ""

    # ---- Abonnenten ----------------------------------------------------
    def subscribe(self, listener: Listener) -> Callable[[], None]:
        with self._listeners_lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._listeners_lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def _emit(self, event: AgentEvent) -> None:
        if isinstance(event, Connected):
            self.info, self.connected = event, True
        elif isinstance(event, TurnStarted):
            self.busy = True
        elif isinstance(event, (TurnEnded, TurnFailed)):
            self.busy = False
        elif isinstance(event, Failed):
            self.connected, self.busy, self.error = False, False, event.message
        elif isinstance(event, Closed):
            self.connected, self.busy, self.closed = False, False, True
        with self._listeners_lock:
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(event)
            except Exception:  # noqa: BLE001 – ein fehlerhafter Abonnent darf die Sitzung nicht stoppen
                pass

    @property
    def ready(self) -> bool:
        return self.connected and not self.busy and not self.closed

    # ---- Steuerung -----------------------------------------------------
    @abstractmethod
    def start(self) -> None:
        """Verbindet im Hintergrund. Meldet ``Connected`` oder ``Failed``."""

    @abstractmethod
    def prompt(self, text: str) -> None:
        """Sendet eine Nachricht. Meldet ``TurnStarted``, Text-/Werkzeug-Ereignisse und ``TurnEnded``."""

    @abstractmethod
    def cancel(self) -> None:
        """Bricht die laufende Antwort ab (die Sitzung bleibt bestehen)."""

    @abstractmethod
    def close(self) -> None:
        """Beendet die Sitzung und den Agent-Prozess."""

    def set_mode(self, mode_id: str) -> None:  # optional
        pass

    def set_config_option(self, config_id: str, value: str) -> None:  # optional
        pass


class Transcript:
    """Sammelt einen Gesprächsverlauf aus Ereignissen (für Ergebnis und Prompt-Vorlage)."""

    def __init__(self) -> None:
        self.turns: list[tuple[str, str]] = []  # (rolle, text) – rolle: "du" | "agent"
        self._current: list[str] | None = None

    def feed(self, event: AgentEvent) -> None:
        if isinstance(event, TurnStarted):
            self.turns.append(("du", event.text))
            self._current = []
            self.turns.append(("agent", ""))
        elif isinstance(event, TextChunk) and event.role == "agent" and self._current is not None:
            self._current.append(event.text)
            self.turns[-1] = ("agent", "".join(self._current))
        elif isinstance(event, (TurnEnded, TurnFailed, Failed, Closed)):
            if self._current is not None and not self.turns[-1][1].strip():
                self.turns.pop()  # leere Antwort nicht speichern
            self._current = None

    @property
    def last_reply(self) -> str:
        for role, text in reversed(self.turns):
            if role == "agent" and text.strip():
                return text.strip()
        return ""

    @property
    def replies(self) -> int:
        return sum(1 for role, text in self.turns if role == "agent" and text.strip())

    def markdown(self) -> str:
        parts = []
        for role, text in self.turns:
            who = "**Du:**" if role == "du" else "**Agent:**"
            parts.append(f"{who}\n\n{text.strip()}")
        return "\n\n---\n\n".join(parts)

    def as_answer(self) -> dict[str, object]:
        return {"antwort": self.last_reply, "verlauf": self.markdown(), "nachrichten": self.replies}
