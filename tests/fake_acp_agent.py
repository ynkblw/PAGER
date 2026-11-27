"""Simulierter ACP-Agent für Tests (keine KI). Spricht JSON-RPC 2.0 über stdin/stdout, eine Zeile je Nachricht.

Verhalten je nach Prompt-Text:
* enthält ``PERMISSION`` → Werkzeugaufruf + ``session/request_permission``, Antwort „erlaubt“/„abgelehnt“
* enthält ``QUESTION``   → Rückfrage per ``elicitation/create`` (Formular), Antwort „Antwort: <Auswahl>“
* enthält ``SLOW``       → streamt langsam, bis ``session/cancel`` kommt (stopReason ``cancelled``)
* sonst                  → „Echo: <text>“ in zwei Teilen + ein Gedanke
Umgebung: ``FAKE_AUTH_REQUIRED=1`` → ``session/new`` meldet „Authentication required“.
"""

import json
import os
import sys
import threading
import time

lock = threading.Lock()
pending: dict = {}
cancelled = threading.Event()


def send(msg):
    with lock:
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()


def update(session_id, upd):
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session_id, "update": upd}})


def chunk(session_id, text, kind="agent_message_chunk"):
    update(session_id, {"sessionUpdate": kind, "content": {"type": "text", "text": text}})


def handle_prompt(rid, params):
    sid = params["sessionId"]
    text = "".join(b.get("text", "") for b in params["prompt"])
    cancelled.clear()
    if "PERMISSION" in text:
        update(sid, {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "Datei schreiben",
                     "kind": "edit", "status": "pending", "locations": [{"path": "notiz.txt"}]})
        ev = threading.Event()
        pending[900] = ev
        send({"jsonrpc": "2.0", "id": 900, "method": "session/request_permission", "params": {
            "sessionId": sid,
            "toolCall": {"toolCallId": "t1", "title": "Datei schreiben", "kind": "edit"},
            "options": [{"optionId": "ok", "name": "Erlauben", "kind": "allow_once"},
                        {"optionId": "no", "name": "Ablehnen", "kind": "reject_once"}]}})
        ev.wait(10)
        outcome = pending.pop("900-result", {}).get("outcome", {})
        allowed = outcome.get("outcome") == "selected" and outcome.get("optionId") == "ok"
        update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "t1",
                     "status": "completed" if allowed else "failed"})
        chunk(sid, "erlaubt" if allowed else "abgelehnt")
        send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
        return
    if "QUESTION" in text:  # wie Claude Codes AskUserQuestion über claude-agent-acp
        ev = threading.Event()
        pending[901] = ev
        send({"jsonrpc": "2.0", "id": 901, "method": "elicitation/create", "params": {
            "mode": "form", "sessionId": sid, "message": "Welche Farbe?",
            "requestedSchema": {"type": "object", "properties": {
                "question_0": {"type": "string", "title": "Farbe", "oneOf": [
                    {"const": "Blau", "title": "Blau"}, {"const": "Rot", "title": "Rot", "description": "kräftig"}]},
                "question_0_custom": {"type": "string", "title": "Other"}}}}})
        ev.wait(10)
        result = pending.pop("901-result", {})
        answer = result.get("content", {}).get("question_0") if result.get("action") == "accept" else result.get("action")
        chunk(sid, f"Antwort: {answer}")
        send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
        return
    if "SLOW" in text:
        for i in range(200):
            if cancelled.wait(0.05):
                send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "cancelled"}})
                return
            chunk(sid, f"{i} ")
        send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
        return
    chunk(sid, "nachdenken …", "agent_thought_chunk")
    chunk(sid, "Echo: ")
    chunk(sid, text)
    send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})


def main():
    sys.stderr.write("fake agent gestartet\n")
    sys.stderr.flush()
    print("kein JSON – muss ignoriert werden", flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method is None:  # Antwort auf unsere Anfrage (Freigabe)
            if rid in pending:
                pending[f"{rid}-result"] = msg.get("result") or {}
                pending.pop(rid).set()
            continue
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": 1, "agentCapabilities": {"loadSession": False},
                "agentInfo": {"name": "fake", "title": "Fake-Agent", "version": "0.1"},
                "authMethods": [{"id": "login", "name": "Fake-Login"}]}})
        elif method == "session/new":
            if os.environ.get("FAKE_AUTH_REQUIRED"):
                send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": "Authentication required"}})
                continue
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "sessionId": "s1",
                "modes": {"currentModeId": "default", "availableModes": [
                    {"id": "default", "name": "Standard"}, {"id": "plan", "name": "Planen"}]},
                "configOptions": [{"id": "model", "name": "Model", "category": "model", "type": "select",
                                   "currentValue": "a", "options": [{"value": "a", "name": "Modell A"},
                                                                    {"value": "b", "name": "Modell B"}]}],
                "_cwd": params.get("cwd"), "_mcp": params.get("mcpServers")}})
        elif method == "session/prompt":
            threading.Thread(target=handle_prompt, args=(rid, params), daemon=True).start()
        elif method == "session/cancel":
            cancelled.set()
        elif method == "session/set_mode":
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
            update(params["sessionId"], {"sessionUpdate": "current_mode_update", "currentModeId": params["modeId"]})
        else:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unknown"}})
    time.sleep(0.05)


if __name__ == "__main__":
    main()
