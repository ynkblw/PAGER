"""Logik für den Walkthrough-Editor (UI-unabhängig, testbar).

Der Editor arbeitet auf einem *Entwurf*: einem einfachen dict im selben Aufbau wie die YAML-Datei.
So bleiben auch halbfertige, noch ungültige Zustände bearbeitbar. Erst beim Speichern/Testen
wird der Entwurf gegen das ``Workflow``-Modell validiert.
"""

from __future__ import annotations

import copy
import datetime as dt
import re
import unicodedata
from pathlib import Path
from typing import Any

import yaml
from jinja2 import TemplateSyntaxError
from pydantic import ValidationError

from pager.core.prompting import DEFAULT_PROMPT, _env, render_prompt
from pager.core.workflow import Workflow, parse_number  # noqa: F401 – vom Editor genutzt

# Fragetyp → (Symbol, Name, Beschreibung)
QUESTION_TYPES: dict[str, tuple[str, str, str]] = {
    "text": ("✏️", "Kurzer Text", "Einzeilige Eingabe, z. B. Name oder Nummer"),
    "textarea": ("📝", "Langer Text", "Mehrzeilige Eingabe für Beschreibungen"),
    "integer": ("🔢", "Ganze Zahl", "z. B. Stückzahl oder Anzahl"),
    "number": ("➗", "Dezimalzahl", "z. B. Preis oder Gewicht, mit Einheit"),
    "choice": ("🔘", "Einfachauswahl", "Genau eine Option aus einer Liste"),
    "multichoice": ("☑️", "Mehrfachauswahl", "Beliebig viele Optionen ankreuzen"),
    "bool": ("🌓", "Ja / Nein", "Einfache Entscheidungsfrage"),
    "scale": ("📶", "Skala", "Bewertung, z. B. von 1 bis 5"),
    "date": ("📅", "Datum", "Auswahl über einen Kalender"),
    "file": ("📄", "Eine Datei", "Upload per Drag & Drop"),
    "files": ("🗂️", "Mehrere Dateien", "Upload per Drag & Drop"),
}

OPTION_TYPES = {"choice", "multichoice"}
RANGE_TYPES = {"number", "integer", "scale"}
UNIT_TYPES = {"number", "integer"}
PLACEHOLDER_TYPES = {"text", "textarea", "number", "integer"}
FILE_TYPES = {"file", "files"}

FILE_FILTER_PRESETS: dict[str, str] = {
    "Alle Dateien": "Alle Dateien (*)",
    "PDF": "PDF (*.pdf);;Alle Dateien (*)",
    "Excel / CSV": "Tabellen (*.xlsx *.xlsm *.csv);;Alle Dateien (*)",
    "Word / Text": "Dokumente (*.docx *.txt *.md);;Alle Dateien (*)",
    "Bilder": "Bilder (*.png *.jpg *.jpeg *.bmp);;Alle Dateien (*)",
    "CAD": "CAD (*.step *.stp *.dxf *.dwg *.igs *.iges);;Alle Dateien (*)",
    "E-Mails": "E-Mails (*.msg *.eml);;Alle Dateien (*)",
}

ICONS = [
    "🤖", "✨", "🧭", "⚖️", "📊", "📈", "📉", "💶", "🧮", "📋", "📝", "📄", "🗂️", "📦", "🏭", "🔧",
    "⚙️", "🛠️", "🔩", "🧪", "🔬", "🧰", "🚚", "🌍", "🔍", "✅", "⚠️", "🛡️", "💡", "🎯", "🗓️", "👥",
    "💬", "📣", "🧑‍💻", "🖥️", "🔐", "♻️", "🌱", "⭐",
]

COLORS = ["#3A6FF8", "#1F9D6B", "#8A5CF6", "#E8590C", "#D14343", "#0E9AA7", "#B7791F", "#475569"]
# Helle Hintergründe für Kacheln (gut lesbar mit dunkler Schrift); "" = weiß
BACKGROUNDS = ["", "#EAF0FF", "#E3F5EC", "#EFE9FE", "#FDEDE2", "#FCE8E8", "#E1F4F5", "#FBF1DC", "#ECEFF4"]

