"""Action-backed task execution."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any

from action_msgs.msg import GoalStatus
from rclpy.action import ActionClient
from rosidl_runtime_py.convert import message_to_ordereddict
from rosidl_runtime_py.set_message import set_message_fields
from rosidl_runtime_py.utilities import get_action

from task_orchestrator_core.task_models import TaskDefinition


class ActionTaskError(Exception):
    """Base class for action-backed task failures."""


class ActionTaskConfigError(ActionTaskError):
    """Raised when an action task definition cannot be used."""


class ActionTaskDataError(ActionTaskError):
    """Raised when task JSON cannot be converted into an action goal."""


class ActionTaskServerUnavailable(ActionTaskError):
    """Raised when the configured action server is not available."""


class ActionTaskTimeout(ActionTaskError):
    """Raised when action goal/result handling times out."""


class ActionTaskRejected(ActionTaskError):
    """Raised when the action server rejects a goal."""


class ActionTaskFailed(ActionTaskError):
    """Raised when the action finishes without succeeding."""


class ActionTaskCanceled(ActionTaskError):
    """Raised when the action result status is canceled."""


@dataclass(frozen=True)
class PreparedActionTask:
    """An action goal prepared from a task definition and JSON payload."""

    task: TaskDefinition
    action_type: Any
    goal: Any
    timeout_sec: float | None


@dataclass(frozen=True)
class ActionTaskResult:
    """Normalized result returned by an action-backed task."""

    result_json: str


class ActionTaskClient:
    """Executes configured ROS2 action tasks."""

    def __init__(self, node: Any, callback_group: Any | None = None) -> None:
        self._node = node
        self._callback_group = callback_group
        self._clients: dict[tuple[str, str], Any] = {}
        self._goal_handles: dict[str, Any] = {}

    def prepare(self, task: TaskDefinition, task_data_json: str) -> PreparedActionTask:
        if not task.topic:
            raise ActionTaskConfigError(f"action task {task.task_name} is missing topic")
        if not task.msg_interface:
            raise ActionTaskConfigError(f"action task {task.task_name} is missing msg_interface")

        try:
            action_type = get_action(task.msg_interface)
        except (AttributeError, ModuleNotFoundError, ValueError) as exc:
            raise ActionTaskConfigError(f"cannot load action interface {task.msg_interface}") from exc

        payload = self._parse_payload(task_data_json)
        goal = action_type.Goal()
        try:
            set_message_fields(goal, payload)
        except Exception as exc:  # noqa: BLE001 - rosidl raises several field/type exceptions here.
            raise ActionTaskDataError(f"cannot convert task_data_json to {task.msg_interface}.Goal: {exc}") from exc

        timeout_sec = task.cancel_timeout if task.cancel_timeout > 0 else None
        return PreparedActionTask(task=task, action_type=action_type, goal=goal, timeout_sec=timeout_sec)

    def execute(self, prepared: PreparedActionTask, task_id: str) -> ActionTaskResult:
        client = self._get_client(prepared)
        if not client.wait_for_server(timeout_sec=prepared.timeout_sec):
            raise ActionTaskServerUnavailable(f"action server is not available: {prepared.task.topic}")

        goal_future = client.send_goal_async(prepared.goal)
        goal_handle = self._wait_for_future(goal_future, prepared.timeout_sec, "action goal request timed out")
        if not getattr(goal_handle, "accepted", False):
            raise ActionTaskRejected(f"action goal was rejected: {prepared.task.topic}")

        self._goal_handles[task_id] = goal_handle
        try:
            result_future = goal_handle.get_result_async()
            result_response = self._wait_for_future(result_future, prepared.timeout_sec, "action result timed out")
            status = getattr(result_response, "status", None)

            if status == GoalStatus.STATUS_SUCCEEDED:
                return ActionTaskResult(
                    result_json=json.dumps(message_to_ordereddict(result_response.result), sort_keys=True)
                )
            if status == GoalStatus.STATUS_CANCELED:
                raise ActionTaskCanceled(f"action goal was canceled: {prepared.task.topic}")

            raise ActionTaskFailed(f"action finished with status {status}: {prepared.task.topic}")
        finally:
            self._goal_handles.pop(task_id, None)

    def cancel(self, task_id: str, timeout_sec: float | None = None) -> bool:
        goal_handle = self._goal_handles.get(task_id)
        if goal_handle is None:
            return False

        cancel_future = goal_handle.cancel_goal_async()
        response = self._wait_for_future(cancel_future, timeout_sec, "action cancel request timed out")
        goals_canceling = getattr(response, "goals_canceling", [])
        return bool(goals_canceling)

    def _get_client(self, prepared: PreparedActionTask) -> Any:
        key = (prepared.task.topic, prepared.task.msg_interface)
        client = self._clients.get(key)
        if client is None:
            client = ActionClient(
                self._node,
                prepared.action_type,
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
            raise ActionTaskDataError(f"task_data_json is not valid JSON: {exc.msg}") from exc

        if not isinstance(payload, dict):
            raise ActionTaskDataError("task_data_json must decode to an object")
        return payload

    def _wait_for_future(self, future: Any, timeout_sec: float | None, timeout_message: str) -> Any:
        event = threading.Event()
        future.add_done_callback(lambda _: event.set())

        if not future.done() and not event.wait(timeout_sec):
            raise ActionTaskTimeout(timeout_message)
        if future.exception() is not None:
            raise ActionTaskFailed(f"action future failed: {future.exception()}") from future.exception()
        return future.result()
