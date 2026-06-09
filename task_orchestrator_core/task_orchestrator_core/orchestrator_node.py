"""ROS2 node for ROS2 Task Orchestrator."""

import json
import threading
import time as wall_time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import rclpy
import yaml
from rclpy.action import ActionServer, CancelResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy
from rosidl_runtime_py.utilities import get_action, get_service

from task_orchestrator_core.active_tasks import ActiveTaskEntry, ActiveTaskRegistry, DuplicateActiveTaskError
from task_orchestrator_core.clients.action_task import (
    ActionTaskCanceled,
    ActionTaskClient,
    ActionTaskConfigError,
    ActionTaskDataError,
    ActionTaskFailed,
    ActionTaskRejected,
    ActionTaskServerUnavailable,
    ActionTaskTimeout,
)
from task_orchestrator_core.clients.service_task import (
    ServiceTaskCallError,
    ServiceTaskClient,
    ServiceTaskConfigError,
    ServiceTaskDataError,
    ServiceTaskServerUnavailable,
    ServiceTaskTimeout,
)
from task_orchestrator_core.constants import (
    ACTION_EXECUTE_TASK,
    API_VERSION,
    NODE_NAME,
    SERVICE_CANCEL_TASKS,
    SERVICE_GET_TASK,
    SERVICE_LIST_TASKS,
    SERVICE_PAUSE_TASKS,
    SERVICE_RESUME_TASKS,
    SERVICE_VALIDATE_TASK,
    TOPIC_ACTIVE_TASKS,
    TOPIC_EVENTS,
    TOPIC_FEEDBACK,
    TOPIC_RESULTS,
)
from task_orchestrator_core.error_model import result_json_with_error
from task_orchestrator_core.registry import TaskRegistry
from task_orchestrator_core.storage import SQLiteTaskStorage
from task_orchestrator_core.system_tasks.control import (
    CancelTaskRequest,
    ControlTaskParser,
    ControlTaskValidationError,
    StopTaskRequest,
)
from task_orchestrator_core.system_tasks.mission import (
    MissionSubtask,
    MissionSubtaskResult,
    MissionTaskParser,
    MissionTaskRequest,
    MissionTaskValidationError,
    mission_status_from_subtask_result_status,
)
from task_orchestrator_core.system_tasks.wait import WaitTaskExecutor, WaitTaskValidationError
from task_orchestrator_core.task_models import TaskConfigError, TaskDefinition
from task_orchestrator_msgs.action import ExecuteTaskV1
from task_orchestrator_msgs.msg import (
    ActiveTaskArrayV1,
    ActiveTaskV1,
    ErrorCodeV1,
    TaskEventV1,
    TaskFeedbackV1,
    TaskRecordV1,
    TaskResultV1,
    TaskSpecV1,
    TaskStatusV1,
)
from task_orchestrator_msgs.srv import (
    CancelTasksV1,
    GetTaskV1,
    ListEventsV1,
    ListTaskRecordsV1,
    ListTasksV1,
    PauseTasksV1,
    ReloadConfigV1,
    ResumeTasksV1,
    StopTasksV1,
    ValidateTaskV1,
)


_CONTROL_TASK_SERVER_TYPES = {"system/cancel_task", "system/stop"}
_PUBLIC_CONTEXT_FIELDS = (
    "idempotency_key",
    "metadata_json",
    "robot_id",
    "fleet_id",
    "site_id",
    "zone_id",
    "operator_id",
    "tenant_id",
    "trace_id",
)


@dataclass(frozen=True)
class _QueuedTaskEntry:
    task_id: str
    task_name: str
    priority: int
    enqueued_index: int
    ready_at_ns: int


class _ChildGoalHandle:
    """Small goal-handle adapter for internally executed mission subtasks."""

    def __init__(self, request: ExecuteTaskV1.Goal) -> None:
        self.request = request
        self.state = ""

    def abort(self) -> None:
        self.state = "aborted"

    def succeed(self) -> None:
        self.state = "succeeded"


@dataclass(frozen=True)
class _TaskExecutionOutcome:
    status: str
    result_json: str
    error_code: str = ""
    error_message: str = ""


