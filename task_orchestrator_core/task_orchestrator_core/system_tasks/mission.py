"""Implementation helpers for the built-in system/mission task."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from task_orchestrator_core.error_model import maybe_add_error
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
    retry_backoff_sec: float = 0.0
    retry_backoff_type: str = "fixed"
    retry_max_backoff_sec: float = 0.0
    retry_error_codes: tuple[str, ...] = field(default_factory=tuple)
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
    """Parses mission JSON into a validated mission graph request."""

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
        self._validate_subtask_graph(subtasks)
        return MissionTaskRequest(mission_id=mission_id, subtasks=subtasks)

    def result_json(
        self,
        mission_id: str,
        status: str,
        mission_results: list[MissionSubtaskResult],
        error_code: str = "",
        error_message: str = "",
    ) -> str:
        payload = {
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
        }
        return json.dumps(maybe_add_error(payload, error_code, error_message), sort_keys=True)

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

        retry_policy = item.get("retry_policy", {})
        if retry_policy is None:
            retry_policy = {}
        if not isinstance(retry_policy, dict):
            raise MissionTaskValidationError(f"subtasks[{index - 1}].retry_policy must be an object")

        max_attempts = int(retry_policy.get("max_attempts", item.get("max_attempts", 1)))
        if max_attempts < 1:
            raise MissionTaskValidationError(f"subtasks[{index - 1}].max_attempts must be at least 1")

        retry_backoff_sec = float(retry_policy.get("backoff_sec", item.get("retry_backoff_sec", 0.0)))
        if retry_backoff_sec < 0:
            raise MissionTaskValidationError(f"subtasks[{index - 1}].retry_backoff_sec must be non-negative")

        retry_backoff_type = str(retry_policy.get("backoff_type", item.get("retry_backoff_type", "fixed"))).lower()
        if retry_backoff_type not in {"fixed", "exponential"}:
            raise MissionTaskValidationError(
                f"subtasks[{index - 1}].retry_policy.backoff_type must be fixed or exponential"
            )

        retry_max_backoff_sec = float(retry_policy.get("max_backoff_sec", item.get("retry_max_backoff_sec", 0.0)))
        if retry_max_backoff_sec < 0:
            raise MissionTaskValidationError(
                f"subtasks[{index - 1}].retry_policy.max_backoff_sec must be non-negative"
            )

        retry_error_codes = retry_policy.get(
            "error_codes",
            retry_policy.get("retry_error_codes", item.get("retry_error_codes", [])),
        )
        if not isinstance(retry_error_codes, list) or not all(isinstance(value, str) for value in retry_error_codes):
            raise MissionTaskValidationError(
                f"subtasks[{index - 1}].retry_policy.error_codes must be a list of strings"
            )

        timeout_sec = float(item.get("timeout_sec", 0.0))
        if timeout_sec < 0:
            raise MissionTaskValidationError(f"subtasks[{index - 1}].timeout_sec must be non-negative")

        depends_on = item.get("depends_on", [])
        if not isinstance(depends_on, list) or not all(isinstance(value, str) for value in depends_on):
            raise MissionTaskValidationError(f"subtasks[{index - 1}].depends_on must be a list of strings")

        condition_json = item.get("condition_json", item.get("condition", ""))
        if isinstance(condition_json, dict):
            condition_json = json.dumps(condition_json, sort_keys=True)
        if not isinstance(condition_json, str):
            raise MissionTaskValidationError(f"subtasks[{index - 1}].condition_json must be a string or object")
        self._validate_condition_json(condition_json, index=index)

        return MissionSubtask(
            subtask_id=subtask_id,
            task_id=task_id,
            task_name=task_name,
            task_data_json=task_data_json,
            allow_skipping=bool(item.get("allow_skipping", False)),
            max_attempts=max_attempts,
            retry_backoff_sec=retry_backoff_sec,
            retry_backoff_type=retry_backoff_type,
            retry_max_backoff_sec=retry_max_backoff_sec,
            retry_error_codes=tuple(retry_error_codes),
            timeout_sec=timeout_sec,
            depends_on=tuple(depends_on),
            condition_json=condition_json,
        )

    def condition_action(self, subtask: MissionSubtask) -> str:
        payload = self._condition_payload(subtask.condition_json)
        return str(payload.get("action", "continue")).lower()

    def condition_error_message(self, subtask: MissionSubtask) -> str:
        payload = self._condition_payload(subtask.condition_json)
        message = payload.get("error_message", payload.get("reason", "Mission condition aborted subtask."))
        return str(message)

    def _validate_condition_json(self, condition_json: str, index: int) -> None:
        payload = self._condition_payload(condition_json)
        action = str(payload.get("action", "continue")).lower()
        if action not in {"continue", "skip", "retry", "abort"}:
            raise MissionTaskValidationError(
                f"subtasks[{index - 1}].condition_json.action must be continue, skip, retry or abort"
            )

    def _condition_payload(self, condition_json: str) -> dict[str, Any]:
        if not condition_json:
            return {}
        try:
            payload = json.loads(condition_json)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _validate_subtask_graph(self, subtasks: tuple[MissionSubtask, ...]) -> None:
        subtask_ids = [subtask.subtask_id for subtask in subtasks]
        duplicate_ids = sorted({subtask_id for subtask_id in subtask_ids if subtask_ids.count(subtask_id) > 1})
        if duplicate_ids:
            raise MissionTaskValidationError(f"duplicate subtask_id values: {', '.join(duplicate_ids)}")

        known_ids = set(subtask_ids)
        for subtask in subtasks:
            unknown_dependencies = sorted(set(subtask.depends_on) - known_ids)
            if unknown_dependencies:
                raise MissionTaskValidationError(
                    f"subtask {subtask.subtask_id} depends on unknown subtasks: {', '.join(unknown_dependencies)}"
                )

        visiting: set[str] = set()
        visited: set[str] = set()
        by_id = {subtask.subtask_id: subtask for subtask in subtasks}

        def visit(subtask_id: str) -> None:
            if subtask_id in visited:
                return
            if subtask_id in visiting:
                raise MissionTaskValidationError(f"mission dependency cycle includes subtask {subtask_id}")
            visiting.add(subtask_id)
            for dependency_id in by_id[subtask_id].depends_on:
                visit(dependency_id)
            visiting.remove(subtask_id)
            visited.add(subtask_id)

        for subtask_id in subtask_ids:
            visit(subtask_id)


def mission_status_from_subtask_result_status(status: str) -> str:
    if status == TaskStatusV1.CANCELED:
        return TaskStatusV1.CANCELED
    return TaskStatusV1.ERROR
