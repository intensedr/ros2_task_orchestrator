"""Implementation helpers for the built-in system/mission task."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from task_orchestrator_msgs.msg import TaskStatusV1


class MissionTaskValidationError(ValueError):
    """Raised when a mission payload is invalid."""


@dataclass(frozen=True)
class MissionSubtask:
    subtask_id: str
    task_id: str
    task_name: str
    task_data_json: str
    allow_skipping: bool = False
    max_attempts: int = 1
    timeout_sec: float = 0.0
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    condition_json: str = ""


@dataclass(frozen=True)
class MissionTaskRequest:
    mission_id: str
    subtasks: tuple[MissionSubtask, ...]


@dataclass(frozen=True)
class MissionSubtaskResult:
    subtask_id: str
    task_id: str
    task_name: str
    status: str
    skipped: bool
    attempts: int
    error_code: str = ""
    error_message: str = ""


class MissionTaskParser:
    """Parses mission JSON into a deterministic linear mission request."""

    def parse(self, task_data_json: str, default_mission_id: str) -> MissionTaskRequest:
        payload_text = task_data_json or "{}"
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise MissionTaskValidationError(f"task_data_json is not valid JSON: {exc.msg}") from exc

        if not isinstance(payload, dict):
            raise MissionTaskValidationError("task_data_json must decode to an object")

        mission_id = payload.get("mission_id", default_mission_id)
        if not isinstance(mission_id, str) or not mission_id:
            raise MissionTaskValidationError("mission_id must be a non-empty string")

        subtasks_payload = payload.get("subtasks", [])
        if not isinstance(subtasks_payload, list):
            raise MissionTaskValidationError("subtasks must be a list")

        subtasks = tuple(
            self._parse_subtask(item, mission_id=mission_id, index=index)
            for index, item in enumerate(subtasks_payload, start=1)
        )
        return MissionTaskRequest(mission_id=mission_id, subtasks=subtasks)

    def result_json(
        self,
        mission_id: str,
        status: str,
        mission_results: list[MissionSubtaskResult],
        error_code: str = "",
        error_message: str = "",
    ) -> str:
        return json.dumps(
            {
                "mission_id": mission_id,
                "status": status,
                "error_code": error_code,
                "error_message": error_message,
                "mission_results": [
                    {
                        "subtask_id": result.subtask_id,
                        "task_id": result.task_id,
                        "task_name": result.task_name,
                        "status": result.status,
                        "skipped": result.skipped,
                        "attempts": result.attempts,
                        "error_code": result.error_code,
                        "error_message": result.error_message,
                    }
                    for result in mission_results
                ],
            },
            sort_keys=True,
        )

    def _parse_subtask(self, item: Any, mission_id: str, index: int) -> MissionSubtask:
        if not isinstance(item, dict):
            raise MissionTaskValidationError(f"subtasks[{index - 1}] must be an object")

        subtask_id = item.get("subtask_id", f"subtask-{index}")
        if not isinstance(subtask_id, str) or not subtask_id:
            raise MissionTaskValidationError(f"subtasks[{index - 1}].subtask_id must be a non-empty string")

        task_name = item.get("task_name")
        if not isinstance(task_name, str) or not task_name:
            raise MissionTaskValidationError(f"subtasks[{index - 1}].task_name must be a non-empty string")

        task_id = item.get("task_id", f"{mission_id}/{subtask_id}")
        if not isinstance(task_id, str) or not task_id:
            raise MissionTaskValidationError(f"subtasks[{index - 1}].task_id must be a non-empty string")

        task_data_json = item.get("task_data_json", "{}")
        if isinstance(task_data_json, dict):
            task_data_json = json.dumps(task_data_json, sort_keys=True)
        if not isinstance(task_data_json, str):
            raise MissionTaskValidationError(f"subtasks[{index - 1}].task_data_json must be a string or object")

        max_attempts = int(item.get("max_attempts", 1))
        if max_attempts < 1:
            raise MissionTaskValidationError(f"subtasks[{index - 1}].max_attempts must be at least 1")

        depends_on = item.get("depends_on", [])
        if not isinstance(depends_on, list) or not all(isinstance(value, str) for value in depends_on):
            raise MissionTaskValidationError(f"subtasks[{index - 1}].depends_on must be a list of strings")

        condition_json = item.get("condition_json", "")
        if isinstance(condition_json, dict):
            condition_json = json.dumps(condition_json, sort_keys=True)
        if not isinstance(condition_json, str):
            raise MissionTaskValidationError(f"subtasks[{index - 1}].condition_json must be a string or object")

        return MissionSubtask(
            subtask_id=subtask_id,
            task_id=task_id,
            task_name=task_name,
            task_data_json=task_data_json,
            allow_skipping=bool(item.get("allow_skipping", False)),
            max_attempts=max_attempts,
            timeout_sec=float(item.get("timeout_sec", 0.0)),
            depends_on=tuple(depends_on),
            condition_json=condition_json,
        )


def mission_status_from_subtask_result_status(status: str) -> str:
    if status == TaskStatusV1.CANCELED:
        return TaskStatusV1.CANCELED
    return TaskStatusV1.ERROR
