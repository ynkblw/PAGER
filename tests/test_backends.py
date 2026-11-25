import sys
import threading
from pathlib import Path

import pytest
from openpyxl import Workbook

from pager.config import Settings
from pager.copilot import BackendError, CopilotCliBackend, DemoBackend, RunCancelled, RunRequest
from pager.core.prompting import prepare_prompt
from pager.core.workflow import load_workflow
from pager.core.workspace import PROMPT_FILE, RunWorkspace, list_runs

FIXTURES = Path(__file__).parent / "fixtures"
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="Fake-CLI als Shebang-Skript")


def fake_cli(tmp_path: Path, body: str) -> str:
    script = tmp_path / "copilot"
    script.write_text(f"#!{sys.executable}\nimport sys, os, time\n{body}\n", encoding="utf-8")
    script.chmod(0o755)
    return str(script)


def request(tmp_path: Path) -> RunRequest:
    (tmp_path / PROMPT_FILE).write_text("Auftrag", encoding="utf-8")
    return RunRequest(workspace=tmp_path, prompt_file=PROMPT_FILE, workflow_id="beispiel",
                      workflow_name="Beispiel")


def test_settings_chat_url():
    assert Settings().backend == "chat"
    assert Settings().resolved_chat_url == "https://github.com/copilot"
    assert Settings(ghe_host="https://acme.ghe.com/").resolved_chat_url == "https://acme.ghe.com/copilot"
    assert Settings(ghe_host="https://acme.ghe.com", chat_url="https://x/y").resolved_chat_url == "https://x/y"


def test_build_command_without_agents(tmp_path: Path):
    s = Settings(backend="copilot-cli", copilot_path=sys.executable, ghe_host="https://acme.ghe.com",
                 model="auto", cli_extra_args=["--deny-tool=shell"])
    backend = CopilotCliBackend(s)
    cmd = backend.build_command(request(tmp_path))
    assert cmd[0] == sys.executable
    assert "@AUFTRAG.md" in cmd[cmd.index("-p") + 1]
    assert "--model=auto" in cmd and "--silent" in cmd and f"--add-dir={tmp_path}" in cmd
    assert not any(a.startswith("--agent") for a in cmd)
    assert cmd[-1] == "--deny-tool=shell"
    assert backend.env()["COPILOT_GH_HOST"] == "https://acme.ghe.com"


def test_missing_cli_gives_helpful_error(tmp_path: Path):
    backend = CopilotCliBackend(Settings(copilot_path=str(tmp_path / "fehlt.exe")))
    with pytest.raises(BackendError, match="nicht gefunden"):
        backend.build_command(request(tmp_path))
    assert not backend.check().ok


@posix_only
def test_cli_streams_output_and_runs_in_workspace(tmp_path: Path):
    exe = fake_cli(tmp_path, "print(open('AUFTRAG.md').read()); print('Host:', os.environ.get('COPILOT_GH_HOST'))")
    backend = CopilotCliBackend(Settings(copilot_path=exe, ghe_host="https://acme.ghe.com"))
    out = "".join(backend.start(request(tmp_path)).stream())
    assert "Auftrag" in out and "Host: https://acme.ghe.com" in out


@posix_only
def test_cli_error_includes_stderr_and_login_hint(tmp_path: Path):
    exe = fake_cli(tmp_path, "sys.stderr.write('Error: not authenticated, please login'); sys.exit(1)")
    with pytest.raises(BackendError, match="anmelden"):
        list(CopilotCliBackend(Settings(copilot_path=exe)).start(request(tmp_path)).stream())


@posix_only
def test_cli_cancel(tmp_path: Path):
    exe = fake_cli(tmp_path, "print('start', flush=True); time.sleep(30)")
    handle = CopilotCliBackend(Settings(copilot_path=exe)).start(request(tmp_path))
    stream = handle.stream()
    assert next(stream).startswith("start")
    threading.Timer(0.2, handle.cancel).start()
    with pytest.raises(RunCancelled):
        list(stream)


def test_demo_backend_produces_report(tmp_path: Path):
    req = request(tmp_path)
    req.display = [("Titel", "Test"), ("Menge", "–")]
    text = "".join(DemoBackend().start(req).stream())
    assert "# Beispiel" in text and "| Titel | Test |" in text and "1 ausgefüllten Angaben" in text


def test_workspace_prepare_embeds_files_and_warns(tmp_path: Path):
    wb = Workbook()
    wb.active.append(["Anlage", "Invest"])
    wb.active.append(["Fräse", 100000])
    xlsx = tmp_path / "anlagen.xlsx"
    wb.save(xlsx)
    image = tmp_path / "foto.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00bin")

    wf = load_workflow(FIXTURES / "beispiel.yaml")
    answers = {"titel": "Test", "anhaenge": [str(image)], "tabelle": str(xlsx)}
    prompt, attachments = prepare_prompt(wf, answers)
    ws = RunWorkspace.create(tmp_path / "runs", wf)
    warnings = ws.prepare(wf, answers, prompt, attachments)

    saved = (ws.root / PROMPT_FILE).read_text(encoding="utf-8")
    assert "### Anhang 1: anlagen.xlsx (Tabelle)" in saved and "| Fräse | 100000 |" in saved
    assert "Tabelle: anlagen.xlsx (Anhang 1)" in saved
    assert "foto.png (wird separat im Chat angehängt)" in saved
    assert (ws.root / "eingaben/anhaenge/foto.png").exists()
    assert warnings == ["foto.png: kein Textformat – bitte im Chat manuell anhängen."]

    ws.write_meta(workflow_id=wf.id, status="done", result_file="r.md")
    ws.write_result("# Ergebnis", "r.md")
    [(found, meta)] = list_runs(tmp_path / "runs")
    assert meta["status"] == "done" and found.read_result() == "# Ergebnis"
