"""Internal extension hooks for task events."""

from __future__ import annotations

from typing import Any, Protocol

from task_orchestrator_msgs.msg import TaskEventV1


class TaskEventHook(Protocol):
    """Receives task events after they are materialized and before publication."""

    def handle_event(self, event: TaskEventV1, data: dict[str, Any]) -> None:
        """Handle an event without mutating public orchestrator state."""
