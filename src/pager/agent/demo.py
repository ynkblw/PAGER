"""Simulierter Agent: verhält sich wie eine ACP-Sitzung, ruft aber keine KI auf.

Damit lassen sich Agent-Fenster und Walkthroughs mit Agent-Schritten ohne Claude Code oder
Copilot erproben. Enthält eine Nachricht „/werkzeug“ (oder bittet sie, pager_test.txt anzulegen),
simuliert der Demo-Agent einen Werkzeugaufruf mit Freigabe-Anfrage; „/frage“ (oder „Rückfrage“)
simuliert eine Auswahl-Rückfrage wie Claude Codes ``AskUserQuestion``.
"""

from __future__ import annotations

import json
import random
import re
import threading

from pager.agent.events import (
    ASK, AutoDecision, Closed, Connected, PermissionRequest, QuestionAnswered, QuestionRequest, Raw, Status,
    TextChunk, ToolCall, TurnEnded, TurnStarted, auto_decision,
)
from pager.agent.session import AgentSession


class DemoAgentSession(AgentSession):
    name = "Demo-Agent"

    def __init__(self, policy: str = "ask", speed: float = 1.0):
        super().__init__()
        self.policy = policy
        self.speed = speed
        self._cancel = threading.Event()
        self._turn = 0

    def start(self) -> None:
        if self.started:
            return
        self.started = True

        def run():
            self._emit(Status("Demo-Agent wird gestartet …"))
            self._raw_out("initialize", {"protocolVersion": 1, "clientInfo": {"name": "PAGER"}})
            self._sleep(0.3)
            self._raw_in({"agentInfo": {"name": "pager-demo", "title": "Demo-Agent", "version": "1.0"}})
            self._emit(Connected("Demo-Agent", "1.0", "demo-session",
                                 modes=[{"id": "default", "name": "Standard"}, {"id": "plan", "name": "Planen"}],
                                 current_mode="default"))

        threading.Thread(target=run, daemon=True).start()

    def prompt(self, text: str) -> None:
        if not self.ready:
            raise RuntimeError("Der Agent ist nicht bereit.")
        self._cancel.clear()
        self._turn += 1
        self._emit(TurnStarted(text))
        self._raw_out("session/prompt", {"sessionId": "demo-session", "prompt": [{"type": "text", "text": text}]})
        threading.Thread(target=self._answer, args=(text,), daemon=True).start()

    def cancel(self) -> None:
        self._cancel.set()

    def close(self) -> None:
        self._cancel.set()
        self._emit(Closed())

    # ------------------------------------------------------------------
    def _answer(self, text: str) -> None:
        if self._sleep(0.6):
            return self._end("cancelled")
        self._chunk("Ich prüfe die Nachricht …", "thought")
        if re.search(r"/werkzeug|pager_test\.txt", text, re.I):
            if not self._tool_demo():
                return self._end("cancelled")
        answer = ""
        if re.search(r"/frage|rückfrage", text, re.I):
            answer = self._question_demo()
            if answer is None:
                return self._end("cancelled")
        reply = (f"Deine Auswahl: **{answer}**\n\n" if answer else "") + (
            f"**Demo-Antwort {self._turn}** – es wurde keine KI aufgerufen.\n\n"
            f"Empfangen ({len(text)} Zeichen):\n\n> {text.strip()[:400]}\n\n"
            "Im Modus „KI-Agent (ACP)“ antwortet an dieser Stelle Claude Code bzw. später die "
            "GitHub Copilot CLI. Schreib „/werkzeug“ für eine Freigabe-Anfrage oder „/frage“ für "
            "eine Rückfrage."
        )
        for token in re.findall(r"\S+\s*|\s+", reply):
            if self._sleep(random.uniform(0.005, 0.02)):
                return self._end("cancelled")
            self._chunk(token)
        self._end("end_turn")

    def _tool_demo(self) -> bool:
        done = threading.Event()
        allowed = {"value": False}

        def responder(option_id):
            allowed["value"] = option_id == "allow"
            done.set()

        options = [{"optionId": "allow", "name": "Erlauben", "kind": "allow_once"},
                   {"optionId": "reject", "name": "Ablehnen", "kind": "reject_once"}]
        self._emit(ToolCall("demo-1", "Datei lesen: beispiel.txt", "read", "pending"))
        req = PermissionRequest("Datei lesen: beispiel.txt", "read", options, "beispiel.txt", responder)
        decision = auto_decision(self.policy, req)
        if decision == ASK:
            self._emit(req)
        else:
            if decision == "allow":
                req.allow()
            else:
                req.reject()
            self._emit(AutoDecision(req.title, decision == "allow"))
        while not done.wait(0.1):
            if self._cancel.is_set():
                req.respond(None)
                return False
        status = "completed" if allowed["value"] else "failed"
        self._emit(ToolCall("demo-1", "Datei lesen: beispiel.txt", "read", status,
                            "Inhalt: Hallo aus der Demo" if allowed["value"] else "abgelehnt", update=True))
        return True

    def _question_demo(self) -> str | None:
        done = threading.Event()
        result: dict = {}

        def responder(action, content):
            result.update(action=action, content=content or {})
            done.set()

        schema = {"type": "object", "properties": {
            "question_0": {"type": "string", "title": "Farbe", "oneOf": [
                {"const": "Blau", "title": "Blau", "description": "ruhig"},
                {"const": "Grün", "title": "Grün", "description": "frisch"},
                {"const": "Rot", "title": "Rot", "description": "kräftig"}]},
            "question_0_custom": {"type": "string", "title": "Other",
                                  "description": "Eigene Antwort (optional)"},
        }}
        req = QuestionRequest("Welche Farbe magst du am liebsten?", schema, responder)
        self._emit(req)
        while not done.wait(0.1):
            if self._cancel.is_set():
                req.cancel()
                return None
        if result["action"] == "cancel":
            return None
        content = result["content"]
        answer = content.get("question_0_custom") or content.get("question_0") or "übersprungen"
        self._emit(QuestionAnswered(req.message, str(answer)))
        return str(answer)

    def _chunk(self, text: str, role: str = "agent") -> None:
        kind = "agent_thought_chunk" if role == "thought" else "agent_message_chunk"
        self._raw_in({"method": "session/update", "params": {"update": {
            "sessionUpdate": kind, "content": {"type": "text", "text": text}}}})
        self._emit(TextChunk(text, role))

    def _end(self, reason: str) -> None:
        self._raw_in({"result": {"stopReason": reason}})
        self._emit(TurnEnded(reason))

    def _sleep(self, seconds: float) -> bool:
        return self._cancel.wait(seconds / max(self.speed, 0.01))

    def _raw_out(self, method: str, params: dict) -> None:
        self._emit(Raw("out", json.dumps({"jsonrpc": "2.0", "method": method, "params": params}, ensure_ascii=False)))

    def _raw_in(self, msg: dict) -> None:
        self._emit(Raw("in", json.dumps({"jsonrpc": "2.0", **msg}, ensure_ascii=False)))
