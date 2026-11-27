"""Backend „KI-Agent über ACP“: sendet den fertigen Auftrag an Claude Code / Copilot CLI per ACP.

Gab es im Walkthrough bereits ein Agent-Fenster, wird dessen Sitzung weiterverwendet. Der Agent
kennt dann das bisherige Gespräch. Sonst startet das Backend eine eigene Sitzung im Arbeitsordner
des Laufs und beendet sie danach wieder.
"""

from __future__ import annotations

import queue
import time
from collections.abc import Callable, Iterator

from pager.agent.events import (
    AgentEvent, Closed, Connected, Failed, PermissionRequest, QuestionRequest, TextChunk, TurnEnded,
    TurnFailed,
)
from pager.agent.profiles import AgentSetupError, build_launch, create_session, run_checks
from pager.agent.session import AgentSession
from pager.config import Settings
from pager.copilot.base import Backend, BackendError, BackendStatus, RunCancelled, RunHandle, RunRequest

_STOP = object()


class AcpBackend(Backend):
    key = "acp"

    def __init__(self, settings: Settings, session: AgentSession | None = None):
        self.settings = settings
        self.session = session

    def check(self) -> BackendStatus:
        try:
            build_launch(self.settings)
        except AgentSetupError as exc:
            return BackendStatus(False, str(exc))
        problems = [c for c in run_checks(self.settings) if c.state == "error"]
        if problems:
            return BackendStatus(False, "\n".join(f"{c.title}: {c.detail}" for c in problems))
        return BackendStatus(True, "Einrichtung in Ordnung.")

    def start(self, request: RunRequest) -> "AgentRunHandle":
        prompt = (request.workspace / request.prompt_file).read_text(encoding="utf-8")
        session, owned = self.session, False
        if session is None or session.closed:
            try:
                session = create_session(self.settings, request.workspace)
            except AgentSetupError as exc:
                raise BackendError(str(exc)) from exc
            owned = True
        return AgentRunHandle(session, prompt, max(1, self.settings.timeout_minutes) * 60, owned)


class AgentRunHandle(RunHandle):
    def __init__(self, session: AgentSession, prompt: str, timeout_s: float, owned: bool):
        self.session, self.prompt, self.timeout_s, self.owned = session, prompt, timeout_s, owned
        self.permission_handler: Callable[[PermissionRequest], None] | None = None
        self._queue: queue.Queue = queue.Queue()
        self._cancelled = False

    def stream(self) -> Iterator[str]:
        unsubscribe = self.session.subscribe(self._queue.put)
        deadline = time.monotonic() + self.timeout_s
        try:
            if not self.session.connected:
                if self.session.closed:
                    raise BackendError("Die Agent-Sitzung ist beendet.")
                self.session.start()  # kehrt sofort zurück, wenn schon gestartet
                self._wait_connected(deadline)
            if self._cancelled:
                raise RunCancelled()
            while self.session.busy:  # z. B. Agent-Fenster antwortet noch
                self._next(deadline)
            self.session.prompt(self.prompt)
            while True:
                event = self._next(deadline)
                if isinstance(event, TextChunk) and event.role == "agent":
                    yield event.text
                elif isinstance(event, (PermissionRequest, QuestionRequest)):
                    if self.permission_handler:
                        self.permission_handler(event)  # Oberfläche zeigt Freigabe bzw. Rückfrage
                    elif isinstance(event, QuestionRequest):
                        event.decline()
                    else:
                        event.reject()
                elif isinstance(event, TurnEnded):
                    if event.stop_reason == "cancelled" or self._cancelled:
                        raise RunCancelled()
                    if event.stop_reason == "refusal":
                        raise BackendError("Der Agent hat die Bearbeitung abgelehnt.")
                    return
                elif isinstance(event, TurnFailed):
                    raise BackendError(f"Der Agent meldet einen Fehler:\n\n{event.message}")
                elif isinstance(event, Failed):
                    raise BackendError(event.message)
                elif isinstance(event, Closed):
                    if self._cancelled:
                        raise RunCancelled()
                    raise BackendError("Die Verbindung zum Agenten wurde beendet.")
        finally:
            unsubscribe()
            if self.owned:
                self.session.close()

    def _wait_connected(self, deadline: float) -> None:
        while True:
            event = self._next(deadline)
            if isinstance(event, Connected):
                return
            if isinstance(event, Failed):
                raise BackendError(event.message)

    def _next(self, deadline: float) -> AgentEvent:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            self.session.cancel()
            raise BackendError(f"Zeitüberschreitung nach {round(self.timeout_s / 60)} Minuten.")
        try:
            event = self._queue.get(timeout=min(remaining, 1.0))
        except queue.Empty:
            return self._next(deadline)
        if event is _STOP:
            raise RunCancelled()
        return event

    def cancel(self) -> None:
        self._cancelled = True
        if self.session.busy:
            self.session.cancel()  # Agent meldet TurnEnded(cancelled)
        else:
            self._queue.put(_STOP)