WORKFLOW_KEYS = ["id", "name", "description", "category", "order", "icon", "color", "background", "version", "owner",
                 "output_name", "steps", "prompt"]
STEP_KEYS = ["id", "title", "description", "kind", "show_if", "agent", "questions"]
AGENT_DEFAULTS: dict[str, Any] = {
    "prompt": "", "auto_start": True, "require_reply": True, "placeholder": "",
    "show_status": True, "show_controls": True, "show_thoughts": True, "show_tools": True, "show_plan": True,
    "show_system": True, "show_input": True, "raw_toggle": False, "show_raw": False,
}
# Bestandteile des Agent-Fensters, die sich im Editor ein- und ausblenden lassen: (Schlüssel, Text, Hinweis)
AGENT_DISPLAY: list[tuple[str, str, str]] = [
    ("show_status", "Statuszeile", "Verbindungsstatus, Name des Agenten, „Abbrechen“"),
    ("show_controls", "Modus/Modell-Wahl", "nur sichtbar, wenn der Agent Modi/Modelle anbietet"),
    ("show_thoughts", "Gedanken des Agenten", "grau/kursiv, bevor er antwortet"),
    ("show_tools", "Werkzeugaufrufe", "z. B. „Datei lesen [fertig]“ und Freigabe-Hinweise"),
    ("show_plan", "Plan", "Aufgabenliste, die der Agent anlegt"),
    ("show_system", "Systemhinweise", "„Verbunden“, „Ende der Antwort“"),
    ("show_input", "Eingabefeld", "ohne Eingabefeld läuft nur die Startnachricht; Rückfragen und "
                                  "Freigaben erscheinen trotzdem"),
    ("raw_toggle", "Knopf „Rohdaten“", "ACP-Protokoll (JSON-RPC) bei Bedarf einblenden"),
    ("show_raw", "Rohdaten von Anfang an zeigen", "für Fehlersuche"),
]
QUESTION_KEYS = ["id", "label", "type", "required", "help", "placeholder", "default", "options", "min",
                 "max", "unit", "filter", "scale_labels", "show_if"]

QUESTION_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
WORKFLOW_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


# ---------------------------------------------------------------------------
# IDs


def slugify(text: str, sep: str = "_") -> str:
    text = (text or "").lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(a, b)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", sep, text).strip(sep)
    text = text[:40].rstrip(sep)
    if text and text[0].isdigit() and sep == "_":
        text = "f_" + text
    return text


def unique_id(base: str, taken: set[str] | list[str]) -> str:
    base = base or "frage"
    if base not in taken:
        return base
    n = 2
    while f"{base}_{n}" in taken:
        n += 1
    return f"{base}_{n}"


def is_auto_id(current: str, text: str, sep: str = "_") -> bool:
    """True, wenn die ID offensichtlich automatisch aus ``text`` erzeugt wurde."""
    slug = slugify(text, sep)
    return current == slug or bool(slug and re.fullmatch(re.escape(slug) + r"_\d+", current or ""))


def question_ids(draft: dict, exclude: dict | None = None) -> list[str]:
    return [q["id"] for s in draft["steps"] for q in s["questions"] if q is not exclude]


# ---------------------------------------------------------------------------
# Neue Elemente


def new_draft() -> dict[str, Any]:
    draft = {
        "id": "neuer-walkthrough",
        "name": "Neuer Walkthrough",
        "description": "",
        "category": "Allgemein",
        "order": 100,
        "icon": "🤖",
        "color": COLORS[0],
        "background": "",
        "output_name": "",
        "steps": [],
        "prompt": "",
    }
    step = new_step(draft)
    step["questions"].append(new_question(draft, "text", step))
    draft["steps"].append(step)
    return draft


def new_step(draft: dict) -> dict[str, Any]:
    title = f"Schritt {len(draft['steps']) + 1}"
    taken = [s["id"] for s in draft["steps"]]
    return {"id": unique_id(slugify(title), taken), "title": title, "description": "",
            "show_if": None, "questions": []}


