"""Implementation of the built-in system/wait task."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class WaitTaskRequest:
    duration_sec: float


@dataclass(frozen=True)
class WaitTaskResult:
    result_json: str


class WaitTaskValidationError(ValueError):
    """Raised when a wait task payload is invalid."""


class WaitTaskExecutor:
    """Executes a local wait without needing any external ROS2 server."""

    def __init__(self, sleep: Callable[[float], None] = time.sleep) -> None:
        self._sleep = sleep

    def parse(self, task_data_json: str) -> WaitTaskRequest:
        payload_text = task_data_json or "{}"
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise WaitTaskValidationError(f"task_data_json is not valid JSON: {exc.msg}") from exc

        if not isinstance(payload, dict):
            raise WaitTaskValidationError("task_data_json must decode to an object")

        duration = payload.get("duration_sec", 0.0)
        if not isinstance(duration, (int, float)):
            raise WaitTaskValidationError("duration_sec must be a number")
        if duration < 0:
            raise WaitTaskValidationError("duration_sec must be non-negative")

        return WaitTaskRequest(duration_sec=float(duration))

    def execute(self, request: WaitTaskRequest) -> WaitTaskResult:
        self._sleep(request.duration_sec)
        return WaitTaskResult(result_json=json.dumps({"duration_sec": request.duration_sec}, sort_keys=True))
