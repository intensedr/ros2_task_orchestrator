"""Service-backed task execution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from rosidl_runtime_py.convert import message_to_ordereddict
from rosidl_runtime_py.set_message import set_message_fields
from rosidl_runtime_py.utilities import get_service

from task_orchestrator_core.task_models import TaskDefinition


class ServiceTaskError(Exception):
    """Base class for service-backed task failures."""


class ServiceTaskConfigError(ServiceTaskError):
    """Raised when a service task definition cannot be used."""


class ServiceTaskDataError(ServiceTaskError):
    """Raised when task JSON cannot be converted into a service request."""


class ServiceTaskServerUnavailable(ServiceTaskError):
    """Raised when the configured service is not available."""


class ServiceTaskTimeout(ServiceTaskError):
    """Raised when a service call times out."""


class ServiceTaskCallError(ServiceTaskError):
    """Raised when a service call raises an exception."""


@dataclass(frozen=True)
class PreparedServiceTask:
    """A service request prepared from a task definition and JSON payload."""

    task: TaskDefinition
    srv_type: Any
    request: Any
    timeout_sec: float | None


@dataclass(frozen=True)
class ServiceTaskResult:
    """Normalized result returned by a service-backed task."""

    result_json: str


class ServiceTaskClient:
    """Executes configured ROS2 service tasks."""

    def __init__(self, node: Any, callback_group: Any | None = None) -> None:
        self._node = node
        self._callback_group = callback_group
        self._clients: dict[tuple[str, str], Any] = {}

    def prepare(self, task: TaskDefinition, task_data_json: str) -> PreparedServiceTask:
        if not task.topic:
            raise ServiceTaskConfigError(f"service task {task.task_name} is missing topic")
        if not task.msg_interface:
            raise ServiceTaskConfigError(f"service task {task.task_name} is missing msg_interface")

        try:
            srv_type = get_service(task.msg_interface)
        except (AttributeError, ModuleNotFoundError, ValueError) as exc:
            raise ServiceTaskConfigError(f"cannot load service interface {task.msg_interface}") from exc

        payload = self._parse_payload(task_data_json)
        request = srv_type.Request()
        try:
            set_message_fields(request, payload)
        except Exception as exc:  # noqa: BLE001 - rosidl raises several field/type exceptions here.
            raise ServiceTaskDataError(f"cannot convert task_data_json to {task.msg_interface}.Request: {exc}") from exc

        timeout_sec = task.cancel_timeout if task.cancel_timeout > 0 else None
        return PreparedServiceTask(task=task, srv_type=srv_type, request=request, timeout_sec=timeout_sec)

    def execute(self, prepared: PreparedServiceTask) -> ServiceTaskResult:
        client = self._get_client(prepared)
        if not client.wait_for_service(timeout_sec=prepared.timeout_sec):
            raise ServiceTaskServerUnavailable(f"service is not available: {prepared.task.topic}")

        try:
            response = client.call(prepared.request, timeout_sec=prepared.timeout_sec)
        except Exception as exc:  # noqa: BLE001 - surface rclpy/client exceptions as task failures.
            raise ServiceTaskCallError(f"service call failed: {exc}") from exc

        if response is None:
            raise ServiceTaskTimeout(f"service call timed out: {prepared.task.topic}")

        return ServiceTaskResult(result_json=json.dumps(message_to_ordereddict(response), sort_keys=True))

    def _get_client(self, prepared: PreparedServiceTask) -> Any:
        key = (prepared.task.topic, prepared.task.msg_interface)
        client = self._clients.get(key)
        if client is None:
            client = self._node.create_client(
                prepared.srv_type,
                prepared.task.topic,
                callback_group=self._callback_group,
            )
            self._clients[key] = client
        return client

    def _parse_payload(self, task_data_json: str) -> dict[str, Any]:
        payload_text = task_data_json or "{}"
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise ServiceTaskDataError(f"task_data_json is not valid JSON: {exc.msg}") from exc

        if not isinstance(payload, dict):
            raise ServiceTaskDataError("task_data_json must decode to an object")
        return payload