def new_agent_step(draft: dict) -> dict[str, Any]:
    """Schritt, der nur aus einem Agent-Fenster besteht (roher Output des Agenten + Eingabe)."""
    title = "Agent-Fenster"
    taken = [s["id"] for s in draft["steps"]] + question_ids(draft)
    return {"id": unique_id(slugify(title), taken), "title": title,
            "description": "", "kind": "agent", "show_if": None, "questions": [],
            "agent": dict(AGENT_DEFAULTS)}


def is_agent_step(step: dict) -> bool:
    return step.get("kind") == "agent"


def new_question(draft: dict, qtype: str = "text", step: dict | None = None) -> dict[str, Any]:
    label = "Neue Frage"
    taken = question_ids(draft) + ([q["id"] for q in step["questions"]] if step else [])
    q: dict[str, Any] = {
        "id": unique_id(slugify(label), taken), "label": label, "type": qtype, "required": False,
        "help": "", "placeholder": "", "default": None, "options": [], "min": None, "max": None,
        "unit": "", "filter": "", "scale_labels": [], "show_if": None,
    }
    ensure_type_defaults(q)
    return q


def ensure_type_defaults(q: dict) -> None:
    """Ergänzt sinnvolle Startwerte, wenn ein Fragetyp gewählt/geändert wird."""
    if q["type"] in OPTION_TYPES and not q.get("options"):
        q["options"] = [{"value": "option_1", "label": "Option 1", "description": ""},
                        {"value": "option_2", "label": "Option 2", "description": ""}]
    if q["type"] == "scale":
        q["min"] = 1 if q.get("min") is None else q["min"]
        q["max"] = 5 if q.get("max") is None else q["max"]
        if not q.get("scale_labels"):
            q["scale_labels"] = ["gering", "hoch"]
    if q["type"] in FILE_TYPES and not q.get("filter"):
        q["filter"] = FILE_FILTER_PRESETS["Alle Dateien"]


# ---------------------------------------------------------------------------
# Laden / Normalisieren / Speichern


def normalize_draft(data: dict) -> dict[str, Any]:
    """Bringt eine YAML-Struktur in die vollständige Entwurfsform (alle Schlüssel vorhanden)."""
    data = copy.deepcopy(data or {})
    draft = new_draft() | {"steps": []}
    draft.update({k: v for k, v in data.items() if k in WORKFLOW_KEYS})
    for key in ("output_name", "description", "prompt", "background"):
        draft[key] = draft.get(key) or ""
    steps = []
    for s in data.get("steps") or []:
        step = {"id": "", "title": "", "description": "", "kind": "questions", "show_if": None,
                "questions": [], "agent": None}
        step.update({k: v for k, v in s.items() if k in STEP_KEYS})
        step["description"] = step["description"] or ""
        step["kind"] = step.get("kind") or "questions"
        if step["kind"] == "agent":
            step["agent"] = {**AGENT_DEFAULTS, **(step.get("agent") or {})}
            step["agent"]["prompt"] = step["agent"]["prompt"] or ""
            step["agent"]["placeholder"] = step["agent"]["placeholder"] or ""
        step["show_if"] = _normalize_condition(step.get("show_if"))
        questions = []
        for qd in s.get("questions") or []:
            q = new_question({"steps": []}, qd.get("type", "text"))
            q.update({k: v for k, v in qd.items() if k in QUESTION_KEYS})
            for key in ("help", "placeholder", "unit", "filter"):
                q[key] = q.get(key) or ""
            q["options"] = [_normalize_option(o) for o in q.get("options") or []]
            q["scale_labels"] = list(q.get("scale_labels") or [])
            q["show_if"] = _normalize_condition(q.get("show_if"))
            questions.append(q)
        step["questions"] = questions
        steps.append(step)
    draft["steps"] = steps
    return draft


def _normalize_option(o: Any) -> dict[str, str]:
    if isinstance(o, dict):
        value = str(o.get("value", o.get("label", "")))
        return {"value": value, "label": str(o.get("label", value)), "description": o.get("description") or ""}
    return {"value": str(o), "label": str(o), "description": ""}


