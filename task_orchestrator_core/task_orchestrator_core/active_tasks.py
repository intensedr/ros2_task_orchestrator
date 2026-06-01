"""In-memory active task tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


class DuplicateActiveTaskError(ValueError):
    """Raised when a task ID is already active."""


@dataclass(frozen=True)
class ActiveTaskEntry:
    """A task currently owned by the orchestrator."""

    api_version: str
    task_id: str
    task_name: str
    source: str
    correlation_id: str
    priority: int
    task_status: str
    created_at: Any
    started_at: Any
    tags: tuple[str, ...]
    task_server_type: str = ""
    blocking: bool = False
    cancel_on_stop: bool = False
    cancel_callback: Callable[[], bool] | None = field(default=None, compare=False, repr=False)


class ActiveTaskRegistry:
    """Tracks active tasks by task ID."""

    def __init__(self) -> None:
        self._tasks: dict[str, ActiveTaskEntry] = {}

    def add(self, task: ActiveTaskEntry) -> None:
        if task.task_id in self._tasks:
            raise DuplicateActiveTaskError(task.task_id)
        self._tasks[task.task_id] = task

    def remove(self, task_id: str) -> ActiveTaskEntry | None:
        return self._tasks.pop(task_id, None)

    def get(self, task_id: str) -> ActiveTaskEntry | None:
        return self._tasks.get(task_id)

    def matching(
        self,
        task_ids: list[str] | tuple[str, ...] | None = None,
        source: str = "",
        correlation_id: str = "",
    ) -> list[ActiveTaskEntry]:
        requested_ids = set(task_ids or [])
        return [
            task
            for task in self.list()
            if (not requested_ids or task.task_id in requested_ids)
            and (not source or task.source == source)
            and (not correlation_id or task.correlation_id == correlation_id)
        ]

    def list(self) -> list[ActiveTaskEntry]:
        return sorted(self._tasks.values(), key=lambda task: _time_key(task.created_at))

    def __len__(self) -> int:
        return len(self._tasks)


def _time_key(value: Any) -> tuple[int, int, str]:
    sec = getattr(value, "sec", 0)
    nanosec = getattr(value, "nanosec", 0)
    return (sec, nanosec, str(value))
