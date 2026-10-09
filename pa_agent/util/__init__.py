"""PA Agent utility package."""

from pa_agent.util.threading import CancelToken, OrchestratorEvent
from pa_agent.util.logging import configure_logging, update_api_key

try:
    from pa_agent.util.event_bus import EventBus
except ModuleNotFoundError as exc:
    if exc.name != "PyQt6":
        raise
    EventBus = None

__all__ = ["CancelToken", "OrchestratorEvent", "EventBus", "configure_logging", "update_api_key"]
