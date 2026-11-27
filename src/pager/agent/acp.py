"""Client für das Agent Client Protocol (ACP, https://agentclientprotocol.com).

ACP ist JSON-RPC 2.0 über stdin/stdout des Agent-Prozesses, eine Nachricht pro Zeile. PAGER ist
der *Client* (wie ein Editor), der Agent ist z. B.

* Claude Code über den Adapter ``claude-agent-acp`` (@agentclientprotocol/claude-agent-acp),
* die GitHub Copilot CLI mit ``copilot --acp``,
* jeder andere ACP-fähige Agent.

Ablauf: ``initialize`` → ``session/new`` → beliebig oft ``session/prompt``. Während einer Antwort
schickt der Agent ``session/update``-Benachrichtigungen (Text, Gedanken, Werkzeugaufrufe, Plan) und
fragt bei Bedarf per ``session/request_permission`` nach Freigaben. ``session/cancel`` bricht ab.

PAGER bietet dem Agenten bewusst *keinen* Datei- oder Terminalzugriff über das Protokoll an
(``clientCapabilities``): Der Agent arbeitet mit seinen eigenen Werkzeugen im Arbeitsordner des
Laufs, und jede Freigabe läuft über PAGER. Angeboten werden dagegen Rückfragen als Formular
(``elicitation.form``): Darüber stellt Claude Code Auswahlfragen (``AskUserQuestion``), die
Copilot CLI ihre ``ask_user``-Fragen.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pager import __version__
from pager.agent.events import (
    ASK, AutoDecision, Closed, ConfigChanged, Connected, Failed, ModeChanged, PermissionRequest, Plan,
    QuestionAnswered, QuestionRequest, Raw, Status, TextChunk, ToolCall, TurnEnded, TurnFailed,
    TurnStarted, auto_decision,
)
from pager.agent.session import AgentSession

PROTOCOL_VERSION = 1
AUTH_REQUIRED = -32000
METHOD_NOT_FOUND = -32601

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class AgentLaunch:
    """Wie der Agent-Prozess gestartet wird (aus den Einstellungen, siehe ``profiles.py``)."""

    name: str
    command: list[str]
    env: dict[str, str] = field(default_factory=dict)  # zusätzlich zur Umgebung von PAGER
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)  # [{name, command, args}]
    login_hint: str = ""


class AcpError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


class _Call:
    def __init__(self, method: str):
        self.method = method
        self.done = threading.Event()
        self.result: Any = None
        self.error: AcpError | None = None
        self.callback = None

    def resolve(self, result: Any = None, error: AcpError | None = None) -> None:
        self.result, self.error = result, error
        self.done.set()
        if self.callback:
            self.callback(self)

    def wait(self, timeout: float | None) -> Any:
        if not self.done.wait(timeout):
            raise AcpError(-1, f"Keine Antwort auf {self.method} (Zeitüberschreitung).")
        if self.error:
            raise self.error
        return self.result


class AcpSession(AgentSession):
    def __init__(self, launch: AgentLaunch, cwd: Path, policy: str = "ask", connect_timeout: float = 180):
        super().__init__()
        self.launch = launch
        self.name = launch.name
        self.cwd = Path(cwd)
        self.policy = policy
        self.connect_timeout = connect_timeout
        self.session_id = ""
        self._proc: subprocess.Popen | None = None
        self._write_lock = threading.Lock()
        self._calls: dict[int, _Call] = {}
        self._calls_lock = threading.Lock()
        self._next_id = 0
        self._stderr: deque[str] = deque(maxlen=40)
        self._open_permissions: list[PermissionRequest | QuestionRequest] = []
        self._closing = False

    # ==================================================================
    # Öffentliche Steuerung
    def start(self) -> None:
        if self.started:
            return
        self.started = True
        threading.Thread(target=self._connect, name="acp-connect", daemon=True).start()

    def prompt(self, text: str) -> None:
        if not self.connected or self.busy:
            raise RuntimeError("Der Agent ist nicht bereit.")
        self._emit(TurnStarted(text))
        self._send_request("session/prompt", {
            "sessionId": self.session_id,
            "prompt": [{"type": "text", "text": text}],
        }, callback=self._prompt_done)

    def cancel(self) -> None:
        self._cancel_open()  # laut ACP: offene Freigaben/Rückfragen mit "cancelled" beantworten
        if self.session_id and self.busy:
            self._send_notification("session/cancel", {"sessionId": self.session_id})

    def _cancel_open(self) -> None:
        for req in list(self._open_permissions):
            if isinstance(req, QuestionRequest):
                req.cancel()
            else:
                req.respond(None)

    def set_mode(self, mode_id: str) -> None:
        def done(c: _Call):
            if c.error is None:
                self._emit(ModeChanged(mode_id))
        self._send_request("session/set_mode", {"sessionId": self.session_id, "modeId": mode_id}, done)

    def set_config_option(self, config_id: str, value: str) -> None:
        def done(c: _Call):
            if c.error is None and isinstance(c.result, dict) and "configOptions" in c.result:
                self._emit(ConfigChanged(c.result["configOptions"]))
        self._send_request("session/set_config_option",
                           {"sessionId": self.session_id, "configId": config_id, "value": value}, done)

    def close(self) -> None:
        if self.closed:
            return
        self._closing = True
        self._cancel_open()
        self._kill()
        self._fail_pending("Sitzung beendet.")
        self._emit(Closed())

    # ==================================================================
    # Verbindungsaufbau
    def _connect(self) -> None:
        self._emit(Status(f"{self.name} wird gestartet …"))
        env = {**os.environ, **self.launch.env, "NO_COLOR": "1"}
        try:
            self._proc = subprocess.Popen(
                self.launch.command, cwd=str(self.cwd), env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=_NO_WINDOW, start_new_session=sys.platform != "win32",
            )
        except OSError as exc:
            self._emit(Failed(f"{self.name} konnte nicht gestartet werden: {exc}\n\n"
                              f"Befehl: {' '.join(self.launch.command)}"))
            return
        threading.Thread(target=self._read_stdout, name="acp-stdout", daemon=True).start()
        threading.Thread(target=self._read_stderr, name="acp-stderr", daemon=True).start()

        try:
            init = self._request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                                       "terminal": False,
                                       "elicitation": {"form": {}}},
                "clientInfo": {"name": "PAGER", "title": "PAGER", "version": __version__},
            }, self.connect_timeout) or {}
            info = init.get("agentInfo") or {}
            agent_name = info.get("title") or info.get("name") or self.name
            self._emit(Status(f"Verbunden mit {agent_name} · Sitzung wird geöffnet …"))
            try:
                new = self._request("session/new", {
                    "cwd": str(self.cwd.resolve()),
                    "mcpServers": [_mcp_server(s) for s in self.launch.mcp_servers if s.get("command")],
                }, self.connect_timeout) or {}
            except AcpError as exc:
                if exc.code == AUTH_REQUIRED or "auth" in exc.message.lower() or "login" in exc.message.lower():
                    methods = ", ".join(m.get("name", m.get("id", "")) for m in init.get("authMethods") or [])
                    hint = self.launch.login_hint or "Bitte zuerst beim Agenten anmelden."
                    self._emit(Failed(f"Anmeldung erforderlich: {exc.message}\n\n{hint}"
                                      + (f"\nAngebotene Anmeldewege: {methods}" if methods else ""),
                                      login_required=True))
                    self.close()
                    return
                raise
        except AcpError as exc:
            if not self._closing:
                self._emit(Failed(self._with_stderr(f"ACP-Verbindung fehlgeschlagen: {exc.message}")))
                self.close()
            return

        self.session_id = new.get("sessionId", "")
        modes = new.get("modes") or {}
        self._emit(Connected(
            agent_name=agent_name,
            agent_version=str(info.get("version") or ""),
            session_id=self.session_id,
            modes=list(modes.get("availableModes") or []),
            current_mode=modes.get("currentModeId") or "",
            config_options=list(new.get("configOptions") or []),
            auth_methods=list(init.get("authMethods") or []),
        ))

    def _prompt_done(self, call: _Call) -> None:
        if call.error:
            self._emit(TurnFailed(call.error.message))
        else:
            self._emit(TurnEnded((call.result or {}).get("stopReason", "end_turn")))

    # ==================================================================
    # JSON-RPC
    def _request(self, method: str, params: dict, timeout: float | None) -> Any:
        call = self._send_request(method, params)
        if call is None:
            raise AcpError(-1, "Der Agent-Prozess läuft nicht.")
        return call.wait(timeout)

    def _send_request(self, method: str, params: dict, callback=None) -> _Call | None:
        with self._calls_lock:
            self._next_id += 1
            rid = self._next_id
            call = _Call(method)
            call.callback = callback
            self._calls[rid] = call
        if not self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}):
            with self._calls_lock:
                self._calls.pop(rid, None)
            if method == "session/prompt":
                self._emit(TurnFailed("Der Agent ist nicht mehr erreichbar."))
            return None
        return call

    def _send_notification(self, method: str, params: dict) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _respond(self, rid: Any, result: Any = None, error: dict | None = None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": rid}
        if error:
            msg["error"] = error
        else:
            msg["result"] = result
        self._write(msg)

    def _write(self, msg: dict) -> bool:
        proc = self._proc
        if not proc or proc.poll() is not None or not proc.stdin:
            return False
        line = json.dumps(msg, ensure_ascii=False)
        self._emit(Raw("out", line))
        try:
            with self._write_lock:
                proc.stdin.write(line.encode("utf-8") + b"\n")
                proc.stdin.flush()
        except (OSError, ValueError):
            return False
        return True

    def _read_stdout(self) -> None:
        proc = self._proc
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            self._emit(Raw("in", line))
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # z. B. Log-Ausgaben auf stdout – stehen in den Rohdaten
            if not isinstance(msg, dict):
                continue
            try:
                self._dispatch(msg)
            except Exception as exc:  # noqa: BLE001 – Protokollfehler sichtbar machen, Sitzung weiterführen
                self._emit(Raw("stderr", f"[PAGER] Nachricht nicht verarbeitet: {type(exc).__name__}: {exc}"))
        code = proc.wait()
        if not self._closing:
            self._fail_pending("Der Agent wurde beendet.")
            self._emit(Failed(self._with_stderr(f"{self.name} wurde beendet (Code {code}).")))
            self._closing = True
            self._emit(Closed())

    def _read_stderr(self) -> None:
        for raw in iter(self._proc.stderr.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                self._stderr.append(line)
                self._emit(Raw("stderr", line))

    def _dispatch(self, msg: dict) -> None:
        if "method" in msg:
            if "id" in msg:
                self._handle_request(msg["id"], msg["method"], msg.get("params") or {})
            else:
                self._handle_notification(msg["method"], msg.get("params") or {})
            return
        if "id" in msg:
            with self._calls_lock:
                call = self._calls.pop(msg["id"], None)
            if call is None:
                return
            if "error" in msg and msg["error"] is not None:
                err = msg["error"]
                call.resolve(error=AcpError(err.get("code", -1), err.get("message", "Fehler"), err.get("data")))
            else:
                call.resolve(result=msg.get("result"))

    def _fail_pending(self, message: str) -> None:
        with self._calls_lock:
            calls, self._calls = list(self._calls.values()), {}
        for call in calls:
            call.resolve(error=AcpError(-1, message))

    # ==================================================================
    # Nachrichten vom Agenten
    def _handle_notification(self, method: str, params: dict) -> None:
        if method != "session/update":
            return  # z. B. elicitation/complete (nur URL-Modus)
        update = params.get("update") or {}
        kind = update.get("sessionUpdate")
        if kind in ("agent_message_chunk", "agent_thought_chunk", "user_message_chunk"):
            text = _content_text(update.get("content"))
            if text:
                role = {"agent_message_chunk": "agent", "agent_thought_chunk": "thought"}.get(kind, "user")
                self._emit(TextChunk(text, role))
        elif kind in ("tool_call", "tool_call_update"):
            self._emit(ToolCall(
                tool_call_id=str(update.get("toolCallId", "")),
                title=update.get("title") or "",
                kind=update.get("kind") or "",
                status=update.get("status") or "",
                detail=_tool_detail(update),
                update=kind == "tool_call_update",
            ))
        elif kind == "plan":
            self._emit(Plan(list(update.get("entries") or [])))
        elif kind == "current_mode_update":
            self._emit(ModeChanged(update.get("currentModeId", "")))
        elif kind == "config_option_update":
            self._emit(ConfigChanged(list(update.get("configOptions") or [])))

    def _handle_request(self, rid: Any, method: str, params: dict) -> None:
        if method == "session/request_permission":
            self._handle_permission(rid, params)
            return
        if method == "elicitation/create":
            self._handle_question(rid, params)
            return
        # Datei-/Terminalzugriff bietet PAGER bewusst nicht an (siehe clientCapabilities)
        self._respond(rid, error={"code": METHOD_NOT_FOUND, "message": f"PAGER unterstützt {method} nicht."})

    def _handle_permission(self, rid: Any, params: dict) -> None:
        tool = params.get("toolCall") or {}
        options = list(params.get("options") or [])

        def responder(option_id: str | None) -> None:
            if req in self._open_permissions:
                self._open_permissions.remove(req)
            outcome = ({"outcome": "selected", "optionId": option_id} if option_id
                       else {"outcome": "cancelled"})
            self._respond(rid, {"outcome": outcome})

        req = PermissionRequest(tool.get("title") or "", tool.get("kind") or "", options,
                                _tool_detail(tool), responder)
        self._open_permissions.append(req)
        decision = auto_decision(self.policy, req)
        if decision == ASK:
            self._emit(req)
            return
        if decision == "allow":
            req.allow()
        else:
            req.reject()
        self._emit(AutoDecision(req.title, decision == "allow"))

    def _handle_question(self, rid: Any, params: dict) -> None:
        if params.get("mode", "form") != "form":
            self._respond(rid, {"action": "decline"})  # URL-Rückfragen bietet PAGER nicht an
            return
        message = params.get("message") or ""

        def responder(action: str, content: dict | None) -> None:
            if req in self._open_permissions:
                self._open_permissions.remove(req)
            result: dict[str, Any] = {"action": action}
            if action == "accept":
                result["content"] = content or {}
            self._respond(rid, result)
            summary = {"decline": "übersprungen", "cancel": "abgebrochen"}.get(action) or ", ".join(
                f"{v if not isinstance(v, list) else ', '.join(map(str, v))}" for v in (content or {}).values()
                if v not in ("", None, [])) or "–"
            self._emit(QuestionAnswered(message, summary))

        req = QuestionRequest(message, params.get("requestedSchema") or {}, responder)
        self._open_permissions.append(req)
        self._emit(req)

    # ==================================================================
    def _with_stderr(self, message: str) -> str:
        tail = "\n".join(list(self._stderr)[-12:])
        return f"{message}\n\n{tail}" if tail else message

    def _kill(self) -> None:
        proc = self._proc
        if not proc or proc.poll() is not None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, creationflags=_NO_WINDOW)
        else:
            try:
                os.killpg(proc.pid, signal.SIGTERM)  # auch Kindprozesse (npx → node)
            except (OSError, ProcessLookupError):
                proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


# ---------------------------------------------------------------------------
# Hilfsfunktionen


def _mcp_server(server: dict[str, Any]) -> dict[str, Any]:
    """MCP-Server aus den Einstellungen → ACP-Format (stdio)."""
    env = server.get("env") or {}
    return {
        "name": server.get("name") or Path(server["command"]).stem,
        "command": server["command"],
        "args": [str(a) for a in server.get("args") or []],
        "env": [{"name": k, "value": str(v)} for k, v in env.items()],
    }


def _content_text(content: Any) -> str:
    if isinstance(content, dict):
        if content.get("type") == "text":
            return content.get("text") or ""
        if content.get("type") == "resource_link":
            return f"[{content.get('name') or content.get('uri')}]"
        if content.get("type") in ("image", "audio"):
            return f"[{content['type']}]"
    if isinstance(content, list):
        return "".join(_content_text(c) for c in content)
    return ""


def _tool_detail(update: dict) -> str:
    """Kurze lesbare Zusammenfassung eines Werkzeugaufrufs (Eingabe, Pfade, Ergebnis)."""
    parts: list[str] = []
    for loc in update.get("locations") or []:
        if isinstance(loc, dict) and loc.get("path"):
            parts.append(loc["path"])
    raw_input = update.get("rawInput")
    if raw_input and not parts:
        text = raw_input if isinstance(raw_input, str) else json.dumps(raw_input, ensure_ascii=False)
        parts.append(text)
    for item in update.get("content") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "content":
            if text := _content_text(item.get("content")):
                parts.append(text)
        elif item.get("type") == "diff" and item.get("path"):
            parts.append(f"Änderung an {item['path']}")
        elif item.get("type") == "terminal":
            parts.append(f"Terminal {item.get('terminalId', '')}")
    text = " · ".join(p.strip() for p in parts if p and p.strip())
    return text[:600] + ("…" if len(text) > 600 else "")