def _normalize_condition(c: Any) -> dict | None:
    if not c:
        return None
    if isinstance(c, str):
        return {"question": c}
    return dict(c)


def load_draft(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Die Datei enthält keinen Walkthrough.")
    return normalize_draft(data)


def clean_draft(draft: dict) -> dict[str, Any]:
    """Reduziert den Entwurf auf die nötigen Angaben (für Validierung und YAML-Datei)."""
    out: dict[str, Any] = {}
    for key in WORKFLOW_KEYS:
        if key == "steps":
            out["steps"] = [_clean_step(s) for s in draft["steps"]]
        elif key == "order":
            if draft.get("order", 100) != 100:
                out["order"] = draft["order"]
        elif _filled(draft.get(key)):
            out[key] = draft[key]
    return out


def _clean_step(s: dict) -> dict[str, Any]:
    out = {k: s[k] for k in ("id", "title") if k in s}
    if s.get("description"):
        out["description"] = s["description"]
    if s.get("show_if"):
        out["show_if"] = _clean_condition(s["show_if"])
    if is_agent_step(s):
        out["kind"] = "agent"
        agent = s.get("agent") or {}
        out["agent"] = {k: agent[k] for k in AGENT_DEFAULTS if k in agent and agent[k] != AGENT_DEFAULTS[k]}
        return out
    out["questions"] = [_clean_question(q) for q in s["questions"]]
    return out


def _clean_question(q: dict) -> dict[str, Any]:
    t = q["type"]
    out: dict[str, Any] = {"id": q["id"], "label": q["label"], "type": t}
    if q.get("required"):
        out["required"] = True
    if q.get("help"):
        out["help"] = q["help"]
    if q.get("placeholder") and t in PLACEHOLDER_TYPES:
        out["placeholder"] = q["placeholder"]
    if t in OPTION_TYPES:
        out["options"] = [{k: v for k, v in o.items() if v or k != "description"} for o in q["options"]]
    if t in RANGE_TYPES:
        for k in ("min", "max"):
            if q.get(k) is not None:
                out[k] = int(q[k]) if float(q[k]).is_integer() else q[k]
    if t in UNIT_TYPES and q.get("unit"):
        out["unit"] = q["unit"]
    if t == "scale" and any(q.get("scale_labels") or []):
        out["scale_labels"] = q["scale_labels"]
    if t in FILE_TYPES and q.get("filter"):
        out["filter"] = q["filter"]
    if q.get("default") is not None and t not in FILE_TYPES:
        out["default"] = q["default"]
    if q.get("show_if"):
        out["show_if"] = _clean_condition(q["show_if"])
    return out


def _clean_condition(c: dict) -> Any:
    c = {k: v for k, v in c.items() if v is not None}
    return c["question"] if list(c) == ["question"] else c


def _filled(v: Any) -> bool:
    return v is not None and v != "" and v != []


class _Dumper(yaml.SafeDumper):
    pass


def _str_representer(dumper: yaml.SafeDumper, data: str):
    style = None
    if "\n" in data:
        # Block-Stil ist nur ohne Leerzeichen am Zeilenende möglich → diese entfernen
        data = "\n".join(line.rstrip() for line in data.split("\n"))
        style = "|"
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_Dumper.add_representer(str, _str_representer)


def to_yaml(draft: dict) -> str:
    header = "# PAGER-Walkthrough – erstellt mit dem Walkthrough-Editor\n"
    body = yaml.dump(clean_draft(draft), Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)
    return header + body


def save_draft(draft: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_yaml(draft), encoding="utf-8")


# ---------------------------------------------------------------------------
# Validierung


def validate_draft(draft: dict) -> tuple[Workflow | None, list[str]]:
    """Gibt den gültigen Workflow oder eine Liste verständlicher Fehlermeldungen zurück."""
    errors: list[str] = []
    if not WORKFLOW_ID_RE.match(draft.get("id") or ""):
        errors.append("Allgemein › ID: nur Kleinbuchstaben, Ziffern, „-“ und „_“ erlaubt.")
    if not (draft.get("name") or "").strip():
        errors.append("Allgemein › Name: bitte ausfüllen.")
    if not draft["steps"]:
        errors.append("Der Walkthrough braucht mindestens einen Schritt.")
    step_ids: set[str] = set()
    for si, s in enumerate(draft["steps"], 1):
        where = f"Schritt {si} „{s.get('title') or '?'}“"
        if is_agent_step(s):
            if not QUESTION_ID_RE.match(s["id"] or ""):
                errors.append(f"{where}: ID „{s['id']}“ ungültig (Kleinbuchstaben, Ziffern und „_“).")
            if s["id"] in question_ids(draft):
                errors.append(f"{where}: ID „{s['id']}“ ist bereits als Frage-ID vergeben.")
            try:
                _env.parse((s.get("agent") or {}).get("prompt") or "")
            except TemplateSyntaxError as exc:
                errors.append(f"{where} › Startnachricht, Zeile {exc.lineno}: {exc.message}")
        elif not s["questions"]:
            errors.append(f"{where}: enthält noch keine Frage.")
        if s["id"] in step_ids:
            errors.append(f"{where}: ID „{s['id']}“ ist doppelt vergeben.")
        step_ids.add(s["id"])
        for qi, q in enumerate(s["questions"], 1):
            if not QUESTION_ID_RE.match(q["id"] or ""):
                errors.append(f"{where} › Frage {qi}: ID „{q['id']}“ ungültig "
                              "(Kleinbuchstaben, Ziffern und „_“, beginnt mit Buchstaben).")
            if not (q["label"] or "").strip():
                errors.append(f"{where} › Frage {qi}: Fragetext fehlt.")
            if q["type"] in OPTION_TYPES:
                values = [o["value"] for o in q["options"]]
                if len(set(values)) != len(values):
                    errors.append(f"{where} › „{q['label']}“: Optionen haben doppelte Werte.")
                if any(not (o["label"] or "").strip() for o in q["options"]):
                    errors.append(f"{where} › „{q['label']}“: eine Option hat keine Beschriftung.")
    try:
        _env.parse(draft.get("prompt") or "")
    except TemplateSyntaxError as exc:
        errors.append(f"Prompt-Vorlage, Zeile {exc.lineno}: {exc.message}")

    wf = None
    try:
        wf = Workflow.model_validate(clean_draft(draft))
    except ValidationError as exc:
        for e in exc.errors():
            msg = e["msg"].removeprefix("Value error, ")
            where = _humanize_loc(draft, e["loc"])
            text = f"{where}: {msg}" if where else msg
            if text not in errors:
                errors.append(text)
    return (wf if not errors else None), errors


def _humanize_loc(draft: dict, loc: tuple) -> str:
    parts = []
    try:
        if len(loc) >= 2 and loc[0] == "steps":
            s = draft["steps"][loc[1]]
            parts.append(f"Schritt {loc[1] + 1} „{s['title']}“")
            if len(loc) >= 4 and loc[2] == "questions":
                parts.append(f"„{s['questions'][loc[3]]['label']}“")
                if len(loc) >= 5:
                    parts.append(str(loc[4]))
        elif loc:
            parts.append(f"Allgemein › {loc[0]}")
    except (IndexError, KeyError, TypeError):
        return ".".join(map(str, loc))
    return " › ".join(parts)


# ---------------------------------------------------------------------------
# Umbenennen / Referenzen


_PROMPT_VARS = r"((?:answers|anzeige|dateien)\.)"
_AGENT_VARS = r"((?:agent|answers|anzeige)\.)"


def rename_agent_step(draft: dict, old: str, new: str) -> None:
    """Benennt die ID eines Agent-Fensters um und passt Prompt-Vorlage und Startnachrichten an."""
    if old == new or not old:
        return
    pattern = _AGENT_VARS + re.escape(old) + r"\b"
    draft["prompt"] = re.sub(pattern, r"\g<1>" + new, draft.get("prompt") or "")
    for s in draft["steps"]:
        if is_agent_step(s) and s.get("agent"):
            s["agent"]["prompt"] = re.sub(pattern, r"\g<1>" + new, s["agent"].get("prompt") or "")


def rename_question(draft: dict, old: str, new: str) -> None:
    """Benennt eine Frage-ID um und zieht Bedingungen und Prompt-Vorlage nach."""
    if old == new:
        return
    for s in draft["steps"]:
        for c in [s.get("show_if"), *(q.get("show_if") for q in s["questions"])]:
            if c and c.get("question") == old:
                c["question"] = new
    draft["prompt"] = re.sub(_PROMPT_VARS + re.escape(old) + r"\b", r"\g<1>" + new, draft.get("prompt") or "")


def references(draft: dict, qid: str) -> list[str]:
    refs = []
    for s in draft["steps"]:
        if (s.get("show_if") or {}).get("question") == qid:
            refs.append(f"Bedingung von Schritt „{s['title']}“")
        for q in s["questions"]:
            if q["id"] != qid and (q.get("show_if") or {}).get("question") == qid:
                refs.append(f"Bedingung von Frage „{q['label']}“")
    if re.search(_PROMPT_VARS + re.escape(qid) + r"\b", draft.get("prompt") or ""):
        refs.append("Prompt-Vorlage")
    return refs


def remove_condition_references(draft: dict, qid: str) -> None:
    for s in draft["steps"]:
        if (s.get("show_if") or {}).get("question") == qid:
            s["show_if"] = None
        for q in s["questions"]:
            if (q.get("show_if") or {}).get("question") == qid:
                q["show_if"] = None


def earlier_questions(draft: dict, target: dict) -> list[dict]:
    """Alle Fragen vor ``target`` (Schritt oder Frage) – nur diese dürfen in Bedingungen stehen."""
    result = []
    for s in draft["steps"]:
        if s is target:
            return result
        for q in s["questions"]:
            if q is target:
                return result
            result.append(q)
    return result


# ---------------------------------------------------------------------------
# Vorschau


def sample_answers(draft: dict) -> dict[str, Any]:
    """Beispielantworten, mit denen die Prompt-Vorlage probeweise gerendert wird."""
    answers: dict[str, Any] = {}
    for s in draft["steps"]:
        if is_agent_step(s):
            answers[s["id"]] = {"antwort": f"‹Antwort aus „{s['title']}“›",
                                "verlauf": "**Du:** …\n\n**Agent:** …", "nachrichten": 1}
        for q in s["questions"]:
            t, label = q["type"], q["label"] or q["id"]
            if q.get("default") is not None:
                answers[q["id"]] = q["default"]
            elif t in ("text", "textarea"):
                answers[q["id"]] = f"‹{label}›"
            elif t in RANGE_TYPES:
                answers[q["id"]] = q.get("min") if q.get("min") is not None else 1
            elif t == "choice" and q["options"]:
                answers[q["id"]] = q["options"][0]["value"]
            elif t == "multichoice" and q["options"]:
                answers[q["id"]] = [q["options"][0]["value"]]
            elif t == "bool":
                answers[q["id"]] = True
            elif t == "date":
                answers[q["id"]] = dt.date.today()
            elif t in FILE_TYPES:
                answers[q["id"]] = [f"beispiel_{q['id']}.pdf"]
    return answers


def preview_prompt(draft: dict) -> str:
    """Rendert die Vorlage mit Beispielantworten (Dateiinhalte als Platzhalter)."""
    try:
        wf = Workflow.model_validate(clean_draft(draft))
    except ValidationError:
        return "(Vorschau erst möglich, wenn alle Fragen gültig sind – siehe Hinweise oben.)"
    try:
        return render_prompt(wf, sample_answers(draft), read_files=False)
    except Exception as exc:  # noqa: BLE001 – Vorlagenfehler anzeigen
        return f"Fehler in der Vorlage: {exc}"


def default_prompt() -> str:
    return DEFAULT_PROMPT


# Öffentliche Namen für den Editor
clean_question = _clean_question


def fmt_plain(value: float) -> str:
    """Zahl für Eingabefelder: ohne Tausenderpunkt, Dezimalkomma."""
    return str(int(value)) if float(value).is_integer() else str(value).replace(".", ",")
