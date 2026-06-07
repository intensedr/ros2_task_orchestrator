"""Pure Python task configuration models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class TaskConfigError(ValueError):
    """Raised when task configuration is malformed."""


def _as_string_list(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise TaskConfigError(f"{field_name} must be a list of strings")
    if not all(isinstance(item, str) for item in value):
        raise TaskConfigError(f"{field_name} must be a list of strings")
    return tuple(value)


@dataclass(frozen=True)
class TaskDefinition:
    """Declarative description of a task exposed by the orchestrator."""

    task_name: str
    topic: str = ""
    msg_interface: str = ""
    task_server_type: str = "action"
    blocking: bool = False
    cancel_on_stop: bool = True
    cancel_reported_as_success: bool = False
    reentrant: bool = True
    is_system_task: bool = False
    priority_default: int = 0
    cancel_timeout: float = 5.0
    resources: tuple[str, ...] = field(default_factory=tuple)
    tags: tuple[str, ...] = field(default_factory=tuple)
    task_group: str = ""
    capability_tags: tuple[str, ...] = field(default_factory=tuple)
    queue_on_conflict_default: bool = False

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "TaskDefinition":
        """Create a task definition from a YAML mapping."""
        task_name = data.get("task_name", data.get("name"))
        if not isinstance(task_name, str) or not task_name:
            raise TaskConfigError("task_name is required")

        task_server_type = data.get("task_server_type", data.get("type", "action"))
        if not isinstance(task_server_type, str) or not task_server_type:
            raise TaskConfigError(f"task_server_type for {task_name} must be a non-empty string")

        topic = data.get("topic", "")
        msg_interface = data.get("msg_interface", data.get("interface", ""))
        if not isinstance(topic, str):
            raise TaskConfigError(f"topic for {task_name} must be a string")
        if not isinstance(msg_interface, str):
            raise TaskConfigError(f"msg_interface for {task_name} must be a string")
        task_group = data.get("task_group", "")
        if task_group is None:
            task_group = ""
        if not isinstance(task_group, str):
            raise TaskConfigError(f"task_group for {task_name} must be a string")

        return cls(
            task_name=task_name,
            topic=topic,
            msg_interface=msg_interface,
            task_server_type=task_server_type,
            blocking=bool(data.get("blocking", False)),
            cancel_on_stop=bool(data.get("cancel_on_stop", True)),
            cancel_reported_as_success=bool(data.get("cancel_reported_as_success", False)),
            reentrant=bool(data.get("reentrant", True)),
            is_system_task=bool(data.get("is_system_task", False)),
            priority_default=int(data.get("priority_default", data.get("priority", 0))),
            cancel_timeout=float(data.get("cancel_timeout", 5.0)),
            resources=_as_string_list(data.get("resources"), "resources"),
            tags=_as_string_list(data.get("tags"), "tags"),
            task_group=task_group,
            capability_tags=_as_string_list(data.get("capability_tags"), "capability_tags"),
            queue_on_conflict_default=bool(data.get("queue_on_conflict_default", False)),
        )


SYSTEM_CANCEL_TASK = TaskDefinition(
    task_name="system/cancel_task",
    topic="",
    msg_interface="task_orchestrator_msgs/srv/CancelTasksV1",
    task_server_type="system/cancel_task",
    blocking=False,
    cancel_on_stop=False,
    reentrant=True,
    is_system_task=True,
    priority_default=0,
    cancel_timeout=0.0,
    resources=(),
    tags=("system", "control"),
)


SYSTEM_WAIT_TASK = TaskDefinition(
    task_name="system/wait",
    topic="",
    msg_interface="task_orchestrator_msgs/action/WaitV1",
    task_server_type="system/wait",
    blocking=False,
    cancel_on_stop=True,
    reentrant=True,
    is_system_task=True,
    priority_default=0,
    cancel_timeout=0.0,
    resources=(),
    tags=("system",),
)


SYSTEM_MISSION_TASK = TaskDefinition(
    task_name="system/mission",
    topic="",
    msg_interface="task_orchestrator_msgs/action/MissionV1",
    task_server_type="system/mission",
    blocking=True,
    cancel_on_stop=True,
    reentrant=False,
    is_system_task=True,
    priority_default=0,
    cancel_timeout=5.0,
    resources=(),
    tags=("system", "mission"),
)


SYSTEM_STOP_TASK = TaskDefinition(
    task_name="system/stop",
    topic="",
    msg_interface="task_orchestrator_msgs/srv/StopTasksV1",
    task_server_type="system/stop",
    blocking=False,
    cancel_on_stop=False,
    reentrant=True,
    is_system_task=True,
    priority_default=0,
    cancel_timeout=0.0,
    resources=(),
    tags=("system", "control"),
)


SYSTEM_TASKS = (SYSTEM_CANCEL_TASK, SYSTEM_MISSION_TASK, SYSTEM_STOP_TASK, SYSTEM_WAIT_TASK)
