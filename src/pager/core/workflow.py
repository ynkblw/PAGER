"""Walkthrough-Modell: Fragebogen (Schritte mit Fragen) + Prompt-Vorlage.

Walkthroughs werden als YAML gespeichert und im eingebauten Editor gepflegt.
Unbekannte Felder (z. B. aus älteren Versionen) werden beim Laden ignoriert.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

QuestionType = Literal[
    "text", "textarea", "number", "integer", "choice", "multichoice",
    "bool", "scale", "date", "file", "files",
]


class Condition(BaseModel):
    """Bedingung für ``show_if``. Kurzform in YAML: ``show_if: frage_id`` (= wahr/ausgefüllt)."""

    model_config = ConfigDict(populate_by_name=True)

    question: str
    equals: Any = None
    not_equals: Any = None
    in_: list[Any] | None = Field(default=None, alias="in")

    @model_validator(mode="before")
    @classmethod
    def _shorthand(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"question": data}
        return data

    def evaluate(self, answers: dict[str, Any]) -> bool:
        value = answers.get(self.question)
        if self.equals is not None:
            return value == self.equals
        if self.not_equals is not None:
            return value != self.not_equals
        if self.in_ is not None:
            if isinstance(value, list):
                return any(v in self.in_ for v in value)
            return value in self.in_
        if isinstance(value, bool):
            return value
        return is_filled(value)


class Option(BaseModel):
    value: str
    label: str
    description: str = ""

    @model_validator(mode="before")
    @classmethod
    def _shorthand(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"value": data, "label": data}
        if isinstance(data, dict) and "label" not in data and "value" in data:
            return {**data, "label": str(data["value"])}
        return data


class Question(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    label: str
    type: QuestionType = "text"
    required: bool = False
    help: str = ""
    placeholder: str = ""
    default: Any = None
    options: list[Option] = Field(default_factory=list)
    min: float | None = None
    max: float | None = None
    unit: str = ""
    file_filter: str = Field(default="Alle Dateien (*)", alias="filter")
    scale_labels: list[str] = Field(default_factory=list)
    show_if: Condition | None = None

    @model_validator(mode="after")
    def _check(self) -> "Question":
        if self.type in ("choice", "multichoice") and not self.options:
            raise ValueError(f"Frage '{self.id}': Typ '{self.type}' benötigt 'options'.")
        if self.type == "scale":
            self.min = 1 if self.min is None else self.min
            self.max = 5 if self.max is None else self.max
        return self

    def is_visible(self, answers: dict[str, Any]) -> bool:
        return self.show_if is None or self.show_if.evaluate(answers)

    def option_label(self, value: Any) -> str:
        for opt in self.options:
            if opt.value == value:
                return opt.label
        return str(value)

    # ---- Validierung & Anzeige ---------------------------------------
    def validate_answer(self, value: Any) -> str | None:
        """Gibt eine Fehlermeldung zurück oder ``None``, wenn die Antwort gültig ist."""
        if not is_filled(value):
            return "Bitte ausfüllen." if self.required else None
        if self.type in ("number", "integer", "scale"):
            try:
                num = parse_number(value)
            except ValueError:
                return "Bitte eine gültige Zahl eingeben."
            if self.type == "integer" and num != int(num):
                return "Bitte eine ganze Zahl eingeben."
            if self.min is not None and num < self.min:
                return f"Der Wert muss mindestens {fmt_number(self.min)} sein."
            if self.max is not None and num > self.max:
                return f"Der Wert darf höchstens {fmt_number(self.max)} sein."
        if self.type == "choice" and value not in {o.value for o in self.options}:
            return "Bitte eine der Optionen wählen."
        if self.type in ("file", "files"):
            missing = [p for p in as_list(value) if not Path(p).is_file()]
            if missing:
                return "Datei nicht gefunden: " + ", ".join(Path(p).name for p in missing)
        return None

    def display(self, value: Any) -> str:
        """Menschlich lesbare Darstellung einer Antwort (für Zusammenfassung und Prompt)."""
        if not is_filled(value):
            return "–"
        match self.type:
            case "bool":
                return "Ja" if value else "Nein"
            case "choice":
                return self.option_label(value)
            case "multichoice":
                return ", ".join(self.option_label(v) for v in value)
            case "number" | "integer" | "scale":
                text = fmt_number(parse_number(value))
                if self.type == "scale" and self.scale_labels:
                    text += f" (Skala {fmt_number(self.min)}–{fmt_number(self.max)})"
                return f"{text} {self.unit}".strip()
            case "file" | "files":
                return ", ".join(Path(p).name for p in as_list(value))
            case "date":
                return value.strftime("%d.%m.%Y") if isinstance(value, dt.date) else str(value)
        return str(value)


StepKind = Literal["questions", "agent"]


class AgentStepConfig(BaseModel):
    """Einstellungen eines Agent-Fensters: direkter Dialog mit dem KI-Agenten (roher Output + Eingabe)."""

    prompt: str = ""  # Startnachricht (Jinja2, kennt alle bisherigen Antworten); leer = Nutzer beginnt
    auto_start: bool = True  # Startnachricht beim Öffnen automatisch senden
    require_reply: bool = True  # „Weiter“ erst, wenn der Agent mindestens einmal geantwortet hat
    placeholder: str = ""  # Hinweis im Eingabefeld

    # Anzeige: welche Bestandteile des Fensters sichtbar sind
    show_status: bool = True  # Statuszeile (Verbindung, Agent, Abbrechen)
    show_controls: bool = True  # Auswahl von Modus und Modell (falls der Agent sie anbietet)
    show_thoughts: bool = True  # Gedanken des Agenten
    show_tools: bool = True  # Werkzeugaufrufe und Freigabe-Hinweise im Verlauf
    show_plan: bool = True  # Plan des Agenten
    show_system: bool = True  # Systemhinweise („Verbunden“, „Ende der Antwort“)
    show_input: bool = True  # Eingabefeld für eigene Nachrichten
    raw_toggle: bool = False  # Knopf „Rohdaten“ (ACP/JSON-RPC) anbieten
    show_raw: bool = False  # Rohdaten von Anfang an einblenden


class Step(BaseModel):
    id: str
    title: str
    description: str = ""
    kind: StepKind = "questions"
    questions: list[Question] = Field(default_factory=list)
    agent: AgentStepConfig | None = None
    show_if: Condition | None = None

    @model_validator(mode="after")
    def _check(self) -> "Step":
        if self.kind == "agent":
            if self.questions:
                raise ValueError(f"Schritt '{self.id}': Ein Agent-Fenster enthält keine Fragen.")
            self.agent = self.agent or AgentStepConfig()
        return self

    @property
    def is_agent(self) -> bool:
        return self.kind == "agent"

    def is_visible(self, answers: dict[str, Any]) -> bool:
        return self.show_if is None or self.show_if.evaluate(answers)


def agent_display(value: Any) -> str:
    """Kurzfassung des Ergebnisses eines Agent-Fensters (für Überprüfung und Tabelle)."""
    if not isinstance(value, dict) or not value.get("nachrichten"):
        return "–"
    reply = " ".join(str(value.get("antwort", "")).split())
    n = value["nachrichten"]
    return f"{n} Antwort{'en' if n != 1 else ''} · „{reply[:160]}{'…' if len(reply) > 160 else ''}“"


class Workflow(BaseModel):
    id: str
    name: str
    description: str = ""
    category: str = "Allgemein"
    order: int = 100  # Sortierung auf der Startseite (kleiner = weiter vorne)
    icon: str = "🤖"
    color: str = "#3A6FF8"
    background: str = ""  # Hintergrundfarbe der Kachel auf der Startseite, leer = weiß
    version: str = "0.1"
    owner: str = ""
    steps: list[Step]
    prompt: str = ""
    output_name: str = "ergebnis.md"

    source: Path | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def _check(self) -> "Workflow":
        seen: set[str] = set()
        for step in self.steps:
            if step.show_if and step.show_if.question not in seen:
                raise ValueError(
                    f"Schritt '{step.id}': show_if verweist auf unbekannte/spätere Frage "
                    f"'{step.show_if.question}'."
                )
            if step.is_agent:
                # Das Ergebnis eines Agent-Fensters steht unter der Schritt-ID in den Antworten
                if step.id in seen:
                    raise ValueError(f"Agent-Fenster '{step.id}': ID ist bereits als Frage-ID vergeben.")
                seen.add(step.id)
            for q in step.questions:
                if q.id in seen:
                    raise ValueError(f"Frage-ID '{q.id}' ist doppelt vergeben.")
                if q.show_if and q.show_if.question not in seen:
                    raise ValueError(
                        f"Frage '{q.id}': show_if verweist auf unbekannte/spätere Frage "
                        f"'{q.show_if.question}'."
                    )
                seen.add(q.id)
        return self

    @property
    def questions(self) -> list[Question]:
        return [q for s in self.steps for q in s.questions]

    @property
    def agent_steps(self) -> list[Step]:
        return [s for s in self.steps if s.is_agent]

    @property
    def has_agent_steps(self) -> bool:
        return any(s.is_agent for s in self.steps)

    def question(self, qid: str) -> Question:
        for q in self.questions:
            if q.id == qid:
                return q
        raise KeyError(qid)

    def visible_steps(self, answers: dict[str, Any]) -> list[Step]:
        return [s for s in self.steps if s.is_visible(answers)]

    def visible_answers(self, answers: dict[str, Any]) -> dict[str, Any]:
        """Nur Antworten auf Fragen, die beim aktuellen Stand sichtbar sind."""
        result = {}
        for step in self.visible_steps(answers):
            if step.is_agent and step.id in answers:
                result[step.id] = answers[step.id]
            for q in step.questions:
                if q.is_visible(answers) and q.id in answers:
                    result[q.id] = answers[q.id]
        return result

    def default_answers(self) -> dict[str, Any]:
        return {q.id: q.default for q in self.questions if q.default is not None}


# ---------------------------------------------------------------------------
# Laden


class WorkflowLoadError(Exception):
    def __init__(self, path: Path, message: str):
        super().__init__(f"{path.name}: {message}")
        self.path = path
        self.message = message


def load_workflow(path: Path) -> Workflow:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise WorkflowLoadError(path, f"Datei nicht lesbar: {exc}") from exc
    if not isinstance(data, dict):
        raise WorkflowLoadError(path, "Erwartet ein YAML-Objekt auf oberster Ebene.")
    try:
        wf = Workflow.model_validate(data)
    except ValidationError as exc:
        msgs = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'workflow'}: {e['msg']}" for e in exc.errors()
        )
        raise WorkflowLoadError(path, msgs) from exc
    wf.source = path
    return wf


def load_workflows(dirs: list[Path]) -> tuple[list[Workflow], list[WorkflowLoadError]]:
    """Lädt alle ``*.yaml``/``*.yml`` aus den Ordnern. Spätere Ordner überschreiben gleiche IDs."""
    by_id: dict[str, Workflow] = {}
    errors: list[WorkflowLoadError] = []
    for directory in dirs:
        if not directory.is_dir():
            continue
        for path in sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")]):
            try:
                wf = load_workflow(path)
            except WorkflowLoadError as exc:
                errors.append(exc)
                continue
            by_id[wf.id] = wf
    workflows = sorted(by_id.values(), key=lambda w: (w.order, w.category.lower(), w.name.lower()))
    return workflows, errors


# ---------------------------------------------------------------------------
# Hilfsfunktionen


def is_filled(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return True
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def parse_number(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("bool ist keine Zahl")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", "").replace(" ", "")
    if "," in text:  # deutsches Format: 1.234,56
        text = text.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", text):  # 10.000 → Tausenderpunkte
        text = text.replace(".", "")
    return float(text)


def fmt_number(value: float) -> str:
    if value == int(value):
        return f"{int(value):,}".replace(",", ".")
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
