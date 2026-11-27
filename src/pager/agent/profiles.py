"""Agent-Profile: Wie PAGER Claude Code, die GitHub Copilot CLI oder einen eigenen ACP-Agenten startet.

* **Claude Code** spricht ACP über den offiziellen Adapter ``claude-agent-acp``
  (npm-Paket ``@agentclientprotocol/claude-agent-acp``). Der Adapter nutzt die installierte
  Claude Code CLI (``CLAUDE_CODE_EXECUTABLE``) und deren Anmeldung.
* **GitHub Copilot CLI** bringt ACP selbst mit: ``copilot --acp``.
* **Eigener Agent**: beliebiger Befehl, der ACP über stdio spricht.

Außerdem: Einrichtungsprüfung für die Einstellungen (``run_checks``), Verbindungstest und
Anmeldung in einem Terminalfenster.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from pager.agent.acp import AcpSession, AgentLaunch
from pager.agent.demo import DemoAgentSession
from pager.agent.events import Connected, Failed
from pager.agent.session import AgentSession
from pager.config import Settings

AGENTS: dict[str, str] = {
    "claude-code": "Claude Code",
    "copilot": "GitHub Copilot CLI",
    "custom": "Eigener ACP-Agent",
}

ADAPTER_PACKAGE = "@agentclientprotocol/claude-agent-acp"
ADAPTER_BINARIES = ("claude-agent-acp", "claude-code-acp")
CLAUDE_INSTALL_URL = "https://docs.anthropic.com/en/docs/claude-code/setup"
COPILOT_INSTALL_URL = "https://docs.github.com/en/copilot/how-tos/copilot-cli"
NODE_URL = "https://nodejs.org/"

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)


class AgentSetupError(Exception):
    """Agent kann mit den aktuellen Einstellungen nicht gestartet werden (Meldung für Nutzer)."""


def split_command(text: str) -> list[str]:
    """Zerlegt eine Befehlszeile. Unter Windows bleiben Backslashes erhalten, Anführungszeichen
    um einzelne Teile (``"C:\\Program Files\\…\\x.exe"``) werden entfernt."""
    if not text.strip():
        return []
    if sys.platform != "win32":
        return shlex.split(text)
    return [_unquote(part) for part in shlex.split(text, posix=False)]


def _unquote(part: str) -> str:
    if len(part) >= 2 and part[0] == part[-1] and part[0] in "\"'":
        return part[1:-1]
    return part


# ---------------------------------------------------------------------------
# Programme finden


def _which(name: str, extra_dirs: list[Path] | None = None) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for d in extra_dirs or []:
        for candidate in (d / name, d / f"{name}.exe", d / f"{name}.cmd"):
            if candidate.is_file():
                return str(candidate)
    return None


def extend_search_path() -> None:
    """Ergänzt ``PATH`` um die Ordner, in denen node, npx, claude und copilot üblicherweise liegen.

    Aus dem Finder gestartete Mac-Apps erben nur ``/usr/bin:/bin:/usr/sbin:/sbin``. Ohne diese
    Ergänzung fänden Skripte mit ``#!/usr/bin/env node`` (npx, claude-agent-acp) kein Node.js.
    Unter Windows erbt die App den PATH des Nutzers, dort ist nichts zu tun.
    """
    if sys.platform == "win32":
        return
    current = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    extra: list[str] = []
    shell = os.environ.get("SHELL") or ("/bin/zsh" if sys.platform == "darwin" else "/bin/sh")
    try:  # PATH der Login-Shell (enthält Homebrew, nvm, volta … aus .zprofile/.zshrc)
        out = subprocess.run([shell, "-ilc", 'printf "__PAGER_PATH__%s" "$PATH"'], capture_output=True,
                             text=True, timeout=5, stdin=subprocess.DEVNULL).stdout
        if "__PAGER_PATH__" in out:
            extra += out.rsplit("__PAGER_PATH__", 1)[1].strip().split(os.pathsep)
    except (OSError, subprocess.SubprocessError):
        pass
    extra += [str(d) for d in _home_bins()]
    merged = list(dict.fromkeys(current + [p for p in extra if p and os.path.isdir(p)]))
    os.environ["PATH"] = os.pathsep.join(merged)


def _home_bins() -> list[Path]:
    home = Path.home()
    dirs = [home / ".local" / "bin", home / ".claude" / "local", Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
    if appdata := os.environ.get("APPDATA"):
        dirs.append(Path(appdata) / "npm")
    return dirs


def find_claude(settings: Settings) -> str | None:
    if settings.claude_path.strip():
        return settings.claude_path if os.path.isfile(settings.claude_path) else None
    return _which("claude", _home_bins())


def find_copilot(settings: Settings) -> str | None:
    if settings.copilot_path.strip():
        return settings.copilot_path if os.path.isfile(settings.copilot_path) else None
    return _which("copilot", _home_bins())


def find_node() -> str | None:
    return _which("node", _home_bins())


def find_adapter(settings: Settings) -> tuple[list[str] | None, str]:
    """Befehl für den Claude-Code-ACP-Adapter und woher er stammt."""
    if settings.acp_adapter.strip():
        cmd = split_command(settings.acp_adapter)
        return cmd, "aus den Einstellungen"
    for name in ADAPTER_BINARIES:
        if path := _which(name, _home_bins()):
            return [path], "installiert"
    if npx := _which("npx", _home_bins()):
        return [npx, "-y", ADAPTER_PACKAGE], "über npx (wird beim ersten Start geladen)"
    return None, ""


def version_of(exe: str) -> str | None:
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30,
                             creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = (out.stdout or out.stderr).strip().splitlines()
    return lines[0].strip() if out.returncode == 0 and lines else None


def claude_logged_in(claude: str) -> tuple[bool | None, str]:
    """(angemeldet?, Beschreibung). ``None`` = unbekannt."""
    try:
        out = subprocess.run([claude, "auth", "status", "--json"], capture_output=True, text=True,
                             timeout=30, creationflags=_NO_WINDOW)
        data = json.loads(out.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None, "Status konnte nicht ermittelt werden."
    if data.get("loggedIn"):
        method = {"claude.ai": "Claude-Abo", "console": "Anthropic Console"}.get(
            data.get("authMethod", ""), data.get("authMethod") or "angemeldet")
        return True, f"Angemeldet ({method})"
    return False, "Nicht angemeldet."


# ---------------------------------------------------------------------------
# Start-Befehl


def build_launch(settings: Settings) -> AgentLaunch:
    agent = settings.agent
    model = settings.agent_model.strip()
    extra = split_command(settings.agent_args)
    mcp = [s for s in settings.mcp_servers if s.get("command")]
    if agent == "claude-code":
        adapter, _ = find_adapter(settings)
        if not adapter:
            raise AgentSetupError(
                "Der ACP-Adapter für Claude Code wurde nicht gefunden.\n"
                f"Installation: npm install -g {ADAPTER_PACKAGE}\n"
                "(benötigt Node.js) – oder den Befehl in den Einstellungen eintragen.")
        env: dict[str, str] = {}
        claude = find_claude(settings)
        # .cmd/.bat (npm-Installation unter Windows) kann der Adapter nicht direkt starten → dann
        # nutzt er seine mitgelieferte Claude-Version (gleiche Anmeldung).
        if settings.use_installed_claude and claude and not claude.lower().endswith((".cmd", ".bat")):
            env["CLAUDE_CODE_EXECUTABLE"] = claude
        if model:
            env["ANTHROPIC_MODEL"] = model
        return AgentLaunch("Claude Code", adapter + extra, env, mcp,
                           login_hint="Einstellungen → KI-Agent → „Anmelden …“ (führt „claude auth login“ aus).")
    if agent == "copilot":
        exe = find_copilot(settings)
        if not exe:
            raise AgentSetupError(
                "Die GitHub Copilot CLI wurde nicht gefunden.\n"
                "Installation: npm install -g @github/copilot  (oder winget install GitHub.Copilot)")
        cmd = [exe, "--acp"]
        if model:
            cmd += ["--model", model]
        env = {"COPILOT_GH_HOST": settings.ghe_host.strip()} if settings.ghe_host.strip() else {}
        return AgentLaunch("GitHub Copilot", cmd + extra, env, mcp,
                           login_hint="Einstellungen → KI-Agent → „Anmelden …“ (führt „copilot login“ aus).")
    cmd = split_command(settings.agent_command)
    if not cmd:
        raise AgentSetupError("Für einen eigenen ACP-Agenten bitte den Startbefehl eintragen.")
    return AgentLaunch(Path(cmd[0]).stem or "ACP-Agent", cmd + extra, {}, mcp)


def create_session(settings: Settings, cwd: Path) -> AgentSession:
    """Neue (noch nicht gestartete) Sitzung passend zum Übergabe-Modus."""
    if settings.backend == "demo":
        return DemoAgentSession(settings.permission_policy)
    return AcpSession(build_launch(settings), cwd, settings.permission_policy)


def agent_available(settings: Settings) -> bool:
    """Kann PAGER in diesem Modus ein Agent-Fenster öffnen?"""
    return settings.backend in ("acp", "demo")


# ---------------------------------------------------------------------------
# Einrichtung prüfen


@dataclass
class Check:
    key: str
    title: str
    state: str  # ok | warn | error | info
    detail: str
    action: str = ""  # install | login | node | docs | ""


def run_checks(settings: Settings) -> list[Check]:
    agent = settings.agent
    checks: list[Check] = []
    if agent == "claude-code":
        claude = find_claude(settings)
        version = version_of(claude) if claude else None
        if claude and version:
            checks.append(Check("cli", "Claude Code CLI", "ok", f"{version} · {claude}"))
        elif claude:
            checks.append(Check("cli", "Claude Code CLI", "error", f"Nicht ausführbar: {claude}", "docs"))
        else:
            checks.append(Check("cli", "Claude Code CLI", "error",
                                "Nicht gefunden. Bitte Claude Code installieren oder den Pfad eintragen.", "docs"))
        node = find_node()
        adapter, source = find_adapter(settings)
        if not adapter:
            checks.append(Check("node", "Node.js", "ok" if node else "error",
                                (version_of(node) or node) if node else "Nicht gefunden – wird für den ACP-Adapter benötigt.",
                                "" if node else "node"))
            checks.append(Check("adapter", "ACP-Adapter", "error",
                                f"Nicht gefunden. Installieren: npm install -g {ADAPTER_PACKAGE}", "install"))
        else:
            state = "ok" if source != "über npx (wird beim ersten Start geladen)" else "warn"
            detail = f"{source}: {shlex.join(adapter)}"
            if state == "warn":
                detail += "  – für schnelleren Start einmalig installieren."
            checks.append(Check("adapter", "ACP-Adapter (claude-agent-acp)", state, detail,
                                "install" if state == "warn" else ""))
        if claude and version:
            logged, text = claude_logged_in(claude)
            checks.append(Check("login", "Anmeldung", {True: "ok", False: "error", None: "warn"}[logged], text,
                                "" if logged else "login"))
    elif agent == "copilot":
        exe = find_copilot(settings)
        version = version_of(exe) if exe else None
        if exe and version:
            checks.append(Check("cli", "GitHub Copilot CLI", "ok", f"{version} · {exe}"))
        else:
            checks.append(Check("cli", "GitHub Copilot CLI", "error",
                                "Nicht gefunden. Installieren: npm install -g @github/copilot", "docs"))
        host = settings.ghe_host.strip() or "github.com"
        checks.append(Check("login", "Anmeldung", "info",
                            f"Host: {host} – wird beim Verbindungstest geprüft.", "login"))
    else:
        cmd = split_command(settings.agent_command)
        if not cmd:
            checks.append(Check("cli", "Startbefehl", "error", "Bitte den Befehl des ACP-Agenten eintragen."))
        else:
            exe = shutil.which(cmd[0]) or (cmd[0] if os.path.isfile(cmd[0]) else None)
            checks.append(Check("cli", "Startbefehl", "ok" if exe else "error",
                                shlex.join(cmd) if exe else f"Programm nicht gefunden: {cmd[0]}"))
    return checks


def test_connection(settings: Settings, cwd: Path, timeout: float = 180) -> tuple[bool, str]:
    """Startet den Agenten, öffnet eine Sitzung (ohne Prompt, also ohne KI-Kosten) und beendet ihn."""
    try:
        session = create_session(settings, cwd)
    except AgentSetupError as exc:
        return False, str(exc)
    if isinstance(session, AcpSession):
        session.connect_timeout = timeout
    done = threading.Event()
    result: dict[str, object] = {}

    def listener(event):
        if isinstance(event, (Connected, Failed)):
            result["event"] = event
            done.set()

    session.subscribe(listener)
    session.start()
    finished = done.wait(timeout + 5)
    session.close()
    event = result.get("event")
    if not finished or event is None:
        return False, "Zeitüberschreitung beim Verbindungsaufbau."
    if isinstance(event, Failed):
        return False, event.message
    parts = [f"Verbunden: {event.agent_name} {event.agent_version}".strip()]
    if event.modes:
        parts.append("Modi: " + ", ".join(m.get("name", m.get("id", "")) for m in event.modes))
    models = next((o for o in event.config_options if o.get("category") == "model"), None)
    if models:
        names = [o.get("name", o.get("value")) for o in models.get("options") or []][:8]
        parts.append("Modelle: " + ", ".join(str(n) for n in names))
    return True, "\n".join(parts)


# ---------------------------------------------------------------------------
# Terminal-Aktionen (Anmeldung, Installation)


def login_command(settings: Settings) -> list[str]:
    if settings.agent == "copilot":
        cmd = [find_copilot(settings) or "copilot", "login"]
        if settings.ghe_host.strip():
            cmd += ["--host", settings.ghe_host.strip()]
        return cmd
    return [find_claude(settings) or "claude", "auth", "login"]


def install_command(settings: Settings) -> list[str]:
    if settings.agent == "copilot":
        return ["npm", "install", "-g", "@github/copilot"]
    return ["npm", "install", "-g", ADAPTER_PACKAGE]


def open_in_terminal(cmd: list[str]) -> bool:
    """Öffnet ein Terminalfenster und führt ``cmd`` darin aus. False, wenn nicht möglich."""
    try:
        if sys.platform == "win32":
            subprocess.Popen(["cmd", "/k", subprocess.list2cmdline(cmd)], creationflags=_NEW_CONSOLE)
            return True
        if sys.platform == "darwin":
            script = Path(tempfile.mkdtemp(prefix="pager-")) / "pager-befehl.command"
            script.write_text("#!/bin/zsh -l\n" + shlex.join(cmd) + "\necho\nread -k1 '?Fertig – Taste drücken …'\n",
                              encoding="utf-8")
            script.chmod(0o755)
            subprocess.Popen(["open", str(script)])
            return True
        for term in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
            if shutil.which(term):
                subprocess.Popen([term, "-e", shlex.join(cmd)])
                return True
    except OSError:
        return False
    return False
