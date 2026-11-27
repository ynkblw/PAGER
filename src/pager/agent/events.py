"""Ereignisse einer Agent-Sitzung und Regeln für Werkzeug-Freigaben.

Eine Sitzung (ACP oder Demo) meldet alles, was passiert, als Ereignis an ihre Abonnenten. Die
Ereignisse kommen aus einem Hintergrund-Thread; die Oberfläche reicht sie per Qt-Signal in den
UI-Thread weiter (siehe ``ui/widgets/agent_console.py``).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentEvent:
    pass


@dataclass
class Raw(AgentEvent):
    """Eine Zeile des Protokolls: ``direction`` ist ``out`` (PAGER → Agent), ``in`` oder ``stderr``."""

    direction: str
    text: str


@dataclass
class Status(AgentEvent):
    text: str


@dataclass
class Connected(AgentEvent):
    agent_name: str
    agent_version: str = ""
    session_id: str = ""
    modes: list[dict[str, Any]] = field(default_factory=list)  # [{id, name, description}]
    current_mode: str = ""
    config_options: list[dict[str, Any]] = field(default_factory=list)  # ACP configOptions
    auth_methods: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TextChunk(AgentEvent):
    """Text eines Beitrags. ``role``: ``agent`` (Antwort), ``thought`` (Gedanken) oder ``user``."""

    text: str
    role: str = "agent"


@dataclass
class ToolCall(AgentEvent):
    tool_call_id: str
    title: str = ""
    kind: str = ""
    status: str = ""  # pending | in_progress | completed | failed
    detail: str = ""
    update: bool = False  # True = Aktualisierung eines bereits gemeldeten Aufrufs


@dataclass
class Plan(AgentEvent):
    entries: list[dict[str, Any]]


@dataclass
class ModeChanged(AgentEvent):
    mode_id: str


@dataclass
class ConfigChanged(AgentEvent):
    config_options: list[dict[str, Any]]


@dataclass
class TurnStarted(AgentEvent):
    text: str


@dataclass
class TurnEnded(AgentEvent):
    stop_reason: str  # end_turn | max_tokens | max_turn_requests | refusal | cancelled


@dataclass
class AutoDecision(AgentEvent):
    """Eine Freigabe wurde nach der eingestellten Regel automatisch entschieden."""

    title: str
    allowed: bool


@dataclass
class Failed(AgentEvent):
    """Schwerer Fehler: Verbindung nicht möglich oder Agent beendet."""

    message: str
    login_required: bool = False


@dataclass
class TurnFailed(AgentEvent):
    """Ein einzelner Auftrag ist fehlgeschlagen, die Sitzung bleibt nutzbar."""

    message: str


@dataclass
class Closed(AgentEvent):
    pass


class PermissionRequest(AgentEvent):
    """Der Agent bittet um Freigabe eines Werkzeugs. ``respond`` ist thread-sicher und einmalig."""

    def __init__(self, title: str, kind: str, options: list[dict[str, Any]], detail: str,
                 responder: Callable[[str | None], None]):
        self.title = title or "Werkzeug"
        self.kind = kind or ""
        self.options = options  # [{optionId, name, kind: allow_once|allow_always|reject_once|reject_always}]
        self.detail = detail
        self._responder = responder
        self._lock = threading.Lock()
        self.answered: str | None | bool = False  # False = offen, None = abgelehnt/abgebrochen

    def respond(self, option_id: str | None) -> None:
        with self._lock:
            if self.answered is not False:
                return
            self.answered = option_id
        self._responder(option_id)

    def option(self, *kinds: str) -> str | None:
        for kind in kinds:
            for opt in self.options:
                if opt.get("kind") == kind:
                    return opt.get("optionId")
        return None

    def allow(self) -> None:
        self.respond(self.option("allow_once", "allow_always"))

    def reject(self) -> None:
        opt = self.option("reject_once", "reject_always")
        self.respond(opt)  # ohne Ablehnungs-Option → abgebrochen

    def __repr__(self) -> str:
        return f"PermissionRequest({self.title!r}, {self.kind!r})"


class QuestionRequest(AgentEvent):
    """Rückfrage des Agenten als Formular (ACP ``elicitation/create``, Modus „form“).

    Claude Code stellt so z. B. Auswahlfragen (``AskUserQuestion``), die Copilot CLI ihre
    ``ask_user``-Fragen. ``schema`` ist ein JSON-Schema mit einfachen Feldern (Text, Zahl, Ja/Nein,
    Auswahl per ``oneOf``/``enum``, Mehrfachauswahl per ``items.anyOf``).
    """

    def __init__(self, message: str, schema: dict[str, Any], responder: Callable[[str, dict | None], None]):
        self.message = message or "Der Agent hat eine Rückfrage."
        self.schema = schema or {"type": "object", "properties": {}}
        self._responder = responder
        self._lock = threading.Lock()
        self.answered = False

    def respond(self, action: str, content: dict[str, Any] | None = None) -> None:
        """``action``: ``accept`` (mit ``content``), ``decline`` (übersprungen) oder ``cancel``."""
        with self._lock:
            if self.answered:
                return
            self.answered = True
        self._responder(action, content if action == "accept" else None)

    def accept(self, content: dict[str, Any]) -> None:
        self.respond("accept", content)

    def decline(self) -> None:
        self.respond("decline")

    def cancel(self) -> None:
        self.respond("cancel")

    def __repr__(self) -> str:
        return f"QuestionRequest({self.message!r})"


@dataclass
class QuestionAnswered(AgentEvent):
    """Zusammenfassung einer beantworteten Rückfrage (für die Anzeige)."""

    message: str
    summary: str


def question_fields(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Felder eines Rückfrage-Schemas in lesbarer Form.

    Jedes Feld: ``{key, kind (choice|multi|text|number|integer|bool), title, description, options:
    [(wert, titel, beschreibung)], required, custom_for}``. ``custom_for`` verweist bei Claude Codes
    „Other“-Feldern (``question_<n>_custom``) auf die zugehörige Auswahl.
    """
    required = set(schema.get("required") or [])
    fields = []
    for key, prop in (schema.get("properties") or {}).items():
        if not isinstance(prop, dict):
            continue
        kind, options = "text", []
        if prop.get("type") == "array":
            items = prop.get("items") or {}
            options = _options(items.get("anyOf") or items.get("oneOf"), items.get("enum"))
            kind = "multi"
        elif prop.get("oneOf") or prop.get("enum"):
            options = _options(prop.get("oneOf"), prop.get("enum"))
            kind = "choice"
        elif prop.get("type") in ("number", "integer", "boolean"):
            kind = {"boolean": "bool"}.get(prop["type"], prop["type"])
        custom_for = ""
        if key.endswith("_custom") and key[: -len("_custom")] in (schema.get("properties") or {}):
            custom_for = key[: -len("_custom")]
        fields.append({
            "key": key, "kind": kind, "title": prop.get("title") or "", "options": options,
            "description": prop.get("description") or "", "required": key in required,
            "custom_for": custom_for, "default": prop.get("default"),
        })
    return fields


def _options(one_of: Any, enum: Any) -> list[tuple[Any, str, str]]:
    if isinstance(one_of, list) and one_of:
        return [(o.get("const"), str(o.get("title") or o.get("const")), o.get("description") or "")
                for o in one_of if isinstance(o, dict)]
    if isinstance(enum, list):
        return [(v, str(v), "") for v in enum]
    return []


# ---------------------------------------------------------------------------
# Regeln für Freigaben

PERMISSION_POLICIES: dict[str, str] = {
    "ask": "Jedes Mal nachfragen",
    "read": "Lesen automatisch erlauben, sonst nachfragen",
    "deny": "Alle Werkzeuge ablehnen (nur Text)",
    "allow": "Alles automatisch erlauben (Vorsicht)",
}

READ_ONLY_KINDS = {"read", "search", "think", "fetch"}

ASK = "ask"


def auto_decision(policy: str, request: PermissionRequest) -> str | None:
    """``"allow"``/``"reject"`` für automatische Entscheidungen, sonst ``ASK``."""
    if policy == "allow":
        return "allow"
    if policy == "deny":
        return "reject"
    if policy == "read" and request.kind in READ_ONLY_KINDS:
        return "allow"
    return ASK


Listener = Callable[[AgentEvent], None]
