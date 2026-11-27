from pager.config import Settings
from pager.copilot.acp_backend import AcpBackend
from pager.copilot.base import Backend, BackendError, BackendStatus, RunCancelled, RunHandle, RunRequest
from pager.copilot.cli import CopilotCliBackend
from pager.copilot.demo import DemoBackend


def create_backend(settings: Settings, session=None) -> Backend:
    """``session``: bereits laufende Agent-Sitzung (aus einem Agent-Fenster), wird weiterverwendet."""
    if settings.backend == "acp":
        return AcpBackend(settings, session)
    if settings.backend == "copilot-cli":
        return CopilotCliBackend(settings)
    return DemoBackend()


__all__ = [
    "AcpBackend", "Backend", "BackendError", "BackendStatus", "CopilotCliBackend", "DemoBackend",
    "RunCancelled", "RunHandle", "RunRequest", "create_backend",
]
