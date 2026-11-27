"""ACP-Client, Agent-Profile, Agent-Fenster im Walkthrough-Modell – gegen einen simulierten Agenten."""

import queue
import sys
import time
from pathlib import Path

import pytest

from pager.agent import profiles as P
from pager.agent.acp import AcpSession, AgentLaunch, _mcp_server
from pager.agent.demo import DemoAgentSession
from pager.agent.events import (
    AutoDecision, Closed, Connected, Failed, ModeChanged, PermissionRequest, QuestionAnswered,
    QuestionRequest, Raw, TextChunk, ToolCall, TurnEnded, TurnStarted, question_fields,
)
from pager.agent.session import Transcript
from pager.config import Settings, builtin_workflow_dir
from pager.copilot import AcpBackend, BackendError, RunRequest, create_backend
from pager.core import authoring as A
from pager.core.prompting import render_agent_prompt, render_prompt
from pager.core.workflow import Workflow, agent_display, load_workflow, load_workflows
from pager.core.workspace import PROMPT_FILE

FAKE = Path(__file__).parent / "fake_acp_agent.py"


def launch(**env) -> AgentLaunch:
    return AgentLaunch("Fake", [sys.executable, str(FAKE)], {k: str(v) for k, v in env.items()},
                       mcp_servers=[{"name": "tia", "command": "tia.exe", "args": ["--x", "1"]}])


class Collector:
    def __init__(self, session):
        self.q: queue.Queue = queue.Queue()
        self.events: list = []
        session.subscribe(self.q.put)

    def until(self, kind, timeout=15.0, where=lambda e: True):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                ev = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            self.events.append(ev)
            if isinstance(ev, kind) and where(ev):
                return ev
        raise AssertionError(f"{kind.__name__} nicht empfangen; bisher: {self.events[-8:]}")

    def text(self, role="agent"):
        return "".join(e.text for e in self.events if isinstance(e, TextChunk) and e.role == role)


@pytest.fixture
def session(tmp_path):
    s = AcpSession(launch(), tmp_path, policy="ask")
    yield s
    s.close()


def test_acp_connect_prompt_and_raw_protocol(session, tmp_path):
    col = Collector(session)
    transcript = Transcript()
    session.subscribe(transcript.feed)
    session.start()
    info = col.until(Connected)
    assert info.agent_name == "Fake-Agent" and info.agent_version == "0.1"
    assert [m["id"] for m in info.modes] == ["default", "plan"]
    assert info.config_options[0]["category"] == "model"
    assert session.ready

    session.prompt("hallo")
    col.until(TurnEnded)
    assert col.text() == "Echo: hallo"
    assert col.text("thought") == "nachdenken …"
    raws = [e for e in col.events if isinstance(e, Raw)]
    assert any(r.direction == "out" and '"session/prompt"' in r.text for r in raws)
    assert any(r.direction == "in" and "kein JSON" in r.text for r in raws)  # Nicht-JSON wird nur angezeigt
    assert any(r.direction == "stderr" and "gestartet" in r.text for r in raws)
    # cwd und MCP-Server werden im ACP-Format übergeben
    new_reply = next(r.text for r in raws if r.direction == "in" and '"sessionId": "s1"' in r.text
                     and "_cwd" in r.text)
    assert str(tmp_path.resolve()) in new_reply and '"name": "tia"' in new_reply

    assert transcript.last_reply == "Echo: hallo" and transcript.replies == 1
    assert transcript.as_answer()["verlauf"].startswith("**Du:**\n\nhallo")

    session.set_mode("plan")
    assert col.until(ModeChanged).mode_id == "plan"


def test_acp_permission_ask_allow(session):
    col = Collector(session)
    session.start()
    col.until(Connected)
    session.prompt("PERMISSION bitte")
    tool = col.until(ToolCall)
    assert tool.title == "Datei schreiben" and tool.detail == "notiz.txt"
    req = col.until(PermissionRequest)
    assert req.kind == "edit" and [o["optionId"] for o in req.options] == ["ok", "no"]
    req.allow()
    req.allow()  # zweite Antwort wird ignoriert
    col.until(TurnEnded)
    assert col.text() == "erlaubt"


