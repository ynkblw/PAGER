import datetime as dt
from pathlib import Path

import pytest

from pager.core.prompting import file_targets, render_prompt
from pager.core.workflow import Workflow, load_workflows, parse_number


def make_wf(**overrides) -> Workflow:
    data = {
        "id": "t",
        "name": "Test",
        "steps": [
            {"id": "s1", "title": "Eins", "questions": [
                {"id": "name", "label": "Name", "required": True},
                {"id": "menge", "label": "Menge", "type": "integer", "min": 1, "unit": "Stk."},
                {"id": "hat_datei", "label": "Datei?", "type": "bool"},
                {"id": "datei", "label": "Datei", "type": "files", "show_if": "hat_datei"},
            ]},
            {"id": "s2", "title": "Zwei", "show_if": {"question": "menge", "in": [1, 2]}, "questions": [
                {"id": "farbe", "label": "Farbe", "type": "choice", "options": ["rot", {"value": "gb", "label": "Gelb"}]},
            ]},
        ],
        "prompt": "Name={{ answers.name }} Farbe={{ anzeige.farbe }}\n{{ dateien_liste }}",
    }
    data.update(overrides)
    return Workflow.model_validate(data)


FIXTURES = Path(__file__).parent / "fixtures"


def test_load_workflows_sorted_by_order(tmp_path: Path):
    (tmp_path / "a.yaml").write_text((FIXTURES / "beispiel.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "b.yaml").write_text("id: zuerst\nname: Zuerst\norder: 1\nsteps:\n  - id: s\n    title: S\n"
                                     "    questions:\n      - {id: q, label: Q}\n", encoding="utf-8")
    workflows, errors = load_workflows([tmp_path, tmp_path / "gibt-es-nicht"])
    assert errors == []
    assert [w.id for w in workflows] == ["zuerst", "beispiel"]


def test_invalid_workflow_reports_error(tmp_path: Path):
    (tmp_path / "kaputt.yaml").write_text("id: x\nname: X\nsteps:\n  - id: a\n    title: A\n"
                                          "    questions:\n      - {id: q, label: Q, type: choice}\n")
    workflows, errors = load_workflows([tmp_path])
    assert workflows == []
    assert "options" in errors[0].message


def test_show_if_must_reference_earlier_question():
    with pytest.raises(ValueError, match="unbekannte"):
        make_wf(steps=[{"id": "s", "title": "S", "questions": [
            {"id": "a", "label": "A", "show_if": "b"}, {"id": "b", "label": "B"}]}])


def test_conditions_and_visible_steps():
    wf = make_wf()
    q = wf.question("datei")
    assert not q.is_visible({"hat_datei": False})  # Kurzform: bool muss wahr sein
    assert q.is_visible({"hat_datei": True})
    assert [s.id for s in wf.visible_steps({"menge": 1})] == ["s1", "s2"]
    assert [s.id for s in wf.visible_steps({"menge": 7})] == ["s1"]
    assert "farbe" not in wf.visible_answers({"menge": 7, "farbe": "rot"})


@pytest.mark.parametrize("text,expected", [
    ("12", 12), ("1,5", 1.5), ("1.234,5", 1234.5), ("10.000", 10000), ("2.5", 2.5), (3, 3),
])
def test_parse_number(text, expected):
    assert parse_number(text) == expected


def test_validation_and_display():
    wf = make_wf()
    assert wf.question("name").validate_answer("  ") == "Bitte ausfüllen."
    assert wf.question("menge").validate_answer("0") is not None
    assert wf.question("menge").validate_answer("1,5") == "Bitte eine ganze Zahl eingeben."
    assert wf.question("menge").validate_answer("12.000") is None
    assert wf.question("menge").display(12000) == "12.000 Stk."
    assert wf.question("farbe").display("gb") == "Gelb"
    assert wf.question("hat_datei").display(False) == "Nein"
    assert wf.question("datei").validate_answer(["/gibt/es/nicht.pdf"]).startswith("Datei nicht gefunden")


def test_prompt_rendering_embeds_text_files(tmp_path: Path):
    a = tmp_path / "a" / "notiz.txt"
    b = tmp_path / "b" / "notiz.txt"
    for f, text in ((a, "Erste Notiz"), (b, "Zweite ```Notiz```")):
        f.parent.mkdir()
        f.write_text(text, encoding="utf-8")
    wf = make_wf()
    answers = {"name": "Teil", "menge": 1, "farbe": "gb", "hat_datei": True, "datei": [str(a), str(b)]}
    targets = file_targets(wf, answers)["datei"]
    assert [rel for _, rel in targets] == ["eingaben/datei/notiz.txt", "eingaben/datei/notiz_2.txt"]
    prompt = render_prompt(wf, answers)
    assert prompt.startswith("Name=Teil Farbe=Gelb")
    assert "- Datei: notiz.txt (Anhang 2)" in prompt
    assert "### Anhang 1: notiz.txt (Datei)\n````\nErste Notiz\n````" in prompt
    assert "Zweite ```Notiz```" in prompt  # eigene Backticks brechen den Block nicht


def test_hidden_file_question_is_not_attached(tmp_path: Path):
    f = tmp_path / "x.txt"
    f.write_text("geheim")
    prompt = render_prompt(make_wf(), {"name": "X", "hat_datei": False, "datei": [str(f)]})
    assert "geheim" not in prompt and "Anhänge" not in prompt


def test_large_file_is_truncated(tmp_path: Path, monkeypatch):
    import pager.core.prompting as P
    monkeypatch.setattr(P, "MAX_ATTACHMENT_CHARS", 10)
    f = tmp_path / "lang.txt"
    f.write_text("x" * 50)
    prompt, atts = P.prepare_prompt(make_wf(), {"name": "X", "hat_datei": True, "datei": [str(f)]})
    assert atts[0].truncated and "x" * 11 not in prompt and "gekürzt" in prompt


def test_default_prompt_contains_answer_table():
    wf = make_wf(prompt="")
    prompt = render_prompt(wf, {"name": "X", "menge": 3, "sop": dt.date(2027, 1, 1)})
    assert "| Name | X |" in prompt and "| Menge | 3 Stk. |" in prompt
