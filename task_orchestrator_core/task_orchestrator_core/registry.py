"""Task registry loading and lookup."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import yaml

from task_orchestrator_core.task_models import SYSTEM_TASKS, TaskConfigError, TaskDefinition


class TaskRegistry:
    """In-memory registry of configured task definitions."""

    def __init__(self, tasks: Iterable[TaskDefinition] = ()) -> None:
        self._tasks: dict[str, TaskDefinition] = {}
        for task in tasks:
            self.add(task)

    @classmethod
    def with_system_tasks(cls) -> "TaskRegistry":
        return cls(SYSTEM_TASKS)

    @classmethod
    def from_yaml_file(cls, path: str | Path, include_system_tasks: bool = True) -> "TaskRegistry":
        try:
            with Path(path).expanduser().open("r", encoding="utf-8") as stream:
                config = yaml.safe_load(stream) or {}
        except OSError as exc:
            raise TaskConfigError(f"cannot read task config {path}: {exc}") from exc
        except yaml.YAMLError as exc:
            raise TaskConfigError(f"cannot parse task config {path}: {exc}") from exc
        return cls.from_config(config, include_system_tasks=include_system_tasks)

    @classmethod
    def from_config(cls, config: dict[str, Any], include_system_tasks: bool = True) -> "TaskRegistry":
        if not isinstance(config, dict):
            raise TaskConfigError("task config must be a mapping")

        task_items = config.get("tasks", [])
        if task_items is None:
            task_items = []
        if not isinstance(task_items, list):
            raise TaskConfigError("tasks must be a list")

        initial_tasks = list(SYSTEM_TASKS) if include_system_tasks else []
        registry = cls(initial_tasks)

        for index, item in enumerate(task_items):
            if not isinstance(item, dict):
                raise TaskConfigError(f"tasks[{index}] must be a mapping")
            registry.add(TaskDefinition.from_mapping(item))

        return registry

    def add(self, task: TaskDefinition) -> None:
        if task.task_name in self._tasks:
            raise TaskConfigError(f"duplicate task_name: {task.task_name}")
        self._tasks[task.task_name] = task

    def get(self, task_name: str) -> TaskDefinition | None:
        return self._tasks.get(task_name)

    def list(self, include_system_tasks: bool = True) -> list[TaskDefinition]:
        tasks = list(self._tasks.values())
        if not include_system_tasks:
            tasks = [task for task in tasks if not task.is_system_task]
        return sorted(tasks, key=lambda task: task.task_name)