@pytest.mark.parametrize("action, expected", [("accept", "Antwort: Rot"), ("decline", "Antwort: decline")])
def test_acp_question_elicitation(session, action, expected):
    col = Collector(session)
    session.start()
    col.until(Connected)
    init = next(e.text for e in col.events if isinstance(e, Raw) and '"initialize"' in e.text)
    assert '"elicitation": {"form": {}}' in init  # sonst schaltet Claude Code AskUserQuestion ab
    session.prompt("QUESTION")
    req = col.until(QuestionRequest)
    assert req.message == "Welche Farbe?"
    fields = question_fields(req.schema)
    assert [(f["key"], f["kind"], f["custom_for"]) for f in fields] == [
        ("question_0", "choice", ""), ("question_0_custom", "text", "question_0")]
    assert fields[0]["options"][1] == ("Rot", "Rot", "kräftig")
    if action == "accept":
        req.accept({"question_0": "Rot"})
    else:
        req.decline()
    assert col.until(QuestionAnswered).summary == ("Rot" if action == "accept" else "übersprungen")
    col.until(TurnEnded)
    assert col.text() == expected


def test_question_fields_kinds():
    schema = {"type": "object", "required": ["n"], "properties": {
        "n": {"type": "integer", "title": "Anzahl"},
        "ok": {"type": "boolean"},
        "tags": {"type": "array", "items": {"anyOf": [{"const": "a", "title": "A"}]}},
        "farbe": {"type": "string", "enum": ["rot", "grün"]},
    }}
    kinds = {f["key"]: (f["kind"], f["required"]) for f in question_fields(schema)}
    assert kinds == {"n": ("integer", True), "ok": ("bool", False), "tags": ("multi", False),
                     "farbe": ("choice", False)}


@pytest.mark.parametrize("policy, expected", [("deny", "abgelehnt"), ("allow", "erlaubt")])
def test_acp_permission_policies(tmp_path, policy, expected):
    s = AcpSession(launch(), tmp_path, policy=policy)
    try:
        col = Collector(s)
        s.start()
        col.until(Connected)
        s.prompt("PERMISSION")
        auto = col.until(AutoDecision)
        assert auto.allowed is (policy == "allow")
        col.until(TurnEnded)
        assert col.text() == expected
        assert not any(isinstance(e, PermissionRequest) for e in col.events)
    finally:
        s.close()


def test_acp_cancel(session):
    col = Collector(session)
    session.start()
    col.until(Connected)
    session.prompt("SLOW")
    col.until(TextChunk)
    session.cancel()
    ended = col.until(TurnEnded)
    assert ended.stop_reason == "cancelled" and not session.busy


def test_acp_auth_required_and_missing_program(tmp_path):
    s = AcpSession(launch(FAKE_AUTH_REQUIRED=1), tmp_path)
    col = Collector(s)
    s.start()
    failed = col.until(Failed)
    assert failed.login_required and "Fake-Login" in failed.message
    col.until(Closed)

    s2 = AcpSession(AgentLaunch("Nix", [str(tmp_path / "gibt-es-nicht")]), tmp_path)
    col2 = Collector(s2)
    s2.start()
    assert "konnte nicht gestartet werden" in col2.until(Failed).message


def test_acp_backend_owns_and_reuses_sessions(tmp_path):
    settings = Settings(backend="acp", agent="custom",
                        agent_command=f'"{sys.executable}" "{FAKE}"', timeout_minutes=1)
    (tmp_path / PROMPT_FILE).write_text("Auftrag XY", encoding="utf-8")
    req = RunRequest(workspace=tmp_path, prompt_file=PROMPT_FILE, workflow_id="w", workflow_name="W")
    backend = create_backend(settings)
    assert isinstance(backend, AcpBackend)
    handle = backend.start(req)
    assert "".join(handle.stream()) == "Echo: Auftrag XY"
    assert handle.session.closed  # eigene Sitzung wird beendet

    shared = AcpSession(launch(), tmp_path)
    try:
        handle = create_backend(settings, shared).start(req)
        seen = []
        handle.permission_handler = seen.append
        assert "".join(handle.stream()) == "Echo: Auftrag XY"
        assert shared.connected and not shared.closed  # Sitzung aus dem Agent-Fenster bleibt offen
    finally:
        shared.close()


