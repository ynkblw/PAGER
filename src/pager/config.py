"""Benutzereinstellungen (persistiert als JSON im Benutzerprofil).

Windows: %APPDATA%\\PAGER\\settings.json  ·  Läufe: %LOCALAPPDATA%\\PAGER\\runs
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

from pager import APP_NAME


def config_dir() -> Path:
    return Path(os.environ.get("PAGER_CONFIG_DIR") or user_config_dir(APP_NAME, appauthor=False))


def data_dir() -> Path:
    return Path(os.environ.get("PAGER_DATA_DIR") or user_data_dir(APP_NAME, appauthor=False))


def user_workflow_dir() -> Path:
    """Persönlicher Ordner für eigene Walkthroughs."""
    return config_dir() / "workflows"


BACKENDS = {
    "chat": "Copilot Chat (Prompt kopieren & einfügen)",
    "acp": "KI-Agent über ACP (Claude Code · GitHub Copilot CLI)",
    "copilot-cli": "GitHub Copilot CLI – Einzelaufruf ohne ACP (alt)",
    "demo": "Demo-Modus (ohne KI)",
}


def builtin_workflow_dir() -> Path:
    """Mitgelieferte Beispiel-Walkthroughs (schreibgeschützt, Bearbeiten erzeugt eine Kopie)."""
    return Path(__file__).resolve().parent / "resources" / "workflows"


@dataclass
class Settings:
    # Verbindung
    backend: str = "chat"
    ghe_host: str = ""  # z. B. https://<instanz>.ghe.com – leer = github.com
    chat_url: str = ""  # leer = <ghe_host>/copilot

    # Copilot CLI (nur falls in der Umgebung freigegeben)
    copilot_path: str = ""  # leer = automatisch im PATH suchen
    model: str = ""  # leer = Copilot-Standard

    # KI-Agent über ACP
    agent: str = "claude-code"  # claude-code | copilot | custom
    claude_path: str = ""  # leer = claude im PATH suchen
    use_installed_claude: bool = True  # Adapter nutzt die installierte Claude Code CLI
    acp_adapter: str = ""  # Befehl des Claude-Code-ACP-Adapters, leer = automatisch
    agent_command: str = ""  # Startbefehl für einen eigenen ACP-Agenten
    agent_args: str = ""  # zusätzliche Parameter für den Agenten
    agent_model: str = ""  # leer = Standard des Agenten
    permission_policy: str = "ask"  # ask | read | deny | allow
    mcp_servers: list[dict] = field(default_factory=list)  # [{name, command, args}] – z. B. später TIA

    # Walkthroughs
    show_examples: bool = True  # mitgelieferte Beispiel-Walkthroughs anzeigen
    workflow_dirs: list[str] = field(default_factory=list)  # zusätzliche Ordner mit *.yaml

    # Erweitert
    cli_extra_args: list[str] = field(default_factory=list)
    timeout_minutes: int = 30
    runs_dir: str = ""  # leer = Standard im Benutzerprofil

    # ------------------------------------------------------------------
    @property
    def runs_path(self) -> Path:
        return Path(self.runs_dir) if self.runs_dir else data_dir() / "runs"

    @property
    def workflow_paths(self) -> list[Path]:
        builtin = [builtin_workflow_dir()] if self.show_examples else []
        return [*builtin, user_workflow_dir(), *(Path(p) for p in self.workflow_dirs if p.strip())]

    @property
    def resolved_chat_url(self) -> str:
        if self.chat_url.strip():
            return self.chat_url.strip()
        return f"{(self.ghe_host.strip() or 'https://github.com').rstrip('/')}/copilot"

    @property
    def backend_label(self) -> str:
        if self.backend == "acp":
            return {"claude-code": "Claude Code", "copilot": "Copilot CLI"}.get(self.agent, "ACP-Agent")
        return {"chat": "Copilot Chat", "copilot-cli": "Copilot CLI", "demo": "Demo-Modus"}.get(
            self.backend, self.backend)

    # ------------------------------------------------------------------
    @staticmethod
    def file() -> Path:
        return config_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        path = cls.file()
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self) -> None:
        path = self.file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
