from pager.config import Settings
from pager.copilot.base import Backend, BackendError, BackendStatus, RunCancelled, RunHandle, RunRequest
from pager.copilot.cli import CopilotCliBackend
from pager.copilot.demo import DemoBackend


def create_backend(settings: Settings) -> Backend:
    if settings.backend == "copilot-cli":
        return CopilotCliBackend(settings)
    return DemoBackend()


__all__ = [
    "Backend", "BackendError", "BackendStatus", "CopilotCliBackend", "DemoBackend",
    "RunCancelled", "RunHandle", "RunRequest", "create_backend",
]