def test_acp_backend_permission_handler_and_errors(tmp_path):
    settings = Settings(backend="acp", agent="custom", agent_command=f'"{sys.executable}" "{FAKE}"')
    (tmp_path / PROMPT_FILE).write_text("PERMISSION", encoding="utf-8")
    req = RunRequest(workspace=tmp_path, prompt_file=PROMPT_FILE, workflow_id="w", workflow_name="W")
    handle = create_backend(settings).start(req)
    handle.permission_handler = lambda r: r.allow()
    assert "".join(handle.stream()) == "erlaubt"

    with pytest.raises(BackendError, match="Startbefehl"):
        create_backend(Settings(backend="acp", agent="custom")).start(req)


def test_demo_agent_session():
    s = DemoAgentSession("deny", speed=50)
    col = Collector(s)
    s.start()
    col.until(Connected)
    s.prompt("Bitte /werkzeug benutzen und eine /frage stellen")
    assert col.until(AutoDecision).allowed is False
    col.until(QuestionRequest).accept({"question_0": "Grün"})
    col.until(TurnEnded)
    assert "Demo-Antwort 1" in col.text() and "Deine Auswahl: **Grün**" in col.text()
    assert any(isinstance(e, TurnStarted) for e in col.events)


def test_build_launch_profiles(tmp_path):
    claude = tmp_path / "claude"
    claude.write_text("")
    s = Settings(agent="claude-code", acp_adapter="mein-adapter --flag", claude_path=str(claude),
                 agent_model="sonnet", mcp_servers=[{"name": "tia", "command": "x.exe", "args": []}])
    la = P.build_launch(s)
    assert la.command == ["mein-adapter", "--flag"] and la.name == "Claude Code"
    assert la.env == {"CLAUDE_CODE_EXECUTABLE": str(claude), "ANTHROPIC_MODEL": "sonnet"}
    assert la.mcp_servers[0]["name"] == "tia"
    s.use_installed_claude = False
    assert "CLAUDE_CODE_EXECUTABLE" not in P.build_launch(s).env

    copilot = tmp_path / "copilot"
    copilot.write_text("")
    la = P.build_launch(Settings(agent="copilot", copilot_path=str(copilot), agent_model="auto",
                                 ghe_host="https://acme.ghe.com", agent_args="--add-dir=/x"))
    assert la.command == [str(copilot), "--acp", "--model", "auto", "--add-dir=/x"]
    assert la.env == {"COPILOT_GH_HOST": "https://acme.ghe.com"}

    with pytest.raises(P.AgentSetupError):
        P.build_launch(Settings(agent="copilot", copilot_path=str(tmp_path / "fehlt")))
    assert isinstance(P.create_session(Settings(backend="demo"), tmp_path), DemoAgentSession)
    assert P.agent_available(Settings(backend="acp")) and not P.agent_available(Settings(backend="chat"))


def test_test_connection_and_checks(tmp_path):
    s = Settings(backend="acp", agent="custom", agent_command=f'"{sys.executable}" "{FAKE}"')
    ok, message = P.test_connection(s, tmp_path, timeout=20)
    assert ok and "Fake-Agent" in message and "Modell A" in message
    [check] = P.run_checks(s)
    assert check.state == "ok"
    ok, message = P.test_connection(Settings(backend="acp", agent="custom"), tmp_path)
    assert not ok and "Startbefehl" in message


def test_settings_roundtrip_with_agent_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("PAGER_CONFIG_DIR", str(tmp_path))
    s = Settings(backend="acp", agent="copilot", permission_policy="read",
                 mcp_servers=[{"name": "tia", "command": "C:\\TiaMcp\\server.exe", "args": ["--a"]}])
    s.save()
    loaded = Settings.load()
    assert loaded.mcp_servers == s.mcp_servers and loaded.permission_policy == "read"
    assert loaded.backend_label == "Copilot CLI"
    assert _mcp_server(s.mcp_servers[0]) == {"name": "tia", "command": "C:\\TiaMcp\\server.exe",
                                             "args": ["--a"], "env": []}
    assert builtin_workflow_dir() in Settings().workflow_paths
    assert builtin_workflow_dir() not in Settings(show_examples=False).workflow_paths


# ---------------------------------------------------------------------------
# Walkthrough-Modell mit Agent-Fenstern


