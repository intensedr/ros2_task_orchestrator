"""Implementation helpers for built-in task control system tasks."""

from __future__ import annotations

import json
from dataclasses import dataclass


class ControlTaskValidationError(ValueError):
    """Raised when a control task payload is invalid."""


@dataclass(frozen=True)
class CancelTaskRequest:
    task_ids: tuple[str, ...]
    source: str = ""
    correlation_id: str = ""


@dataclass(frozen=True)
class StopTaskRequest:
    source: str = ""
    correlation_id: str = ""


class ControlTaskParser:
    """Parses system task-control payloads and formats stable JSON results."""

    def parse_cancel(self, task_data_json: str) -> CancelTaskRequest:
        payload = self._decode_payload(task_data_json)
        task_ids = payload.get("task_ids", [])
        if not isinstance(task_ids, list) or not all(isinstance(value, str) for value in task_ids):
            raise ControlTaskValidationError("task_ids must be a list of strings")

        return CancelTaskRequest(
            task_ids=tuple(task_ids),
            source=self._optional_string(payload, "source"),
            correlation_id=self._optional_string(payload, "correlation_id"),
        )

    def parse_stop(self, task_data_json: str) -> StopTaskRequest:
        payload = self._decode_payload(task_data_json)
        return StopTaskRequest(
            source=self._optional_string(payload, "source"),
            correlation_id=self._optional_string(payload, "correlation_id"),
        )

    def cancel_result_json(
        self,
        success: bool,
        canceled_task_ids: list[str],
        failed_task_ids: list[str],
        error_code: str = "",
        error_message: str = "",
    ) -> str:
        return json.dumps(
            {
                "success": success,
                "canceled_task_ids": canceled_task_ids,
                "failed_task_ids": failed_task_ids,
                "error_code": error_code,
                "error_message": error_message,
            },
            sort_keys=True,
        )

    def stop_result_json(
        self,
        success: bool,
        stopped_task_ids: list[str],
        error_code: str = "",
        error_message: str = "",
    ) -> str:
        return json.dumps(
            {
                "success": success,
                "stopped_task_ids": stopped_task_ids,
                "error_code": error_code,
                "error_message": error_message,
            },
            sort_keys=True,
        )

    def _decode_payload(self, task_data_json: str) -> dict[str, object]:
        payload_text = task_data_json or "{}"
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise ControlTaskValidationError(f"task_data_json is not valid JSON: {exc.msg}") from exc

        if not isinstance(payload, dict):
            raise ControlTaskValidationError("task_data_json must decode to an object")
        return payload

    def _optional_string(self, payload: dict[str, object], field_name: str) -> str:
        value = payload.get(field_name, "")
        if not isinstance(value, str):
            raise ControlTaskValidationError(f"{field_name} must be a string")
        return value