class TaskOrchestratorNode(Node):
    """Public API node for the task orchestrator."""

    def __init__(self, parameter_overrides: list[Any] | None = None) -> None:
        super().__init__(NODE_NAME, parameter_overrides=parameter_overrides)

        self.declare_parameter("api_version", API_VERSION)
        self.declare_parameter("event_record_limit", 1000)
        self.declare_parameter("task_record_limit", 1000)
        self.declare_parameter("tasks_config_path", "")
        self.declare_parameter("mission_templates_path", "")
        self.declare_parameter("queue.max_size", 100)
        self.declare_parameter("queue.poll_interval_sec", 0.05)
        self.declare_parameter("storage.enabled", False)
        self.declare_parameter("storage.sqlite_path", "")
        self.declare_parameter("storage.retention_days", 30)
        self.api_version = self.get_parameter("api_version").value
        self._event_record_limit = max(0, int(self.get_parameter("event_record_limit").value))
        self._task_record_limit = max(0, int(self.get_parameter("task_record_limit").value))
        self._queue_max_size = max(0, int(self.get_parameter("queue.max_size").value))
        self._queue_poll_interval_sec = max(0.001, float(self.get_parameter("queue.poll_interval_sec").value))
        self._callback_group = ReentrantCallbackGroup()
        self._task_registry = self._load_task_registry()
        self._active_tasks = ActiveTaskRegistry()
        self._active_mission_subtasks: dict[str, str] = {}
        self._queued_tasks: OrderedDict[str, _QueuedTaskEntry] = OrderedDict()
        self._queue_condition = threading.Condition()
        self._queue_sequence = 0
        self._idempotency_task_ids: dict[str, str] = {}
        self._event_records: OrderedDict[str, TaskEventV1] = OrderedDict()
        self._task_records: OrderedDict[str, TaskRecordV1] = OrderedDict()
        self._storage = self._make_storage()
        self._control_task = ControlTaskParser()
        self._mission_task = MissionTaskParser()
        self._wait_task = WaitTaskExecutor()
        self._retry_sleep = wall_time.sleep
        self._action_task_client = ActionTaskClient(self, callback_group=self._callback_group)
        self._service_task_client = ServiceTaskClient(self, callback_group=self._callback_group)

        active_qos = QoSProfile(
            depth=10,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
        )
        default_qos = QoSProfile(depth=10, reliability=QoSReliabilityPolicy.RELIABLE)

        self._active_tasks_pub = self.create_publisher(ActiveTaskArrayV1, TOPIC_ACTIVE_TASKS, active_qos)
        self._results_pub = self.create_publisher(TaskResultV1, TOPIC_RESULTS, default_qos)
        self._events_pub = self.create_publisher(TaskEventV1, TOPIC_EVENTS, default_qos)
        self._feedback_pub = self.create_publisher(TaskFeedbackV1, TOPIC_FEEDBACK, default_qos)

        self._execute_task_server = ActionServer(
            self,
            ExecuteTaskV1,
            ACTION_EXECUTE_TASK,
            execute_callback=self._execute_task_cb,
            cancel_callback=self._cancel_execute_task_goal,
            callback_group=self._callback_group,
        )

        self._list_tasks_service = self.create_service(
            ListTasksV1,
            SERVICE_LIST_TASKS,
            self._list_tasks,
            callback_group=self._callback_group,
        )
        self._list_events_service = self.create_service(
            ListEventsV1,
            "/task_orchestrator/list_events",
            self._list_events,
            callback_group=self._callback_group,
        )
        self._get_task_service = self.create_service(
            GetTaskV1,
            SERVICE_GET_TASK,
            self._get_task,
            callback_group=self._callback_group,
        )
        self._validate_task_service = self.create_service(
            ValidateTaskV1,
            SERVICE_VALIDATE_TASK,
            self._validate_task,
            callback_group=self._callback_group,
        )
        self._list_task_records_service = self.create_service(
            ListTaskRecordsV1,
            "/task_orchestrator/list_task_records",
            self._list_task_records,
            callback_group=self._callback_group,
        )
        self._reload_config_service = self.create_service(
            ReloadConfigV1,
            "/task_orchestrator/reload_config",
            self._reload_config,
            callback_group=self._callback_group,
        )
        self._cancel_tasks_service = self.create_service(
            CancelTasksV1,
            SERVICE_CANCEL_TASKS,
            self._cancel_tasks,
            callback_group=self._callback_group,
        )
        self._pause_tasks_service = self.create_service(
            PauseTasksV1,
            SERVICE_PAUSE_TASKS,
            self._unsupported_task_control,
            callback_group=self._callback_group,
        )
        self._resume_tasks_service = self.create_service(
            ResumeTasksV1,
            SERVICE_RESUME_TASKS,
            self._unsupported_task_control,
            callback_group=self._callback_group,
        )
        self._stop_tasks_service = self.create_service(
            StopTasksV1,
            "/task_orchestrator/stop",
            self._stop_tasks,
            callback_group=self._callback_group,
        )

        self._publish_empty_active_tasks()
        self._recover_persisted_queued_tasks()
        self.get_logger().info("Task orchestrator started.")

    def destroy_node(self) -> bool:
        if self._storage is not None:
            self._storage.close()
            self._storage = None
        return super().destroy_node()

    def _make_storage(self) -> SQLiteTaskStorage | None:
        if not bool(self.get_parameter("storage.enabled").value):
            return None

        sqlite_path = str(self.get_parameter("storage.sqlite_path").value).strip()
        retention_days = max(0, int(self.get_parameter("storage.retention_days").value))
        try:
            storage = SQLiteTaskStorage(sqlite_path=sqlite_path, retention_days=retention_days)
        except Exception as exc:  # noqa: BLE001 - storage is optional.
            self.get_logger().error(f"SQLite storage disabled: {exc}")
            return None

        self.get_logger().info(f"SQLite storage enabled: {sqlite_path}")
        return storage

    def _execute_task_cb(self, goal_handle: Any) -> ExecuteTaskV1.Result:
        """Execute a configured task."""
        return self._execute_task(goal_handle)

    def _cancel_execute_task_goal(self, goal_handle: Any) -> CancelResponse:
        task_id = goal_handle.request.task_id
        active_task = self._active_tasks.get(task_id)
        if active_task is None:
            return CancelResponse.REJECT
        if not self._cancel_active_task(active_task):
            return CancelResponse.REJECT
        return CancelResponse.ACCEPT

    def _execute_task(
        self,
        goal_handle: Any,
        ignored_active_task_ids: set[str] | None = None,
    ) -> ExecuteTaskV1.Result:
        request = goal_handle.request
        received_at = self.get_clock().now().to_msg()
        created_at = request.created_at if self._time_is_set(request.created_at) else received_at
        request.api_version = request.api_version or self.api_version
        request.created_at = created_at
        task_id_generated = not request.task_id
        task_id = request.task_id or str(uuid.uuid4())
        request.task_id = task_id
        task = self._task_registry.get(request.task_name)
        if task is not None and request.priority == 0:
            request.priority = task.priority_default

        metadata_error = self._normalize_request_metadata(request)
        if metadata_error:
            result = self._make_result(
                request=request,
                task_id=task_id,
                status=TaskStatusV1.REJECTED,
                error_code=ErrorCodeV1.TASK_DATA_PARSING_FAILED,
                error_message=metadata_error,
                created_at=created_at,
                started_at=created_at,
                finished_at=created_at,
            )
            goal_handle.abort()
            self._publish_terminal_result_and_event(result, "task.rejected")
            return result

        idempotent_result = self._claim_or_get_idempotent_result(request)
        if idempotent_result is not None:
            if idempotent_result.status == TaskStatusV1.DONE:
                goal_handle.succeed()
            else:
                goal_handle.abort()
            return idempotent_result

        self._publish_event(
            event_type="task.received",
            task_id=task_id,
            task_name=request.task_name,
            source=request.source,
            correlation_id=request.correlation_id,
            status=TaskStatusV1.RECEIVED,
            priority=request.priority,
            created_at=created_at,
            data={
                "task_id_generated": task_id_generated,
                "priority": request.priority,
                "tags": list(request.tags),
                **self._request_scheduling_observability_data(request),
                **self._request_context_observability_data(request),
            },
            context=request,
        )
        self._store_received_task_record(request, task_id, created_at)

        if task is None:
            result = self._make_result(
                request=request,
                task_id=task_id,
                status=TaskStatusV1.REJECTED,
                error_code=ErrorCodeV1.UNKNOWN_TASK,
                error_message=f"Unknown task: {request.task_name}",
                created_at=created_at,
                started_at=created_at,
                finished_at=created_at,
            )
            goal_handle.abort()
            self._publish_terminal_result_and_event(result, "task.rejected")
            return result

        if self._active_tasks.get(task_id) is not None:
            result = self._make_result(
                request=request,
                task_id=task_id,
                status=TaskStatusV1.REJECTED,
                error_code=ErrorCodeV1.DUPLICATE_TASK_ID,
                error_message=f"Task ID is already active: {task_id}",
                created_at=created_at,
                started_at=created_at,
                finished_at=created_at,
            )
            goal_handle.abort()
            self._publish_terminal_result_and_event(result, "task.rejected")
            return result

        queue_result = self._queue_until_admitted(
            task=task,
            request=request,
            goal_handle=goal_handle,
            ignored_active_task_ids=ignored_active_task_ids,
        )
        if queue_result is not None:
            goal_handle.abort()
            self._publish_terminal_result_and_event(queue_result, self._terminal_event_type(queue_result.status))
            return queue_result

        policy_error = self._apply_start_policy(task, ignored_active_task_ids=ignored_active_task_ids)
        if policy_error:
            result = self._make_result(
                request=request,
                task_id=task_id,
                status=TaskStatusV1.REJECTED,
                error_code=ErrorCodeV1.RESOURCE_CONFLICT,
                error_message=policy_error,
                created_at=created_at,
                started_at=created_at,
                finished_at=created_at,
            )
            goal_handle.abort()
            self._publish_terminal_result_and_event(result, "task.rejected")
            return result

        try:
            prepared_task = self._prepare_task(task, request, task_id)
        except (
            ActionTaskDataError,
            ControlTaskValidationError,
            MissionTaskValidationError,
            ServiceTaskDataError,
            WaitTaskValidationError,
        ) as exc:
            result = self._make_result(
                request=request,
                task_id=task_id,
                status=TaskStatusV1.REJECTED,
                error_code=ErrorCodeV1.TASK_DATA_PARSING_FAILED,
                error_message=str(exc),
                created_at=created_at,
                started_at=created_at,
                finished_at=created_at,
            )
            goal_handle.abort()
            self._publish_terminal_result_and_event(result, "task.rejected")
            return result
        except (ActionTaskConfigError, ServiceTaskConfigError) as exc:
            result = self._make_result(
                request=request,
                task_id=task_id,
                status=TaskStatusV1.REJECTED,
                error_code=ErrorCodeV1.TASK_START_FAILED,
                error_message=str(exc),
                created_at=created_at,
                started_at=created_at,
                finished_at=created_at,
            )
            goal_handle.abort()
            self._publish_terminal_result_and_event(result, "task.rejected")
            return result
        except NotImplementedError:
            result = self._make_result(
                request=request,
                task_id=task_id,
                status=TaskStatusV1.REJECTED,
                error_code=ErrorCodeV1.UNSUPPORTED,
                error_message=f"Task server type is not implemented yet: {task.task_server_type}",
                created_at=created_at,
                started_at=created_at,
                finished_at=created_at,
            )
            goal_handle.abort()
            self._publish_terminal_result_and_event(result, "task.rejected")
            return result

        started_previous_status = self._current_record_status(task_id) or TaskStatusV1.RECEIVED
        started_at = self.get_clock().now().to_msg()
        try:
            active_task = ActiveTaskEntry(
                api_version=self.api_version,
                task_id=task_id,
                task_name=request.task_name,
                source=request.source,
                correlation_id=request.correlation_id,
                priority=request.priority,
                status=TaskStatusV1.IN_PROGRESS,
                created_at=created_at,
                started_at=started_at,
                tags=tuple(request.tags),
                task_server_type=task.task_server_type,
                blocking=task.blocking,
                cancel_on_stop=task.cancel_on_stop,
                resources=task.resources,
                task_group=task.task_group,
                capability_tags=task.capability_tags,
                robot_id=request.robot_id,
                fleet_id=request.fleet_id,
                site_id=request.site_id,
                zone_id=request.zone_id,
                operator_id=request.operator_id,
                tenant_id=request.tenant_id,
                trace_id=request.trace_id,
                metadata_json=request.metadata_json,
                idempotency_key=request.idempotency_key,
                timeout_sec=self._effective_timeout_sec(request),
                cancel_callback=self._make_cancel_callback(self._task_with_request_timeout(task, request), task_id),
            )
            self._active_tasks.add(active_task)
            self._store_active_task_record(active_task, request.task_data_json)
        except DuplicateActiveTaskError:
            result = self._make_result(
                request=request,
                task_id=task_id,
                status=TaskStatusV1.REJECTED,
                error_code=ErrorCodeV1.DUPLICATE_TASK_ID,
                error_message=f"Task ID is already active: {task_id}",
                created_at=created_at,
                started_at=created_at,
                finished_at=created_at,
            )
            goal_handle.abort()
            self._publish_terminal_result_and_event(result, "task.rejected")
            return result

        self._publish_event(
            event_type="task.started",
            task_id=task_id,
            task_name=request.task_name,
            source=request.source,
            correlation_id=request.correlation_id,
            previous_status=started_previous_status,
            status=TaskStatusV1.IN_PROGRESS,
            priority=request.priority,
            created_at=created_at,
            started_at=started_at,
            data=self._task_definition_observability_data(task, request),
            context=request,
        )
        self._publish_task_feedback(
            task_id=task_id,
            task_name=request.task_name,
            source=request.source,
            correlation_id=request.correlation_id,
            progress=0.0,
            feedback={
                "status": TaskStatusV1.IN_PROGRESS,
                "event_type": "task.started",
            },
            priority=request.priority,
            created_at=created_at,
            started_at=started_at,
            status=TaskStatusV1.IN_PROGRESS,
            context=request,
        )
        self._publish_active_tasks()

        try:
            outcome = self._execute_prepared_task(task, prepared_task, task_id, request)
            finished_at = self.get_clock().now().to_msg()
            outcome = self._apply_terminal_deadline(request, outcome, finished_at)
            result = self._make_result(
                request=request,
                task_id=task_id,
                status=outcome.status,
                error_code=outcome.error_code,
                error_message=outcome.error_message,
                result_json=outcome.result_json,
                created_at=created_at,
                started_at=started_at,
                finished_at=finished_at,
            )
            if outcome.status == TaskStatusV1.DONE:
                goal_handle.succeed()
            else:
                goal_handle.abort()
            return result
        except (ActionTaskServerUnavailable, ServiceTaskServerUnavailable) as exc:
            finished_at = self.get_clock().now().to_msg()
            result = self._make_result(
                request=request,
                task_id=task_id,
                status=TaskStatusV1.ERROR,
                error_code=ErrorCodeV1.SERVER_UNAVAILABLE,
                error_message=str(exc),
                created_at=created_at,
                started_at=started_at,
                finished_at=finished_at,
            )
            goal_handle.abort()
            return result
        except (ActionTaskTimeout, ServiceTaskTimeout) as exc:
            finished_at = self.get_clock().now().to_msg()
            result = self._make_result(
                request=request,
                task_id=task_id,
                status=TaskStatusV1.ERROR,
                error_code=ErrorCodeV1.TASK_TIMEOUT,
                error_message=str(exc),
                created_at=created_at,
                started_at=started_at,
                finished_at=finished_at,
            )
            goal_handle.abort()
            return result
        except (ActionTaskFailed, ActionTaskRejected, ServiceTaskCallError) as exc:
            finished_at = self.get_clock().now().to_msg()
            result = self._make_result(
                request=request,
                task_id=task_id,
                status=TaskStatusV1.ERROR,
                error_code=ErrorCodeV1.TASK_START_FAILED,
                error_message=str(exc),
                created_at=created_at,
                started_at=started_at,
                finished_at=finished_at,
            )
            goal_handle.abort()
            return result
        except ActionTaskCanceled as exc:
            finished_at = self.get_clock().now().to_msg()
            status = TaskStatusV1.DONE if task.cancel_reported_as_success else TaskStatusV1.CANCELED
            result = self._make_result(
                request=request,
                task_id=task_id,
                status=status,
                error_message="" if task.cancel_reported_as_success else str(exc),
                created_at=created_at,
                started_at=started_at,
                finished_at=finished_at,
            )
            if task.cancel_reported_as_success:
                goal_handle.succeed()
            else:
                goal_handle.abort()
            return result
        except Exception as exc:  # noqa: BLE001 - convert unexpected executor failures into public task errors.
            finished_at = self.get_clock().now().to_msg()
            result = self._make_result(
                request=request,
                task_id=task_id,
                status=TaskStatusV1.ERROR,
                error_code=ErrorCodeV1.INTERNAL_ERROR,
                error_message=str(exc),
                created_at=created_at,
                started_at=started_at,
                finished_at=finished_at,
            )
            goal_handle.abort()
            return result
        finally:
            self._active_tasks.remove(task_id)
            self._notify_queue()
            self._publish_active_tasks()
            if "result" in locals():
                self._publish_terminal_result_and_event(result, self._terminal_event_type(result.status))

    def _prepare_task(self, task: TaskDefinition, request: ExecuteTaskV1.Goal, task_id: str) -> Any:
        task_data_json = request.task_data_json
        if task.task_server_type == "system/cancel_task":
            return self._control_task.parse_cancel(task_data_json)
        if task.task_server_type == "system/mission":
            return self._mission_task.parse(
                self._resolve_mission_task_data_json(task_data_json),
                default_mission_id=task_id,
            )
        if task.task_server_type == "system/stop":
            return self._control_task.parse_stop(task_data_json)
        if task.task_server_type == "system/wait":
            return self._wait_task.parse(task_data_json)
        effective_task = self._task_with_request_timeout(task, request)
        if task.task_server_type == "action":
            return self._action_task_client.prepare(effective_task, task_data_json)
        if task.task_server_type == "service":
            return self._service_task_client.prepare(effective_task, task_data_json)
        raise NotImplementedError

    def _resolve_mission_task_data_json(self, task_data_json: str) -> str:
        payload_text = task_data_json or "{}"
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            return task_data_json
        if not isinstance(payload, dict):
            return task_data_json
        if "template_path" not in payload and "template_id" not in payload:
            return task_data_json

        template_payload = self._load_mission_template_payload(payload)
        params = self._mission_template_parameters(template_payload, payload)
        rendered_payload = self._render_mission_template_value(
            {
                key: value
                for key, value in template_payload.items()
                if key not in {"parameters"}
            },
            params,
        )
        request_overlay = {
            key: value
            for key, value in payload.items()
            if key not in {"template_path", "template_id", "params"}
        }
        merged_payload = self._deep_merge_dicts(rendered_payload, request_overlay)
        return json.dumps(merged_payload, sort_keys=True)

    def _load_mission_template_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        template_path = self._mission_template_path(payload)
        try:
            with template_path.open("r", encoding="utf-8") as stream:
                template_payload = yaml.safe_load(stream) or {}
        except OSError as exc:
            raise MissionTaskValidationError(f"cannot read mission template {template_path}: {exc}") from exc
        except yaml.YAMLError as exc:
            raise MissionTaskValidationError(f"cannot parse mission template {template_path}: {exc}") from exc

        if not isinstance(template_payload, dict):
            raise MissionTaskValidationError("mission template must decode to an object")
        return template_payload

    def _mission_template_path(self, payload: dict[str, Any]) -> Path:
        templates_root = str(self.get_parameter("mission_templates_path").value or "").strip()
        template_path_value = payload.get("template_path", "")
        template_id = payload.get("template_id", "")

        if template_path_value:
            if not isinstance(template_path_value, str):
                raise MissionTaskValidationError("template_path must be a string")
            template_path = Path(template_path_value).expanduser()
            if not template_path.is_absolute() and templates_root:
                template_path = Path(templates_root).expanduser() / template_path
            return template_path

        if not isinstance(template_id, str) or not template_id:
            raise MissionTaskValidationError("template_id must be a non-empty string")

        root = Path(templates_root).expanduser() if templates_root else Path(".")
        template_id_path = Path(template_id)
        candidate_names = [template_id_path]
        if not template_id_path.suffix:
            candidate_names.extend(
                [
                    template_id_path.with_suffix(".yaml"),
                    template_id_path.with_suffix(".yml"),
                    template_id_path.with_suffix(".json"),
                ]
            )
        for candidate_name in candidate_names:
            candidate = candidate_name if candidate_name.is_absolute() else root / candidate_name
            if candidate.exists():
                return candidate
        return root / candidate_names[0]

    def _mission_template_parameters(
        self,
        template_payload: dict[str, Any],
        request_payload: dict[str, Any],
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        template_params = template_payload.get("parameters", {})
        request_params = request_payload.get("params", {})
        if template_params:
            if not isinstance(template_params, dict):
                raise MissionTaskValidationError("mission template parameters must be an object")
            params.update(template_params)
        if request_params:
            if not isinstance(request_params, dict):
                raise MissionTaskValidationError("mission params must be an object")
            params.update(request_params)
        return params

    def _render_mission_template_value(self, value: Any, params: dict[str, Any]) -> Any:
        if isinstance(value, dict):
            return {key: self._render_mission_template_value(item, params) for key, item in value.items()}
        if isinstance(value, list):
            return [self._render_mission_template_value(item, params) for item in value]
        if isinstance(value, str):
            for key, param_value in params.items():
                placeholder = "${" + key + "}"
                if value == placeholder:
                    return param_value
                value = value.replace(placeholder, str(param_value))
        return value

    def _deep_merge_dicts(self, base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in overlay.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._deep_merge_dicts(merged[key], value)
            else:
                merged[key] = value
        return merged

    def _execute_prepared_task(
        self,
        task: TaskDefinition,
        prepared_task: Any,
        task_id: str,
        request: ExecuteTaskV1.Goal,
    ) -> _TaskExecutionOutcome:
        if task.task_server_type == "system/cancel_task":
            return self._execute_cancel_task(prepared_task, task_id, request)
        if task.task_server_type == "system/mission":
            return self._execute_mission(prepared_task, task_id, request)
        if task.task_server_type == "system/stop":
            return self._execute_stop_task(prepared_task, task_id, request)
        if task.task_server_type == "system/wait":
            timeout_sec = self._effective_timeout_sec(request)
            if timeout_sec > 0 and prepared_task.duration_sec > timeout_sec:
                return _TaskExecutionOutcome(
                    status=TaskStatusV1.ERROR,
                    result_json="{}",
                    error_code=ErrorCodeV1.TASK_TIMEOUT,
                    error_message=(
                        f"Task timeout exceeded before starting wait: "
                        f"duration_sec={prepared_task.duration_sec}, timeout_sec={timeout_sec}"
                    ),
                )
            return _TaskExecutionOutcome(
                status=TaskStatusV1.DONE,
                result_json=self._wait_task.execute(prepared_task).result_json,
            )
        if task.task_server_type == "action":
            return _TaskExecutionOutcome(
                status=TaskStatusV1.DONE,
                result_json=self._action_task_client.execute(prepared_task, task_id).result_json,
            )
        if task.task_server_type == "service":
            return _TaskExecutionOutcome(
                status=TaskStatusV1.DONE,
                result_json=self._service_task_client.execute(prepared_task).result_json,
            )
        raise NotImplementedError

    def _execute_cancel_task(
        self,
        cancel_request: CancelTaskRequest,
        task_id: str,
        execute_request: ExecuteTaskV1.Goal,
    ) -> _TaskExecutionOutcome:
        request = CancelTasksV1.Request()
        request.task_ids = list(cancel_request.task_ids)
        request.source = cancel_request.source
        request.correlation_id = cancel_request.correlation_id

        self._publish_system_control_event(
            event_type="system.cancel.requested",
            task_id=task_id,
            task_name=execute_request.task_name,
            source=execute_request.source,
            correlation_id=execute_request.correlation_id,
            data=self._cancel_request_observability_data(request),
        )
        response = self._cancel_matching_tasks(
            request,
            CancelTasksV1.Response(),
            ignored_active_task_ids={task_id},
        )
        self._publish_system_control_event(
            event_type="system.cancel.completed",
            task_id=task_id,
            task_name=execute_request.task_name,
            source=execute_request.source,
            correlation_id=execute_request.correlation_id,
            success=response.success,
            error_code=response.error_code,
            error_message=response.error_message,
            data=self._cancel_response_observability_data(request, response),
        )
        return _TaskExecutionOutcome(
            status=TaskStatusV1.DONE if response.success else TaskStatusV1.ERROR,
            error_code=response.error_code,
            error_message=response.error_message,
            result_json=self._control_task.cancel_result_json(
                success=response.success,
                canceled_task_ids=list(response.canceled_task_ids),
                failed_task_ids=list(response.failed_task_ids),
                error_code=response.error_code,
                error_message=response.error_message,
            ),
        )

    def _execute_stop_task(
        self,
        stop_request: StopTaskRequest,
        task_id: str,
        execute_request: ExecuteTaskV1.Goal,
    ) -> _TaskExecutionOutcome:
        request = StopTasksV1.Request()
        request.source = stop_request.source
        request.correlation_id = stop_request.correlation_id

        self._publish_system_control_event(
            event_type="system.stop.requested",
            task_id=task_id,
            task_name=execute_request.task_name,
            source=execute_request.source,
            correlation_id=execute_request.correlation_id,
            data=self._stop_request_observability_data(request),
        )
        response = self._stop_matching_tasks(request, StopTasksV1.Response())
        self._publish_system_control_event(
            event_type="system.stop.completed",
            task_id=task_id,
            task_name=execute_request.task_name,
            source=execute_request.source,
            correlation_id=execute_request.correlation_id,
            success=response.success,
            error_code=response.error_code,
            error_message=response.error_message,
            data=self._stop_response_observability_data(request, response),
        )
        return _TaskExecutionOutcome(
            status=TaskStatusV1.DONE if response.success else TaskStatusV1.ERROR,
            error_code=response.error_code,
            error_message=response.error_message,
            result_json=self._control_task.stop_result_json(
                success=response.success,
                stopped_task_ids=list(response.stopped_task_ids),
                error_code=response.error_code,
                error_message=response.error_message,
            ),
        )

    def _execute_mission(
        self,
        mission: MissionTaskRequest,
        mission_task_id: str,
        request: ExecuteTaskV1.Goal,
    ) -> _TaskExecutionOutcome:
        mission_results: list[MissionSubtaskResult] = []
        total_subtasks = len(mission.subtasks)

        self._publish_mission_lifecycle_event(
            event_type="mission.started",
            mission_id=mission.mission_id,
            mission_task_id=mission_task_id,
            source=request.source,
            correlation_id=request.correlation_id,
            status=TaskStatusV1.IN_PROGRESS,
            priority=request.priority,
            data={
                "total_subtasks": total_subtasks,
            },
        )
        self._publish_mission_feedback(
            mission_id=mission.mission_id,
            mission_task_id=mission_task_id,
            active_subtask_id="",
            completed_subtasks=0,
            total_subtasks=total_subtasks,
            source=request.source,
            correlation_id=request.correlation_id,
            priority=request.priority,
        )

        for index, subtask in enumerate(mission.subtasks, start=1):
            self._publish_mission_subtask_event(
                event_type="mission.subtask.started",
                mission_id=mission.mission_id,
                mission_task_id=mission_task_id,
                subtask=subtask,
                source=request.source,
                correlation_id=request.correlation_id,
                status=TaskStatusV1.IN_PROGRESS,
                priority=request.priority,
                data={
                    "subtask_index": index,
                    "total_subtasks": total_subtasks,
                    "max_attempts": subtask.max_attempts,
                    "retry_backoff_sec": subtask.retry_backoff_sec,
                    "timeout_sec": subtask.timeout_sec,
                    "allow_skipping": subtask.allow_skipping,
                },
            )
            self._active_mission_subtasks[mission_task_id] = subtask.task_id
            subtask_result = self._execute_mission_subtask(
                subtask=subtask,
                mission_task_id=mission_task_id,
                request=request,
            )
            mission_results.append(subtask_result)
            self._active_mission_subtasks.pop(mission_task_id, None)
            self._publish_mission_subtask_result_event(
                mission_id=mission.mission_id,
                mission_task_id=mission_task_id,
                subtask_result=subtask_result,
                source=request.source,
                correlation_id=request.correlation_id,
                subtask_index=index,
                total_subtasks=total_subtasks,
                priority=request.priority,
            )

            self._publish_mission_feedback(
                mission_id=mission.mission_id,
                mission_task_id=mission_task_id,
                active_subtask_id=subtask.subtask_id,
                completed_subtasks=index,
                total_subtasks=total_subtasks,
                source=request.source,
                correlation_id=request.correlation_id,
                priority=request.priority,
            )

            if subtask_result.status == TaskStatusV1.DONE:
                continue
            if subtask_result.skipped:
                continue

            mission_status = mission_status_from_subtask_result_status(subtask_result.status)
            pending_status = TaskStatusV1.CANCELED if mission_status == TaskStatusV1.CANCELED else TaskStatusV1.PENDING
            mission_results.extend(
                self._remaining_mission_subtask_results(
                    subtasks=mission.subtasks[index:],
                    status=pending_status,
                    error_code=subtask_result.error_code,
                    error_message=subtask_result.error_message,
                )
            )
            terminal_event_type = "mission.canceled" if mission_status == TaskStatusV1.CANCELED else "mission.failed"
            self._publish_mission_lifecycle_event(
                event_type=terminal_event_type,
                mission_id=mission.mission_id,
                mission_task_id=mission_task_id,
                source=request.source,
                correlation_id=request.correlation_id,
                status=mission_status,
                previous_status=TaskStatusV1.IN_PROGRESS,
                error_code=subtask_result.error_code,
                error_message=subtask_result.error_message,
                priority=request.priority,
                data={
                    "completed_subtasks": index,
                    "total_subtasks": total_subtasks,
                    "failed_subtask_id": subtask_result.subtask_id,
                    "failed_task_id": subtask_result.task_id,
                },
            )
            return _TaskExecutionOutcome(
                status=mission_status,
                error_code=subtask_result.error_code,
                error_message=subtask_result.error_message,
                result_json=self._mission_task.result_json(
                    mission_id=mission.mission_id,
                    status=mission_status,
                    mission_results=mission_results,
                    error_code=subtask_result.error_code,
                    error_message=subtask_result.error_message,
                ),
            )

        self._publish_mission_lifecycle_event(
            event_type="mission.completed",
            mission_id=mission.mission_id,
            mission_task_id=mission_task_id,
            source=request.source,
            correlation_id=request.correlation_id,
            status=TaskStatusV1.DONE,
            previous_status=TaskStatusV1.IN_PROGRESS,
            priority=request.priority,
            data={
                "completed_subtasks": len(mission_results),
                "total_subtasks": total_subtasks,
            },
        )
        return _TaskExecutionOutcome(
            status=TaskStatusV1.DONE,
            result_json=self._mission_task.result_json(
                mission_id=mission.mission_id,
                status=TaskStatusV1.DONE,
                mission_results=mission_results,
            ),
        )

    def _execute_mission_subtask(
        self,
        subtask: MissionSubtask,
        mission_task_id: str,
        request: ExecuteTaskV1.Goal,
    ) -> MissionSubtaskResult:
        last_result: ExecuteTaskV1.Result | None = None

        for attempt in range(1, subtask.max_attempts + 1):
            subtask_request = ExecuteTaskV1.Goal()
            subtask_request.api_version = self.api_version
            subtask_request.task_id = self._subtask_attempt_task_id(subtask, attempt)
            subtask_request.task_name = subtask.task_name
            subtask_request.source = request.source
            subtask_request.correlation_id = request.correlation_id
            subtask_request.priority = request.priority
            subtask_request.task_data_json = subtask.task_data_json
            subtask_request.tags = list(request.tags)
            subtask_request.timeout_sec = subtask.timeout_sec
            subtask_request.metadata_json = request.metadata_json
            subtask_request.robot_id = request.robot_id
            subtask_request.fleet_id = request.fleet_id
            subtask_request.site_id = request.site_id
            subtask_request.zone_id = request.zone_id
            subtask_request.operator_id = request.operator_id
            subtask_request.tenant_id = request.tenant_id
            subtask_request.trace_id = request.trace_id

            child_goal_handle = _ChildGoalHandle(subtask_request)
            last_result = self._execute_task(
                child_goal_handle,
                ignored_active_task_ids={mission_task_id},
            )

            if last_result.status == TaskStatusV1.DONE:
                return MissionSubtaskResult(
                    subtask_id=subtask.subtask_id,
                    task_id=subtask_request.task_id,
                    task_name=subtask.task_name,
                    status=TaskStatusV1.DONE,
                    skipped=False,
                    attempts=attempt,
                )

            if attempt < subtask.max_attempts and subtask.retry_backoff_sec > 0:
                self._retry_sleep(subtask.retry_backoff_sec)

        assert last_result is not None
        if subtask.allow_skipping:
            return MissionSubtaskResult(
                subtask_id=subtask.subtask_id,
                task_id=last_result.task_id,
                task_name=subtask.task_name,
                status=TaskStatusV1.SKIPPED,
                skipped=True,
                attempts=subtask.max_attempts,
                error_code=last_result.error_code,
                error_message=last_result.error_message,
            )

        return MissionSubtaskResult(
            subtask_id=subtask.subtask_id,
            task_id=last_result.task_id,
            task_name=subtask.task_name,
            status=last_result.status,
            skipped=False,
            attempts=subtask.max_attempts,
            error_code=last_result.error_code,
            error_message=last_result.error_message,
        )

    def _subtask_attempt_task_id(self, subtask: MissionSubtask, attempt: int) -> str:
        if attempt == 1:
            return subtask.task_id
        return f"{subtask.task_id}/attempt-{attempt}"

    def _remaining_mission_subtask_results(
        self,
        subtasks: tuple[MissionSubtask, ...],
        status: str,
        error_code: str,
        error_message: str,
    ) -> list[MissionSubtaskResult]:
        return [
            MissionSubtaskResult(
                subtask_id=subtask.subtask_id,
                task_id=subtask.task_id,
                task_name=subtask.task_name,
                status=status,
                skipped=False,
                attempts=0,
                error_code=error_code,
                error_message=error_message,
            )
            for subtask in subtasks
        ]

    def _queue_until_admitted(
        self,
        task: TaskDefinition,
        request: ExecuteTaskV1.Goal,
        goal_handle: Any,
        ignored_active_task_ids: set[str] | None = None,
    ) -> ExecuteTaskV1.Result | None:
        ready_at_ns = self._requested_ready_at_ns(request)
        now_ns = self._now_ns()
        queue_requested = (
            bool(request.queue_on_conflict)
            or task.queue_on_conflict_default
            or ready_at_ns > now_ns
        )
        if not queue_requested:
            return self._deadline_result_if_elapsed(request)

        deadline_result = self._deadline_result_if_elapsed(request)
        if deadline_result is not None:
            return deadline_result

        with self._queue_condition:
            if self._queue_max_size and len(self._queued_tasks) >= self._queue_max_size:
                return self._make_result(
                    request=request,
                    task_id=request.task_id,
                    status=TaskStatusV1.REJECTED,
                    error_code=ErrorCodeV1.POLICY_REJECTED,
                    error_message="Task queue is full.",
                    created_at=request.created_at,
                    started_at=request.created_at,
                    finished_at=self.get_clock().now().to_msg(),
                )

            self._queue_sequence += 1
            entry = _QueuedTaskEntry(
                task_id=request.task_id,
                task_name=request.task_name,
                priority=request.priority,
                enqueued_index=self._queue_sequence,
                ready_at_ns=ready_at_ns,
            )
            self._queued_tasks[entry.task_id] = entry
            self._store_queued_task_record(request, entry)
            self._publish_queued_task_event(request, entry)

            while True:
                if self._goal_cancel_requested(goal_handle):
                    self._queued_tasks.pop(entry.task_id, None)
                    self._notify_queue_locked()
                    finished_at = self.get_clock().now().to_msg()
                    return self._make_result(
                        request=request,
                        task_id=request.task_id,
                        status=TaskStatusV1.CANCELED,
                        error_code=ErrorCodeV1.TASK_CANCEL_FAILED,
                        error_message="Queued task was canceled before start.",
                        created_at=request.created_at,
                        started_at=request.created_at,
                        finished_at=finished_at,
                    )

                deadline_result = self._deadline_result_if_elapsed(request)
                if deadline_result is not None:
                    self._queued_tasks.pop(entry.task_id, None)
                    self._notify_queue_locked()
                    return deadline_result

                now_ns = self._now_ns()
                policy_error = self._apply_start_policy(task, ignored_active_task_ids=ignored_active_task_ids)
                if now_ns >= entry.ready_at_ns and not policy_error and self._is_queue_head_locked(entry, now_ns):
                    self._queued_tasks.pop(entry.task_id, None)
                    self._notify_queue_locked()
                    self._publish_dequeued_task_event(request, entry)
                    return None

                wait_sec = self._queue_poll_interval_sec
                if entry.ready_at_ns > now_ns:
                    wait_sec = min(wait_sec, max(0.001, (entry.ready_at_ns - now_ns) / 1_000_000_000.0))
                self._queue_condition.wait(timeout=wait_sec)

    def _publish_queued_task_event(self, request: ExecuteTaskV1.Goal, entry: _QueuedTaskEntry) -> None:
        queue_data = {
            "ready_at_ns": entry.ready_at_ns,
            "queue_position": self._queue_position(entry),
            **self._request_scheduling_observability_data(request),
            **self._request_context_observability_data(request),
        }
        self._publish_event(
            event_type="task.queued",
            task_id=request.task_id,
            task_name=request.task_name,
            source=request.source,
            correlation_id=request.correlation_id,
            previous_status=TaskStatusV1.RECEIVED,
            status=TaskStatusV1.QUEUED,
            priority=request.priority,
            created_at=request.created_at,
            data=queue_data,
            context=request,
        )
        self._publish_task_feedback(
            task_id=request.task_id,
            task_name=request.task_name,
            source=request.source,
            correlation_id=request.correlation_id,
            progress=0.0,
            feedback={
                "status": TaskStatusV1.QUEUED,
                "event_type": "task.queued",
                "queue_position": queue_data["queue_position"],
            },
            priority=request.priority,
            created_at=request.created_at,
            status=TaskStatusV1.QUEUED,
            context=request,
        )

    def _publish_dequeued_task_event(self, request: ExecuteTaskV1.Goal, entry: _QueuedTaskEntry) -> None:
        self._publish_event(
            event_type="task.dequeued",
            task_id=request.task_id,
            task_name=request.task_name,
            source=request.source,
            correlation_id=request.correlation_id,
            previous_status=TaskStatusV1.QUEUED,
            status=TaskStatusV1.QUEUED,
            priority=request.priority,
            created_at=request.created_at,
            data={
                "ready_at_ns": entry.ready_at_ns,
                **self._request_scheduling_observability_data(request),
                **self._request_context_observability_data(request),
            },
            context=request,
        )

    def _store_queued_task_record(self, request: ExecuteTaskV1.Goal, entry: _QueuedTaskEntry) -> None:
        result = TaskResultV1()
        result.api_version = self.api_version
        result.task_id = request.task_id
        result.task_name = request.task_name
        result.source = request.source
        result.priority = request.priority
        result.correlation_id = request.correlation_id
        result.status = TaskStatusV1.QUEUED
        result.result_json = "{}"
        result.created_at = request.created_at
        self._copy_public_context(request, result)

        record = TaskRecordV1()
        record.result = result
        record.active = False
        record.task_data_json = request.task_data_json
        record.tags = list(request.tags)
        self._copy_request_scheduling(request, record)
        self._copy_public_context(request, record)
        self._set_task_record(entry.task_id, record)

    def _is_queue_head_locked(self, entry: _QueuedTaskEntry, now_ns: int) -> bool:
        ready_entries = [item for item in self._queued_tasks.values() if item.ready_at_ns <= now_ns]
        if not ready_entries:
            return False
        head = min(ready_entries, key=lambda item: (-item.priority, item.enqueued_index))
        return head.task_id == entry.task_id

    def _queue_position(self, entry: _QueuedTaskEntry) -> int:
        with self._queue_condition:
            entries = sorted(
                self._queued_tasks.values(),
                key=lambda item: (item.ready_at_ns, -item.priority, item.enqueued_index),
            )
            for index, item in enumerate(entries, start=1):
                if item.task_id == entry.task_id:
                    return index
        return 0

    def _notify_queue(self) -> None:
        with self._queue_condition:
            self._notify_queue_locked()

    def _notify_queue_locked(self) -> None:
        self._queue_condition.notify_all()

    def _normalize_request_metadata(self, request: ExecuteTaskV1.Goal) -> str:
        if request.delay_sec < 0:
            return "delay_sec must be non-negative"
        if request.timeout_sec < 0:
            return "timeout_sec must be non-negative"

        metadata_json = request.metadata_json or "{}"
        try:
            metadata = json.loads(metadata_json)
        except json.JSONDecodeError as exc:
            return f"metadata_json is not valid JSON: {exc.msg}"
        if not isinstance(metadata, dict):
            return "metadata_json must decode to an object"
        request.metadata_json = self._json_dumps(metadata)
        return ""

    def _claim_or_get_idempotent_result(self, request: ExecuteTaskV1.Goal) -> ExecuteTaskV1.Result | None:
        if not request.idempotency_key:
            return None

        with self._queue_condition:
            existing_task_id = self._idempotency_task_ids.get(request.idempotency_key)
        if not existing_task_id:
            existing_record = self._stored_task_record_by_idempotency_key(request.idempotency_key)
            if existing_record is not None:
                existing_task_id = existing_record.result.task_id
                if existing_task_id == request.task_id and existing_record.result.status == TaskStatusV1.QUEUED:
                    with self._queue_condition:
                        self._idempotency_task_ids[request.idempotency_key] = request.task_id
                    return None
                if existing_record.result.status in self._terminal_statuses():
                    return self._task_result_to_execute_result(existing_record.result)

        if not existing_task_id:
            with self._queue_condition:
                self._idempotency_task_ids[request.idempotency_key] = request.task_id
            return None

        record = self._task_records.get(existing_task_id)
        if record is None and self._storage is not None:
            record = self._stored_task_record(existing_task_id)
        if record is not None and record.result.status in self._terminal_statuses():
            return self._task_result_to_execute_result(record.result)

        now = self.get_clock().now().to_msg()
        return self._make_result(
            request=request,
            task_id=request.task_id,
            status=TaskStatusV1.REJECTED,
            error_code=ErrorCodeV1.DUPLICATE_TASK_ID,
            error_message=(
                f"Idempotency key is already claimed by task {existing_task_id}: "
                f"{request.idempotency_key}"
            ),
            created_at=request.created_at,
            started_at=now,
            finished_at=now,
        )

    def _terminal_statuses(self) -> set[str]:
        return {
            TaskStatusV1.DONE,
            TaskStatusV1.ERROR,
            TaskStatusV1.CANCELED,
            TaskStatusV1.SKIPPED,
            TaskStatusV1.REJECTED,
        }

    def _requested_ready_at_ns(self, request: ExecuteTaskV1.Goal) -> int:
        now_ns = self._now_ns()
        ready_at_ns = now_ns
        if self._time_is_set(request.scheduled_at):
            ready_at_ns = max(ready_at_ns, self._time_to_ns(request.scheduled_at))
        if request.delay_sec > 0:
            ready_at_ns = max(
                ready_at_ns,
                self._time_to_ns(request.created_at) + int(request.delay_sec * 1_000_000_000),
            )
        return ready_at_ns

    def _deadline_result_if_elapsed(self, request: ExecuteTaskV1.Goal) -> ExecuteTaskV1.Result | None:
        if not self._time_is_set(request.deadline_at):
            return None
        if self._now_ns() <= self._time_to_ns(request.deadline_at):
            return None
        now = self.get_clock().now().to_msg()
        return self._make_result(
            request=request,
            task_id=request.task_id,
            status=TaskStatusV1.REJECTED,
            error_code=ErrorCodeV1.DEADLINE_EXCEEDED,
            error_message="Task deadline elapsed before start.",
            created_at=request.created_at,
            started_at=now,
            finished_at=now,
        )

    def _apply_terminal_deadline(
        self,
        request: ExecuteTaskV1.Goal,
        outcome: _TaskExecutionOutcome,
        finished_at: Any,
    ) -> _TaskExecutionOutcome:
        if outcome.status != TaskStatusV1.DONE:
            return outcome
        if not self._time_is_set(request.deadline_at):
            return outcome
        if self._time_to_ns(finished_at) <= self._time_to_ns(request.deadline_at):
            return outcome
        return _TaskExecutionOutcome(
            status=TaskStatusV1.ERROR,
            error_code=ErrorCodeV1.DEADLINE_EXCEEDED,
            error_message="Task deadline elapsed before completion.",
            result_json=outcome.result_json,
        )

    def _effective_timeout_sec(self, request: ExecuteTaskV1.Goal) -> float:
        candidates: list[float] = []
        if request.timeout_sec > 0:
            candidates.append(float(request.timeout_sec))
        if self._time_is_set(request.deadline_at):
            remaining_sec = (self._time_to_ns(request.deadline_at) - self._now_ns()) / 1_000_000_000.0
            if remaining_sec > 0:
                candidates.append(remaining_sec)
        return min(candidates) if candidates else 0.0

    def _task_with_request_timeout(self, task: TaskDefinition, request: ExecuteTaskV1.Goal) -> TaskDefinition:
        timeout_sec = self._effective_timeout_sec(request)
        if timeout_sec <= 0:
            return task
        return replace(task, cancel_timeout=timeout_sec)

    def _goal_cancel_requested(self, goal_handle: Any) -> bool:
        value = getattr(goal_handle, "is_cancel_requested", False)
        return bool(value() if callable(value) else value)

    def _now_ns(self) -> int:
        return self._time_to_ns(self.get_clock().now().to_msg())

    def _time_to_ns(self, value: Any) -> int:
        return int(getattr(value, "sec", 0)) * 1_000_000_000 + int(getattr(value, "nanosec", 0))

    def _task_result_to_execute_result(self, source: TaskResultV1) -> ExecuteTaskV1.Result:
        result = ExecuteTaskV1.Result()
        result.api_version = source.api_version
        result.task_id = source.task_id
        result.task_name = source.task_name
        result.source = source.source
        result.priority = source.priority
        result.correlation_id = source.correlation_id
        result.status = source.status
        result.error_code = source.error_code
        result.error_message = source.error_message
        result.result_json = source.result_json
        result.created_at = source.created_at
        result.started_at = source.started_at
        result.finished_at = source.finished_at
        result.duration_sec = source.duration_sec
        result.total_duration_sec = source.total_duration_sec
        self._copy_public_context(source, result)
        return result

    def _apply_start_policy(
        self,
        task: TaskDefinition,
        ignored_active_task_ids: set[str] | None = None,
    ) -> str:
        if task.task_server_type in _CONTROL_TASK_SERVER_TYPES:
            return ""

        ignored_active_ids = set(ignored_active_task_ids or set())

        if not task.reentrant:
            for active_task in self._active_tasks.list():
                if active_task.task_name != task.task_name:
                    continue
                if not self._cancel_active_task(active_task):
                    return f"Task {task.task_name} is already active and cannot be replaced."
                ignored_active_ids.add(active_task.task_id)

        blocking_tasks = [
            active_task
            for active_task in self._active_tasks.list()
            if active_task.blocking and active_task.task_id not in ignored_active_ids
        ]
        if blocking_tasks:
            blocking_task_ids = ", ".join(task.task_id for task in blocking_tasks)
            return f"Active blocking task prevents starting {task.task_name}: {blocking_task_ids}"

        requested_resources = set(task.resources)
        if requested_resources:
            for active_task in self._active_tasks.list():
                if active_task.task_id in ignored_active_ids:
                    continue
                shared_resources = requested_resources.intersection(active_task.resources)
                if shared_resources:
                    return (
                        f"Task {task.task_name} conflicts with active task {active_task.task_id} "
                        f"on resources: {', '.join(sorted(shared_resources))}"
                    )

        if task.task_group:
            for active_task in self._active_tasks.list():
                if active_task.task_id in ignored_active_ids:
                    continue
                if active_task.task_group == task.task_group:
                    return (
                        f"Task {task.task_name} conflicts with active task {active_task.task_id} "
                        f"in task group: {task.task_group}"
                    )

        return ""

    def _list_tasks(self, _request: ListTasksV1.Request, response: ListTasksV1.Response) -> ListTasksV1.Response:
        response.tasks = [
            self._task_definition_to_msg(task) for task in self._task_registry.list(_request.include_system_tasks)
        ]
        return response

    def _list_events(
        self,
        request: ListEventsV1.Request,
        response: ListEventsV1.Response,
    ) -> ListEventsV1.Response:
        if self._storage is not None:
            response.events = self._stored_events(request)
            return response

        events = [
            self._copy_task_event(event)
            for event in reversed(self._event_records.values())
            if self._event_matches(event, request)
        ]
        if request.limit > 0:
            events = events[: request.limit]
        response.events = events
        return response

    def _event_matches(self, event: TaskEventV1, request: ListEventsV1.Request) -> bool:
        return (
            (not request.task_id or event.task_id == request.task_id)
            and (not request.task_name or event.task_name == request.task_name)
            and (not request.event_type or event.event_type == request.event_type)
            and (not request.status or event.status == request.status)
            and (not request.source or event.source == request.source)
            and (not request.correlation_id or event.correlation_id == request.correlation_id)
            and (not request.robot_id or event.robot_id == request.robot_id)
            and (not request.fleet_id or event.fleet_id == request.fleet_id)
            and (not request.site_id or event.site_id == request.site_id)
            and (not request.zone_id or event.zone_id == request.zone_id)
            and (not request.operator_id or event.operator_id == request.operator_id)
            and (not request.tenant_id or event.tenant_id == request.tenant_id)
            and (not request.trace_id or event.trace_id == request.trace_id)
            and (not request.idempotency_key or event.idempotency_key == request.idempotency_key)
        )

    def _stored_events(self, request: ListEventsV1.Request) -> list[TaskEventV1]:
        if self._storage is None:
            return []
        try:
            return self._storage.list_events(request)
        except Exception as exc:  # noqa: BLE001 - storage must not break ROS2 service calls.
            self.get_logger().error(f"Failed to query SQLite events: {exc}")
            return []

    def _stored_task_record(self, task_id: str) -> TaskRecordV1 | None:
        if self._storage is None:
            return None
        try:
            return self._storage.get_task_record(task_id)
        except Exception as exc:  # noqa: BLE001 - storage must not break ROS2 service calls.
            self.get_logger().error(f"Failed to query SQLite task record {task_id}: {exc}")
            return None

    def _stored_task_records(self, request: ListTaskRecordsV1.Request) -> list[TaskRecordV1]:
        if self._storage is None:
            return []
        try:
            return self._storage.list_task_records(request)
        except Exception as exc:  # noqa: BLE001 - storage must not break ROS2 service calls.
            self.get_logger().error(f"Failed to query SQLite task records: {exc}")
            return []

    def _stored_queued_task_records(self) -> list[TaskRecordV1]:
        if self._storage is None:
            return []
        try:
            return self._storage.list_queued_task_records()
        except Exception as exc:  # noqa: BLE001 - storage must not break startup recovery.
            self.get_logger().error(f"Failed to query SQLite queued task records: {exc}")
            return []

    def _stored_task_record_by_idempotency_key(self, idempotency_key: str) -> TaskRecordV1 | None:
        if self._storage is None:
            return None
        try:
            return self._storage.get_task_record_by_idempotency_key(idempotency_key)
        except Exception as exc:  # noqa: BLE001 - storage must not break task submission.
            self.get_logger().error(f"Failed to query SQLite idempotency key {idempotency_key}: {exc}")
            return None

    def _recover_persisted_queued_tasks(self) -> None:
        queued_records = self._stored_queued_task_records()
        if not queued_records:
            return

        self.get_logger().info(f"Recovering {len(queued_records)} queued task(s) from SQLite.")
        for record in queued_records:
            self._publish_event(
                event_type="task.recovered",
                task_id=record.result.task_id,
                task_name=record.result.task_name,
                source=record.result.source,
                correlation_id=record.result.correlation_id,
                status=TaskStatusV1.QUEUED,
                priority=record.result.priority,
                created_at=record.result.created_at,
                data={
                    "recovery_source": "sqlite",
                    "task_data_json_present": bool(record.task_data_json),
                    "scheduled_at_set": self._time_is_set(record.scheduled_at),
                    "delay_sec": float(record.delay_sec),
                    "deadline_at_set": self._time_is_set(record.deadline_at),
                    "timeout_sec": float(record.timeout_sec),
                    "queue_on_conflict": bool(record.queue_on_conflict),
                },
                context=record,
            )
            thread = threading.Thread(
                target=self._execute_recovered_queued_task,
                args=(self._copy_task_record(record),),
                name=f"task-orchestrator-recover-{record.result.task_id}",
                daemon=True,
            )
            thread.start()

    def _execute_recovered_queued_task(self, record: TaskRecordV1) -> None:
        try:
            request = self._task_record_to_execute_goal(record)
            self._execute_task(_ChildGoalHandle(request))
        except Exception as exc:  # noqa: BLE001 - recovery must not stop node startup.
            self.get_logger().error(f"Failed to recover queued task {record.result.task_id}: {exc}")

    def _task_record_to_execute_goal(self, record: TaskRecordV1) -> ExecuteTaskV1.Goal:
        result = record.result
        request = ExecuteTaskV1.Goal()
        request.api_version = result.api_version or self.api_version
        request.task_id = result.task_id
        request.task_name = result.task_name
        request.source = result.source
        request.priority = result.priority
        request.correlation_id = result.correlation_id
        request.created_at = result.created_at
        request.task_data_json = record.task_data_json
        request.tags = list(record.tags)
        request.scheduled_at = record.scheduled_at
        request.delay_sec = record.delay_sec
        request.deadline_at = record.deadline_at
        request.timeout_sec = record.timeout_sec
        request.queue_on_conflict = record.queue_on_conflict
        self._copy_public_context(record, request)
        return request

    def _current_record_status(self, task_id: str) -> str:
        record = self._task_records.get(task_id)
        if record is None:
            return ""
        return record.result.status

    def _get_task(self, request: GetTaskV1.Request, response: GetTaskV1.Response) -> GetTaskV1.Response:
        record = self._task_records.get(request.task_id)
        if record is None and self._storage is not None:
            record = self._stored_task_record(request.task_id)
        if record is None:
            active_task = self._active_tasks.get(request.task_id)
            if active_task is not None:
                record = self._active_task_to_record(active_task, task_data_json="")

        response.found = record is not None
        if record is not None:
            response.task = self._copy_task_record(record)
        return response

    def _validate_task(
        self,
        request: ValidateTaskV1.Request,
        response: ValidateTaskV1.Response,
    ) -> ValidateTaskV1.Response:
        task = self._task_registry.get(request.task_name)
        if task is None:
            response.valid = False
            response.error_code = ErrorCodeV1.UNKNOWN_TASK
            response.error_message = f"Unknown task: {request.task_name}"
            response.normalized_task_data_json = request.task_data_json
            response.schema_json = "{}"
            return response

        try:
            response.schema_json = self._task_schema_json(task) if request.include_schema else "{}"
        except Exception as exc:  # noqa: BLE001 - schema generation is best-effort validation metadata.
            response.valid = False
            response.error_code = ErrorCodeV1.TASK_START_FAILED
            response.error_message = f"Cannot generate task schema: {exc}"
            response.normalized_task_data_json = request.task_data_json
            response.schema_json = "{}"
            return response

        task_id = request.task_id or "validation"
        validation_goal = ExecuteTaskV1.Goal()
        validation_goal.api_version = self.api_version
        validation_goal.task_id = task_id
        validation_goal.task_name = request.task_name
        validation_goal.task_data_json = request.task_data_json

        try:
            normalized_task_data_json = self._normalized_task_data_json(task, request.task_data_json)
            validation_goal.task_data_json = normalized_task_data_json
            self._prepare_task(task, validation_goal, task_id)
        except (
            ActionTaskDataError,
            ControlTaskValidationError,
            MissionTaskValidationError,
            ServiceTaskDataError,
            WaitTaskValidationError,
        ) as exc:
            response.valid = False
            response.error_code = ErrorCodeV1.TASK_DATA_PARSING_FAILED
            response.error_message = str(exc)
            response.normalized_task_data_json = request.task_data_json
            return response
        except (ActionTaskConfigError, ServiceTaskConfigError) as exc:
            response.valid = False
            response.error_code = ErrorCodeV1.TASK_START_FAILED
            response.error_message = str(exc)
            response.normalized_task_data_json = request.task_data_json
            return response
        except NotImplementedError:
            response.valid = False
            response.error_code = ErrorCodeV1.UNSUPPORTED
            response.error_message = f"Task server type is not implemented yet: {task.task_server_type}"
            response.normalized_task_data_json = request.task_data_json
            return response

        response.valid = True
        response.error_code = ""
        response.error_message = ""
        response.normalized_task_data_json = normalized_task_data_json
        return response

    def _normalized_task_data_json(self, task: TaskDefinition, task_data_json: str) -> str:
        payload_text = task_data_json or "{}"
        if task.task_server_type == "system/mission":
            payload_text = self._resolve_mission_task_data_json(payload_text)

        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            return payload_text
        if not isinstance(payload, dict):
            return payload_text
        return self._json_dumps(payload)

    def _task_schema_json(self, task: TaskDefinition) -> str:
        schema: dict[str, Any] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": task.task_name,
            "type": "object",
            "additionalProperties": True,
            "x-task-server-type": task.task_server_type,
        }

        if task.task_server_type == "system/wait":
            schema["properties"] = {
                "duration_sec": {
                    "type": "number",
                    "minimum": 0,
                },
            }
            return self._json_dumps(schema)
        if task.task_server_type == "system/mission":
            schema["properties"] = self._mission_schema_properties()
            return self._json_dumps(schema)
        if task.task_server_type == "system/cancel_task":
            schema["properties"] = {
                "task_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "source": {"type": "string"},
                "correlation_id": {"type": "string"},
            }
            return self._json_dumps(schema)
        if task.task_server_type == "system/stop":
            schema["properties"] = {
                "source": {"type": "string"},
                "correlation_id": {"type": "string"},
            }
            return self._json_dumps(schema)
        if task.task_server_type == "action":
            schema["x-ros-interface"] = task.msg_interface
            schema["properties"] = self._ros_message_schema_properties(get_action(task.msg_interface).Goal)
            return self._json_dumps(schema)
        if task.task_server_type == "service":
            schema["x-ros-interface"] = task.msg_interface
            schema["properties"] = self._ros_message_schema_properties(get_service(task.msg_interface).Request)
            return self._json_dumps(schema)

        schema["x-error"] = f"Task server type is not implemented yet: {task.task_server_type}"
        return self._json_dumps(schema)

    def _mission_schema_properties(self) -> dict[str, Any]:
        return {
            "mission_id": {"type": "string"},
            "template_path": {"type": "string"},
            "template_id": {"type": "string"},
            "params": {"type": "object"},
            "subtasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": True,
                    "properties": {
                        "subtask_id": {"type": "string"},
                        "task_id": {"type": "string"},
                        "task_name": {"type": "string"},
                        "task_data_json": {},
                        "allow_skipping": {"type": "boolean"},
                        "max_attempts": {"type": "integer", "minimum": 1},
                        "retry_backoff_sec": {"type": "number", "minimum": 0},
                        "timeout_sec": {"type": "number", "minimum": 0},
                        "depends_on": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "condition_json": {},
                    },
                },
            },
        }

    def _ros_message_schema_properties(self, message_type: Any) -> dict[str, Any]:
        return {
            field_name: self._ros_field_schema(field_type)
            for field_name, field_type in message_type.get_fields_and_field_types().items()
        }

    def _ros_field_schema(self, field_type: str) -> dict[str, Any]:
        sequence_prefix = "sequence<"
        if field_type.startswith(sequence_prefix) and field_type.endswith(">"):
            item_type = field_type[len(sequence_prefix) : -1]
            return {
                "type": "array",
                "items": self._ros_field_schema(item_type),
                "x-ros-type": field_type,
            }

        if "[" in field_type and field_type.endswith("]"):
            item_type = field_type.split("[", 1)[0]
            return {
                "type": "array",
                "items": self._ros_field_schema(item_type),
                "x-ros-type": field_type,
            }

        primitive_schema = self._ros_primitive_schema(field_type)
        if primitive_schema is not None:
            return primitive_schema

        return {
            "type": "object",
            "x-ros-type": field_type,
        }

    def _ros_primitive_schema(self, field_type: str) -> dict[str, Any] | None:
        if field_type in {"bool", "boolean"}:
            return {
                "type": "boolean",
                "x-ros-type": field_type,
            }
        if field_type in {"float", "double", "float32", "float64"}:
            return {
                "type": "number",
                "x-ros-type": field_type,
            }
        if field_type in {
            "byte",
            "char",
            "int8",
            "uint8",
            "int16",
            "uint16",
            "int32",
            "uint32",
            "int64",
            "uint64",
        }:
            return {
                "type": "integer",
                "x-ros-type": field_type,
            }
        if field_type in {"string", "wstring"}:
            return {
                "type": "string",
                "x-ros-type": field_type,
            }
        return None

    def _list_task_records(
        self,
        request: ListTaskRecordsV1.Request,
        response: ListTaskRecordsV1.Response,
    ) -> ListTaskRecordsV1.Response:
        if self._storage is not None:
            response.records = self._stored_task_records(request)
            return response

        records_by_id = OrderedDict(self._task_records)
        for active_task in self._active_tasks.list():
            if active_task.task_id not in records_by_id:
                records_by_id[active_task.task_id] = self._active_task_to_record(active_task, task_data_json="")

        records = [
            self._copy_task_record(record)
            for record in reversed(records_by_id.values())
            if self._task_record_matches(record, request)
        ]
        if request.limit > 0:
            records = records[: request.limit]
        response.records = records
        return response

    def _task_record_matches(self, record: TaskRecordV1, request: ListTaskRecordsV1.Request) -> bool:
        result = record.result
        return (
            (not request.task_name or result.task_name == request.task_name)
            and (not request.status or result.status == request.status)
            and (not request.source or result.source == request.source)
            and (not request.correlation_id or result.correlation_id == request.correlation_id)
            and (not request.robot_id or result.robot_id == request.robot_id)
            and (not request.fleet_id or result.fleet_id == request.fleet_id)
            and (not request.site_id or result.site_id == request.site_id)
            and (not request.zone_id or result.zone_id == request.zone_id)
            and (not request.operator_id or result.operator_id == request.operator_id)
            and (not request.tenant_id or result.tenant_id == request.tenant_id)
            and (not request.trace_id or result.trace_id == request.trace_id)
            and (not request.idempotency_key or result.idempotency_key == request.idempotency_key)
        )

    def _cancel_tasks(
        self,
        request: CancelTasksV1.Request,
        response: CancelTasksV1.Response,
    ) -> CancelTasksV1.Response:
        self._publish_system_control_event(
            event_type="system.cancel.requested",
            task_id="",
            task_name="system/cancel_task",
            source=request.source,
            correlation_id=request.correlation_id,
            data=self._cancel_request_observability_data(request),
        )
        response = self._cancel_matching_tasks(request, response)
        self._publish_system_control_event(
            event_type="system.cancel.completed",
            task_id="",
            task_name="system/cancel_task",
            source=request.source,
            correlation_id=request.correlation_id,
            success=response.success,
            error_code=response.error_code,
            error_message=response.error_message,
            data=self._cancel_response_observability_data(request, response),
        )
        return response

    def _cancel_matching_tasks(
        self,
        request: CancelTasksV1.Request,
        response: CancelTasksV1.Response,
        ignored_active_task_ids: set[str] | None = None,
    ) -> CancelTasksV1.Response:
        ignored_ids = set(ignored_active_task_ids or set())
        selected_tasks = self._active_tasks.matching(
            task_ids=list(request.task_ids),
            source=request.source,
            correlation_id=request.correlation_id,
        )
        selected_tasks = [task for task in selected_tasks if task.task_id not in ignored_ids]
        selected_ids = {task.task_id for task in selected_tasks}
        requested_ids = set(request.task_ids)

        canceled_task_ids: list[str] = []
        failed_task_ids = sorted(requested_ids - selected_ids)

        for task in selected_tasks:
            if self._cancel_active_task(task):
                canceled_task_ids.append(task.task_id)
            else:
                failed_task_ids.append(task.task_id)

        response.canceled_task_ids = canceled_task_ids
        response.failed_task_ids = sorted(failed_task_ids)
        response.success = not response.failed_task_ids
        response.error_code = "" if response.success else ErrorCodeV1.TASK_CANCEL_FAILED
        response.error_message = "" if response.success else "Some tasks could not be canceled."
        return response

    def _stop_tasks(
        self,
        request: StopTasksV1.Request,
        response: StopTasksV1.Response,
    ) -> StopTasksV1.Response:
        self._publish_system_control_event(
            event_type="system.stop.requested",
            task_id="",
            task_name="system/stop",
            source=request.source,
            correlation_id=request.correlation_id,
            data=self._stop_request_observability_data(request),
        )
        response = self._stop_matching_tasks(request, response)
        self._publish_system_control_event(
            event_type="system.stop.completed",
            task_id="",
            task_name="system/stop",
            source=request.source,
            correlation_id=request.correlation_id,
            success=response.success,
            error_code=response.error_code,
            error_message=response.error_message,
            data=self._stop_response_observability_data(request, response),
        )
        return response

    def _stop_matching_tasks(
        self,
        request: StopTasksV1.Request,
        response: StopTasksV1.Response,
    ) -> StopTasksV1.Response:
        stopped_task_ids: list[str] = []
        failed_task_ids: list[str] = []

        for task in self._active_tasks.matching(source=request.source, correlation_id=request.correlation_id):
            if not task.cancel_on_stop:
                continue
            if self._cancel_active_task(task):
                stopped_task_ids.append(task.task_id)
            else:
                failed_task_ids.append(task.task_id)

        response.stopped_task_ids = stopped_task_ids
        response.success = not failed_task_ids
        response.error_code = "" if response.success else ErrorCodeV1.TASK_CANCEL_FAILED
        response.error_message = "" if response.success else "Some tasks could not be stopped."
        return response

    def _reload_config(
        self,
        _request: ReloadConfigV1.Request,
        response: ReloadConfigV1.Response,
    ) -> ReloadConfigV1.Response:
        tasks_config_path = str(self.get_parameter("tasks_config_path").value)
        try:
            self._task_registry = self._load_task_registry(strict=True)
        except TaskConfigError as exc:
            response.success = False
            response.error_code = ErrorCodeV1.TASK_DATA_PARSING_FAILED
            response.error_message = str(exc)
            self._publish_system_config_event(
                event_type="system.config.reload_failed",
                success=False,
                error_code=response.error_code,
                error_message=response.error_message,
                data={
                    "tasks_config_path": tasks_config_path,
                },
            )
            return response

        response.success = True
        response.error_code = ""
        response.error_message = ""
        self._publish_system_config_event(
            event_type="system.config.reloaded",
            success=True,
            data={
                "tasks_config_path": tasks_config_path,
                "task_count": len(self._task_registry.list(include_system_tasks=True)),
            },
        )
        return response

    def _unsupported_task_control(self, _request: Any, response: Any) -> Any:
        response.success = False
        response.error_code = ErrorCodeV1.UNSUPPORTED
        response.error_message = "Task control capability is not available."
        return response

    def _make_cancel_callback(self, task: TaskDefinition, task_id: str) -> Any:
        if task.task_server_type == "action":
            timeout_sec = task.cancel_timeout if task.cancel_timeout > 0 else None
            return lambda: self._action_task_client.cancel(task_id, timeout_sec=timeout_sec)
        if task.task_server_type == "system/mission":
            return lambda: self._cancel_mission(task_id)
        return None

    def _cancel_active_task(self, task: ActiveTaskEntry) -> bool:
        if task.cancel_callback is None:
            return False
        try:
            return task.cancel_callback()
        except Exception as exc:  # noqa: BLE001 - cancellation failures are reported through the public service.
            self.get_logger().warning(f"Failed to cancel task {task.task_id}: {exc}")
            return False

    def _cancel_mission(self, mission_task_id: str) -> bool:
        active_subtask_id = self._active_mission_subtasks.get(mission_task_id)
        if not active_subtask_id:
            return False

        active_subtask = self._active_tasks.get(active_subtask_id)
        if active_subtask is None:
            return False
        return self._cancel_active_task(active_subtask)

    def _publish_empty_active_tasks(self) -> None:
        msg = ActiveTaskArrayV1()
        msg.stamp = self.get_clock().now().to_msg()
        msg.api_version = self.api_version
        msg.task_name = "task_orchestrator/active_tasks"
        msg.source = "system"
        msg.created_at = msg.stamp
        msg.started_at = msg.stamp
        msg.finished_at = msg.stamp
        msg.result_json = "{}"
        self._active_tasks_pub.publish(msg)

    def _publish_active_tasks(self) -> None:
        msg = ActiveTaskArrayV1()
        msg.stamp = self.get_clock().now().to_msg()
        msg.api_version = self.api_version
        msg.task_name = "task_orchestrator/active_tasks"
        msg.source = "system"
        msg.created_at = msg.stamp
        msg.started_at = msg.stamp
        msg.finished_at = msg.stamp
        msg.status = TaskStatusV1.IN_PROGRESS if len(self._active_tasks) else ""
        msg.result_json = "{}"
        msg.active_tasks = [self._active_task_to_msg(task) for task in self._active_tasks.list()]
        self._active_tasks_pub.publish(msg)

    def _publish_result(self, result: ExecuteTaskV1.Result) -> None:
        self._results_pub.publish(self._execute_result_to_task_result_msg(result))

    def _store_received_task_record(self, request: ExecuteTaskV1.Goal, task_id: str, created_at: Any) -> None:
        result = TaskResultV1()
        result.api_version = self.api_version
        result.task_id = task_id
        result.task_name = request.task_name
        result.source = request.source
        result.priority = request.priority
        result.correlation_id = request.correlation_id
        result.status = TaskStatusV1.RECEIVED
        result.result_json = "{}"
        result.created_at = created_at
        self._copy_public_context(request, result)

        record = TaskRecordV1()
        record.result = result
        record.active = False
        record.task_data_json = request.task_data_json
        record.tags = list(request.tags)
        self._copy_request_scheduling(request, record)
        self._copy_public_context(request, record)
        self._set_task_record(task_id, record)

    def _store_active_task_record(self, task: ActiveTaskEntry, task_data_json: str) -> None:
        self._set_task_record(task.task_id, self._active_task_to_record(task, task_data_json=task_data_json))

    def _store_terminal_task_record(self, result: ExecuteTaskV1.Result) -> None:
        existing_record = self._task_records.get(result.task_id)

        record = TaskRecordV1()
        record.result = self._execute_result_to_task_result_msg(result)
        record.active = False
        if existing_record is not None:
            record.task_data_json = existing_record.task_data_json
            record.tags = list(existing_record.tags)
            self._copy_record_scheduling(existing_record, record)
            self._copy_public_context(existing_record, record)
        self._set_task_record(result.task_id, record)

    def _set_task_record(self, task_id: str, record: TaskRecordV1) -> None:
        self._sync_task_record_metadata(record)
        self._task_records[task_id] = record
        self._task_records.move_to_end(task_id)
        self._write_task_record(record)
        self._enforce_task_record_limit()

    def _enforce_task_record_limit(self) -> None:
        while len(self._task_records) > self._task_record_limit:
            evicted_task_id = self._oldest_inactive_task_record_id()
            if not evicted_task_id:
                return
            self._task_records.pop(evicted_task_id, None)

    def _oldest_inactive_task_record_id(self) -> str:
        for task_id, record in self._task_records.items():
            if not record.active:
                return task_id
        return ""

    def _active_task_to_record(self, task: ActiveTaskEntry, task_data_json: str) -> TaskRecordV1:
        result = TaskResultV1()
        result.api_version = task.api_version
        result.task_id = task.task_id
        result.task_name = task.task_name
        result.source = task.source
        result.priority = task.priority
        result.correlation_id = task.correlation_id
        result.status = task.status
        result.result_json = "{}"
        result.created_at = task.created_at
        result.started_at = task.started_at
        self._copy_public_context(task, result)

        record = TaskRecordV1()
        record.result = result
        record.active = True
        record.task_data_json = task_data_json
        record.tags = list(task.tags)
        self._copy_active_task_scheduling(task, record)
        self._copy_public_context(task, record)
        return record

    def _copy_task_record(self, record: TaskRecordV1) -> TaskRecordV1:
        copied_record = TaskRecordV1()
        copied_record.result = self._copy_task_result(record.result)
        copied_record.active = record.active
        copied_record.task_data_json = record.task_data_json
        copied_record.tags = list(record.tags)
        self._copy_record_scheduling(record, copied_record)
        self._copy_public_context(record, copied_record)
        self._sync_task_record_metadata(copied_record)
        return copied_record

    def _copy_task_result(self, result: TaskResultV1) -> TaskResultV1:
        copied_result = TaskResultV1()
        copied_result.api_version = result.api_version
        copied_result.task_id = result.task_id
        copied_result.task_name = result.task_name
        copied_result.source = result.source
        copied_result.priority = result.priority
        copied_result.correlation_id = result.correlation_id
        copied_result.status = result.status
        copied_result.error_code = result.error_code
        copied_result.error_message = result.error_message
        copied_result.result_json = result.result_json
        copied_result.created_at = result.created_at
        copied_result.started_at = result.started_at
        copied_result.finished_at = result.finished_at
        copied_result.duration_sec = result.duration_sec
        copied_result.total_duration_sec = result.total_duration_sec
        self._copy_public_context(result, copied_result)
        return copied_result

    def _copy_public_context(self, source: Any, target: Any) -> None:
        for field_name in _PUBLIC_CONTEXT_FIELDS:
            if hasattr(source, field_name) and hasattr(target, field_name):
                setattr(target, field_name, getattr(source, field_name))

    def _copy_request_scheduling(self, source: ExecuteTaskV1.Goal, target: TaskRecordV1) -> None:
        target.scheduled_at = source.scheduled_at
        target.delay_sec = source.delay_sec
        target.deadline_at = source.deadline_at
        target.timeout_sec = source.timeout_sec
        target.queue_on_conflict = source.queue_on_conflict

    def _copy_record_scheduling(self, source: TaskRecordV1, target: TaskRecordV1) -> None:
        target.scheduled_at = source.scheduled_at
        target.delay_sec = source.delay_sec
        target.deadline_at = source.deadline_at
        target.timeout_sec = source.timeout_sec
        target.queue_on_conflict = source.queue_on_conflict

    def _copy_active_task_scheduling(self, source: ActiveTaskEntry, target: TaskRecordV1) -> None:
        target.timeout_sec = source.timeout_sec

    def _execute_result_to_task_result_msg(self, result: ExecuteTaskV1.Result) -> TaskResultV1:
        msg = TaskResultV1()
        msg.api_version = result.api_version
        msg.task_id = result.task_id
        msg.task_name = result.task_name
        msg.source = result.source
        msg.priority = result.priority
        msg.correlation_id = result.correlation_id
        msg.status = result.status
        msg.error_code = result.error_code
        msg.error_message = result.error_message
        msg.result_json = result.result_json
        msg.created_at = result.created_at
        msg.started_at = result.started_at
        msg.finished_at = result.finished_at
        msg.duration_sec = result.duration_sec
        msg.total_duration_sec = result.total_duration_sec
        self._copy_public_context(result, msg)
        return msg

    def _sync_task_record_metadata(self, record: TaskRecordV1) -> None:
        result = record.result
        record.api_version = result.api_version
        record.task_id = result.task_id
        record.task_name = result.task_name
        record.source = result.source
        record.priority = result.priority
        record.correlation_id = result.correlation_id
        record.created_at = result.created_at
        record.started_at = result.started_at
        record.finished_at = result.finished_at
        record.status = result.status
        record.error_code = result.error_code
        record.error_message = result.error_message
        record.result_json = result.result_json
        record.duration_sec = result.duration_sec
        record.total_duration_sec = result.total_duration_sec
        record.idempotency_key = result.idempotency_key
        record.metadata_json = result.metadata_json
        record.robot_id = result.robot_id
        record.fleet_id = result.fleet_id
        record.site_id = result.site_id
        record.zone_id = result.zone_id
        record.operator_id = result.operator_id
        record.tenant_id = result.tenant_id
        record.trace_id = result.trace_id

    def _publish_mission_lifecycle_event(
        self,
        event_type: str,
        mission_id: str,
        mission_task_id: str,
        source: str,
        correlation_id: str,
        status: str,
        previous_status: str = "",
        error_code: str = "",
        error_message: str = "",
        priority: int = 0,
        data: dict[str, Any] | None = None,
    ) -> None:
        event_data = {
            "mission_id": mission_id,
            "mission_task_id": mission_task_id,
            **(data or {}),
        }
        self._publish_event(
            event_type=event_type,
            task_id=mission_task_id,
            task_name="system/mission",
            source=source,
            correlation_id=correlation_id,
            previous_status=previous_status,
            status=status,
            error_code=error_code,
            error_message=error_message,
            priority=priority,
            data=event_data,
        )

    def _publish_mission_subtask_event(
        self,
        event_type: str,
        mission_id: str,
        mission_task_id: str,
        subtask: MissionSubtask,
        source: str,
        correlation_id: str,
        status: str,
        priority: int = 0,
        data: dict[str, Any] | None = None,
    ) -> None:
        event_data = {
            "mission_id": mission_id,
            "mission_task_id": mission_task_id,
            "subtask_id": subtask.subtask_id,
            "subtask_task_id": subtask.task_id,
            **(data or {}),
        }
        self._publish_event(
            event_type=event_type,
            task_id=subtask.task_id,
            task_name=subtask.task_name,
            source=source,
            correlation_id=correlation_id,
            status=status,
            priority=priority,
            data=event_data,
        )

    def _publish_mission_subtask_result_event(
        self,
        mission_id: str,
        mission_task_id: str,
        subtask_result: MissionSubtaskResult,
        source: str,
        correlation_id: str,
        subtask_index: int,
        total_subtasks: int,
        priority: int = 0,
    ) -> None:
        event_type = "mission.subtask.completed"
        if subtask_result.skipped:
            event_type = "mission.subtask.skipped"
        elif subtask_result.status != TaskStatusV1.DONE:
            event_type = "mission.subtask.failed"

        self._publish_event(
            event_type=event_type,
            task_id=subtask_result.task_id,
            task_name=subtask_result.task_name,
            source=source,
            correlation_id=correlation_id,
            previous_status=TaskStatusV1.IN_PROGRESS,
            status=subtask_result.status,
            error_code=subtask_result.error_code,
            error_message=subtask_result.error_message,
            priority=priority,
            data={
                "mission_id": mission_id,
                "mission_task_id": mission_task_id,
                "subtask_id": subtask_result.subtask_id,
                "subtask_index": subtask_index,
                "total_subtasks": total_subtasks,
                "attempts": subtask_result.attempts,
                "skipped": subtask_result.skipped,
            },
        )

    def _publish_system_control_event(
        self,
        event_type: str,
        task_id: str,
        task_name: str,
        source: str,
        correlation_id: str,
        data: dict[str, Any],
        success: bool | None = None,
        error_code: str = "",
        error_message: str = "",
    ) -> None:
        status = TaskStatusV1.IN_PROGRESS
        if success is True:
            status = TaskStatusV1.DONE
        elif success is False:
            status = TaskStatusV1.ERROR

        self._publish_event(
            event_type=event_type,
            task_id=task_id,
            task_name=task_name,
            source=source,
            correlation_id=correlation_id,
            previous_status=TaskStatusV1.IN_PROGRESS if success is not None else "",
            status=status,
            error_code=error_code,
            error_message=error_message,
            data={
                **data,
                "success": success,
            },
        )

    def _publish_system_config_event(
        self,
        event_type: str,
        success: bool,
        data: dict[str, Any],
        error_code: str = "",
        error_message: str = "",
    ) -> None:
        self._publish_event(
            event_type=event_type,
            task_id="",
            task_name="system/config",
            source="system",
            correlation_id="",
            status=TaskStatusV1.DONE if success else TaskStatusV1.ERROR,
            error_code=error_code,
            error_message=error_message,
            data={
                **data,
                "success": success,
            },
        )

    def _cancel_request_observability_data(self, request: CancelTasksV1.Request) -> dict[str, Any]:
        return {
            "requested_task_ids": list(request.task_ids),
            "selector_source": request.source,
            "selector_correlation_id": request.correlation_id,
        }

    def _cancel_response_observability_data(
        self,
        request: CancelTasksV1.Request,
        response: CancelTasksV1.Response,
    ) -> dict[str, Any]:
        return {
            **self._cancel_request_observability_data(request),
            "canceled_task_ids": list(response.canceled_task_ids),
            "failed_task_ids": list(response.failed_task_ids),
        }

    def _stop_request_observability_data(self, request: StopTasksV1.Request) -> dict[str, Any]:
        return {
            "selector_source": request.source,
            "selector_correlation_id": request.correlation_id,
        }

    def _stop_response_observability_data(
        self,
        request: StopTasksV1.Request,
        response: StopTasksV1.Response,
    ) -> dict[str, Any]:
        return {
            **self._stop_request_observability_data(request),
            "stopped_task_ids": list(response.stopped_task_ids),
        }

    def _publish_mission_feedback(
        self,
        mission_id: str,
        mission_task_id: str,
        active_subtask_id: str,
        completed_subtasks: int,
        total_subtasks: int,
        source: str,
        correlation_id: str,
        priority: int = 0,
    ) -> None:
        msg = TaskFeedbackV1()
        msg.api_version = self.api_version
        msg.task_id = mission_task_id
        msg.task_name = "system/mission"
        msg.source = source
        msg.priority = priority
        msg.correlation_id = correlation_id
        msg.status = TaskStatusV1.IN_PROGRESS
        msg.result_json = "{}"
        msg.progress = float(completed_subtasks / total_subtasks) if total_subtasks else 1.0
        msg.feedback_json = json.dumps(
            {
                "mission_id": mission_id,
                "active_subtask_id": active_subtask_id,
                "completed_subtasks": completed_subtasks,
                "total_subtasks": total_subtasks,
            },
            sort_keys=True,
        )
        msg.stamp = self.get_clock().now().to_msg()
        self._feedback_pub.publish(msg)

    def _publish_task_feedback(
        self,
        task_id: str,
        task_name: str,
        source: str,
        correlation_id: str,
        progress: float,
        feedback: dict[str, Any],
        priority: int = 0,
        created_at: Any | None = None,
        started_at: Any | None = None,
        finished_at: Any | None = None,
        status: str = "",
        error_code: str = "",
        error_message: str = "",
        result_json: str = "{}",
        context: Any | None = None,
    ) -> None:
        msg = TaskFeedbackV1()
        msg.api_version = self.api_version
        msg.task_id = task_id
        msg.task_name = task_name
        msg.source = source
        msg.priority = priority
        msg.correlation_id = correlation_id
        if created_at is not None:
            msg.created_at = created_at
        if started_at is not None:
            msg.started_at = started_at
        if finished_at is not None:
            msg.finished_at = finished_at
        msg.status = status
        msg.error_code = error_code
        msg.error_message = error_message
        msg.result_json = result_json
        msg.duration_sec = self._duration_sec(msg.started_at, msg.finished_at)
        msg.total_duration_sec = self._duration_sec(msg.created_at, msg.finished_at)
        if context is not None:
            self._copy_public_context(context, msg)
        msg.progress = progress
        msg.feedback_json = self._json_dumps(feedback)
        msg.stamp = self.get_clock().now().to_msg()
        self._feedback_pub.publish(msg)

    def _publish_event(
        self,
        event_type: str,
        task_id: str,
        task_name: str,
        source: str,
        correlation_id: str,
        status: str,
        previous_status: str = "",
        error_code: str = "",
        error_message: str = "",
        priority: int = 0,
        created_at: Any | None = None,
        started_at: Any | None = None,
        finished_at: Any | None = None,
        result_json: str = "{}",
        data: dict[str, Any] | None = None,
        context: Any | None = None,
    ) -> None:
        msg = TaskEventV1()
        msg.api_version = self.api_version
        msg.event_id = str(uuid.uuid4())
        msg.event_type = event_type
        msg.task_id = task_id
        msg.task_name = task_name
        msg.source = source
        msg.priority = priority
        msg.correlation_id = correlation_id
        if created_at is not None:
            msg.created_at = created_at
        if started_at is not None:
            msg.started_at = started_at
        if finished_at is not None:
            msg.finished_at = finished_at
        msg.previous_status = previous_status
        msg.status = status
        msg.error_code = error_code
        msg.error_message = error_message
        msg.result_json = result_json
        msg.duration_sec = self._duration_sec(msg.started_at, msg.finished_at)
        msg.total_duration_sec = self._duration_sec(msg.created_at, msg.finished_at)
        if context is not None:
            self._copy_public_context(context, msg)
        msg.data_json = self._json_dumps(data or {})
        msg.stamp = self.get_clock().now().to_msg()
        self._set_event_record(msg)
        self._events_pub.publish(msg)
        self._log_task_event(msg, data or {})

    def _set_event_record(self, event: TaskEventV1) -> None:
        self._write_event(event)
        if self._event_record_limit == 0:
            return
        self._event_records[event.event_id] = self._copy_task_event(event)
        self._event_records.move_to_end(event.event_id)
        while len(self._event_records) > self._event_record_limit:
            self._event_records.popitem(last=False)

    def _write_task_record(self, record: TaskRecordV1) -> None:
        if self._storage is None:
            return
        try:
            self._storage.write_task_record(record)
        except Exception as exc:  # noqa: BLE001 - persistence must not break task execution.
            self.get_logger().error(f"Failed to write SQLite task record {record.result.task_id}: {exc}")

    def _write_event(self, event: TaskEventV1) -> None:
        if self._storage is None:
            return
        try:
            self._storage.write_event(event)
        except Exception as exc:  # noqa: BLE001 - persistence must not break event publication.
            self.get_logger().error(f"Failed to write SQLite event {event.event_id}: {exc}")

    def _copy_task_event(self, event: TaskEventV1) -> TaskEventV1:
        copied_event = TaskEventV1()
        copied_event.api_version = event.api_version
        copied_event.event_id = event.event_id
        copied_event.event_type = event.event_type
        copied_event.task_id = event.task_id
        copied_event.task_name = event.task_name
        copied_event.source = event.source
        copied_event.priority = event.priority
        copied_event.correlation_id = event.correlation_id
        copied_event.created_at = event.created_at
        copied_event.started_at = event.started_at
        copied_event.finished_at = event.finished_at
        copied_event.previous_status = event.previous_status
        copied_event.status = event.status
        copied_event.error_code = event.error_code
        copied_event.error_message = event.error_message
        copied_event.result_json = event.result_json
        copied_event.data_json = event.data_json
        copied_event.duration_sec = event.duration_sec
        copied_event.total_duration_sec = event.total_duration_sec
        self._copy_public_context(event, copied_event)
        copied_event.stamp = event.stamp
        return copied_event

    def _publish_terminal_result_and_event(self, result: ExecuteTaskV1.Result, event_type: str) -> None:
        self._store_terminal_task_record(result)
        self._publish_result(result)
        terminal_data = self._terminal_observability_data(result)
        self._publish_task_feedback(
            task_id=result.task_id,
            task_name=result.task_name,
            source=result.source,
            correlation_id=result.correlation_id,
            progress=1.0,
            feedback={
                "status": result.status,
                "event_type": event_type,
                "error_code": result.error_code,
                "duration_sec": terminal_data["duration_sec"],
            },
            priority=result.priority,
            created_at=result.created_at,
            started_at=result.started_at,
            finished_at=result.finished_at,
            status=result.status,
            error_code=result.error_code,
            error_message=result.error_message,
            result_json=result.result_json,
            context=result,
        )
        self._publish_event(
            event_type=event_type,
            task_id=result.task_id,
            task_name=result.task_name,
            source=result.source,
            correlation_id=result.correlation_id,
            previous_status="" if result.status == TaskStatusV1.REJECTED else TaskStatusV1.IN_PROGRESS,
            status=result.status,
            error_code=result.error_code,
            error_message=result.error_message,
            priority=result.priority,
            created_at=result.created_at,
            started_at=result.started_at,
            finished_at=result.finished_at,
            result_json=result.result_json,
            data=terminal_data,
            context=result,
        )

    def _terminal_event_type(self, status: str) -> str:
        if status == TaskStatusV1.ERROR:
            return "task.failed"
        if status == TaskStatusV1.DONE:
            return "task.completed"
        if status == TaskStatusV1.CANCELED:
            return "task.canceled"
        if status == TaskStatusV1.REJECTED:
            return "task.rejected"
        return "task.failed"

    def _task_definition_observability_data(
        self,
        task: TaskDefinition,
        request: ExecuteTaskV1.Goal,
    ) -> dict[str, Any]:
        return {
            "task_server_type": task.task_server_type,
            "blocking": task.blocking,
            "cancel_on_stop": task.cancel_on_stop,
            "cancel_reported_as_success": task.cancel_reported_as_success,
            "reentrant": task.reentrant,
            "priority": request.priority,
            "tags": list(request.tags),
            "resources": list(task.resources),
            "task_group": task.task_group,
            "capability_tags": list(task.capability_tags),
            "queue_on_conflict_default": task.queue_on_conflict_default,
            **self._request_scheduling_observability_data(request),
            **self._request_context_observability_data(request),
        }

    def _request_scheduling_observability_data(self, request: ExecuteTaskV1.Goal) -> dict[str, Any]:
        return {
            "scheduled_at_set": self._time_is_set(request.scheduled_at),
            "deadline_at_set": self._time_is_set(request.deadline_at),
            "delay_sec": float(request.delay_sec),
            "timeout_sec": float(request.timeout_sec),
            "queue_on_conflict": bool(request.queue_on_conflict),
        }

    def _request_context_observability_data(self, request: Any) -> dict[str, Any]:
        return {
            "idempotency_key": getattr(request, "idempotency_key", ""),
            "robot_id": getattr(request, "robot_id", ""),
            "fleet_id": getattr(request, "fleet_id", ""),
            "site_id": getattr(request, "site_id", ""),
            "zone_id": getattr(request, "zone_id", ""),
            "operator_id": getattr(request, "operator_id", ""),
            "tenant_id": getattr(request, "tenant_id", ""),
            "trace_id": getattr(request, "trace_id", ""),
            "has_metadata_json": bool(getattr(request, "metadata_json", "")),
        }

    def _terminal_observability_data(self, result: ExecuteTaskV1.Result) -> dict[str, Any]:
        return {
            "status": result.status,
            "error_code": result.error_code,
            "has_error": bool(result.error_code or result.error_message),
            "has_result_json": bool(result.result_json and result.result_json != "{}"),
            "result_size": len(result.result_json or ""),
            "duration_sec": result.duration_sec,
            "total_duration_sec": result.total_duration_sec,
        }

    def _duration_sec(self, start_time: Any, finish_time: Any) -> float:
        start_sec = getattr(start_time, "sec", 0)
        start_nanosec = getattr(start_time, "nanosec", 0)
        finish_sec = getattr(finish_time, "sec", 0)
        finish_nanosec = getattr(finish_time, "nanosec", 0)
        duration = (finish_sec - start_sec) + ((finish_nanosec - start_nanosec) / 1_000_000_000.0)
        return max(0.0, duration)

    def _time_is_set(self, value: Any) -> bool:
        return bool(getattr(value, "sec", 0) or getattr(value, "nanosec", 0))

    def _json_dumps(self, payload: dict[str, Any]) -> str:
        return json.dumps(payload, sort_keys=True)

    def _log_task_event(self, event: TaskEventV1, data: dict[str, Any]) -> None:
        message = self._json_dumps(self._structured_log_payload(event, data))
        if event.status == TaskStatusV1.ERROR:
            self.get_logger().error(message)
        elif event.status == TaskStatusV1.REJECTED:
            self.get_logger().warning(message)
        else:
            self.get_logger().info(message)

    def _structured_log_payload(self, event: TaskEventV1, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "event": "orchestrator_event",
            "schema": "task_orchestrator.event.v1",
            "api_version": event.api_version,
            "event_id": event.event_id,
            "event_category": self._event_category(event.event_type),
            "event_type": event.event_type,
            "task_id": event.task_id,
            "task_name": event.task_name,
            "source": event.source,
            "correlation_id": event.correlation_id,
            "trace_id": event.trace_id,
            "robot_id": event.robot_id,
            "fleet_id": event.fleet_id,
            "site_id": event.site_id,
            "zone_id": event.zone_id,
            "operator_id": event.operator_id,
            "tenant_id": event.tenant_id,
            "idempotency_key": event.idempotency_key,
            "previous_status": event.previous_status,
            "status": event.status,
            "error_code": event.error_code,
            "has_error": bool(event.error_code or event.error_message),
            "task_server_type": str(data.get("task_server_type", "")),
            "duration_sec": float(data.get("duration_sec", 0.0)),
            "total_duration_sec": float(data.get("total_duration_sec", 0.0)),
            "result_size": int(data.get("result_size", 0)),
            "task_record_count": len(self._task_records),
            "event_record_count": len(self._event_records),
            "data": data,
        }

    def _event_category(self, event_type: str) -> str:
        return event_type.split(".", 1)[0] if event_type else ""

    def _make_result(
        self,
        request: ExecuteTaskV1.Goal,
        task_id: str,
        status: str,
        created_at: Any,
        started_at: Any,
        finished_at: Any,
        error_code: str = "",
        error_message: str = "",
        result_json: str = "{}",
    ) -> ExecuteTaskV1.Result:
        result = ExecuteTaskV1.Result()
        result.api_version = self.api_version
        result.task_id = task_id
        result.task_name = request.task_name
        result.source = request.source
        result.priority = request.priority
        result.correlation_id = request.correlation_id
        result.status = status
        result.error_code = error_code
        result.error_message = error_message
        result.result_json = result_json_with_error(result_json, error_code, error_message)
        result.created_at = created_at
        result.started_at = started_at
        result.finished_at = finished_at
        result.duration_sec = self._duration_sec(started_at, finished_at)
        result.total_duration_sec = self._duration_sec(created_at, finished_at)
        self._copy_public_context(request, result)
        return result

    def _task_definition_to_msg(self, task: TaskDefinition) -> TaskSpecV1:
        msg = TaskSpecV1()
        msg.api_version = self.api_version
        msg.source = "system"
        msg.priority = task.priority_default
        msg.result_json = "{}"
        msg.task_name = task.task_name
        msg.topic = task.topic
        msg.msg_interface = task.msg_interface
        msg.task_server_type = task.task_server_type
        msg.blocking = task.blocking
        msg.cancel_on_stop = task.cancel_on_stop
        msg.cancel_reported_as_success = task.cancel_reported_as_success
        msg.reentrant = task.reentrant
        msg.is_system_task = task.is_system_task
        msg.priority_default = task.priority_default
        msg.cancel_timeout = task.cancel_timeout
        msg.resources = list(task.resources)
        msg.tags = list(task.tags)
        msg.task_group = task.task_group
        msg.capability_tags = list(task.capability_tags)
        msg.queue_on_conflict_default = task.queue_on_conflict_default
        return msg

    def _active_task_to_msg(self, task: ActiveTaskEntry) -> ActiveTaskV1:
        msg = ActiveTaskV1()
        msg.api_version = task.api_version
        msg.task_id = task.task_id
        msg.task_name = task.task_name
        msg.source = task.source
        msg.correlation_id = task.correlation_id
        msg.priority = task.priority
        msg.status = task.status
        msg.created_at = task.created_at
        msg.started_at = task.started_at
        msg.error_code = ""
        msg.error_message = ""
        msg.result_json = "{}"
        msg.tags = list(task.tags)
        self._copy_public_context(task, msg)
        return msg

    def _load_task_registry(self, strict: bool = False) -> TaskRegistry:
        tasks_config_path = self.get_parameter("tasks_config_path").value
        if not tasks_config_path:
            return TaskRegistry.with_system_tasks()

        try:
            return TaskRegistry.from_yaml_file(tasks_config_path)
        except TaskConfigError as exc:
            if strict:
                raise
            self.get_logger().error(f"Failed to load task registry: {exc}")
            return TaskRegistry.with_system_tasks()


def main(args: list[str] | None = None) -> None:
    """Run the task orchestrator node."""
    rclpy.init(args=args)
    node = TaskOrchestratorNode()
    executor = MultiThreadedExecutor()
    executor.add_node(node)

    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