def agent_workflow(**step) -> dict:
    return {"id": "w", "name": "W", "steps": [
        {"id": "eins", "title": "Eins", "questions": [{"id": "thema", "label": "Thema"}]},
        {"id": "dialog", "title": "Dialog", "kind": "agent", **step},
    ], "prompt": "Ergebnis: {{ agent.dialog.antwort }} / {{ anzeige.dialog }}"}


def test_agent_step_model_and_prompt():
    wf = Workflow.model_validate(agent_workflow(agent={"prompt": "Thema {{ anzeige.thema }}"}))
    step = wf.steps[1]
    assert step.is_agent and step.agent.auto_start and wf.has_agent_steps
    answers = {"thema": "Test", "dialog": {"antwort": "Hallo!", "verlauf": "…", "nachrichten": 2}}
    assert render_agent_prompt(wf, step, answers) == "Thema Test"
    assert wf.visible_answers(answers)["dialog"]["antwort"] == "Hallo!"
    assert render_prompt(wf, answers).strip() == "Ergebnis: Hallo! / 2 Antworten · „Hallo!“"
    assert agent_display(None) == "–"
    # ohne Antwort bleibt die Vorlage renderbar
    assert "Ergebnis:  /" in render_prompt(wf, {"thema": "x"})

    with pytest.raises(ValueError, match="keine Fragen"):
        Workflow.model_validate(agent_workflow(questions=[{"id": "x", "label": "X"}]))
    bad = agent_workflow()
    bad["steps"][1]["id"] = "thema"
    with pytest.raises(ValueError, match="bereits als Frage-ID"):
        Workflow.model_validate(bad)


def test_authoring_agent_step_roundtrip(tmp_path):
    draft = A.new_draft()
    step = A.new_agent_step(draft)
    step["agent"]["prompt"] = "Los"
    step["agent"]["show_raw"] = True
    step["agent"]["show_thoughts"] = False
    draft["steps"].append(step)
    draft["prompt"] = f"{{{{ agent.{step['id']}.antwort }}}}"
    wf, errors = A.validate_draft(draft)
    assert errors == [] and wf.steps[1].agent.show_raw and not wf.steps[1].agent.show_thoughts
    assert wf.steps[1].agent.show_tools  # nicht angegebene Bestandteile bleiben sichtbar

    A.save_draft(draft, tmp_path / "a.yaml")
    text = (tmp_path / "a.yaml").read_text(encoding="utf-8")
    assert "kind: agent" in text and "auto_start" not in text  # Standardwerte werden weggelassen
    assert "show_thoughts: false" in text and "show_tools" not in text
    loaded = A.load_draft(tmp_path / "a.yaml")
    assert loaded["steps"][1]["agent"]["prompt"] == "Los" and loaded["steps"][1]["agent"]["auto_start"]

    A.rename_agent_step(draft, step["id"], "pruefung")
    assert draft["prompt"] == "{{ agent.pruefung.antwort }}"
    assert "‹Antwort aus" in A.preview_prompt(draft | {"steps": draft["steps"][:1] + [
        {**step, "id": "pruefung"}]})

    step["agent"]["prompt"] = "{% if %}"
    assert any("Startnachricht" in e for e in A.validate_draft(draft)[1])


def test_builtin_example_workflow():
    workflows, errors = load_workflows([builtin_workflow_dir()])
    assert errors == []
    wf = next(w for w in workflows if w.id == "agent-direktkontakt")
    agent_step = wf.agent_steps[0]
    assert agent_step.agent.require_reply and agent_step.agent.show_thoughts
    assert not agent_step.agent.show_raw and not agent_step.agent.raw_toggle  # keine Rohdaten im Beispiel
    start = render_agent_prompt(wf, agent_step, wf.default_answers())
    assert "Verbindungstest" in start and "keine Werkzeuge" in start
    start = render_agent_prompt(wf, agent_step, {**wf.default_answers(), "werkzeugtest": True})
    assert "pager_test.txt" in start
    final = render_prompt(wf, {**wf.default_answers(), "funktioniert": "ja",
                               "direktkontakt": {"antwort": "Ich bin Claude.", "nachrichten": 1}})
    assert "Ich bin Claude." in final and "Ergebnis laut Nutzer: Ja" in final
    # Editor-Rundweg verliert nichts
    draft = A.load_draft(wf.source)
    assert A.validate_draft(draft)[1] == []
    assert load_workflow(wf.source).model_dump() == Workflow.model_validate(A.clean_draft(draft)).model_dump()
