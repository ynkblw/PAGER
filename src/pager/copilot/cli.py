"""Optionales Backend über die GitHub Copilot CLI (``copilot -p …``).

Nur nutzbar, wenn die Copilot CLI in der Umgebung freigegeben ist. Es werden keine Custom Agents
und keine Repositories benötigt: Der vollständige Prompt (inkl. eingebetteter Dateiinhalte) steht
in ``AUFTRAG.md`` im Arbeitsordner, per Kommandozeile geht nur ein kurzer Einstiegs-Prompt. Das
umgeht Längen- und Quoting-Grenzen von ``cmd.exe`` unter Windows.

Anmeldung an der GHE-Instanz: ``copilot login --host https://<instanz>.ghe.com``.
"""

from __future__ import annotations

import codecs
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator

from pager.config import Settings
from pager.copilot.base import Backend, BackendError, BackendStatus, RunCancelled, RunHandle, RunRequest

KICKOFF_PROMPT = (
    "Lies den Auftrag in @{prompt_file} vollständig und bearbeite ihn. "
    "Alle Unterlagen sind darin als Anhänge enthalten. "
    "Gib als Antwort ausschließlich das fertige Ergebnis in Markdown aus."
)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)


class CopilotCliBackend(Backend):
    key = "copilot-cli"

    def __init__(self, settings: Settings):
        self.settings = settings

    # ------------------------------------------------------------------
    def executable(self) -> str | None:
        if self.settings.copilot_path:
            return self.settings.copilot_path if os.path.isfile(self.settings.copilot_path) else None
        return shutil.which("copilot")

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.settings.ghe_host.strip():
            env["COPILOT_GH_HOST"] = self.settings.ghe_host.strip()
        env["NO_COLOR"] = "1"
        return env

    def build_command(self, request: RunRequest) -> list[str]:
        exe = self.executable()
        if not exe:
            raise BackendError(
                "Die GitHub Copilot CLI wurde nicht gefunden.\n"
                "Installation: winget install GitHub.Copilot  (oder npm i -g @github/copilot)\n"
                "Alternativ den Pfad in den Einstellungen hinterlegen oder den Demo-Modus nutzen."
            )
        cmd = [
            exe,
            "-p", KICKOFF_PROMPT.format(prompt_file=request.prompt_file),
            "--silent",
            "--no-color",
            f"--add-dir={request.workspace}",
        ]
        if self.settings.model.strip():
            cmd.append(f"--model={self.settings.model.strip()}")
        cmd.extend(a for a in self.settings.cli_extra_args if a.strip())
        return cmd

    # ------------------------------------------------------------------
    def check(self) -> BackendStatus:
        exe = self.executable()
        if not exe:
            return BackendStatus(False, "Copilot CLI nicht gefunden.")
        try:
            out = subprocess.run(
                [exe, "--version"], capture_output=True, text=True, timeout=20,
                env=self.env(), creationflags=_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return BackendStatus(False, f"Copilot CLI nicht ausführbar: {exc}")
        version = (out.stdout or out.stderr).strip().splitlines()[0:1]
        host = self.settings.ghe_host or "github.com"
        return BackendStatus(out.returncode == 0, f"{version[0] if version else 'Copilot CLI'} · {host}")

    def open_login(self) -> list[str]:
        """Startet ``copilot login`` in einem eigenen Konsolenfenster (Windows). Gibt den Befehl zurück."""
        exe = self.executable() or "copilot"
        cmd = [exe, "login"]
        if self.settings.ghe_host.strip():
            cmd += ["--host", self.settings.ghe_host.strip()]
        if sys.platform == "win32":
            subprocess.Popen(cmd, env=self.env(), creationflags=_NEW_CONSOLE)
        return cmd

    def start(self, request: RunRequest) -> RunHandle:
        return _CliRun(
            self.build_command(request),
            cwd=str(request.workspace),
            env=self.env(),
            timeout_s=max(1, self.settings.timeout_minutes) * 60,
        )


class _CliRun(RunHandle):
    def __init__(self, cmd: list[str], cwd: str, env: dict[str, str], timeout_s: int):
        self.cmd, self.cwd, self.env, self.timeout_s = cmd, cwd, env, timeout_s
        self._proc: subprocess.Popen | None = None
        self._cancelled = False
        self._timed_out = False
        self._lock = threading.Lock()

    def stream(self) -> Iterator[str]:
        with self._lock:
            if self._cancelled:
                raise RunCancelled()
            try:
                self._proc = subprocess.Popen(
                    self.cmd, cwd=self.cwd, env=self.env,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    creationflags=_NO_WINDOW,
                )
            except OSError as exc:
                raise BackendError(f"Copilot CLI konnte nicht gestartet werden: {exc}") from exc
        proc = self._proc

        stderr_chunks: list[bytes] = []
        err_thread = threading.Thread(
            target=lambda: stderr_chunks.extend(iter(lambda: proc.stderr.read(4096), b"")), daemon=True
        )
        err_thread.start()
        timer = threading.Timer(self.timeout_s, self._timeout)
        timer.start()

        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while chunk := proc.stdout.read1(4096):
                if text := decoder.decode(chunk):
                    yield text
            if tail := decoder.decode(b"", final=True):
                yield tail
            code = proc.wait()
        finally:
            timer.cancel()
            err_thread.join(timeout=2)

        if self._cancelled:
            raise RunCancelled()
        if self._timed_out:
            raise BackendError(f"Zeitüberschreitung nach {self.timeout_s // 60} Minuten.")
        if code != 0:
            stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
            hint = ""
            if any(k in stderr.lower() for k in ("auth", "login", "401", "token")):
                hint = "\n\nTipp: In den Einstellungen „Bei Copilot anmelden“ ausführen."
            raise BackendError(f"Copilot CLI beendet mit Code {code}.\n\n{stderr[-2000:]}{hint}")

    def _timeout(self) -> None:
        self._timed_out = True
        self._kill()

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
        self._kill()

    def _kill(self) -> None:
        proc = self._proc
        if not proc or proc.poll() is not None:
            return
        if sys.platform == "win32":
            # copilot.cmd startet node als Kindprozess → ganzen Prozessbaum beenden
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, creationflags=_NO_WINDOW)
        else:
            proc.terminate()
