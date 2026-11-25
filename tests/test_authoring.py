from pathlib import Path

from pager.core import authoring as A
from pager.core.workflow import load_workflow

FIXTURES = Path(__file__).parent / "fixtures"


def test_slugify_and_unique_ids():
    assert A.slugify("Größe & Gewicht (kg)") == "groesse_gewicht_kg"
    assert A.slugify("Neuer Walkthrough", "-") == "neuer-walkthrough"
    assert A.slugify("3D-Modell") == "f_3d_modell"
    assert A.unique_id("frage", ["frage", "frage_2"]) == "frage_3"
    assert A.is_auto_id("neue_frage_2", "Neue Frage")
    assert not A.is_auto_id("eigene_id", "Neue Frage")


def test_new_draft_is_valid_and_roundtrips(tmp_path: Path):
    draft = A.new_draft()
    wf, errors = A.validate_draft(draft)
    assert errors == [] and wf.name == "Neuer Walkthrough"

    path = tmp_path / "neu.yaml"
    A.save_draft(draft, path)
    loaded = load_workflow(path)
    assert loaded.id == draft["id"] and loaded.questions[0].label == "Neue Frage"
    assert A.load_draft(path)["name"] == draft["name"]


def test_fixture_roundtrip_keeps_content(tmp_path: Path):
    draft = A.load_draft(FIXTURES / "beispiel.yaml")
    assert draft["steps"][1]["questions"][0]["type"] == "file"
    A.save_draft(draft, tmp_path / "b.yaml")
    wf = load_workflow(tmp_path / "b.yaml")
    original = load_workflow(FIXTURES / "beispiel.yaml")
    assert wf.model_dump() == original.model_dump()
    text = (tmp_path / "b.yaml").read_text(encoding="utf-8")
    assert "prompt: |" in text  # mehrzeilig als Block, gut lesbar
    assert "required: false" not in text  # keine überflüssigen Standardwerte


def test_clean_drops_fields_irrelevant_for_type():
    draft = A.new_draft()
    q = draft["steps"][0]["questions"][0]
    q["type"] = "choice"
    A.ensure_type_defaults(q)
    q["type"] = "text"  # Optionen bleiben im Entwurf, landen aber nicht in der Datei
    assert "options" not in A.clean_question(q)


def test_validation_messages_are_readable():
    draft = A.new_draft()
    q = draft["steps"][0]["questions"][0]
    q["id"] = "Ungültig!"
    q["label"] = ""
    draft["prompt"] = "{% if %}"
    draft["steps"].append(A.new_step(draft))
    _, errors = A.validate_draft(draft)
    text = "\n".join(errors)
    assert "ID „Ungültig!“ ungültig" in text
    assert "Fragetext fehlt" in text
    assert "enthält noch keine Frage" in text
    assert "Prompt-Vorlage, Zeile 1" in text


def test_condition_on_later_question_is_reported():
    draft = A.new_draft()
    step = draft["steps"][0]
    later = A.new_question(draft, "bool", step)
    step["questions"].append(later)
    step["questions"][0]["show_if"] = {"question": later["id"], "equals": True}
    _, errors = A.validate_draft(draft)
    assert any("spätere Frage" in e for e in errors)


def test_rename_updates_conditions_and_prompt():
    draft = A.new_draft()
    step = draft["steps"][0]
    first = step["questions"][0]
    second = A.new_question(draft, "text", step)
    second["show_if"] = {"question": first["id"]}
    step["questions"].append(second)
    draft["prompt"] = f"A={{{{ answers.{first['id']} }}}} B={{{{ anzeige.{first['id']}_x }}}}"

    A.rename_question(draft, first["id"], "titel")
    assert second["show_if"]["question"] == "titel"
    assert "answers.titel }}" in draft["prompt"]
    assert "anzeige.neue_frage_x" in draft["prompt"]  # nur ganze Bezeichner werden ersetzt
    assert set(A.references(draft, "titel")) == {f"Bedingung von Frage „{second['label']}“", "Prompt-Vorlage"}

    A.remove_condition_references(draft, "titel")
    assert second["show_if"] is None


def test_earlier_questions_and_preview():
    draft = A.load_draft(FIXTURES / "beispiel.yaml")
    second_step = draft["steps"][1]
    assert [q["id"] for q in A.earlier_questions(draft, second_step)] == ["titel", "menge"]
    preview = A.preview_prompt(draft)
    assert "Bearbeite ‹Titel›." in preview
    assert "Tabelle: beispiel_tabelle.pdf (Anhang 1)" in preview
    assert "‹Inhalt von beispiel_tabelle.pdf›" in preview
