"""ROS2 node for ROS2 Task Orchestrator."""

import json
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import rclpy
from rclpy.action import ActionServer, CancelResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy

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
    TOPIC_ACTIVE_TASKS,
    TOPIC_EVENTS,
    TOPIC_FEEDBACK,
    TOPIC_RESULTS,
)
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
    mission_status_from_subtask_status,
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
)


_CONTROL_TASK_SERVER_TYPES = {"system/cancel_task", "system/stop"}


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
    task_status: str
    task_result_json: str
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
        self.declare_parameter("storage.enabled", False)
        self.declare_parameter("storage.sqlite_path", "")
        self.declare_parameter("storage.retention_days", 30)
        self.api_version = self.get_parameter("api_version").value
        self._event_record_limit = max(0, int(self.get_parameter("event_record_limit").value))
        self._task_record_limit = max(0, int(self.get_parameter("task_record_limit").value))
        self._callback_group = ReentrantCallbackGroup()
        self._task_registry = self._load_task_registry()
        self._active_tasks = ActiveTaskRegistry()
        self._active_mission_subtasks: dict[str, str] = {}
        self._event_records: OrderedDict[str, TaskEventV1] = OrderedDict()
        self._task_records: OrderedDict[str, TaskRecordV1] = OrderedDict()
        self._storage = self._make_storage()
        self._control_task = ControlTaskParser()
        self._mission_task = MissionTaskParser()
        self._wait_task = WaitTaskExecutor()
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
            "/task_orchestrator/list_tasks",
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
            "/task_orchestrator/get_task",
            self._get_task,
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
            "/task_orchestrator/cancel_tasks",
            self._cancel_tasks,
            callback_group=self._callback_group,
        )
        self._pause_tasks_service = self.create_service(
            PauseTasksV1,
            "/task_orchestrator/pause_tasks",
            self._unsupported_task_control,
            callback_group=self._callback_group,
        )
        self._resume_tasks_service = self.create_service(
            ResumeTasksV1,
            "/task_orchestrator/resume_tasks",
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
        created_at = self.get_clock().now().to_msg()
        task_id_generated = not request.task_id
        task_id = request.task_id or str(uuid.uuid4())
        request.task_id = task_id
        task = self._task_registry.get(request.task_name)

        self._publish_event(
            event_type="task.received",
            task_id=task_id,
            task_name=request.task_name,
            source=request.source,
            correlation_id=request.correlation_id,
            current_status=TaskStatusV1.RECEIVED,
            data={
                "task_id_generated": task_id_generated,
                "priority": request.priority,
                "tags": list(request.tags),
            },
        )
        self._store_received_task_record(request, task_id, created_at)

        if task is None:
            result = self._make_result(
                request=request,
                task_id=task_id,
                task_status=TaskStatusV1.REJECTED,
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
                task_status=TaskStatusV1.REJECTED,
                error_code=ErrorCodeV1.DUPLICATE_TASK_ID,
                error_message=f"Task ID is already active: {task_id}",
                created_at=created_at,
                started_at=created_at,
                finished_at=created_at,
            )
            goal_handle.abort()
            self._publish_terminal_result_and_event(result, "task.rejected")
            return result

        policy_error = self._apply_start_policy(task, ignored_active_task_ids=ignored_active_task_ids)
        if policy_error:
            result = self._make_result(
                request=request,
                task_id=task_id,
                task_status=TaskStatusV1.REJECTED,
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
            prepared_task = self._prepare_task(task, request.task_data_json, task_id)
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
                task_status=TaskStatusV1.REJECTED,
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
                task_status=TaskStatusV1.REJECTED,
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
                task_status=TaskStatusV1.REJECTED,
                error_code=ErrorCodeV1.UNSUPPORTED,
                error_message=f"Task server type is not implemented yet: {task.task_server_type}",
                created_at=created_at,
                started_at=created_at,
                finished_at=created_at,
            )
            goal_handle.abort()
            self._publish_terminal_result_and_event(result, "task.rejected")
            return result

        started_at = self.get_clock().now().to_msg()
        try:
            active_task = ActiveTaskEntry(
                api_version=self.api_version,
                task_id=task_id,
                task_name=request.task_name,
                source=request.source,
                correlation_id=request.correlation_id,
                priority=request.priority,
                task_status=TaskStatusV1.IN_PROGRESS,
                created_at=created_at,
                started_at=started_at,
                tags=tuple(request.tags),
                task_server_type=task.task_server_type,
                blocking=task.blocking,
                cancel_on_stop=task.cancel_on_stop,
                cancel_callback=self._make_cancel_callback(task, task_id),
            )
            self._active_tasks.add(active_task)
            self._store_active_task_record(active_task, request.task_data_json)
        except DuplicateActiveTaskError:
            result = self._make_result(
                request=request,
                task_id=task_id,
                task_status=TaskStatusV1.REJECTED,
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
            previous_status=TaskStatusV1.RECEIVED,
            current_status=TaskStatusV1.IN_PROGRESS,
            data=self._task_definition_observability_data(task, request),
        )
        self._publish_task_feedback(
            task_id=task_id,
            task_name=request.task_name,
            source=request.source,
            correlation_id=request.correlation_id,
            progress=0.0,
            feedback={
                "task_status": TaskStatusV1.IN_PROGRESS,
                "event_type": "task.started",
            },
        )
        self._publish_active_tasks()

        try:
            outcome = self._execute_prepared_task(task, prepared_task, task_id, request)
            finished_at = self.get_clock().now().to_msg()
            result = self._make_result(
                request=request,
                task_id=task_id,
                task_status=outcome.task_status,
                error_code=outcome.error_code,
                error_message=outcome.error_message,
                task_result_json=outcome.task_result_json,
                created_at=created_at,
                started_at=started_at,
                finished_at=finished_at,
            )
            if outcome.task_status == TaskStatusV1.DONE:
                goal_handle.succeed()
            else:
                goal_handle.abort()
            return result
        except (ActionTaskServerUnavailable, ServiceTaskServerUnavailable) as exc:
            finished_at = self.get_clock().now().to_msg()
            result = self._make_result(
                request=request,
                task_id=task_id,
                task_status=TaskStatusV1.ERROR,
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
                task_status=TaskStatusV1.ERROR,
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
                task_status=TaskStatusV1.ERROR,
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
            task_status = TaskStatusV1.DONE if task.cancel_reported_as_success else TaskStatusV1.CANCELED
            result = self._make_result(
                request=request,
                task_id=task_id,
                task_status=task_status,
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
                task_status=TaskStatusV1.ERROR,
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
            self._publish_active_tasks()
            if "result" in locals():
                self._publish_terminal_result_and_event(result, self._terminal_event_type(result.task_status))

    def _prepare_task(self, task: TaskDefinition, task_data_json: str, task_id: str) -> Any:
        if task.task_server_type == "system/cancel_task":
            return self._control_task.parse_cancel(task_data_json)
        if task.task_server_type == "system/mission":
            return self._mission_task.parse(task_data_json, default_mission_id=task_id)
        if task.task_server_type == "system/stop":
            return self._control_task.parse_stop(task_data_json)
        if task.task_server_type == "system/wait":
            return self._wait_task.parse(task_data_json)
        if task.task_server_type == "action":
            return self._action_task_client.prepare(task, task_data_json)
        if task.task_server_type == "service":
            return self._service_task_client.prepare(task, task_data_json)
        raise NotImplementedError

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
            return _TaskExecutionOutcome(
                task_status=TaskStatusV1.DONE,
                task_result_json=self._wait_task.execute(prepared_task).task_result_json,
            )
        if task.task_server_type == "action":
            return _TaskExecutionOutcome(
                task_status=TaskStatusV1.DONE,
                task_result_json=self._action_task_client.execute(prepared_task, task_id).task_result_json,
            )
        if task.task_server_type == "service":
            return _TaskExecutionOutcome(
                task_status=TaskStatusV1.DONE,
                task_result_json=self._service_task_client.execute(prepared_task).task_result_json,
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
            task_status=TaskStatusV1.DONE if response.success else TaskStatusV1.ERROR,
            error_code=response.error_code,
            error_message=response.error_message,
            task_result_json=self._control_task.cancel_result_json(
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
            task_status=TaskStatusV1.DONE if response.success else TaskStatusV1.ERROR,
            error_code=response.error_code,
            error_message=response.error_message,
            task_result_json=self._control_task.stop_result_json(
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
            current_status=TaskStatusV1.IN_PROGRESS,
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
        )

        for index, subtask in enumerate(mission.subtasks, start=1):
            self._publish_mission_subtask_event(
                event_type="mission.subtask.started",
                mission_id=mission.mission_id,
                mission_task_id=mission_task_id,
                subtask=subtask,
                source=request.source,
                correlation_id=request.correlation_id,
                current_status=TaskStatusV1.IN_PROGRESS,
                data={
                    "subtask_index": index,
                    "total_subtasks": total_subtasks,
                    "max_attempts": subtask.max_attempts,
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
            )

            self._publish_mission_feedback(
                mission_id=mission.mission_id,
                mission_task_id=mission_task_id,
                active_subtask_id=subtask.subtask_id,
                completed_subtasks=index,
                total_subtasks=total_subtasks,
                source=request.source,
                correlation_id=request.correlation_id,
            )

            if subtask_result.task_status == TaskStatusV1.DONE:
                continue
            if subtask_result.skipped:
                continue

            mission_status = mission_status_from_subtask_status(subtask_result.task_status)
            terminal_event_type = "mission.canceled" if mission_status == TaskStatusV1.CANCELED else "mission.failed"
            self._publish_mission_lifecycle_event(
                event_type=terminal_event_type,
                mission_id=mission.mission_id,
                mission_task_id=mission_task_id,
                source=request.source,
                correlation_id=request.correlation_id,
                current_status=mission_status,
                previous_status=TaskStatusV1.IN_PROGRESS,
                error_code=subtask_result.error_code,
                error_message=subtask_result.error_message,
                data={
                    "completed_subtasks": len(mission_results),
                    "total_subtasks": total_subtasks,
                    "failed_subtask_id": subtask_result.subtask_id,
                    "failed_task_id": subtask_result.task_id,
                },
            )
            return _TaskExecutionOutcome(
                task_status=mission_status,
                error_code=subtask_result.error_code,
                error_message=subtask_result.error_message,
                task_result_json=self._mission_task.result_json(
                    mission_id=mission.mission_id,
                    task_status=mission_status,
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
            current_status=TaskStatusV1.DONE,
            previous_status=TaskStatusV1.IN_PROGRESS,
            data={
                "completed_subtasks": len(mission_results),
                "total_subtasks": total_subtasks,
            },
        )
        return _TaskExecutionOutcome(
            task_status=TaskStatusV1.DONE,
            task_result_json=self._mission_task.result_json(
                mission_id=mission.mission_id,
                task_status=TaskStatusV1.DONE,
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

            child_goal_handle = _ChildGoalHandle(subtask_request)
            last_result = self._execute_task(
                child_goal_handle,
                ignored_active_task_ids={mission_task_id},
            )

            if last_result.task_status == TaskStatusV1.DONE:
                return MissionSubtaskResult(
                    subtask_id=subtask.subtask_id,
                    task_id=subtask_request.task_id,
                    task_name=subtask.task_name,
                    task_status=TaskStatusV1.DONE,
                    skipped=False,
                    attempts=attempt,
                )

        assert last_result is not None
        if subtask.allow_skipping:
            return MissionSubtaskResult(
                subtask_id=subtask.subtask_id,
                task_id=last_result.task_id,
                task_name=subtask.task_name,
                task_status=TaskStatusV1.SKIPPED,
                skipped=True,
                attempts=subtask.max_attempts,
                error_code=last_result.error_code,
                error_message=last_result.error_message,
            )

        return MissionSubtaskResult(
            subtask_id=subtask.subtask_id,
            task_id=last_result.task_id,
            task_name=subtask.task_name,
            task_status=last_result.task_status,
            skipped=False,
            attempts=subtask.max_attempts,
            error_code=last_result.error_code,
            error_message=last_result.error_message,
        )

    def _subtask_attempt_task_id(self, subtask: MissionSubtask, attempt: int) -> str:
        if attempt == 1:
            return subtask.task_id
        return f"{subtask.task_id}/attempt-{attempt}"

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
            and (not request.current_status or event.current_status == request.current_status)
            and (not request.source or event.source == request.source)
            and (not request.correlation_id or event.correlation_id == request.correlation_id)
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
            and (not request.task_status or result.task_status == request.task_status)
            and (not request.source or result.source == request.source)
            and (not request.correlation_id or result.correlation_id == request.correlation_id)
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
        self._active_tasks_pub.publish(msg)

    def _publish_active_tasks(self) -> None:
        msg = ActiveTaskArrayV1()
        msg.stamp = self.get_clock().now().to_msg()
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
        result.correlation_id = request.correlation_id
        result.task_status = TaskStatusV1.RECEIVED
        result.task_result_json = "{}"
        result.created_at = created_at

        record = TaskRecordV1()
        record.result = result
        record.active = False
        record.task_data_json = request.task_data_json
        record.tags = list(request.tags)
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
        self._set_task_record(result.task_id, record)

    def _set_task_record(self, task_id: str, record: TaskRecordV1) -> None:
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
        result.correlation_id = task.correlation_id
        result.task_status = task.task_status
        result.task_result_json = "{}"
        result.created_at = task.created_at
        result.started_at = task.started_at

        record = TaskRecordV1()
        record.result = result
        record.active = True
        record.task_data_json = task_data_json
        record.tags = list(task.tags)
        return record

    def _copy_task_record(self, record: TaskRecordV1) -> TaskRecordV1:
        copied_record = TaskRecordV1()
        copied_record.result = self._copy_task_result(record.result)
        copied_record.active = record.active
        copied_record.task_data_json = record.task_data_json
        copied_record.tags = list(record.tags)
        return copied_record

    def _copy_task_result(self, result: TaskResultV1) -> TaskResultV1:
        copied_result = TaskResultV1()
        copied_result.api_version = result.api_version
        copied_result.task_id = result.task_id
        copied_result.task_name = result.task_name
        copied_result.source = result.source
        copied_result.correlation_id = result.correlation_id
        copied_result.task_status = result.task_status
        copied_result.error_code = result.error_code
        copied_result.error_message = result.error_message
        copied_result.task_result_json = result.task_result_json
        copied_result.created_at = result.created_at
        copied_result.started_at = result.started_at
        copied_result.finished_at = result.finished_at
        return copied_result

    def _execute_result_to_task_result_msg(self, result: ExecuteTaskV1.Result) -> TaskResultV1:
        msg = TaskResultV1()
        msg.api_version = result.api_version
        msg.task_id = result.task_id
        msg.task_name = result.task_name
        msg.source = result.source
        msg.correlation_id = result.correlation_id
        msg.task_status = result.task_status
        msg.error_code = result.error_code
        msg.error_message = result.error_message
        msg.task_result_json = result.task_result_json
        msg.created_at = result.created_at
        msg.started_at = result.started_at
        msg.finished_at = result.finished_at
        return msg

    def _publish_mission_lifecycle_event(
        self,
        event_type: str,
        mission_id: str,
        mission_task_id: str,
        source: str,
        correlation_id: str,
        current_status: str,
        previous_status: str = "",
        error_code: str = "",
        error_message: str = "",
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
            current_status=current_status,
            error_code=error_code,
            error_message=error_message,
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
        current_status: str,
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
            current_status=current_status,
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
    ) -> None:
        event_type = "mission.subtask.completed"
        if subtask_result.skipped:
            event_type = "mission.subtask.skipped"
        elif subtask_result.task_status != TaskStatusV1.DONE:
            event_type = "mission.subtask.failed"

        self._publish_event(
            event_type=event_type,
            task_id=subtask_result.task_id,
            task_name=subtask_result.task_name,
            source=source,
            correlation_id=correlation_id,
            previous_status=TaskStatusV1.IN_PROGRESS,
            current_status=subtask_result.task_status,
            error_code=subtask_result.error_code,
            error_message=subtask_result.error_message,
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
        current_status = TaskStatusV1.IN_PROGRESS
        if success is True:
            current_status = TaskStatusV1.DONE
        elif success is False:
            current_status = TaskStatusV1.ERROR

        self._publish_event(
            event_type=event_type,
            task_id=task_id,
            task_name=task_name,
            source=source,
            correlation_id=correlation_id,
            previous_status=TaskStatusV1.IN_PROGRESS if success is not None else "",
            current_status=current_status,
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
            current_status=TaskStatusV1.DONE if success else TaskStatusV1.ERROR,
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
    ) -> None:
        msg = TaskFeedbackV1()
        msg.api_version = self.api_version
        msg.task_id = mission_task_id
        msg.task_name = "system/mission"
        msg.source = source
        msg.correlation_id = correlation_id
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
    ) -> None:
        msg = TaskFeedbackV1()
        msg.api_version = self.api_version
        msg.task_id = task_id
        msg.task_name = task_name
        msg.source = source
        msg.correlation_id = correlation_id
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
        current_status: str,
        previous_status: str = "",
        error_code: str = "",
        error_message: str = "",
        data: dict[str, Any] | None = None,
    ) -> None:
        msg = TaskEventV1()
        msg.api_version = self.api_version
        msg.event_id = str(uuid.uuid4())
        msg.event_type = event_type
        msg.task_id = task_id
        msg.task_name = task_name
        msg.source = source
        msg.correlation_id = correlation_id
        msg.previous_status = previous_status
        msg.current_status = current_status
        msg.error_code = error_code
        msg.error_message = error_message
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
        copied_event.correlation_id = event.correlation_id
        copied_event.previous_status = event.previous_status
        copied_event.current_status = event.current_status
        copied_event.error_code = event.error_code
        copied_event.error_message = event.error_message
        copied_event.data_json = event.data_json
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
                "task_status": result.task_status,
                "event_type": event_type,
                "error_code": result.error_code,
                "duration_sec": terminal_data["duration_sec"],
            },
        )
        self._publish_event(
            event_type=event_type,
            task_id=result.task_id,
            task_name=result.task_name,
            source=result.source,
            correlation_id=result.correlation_id,
            previous_status="" if result.task_status == TaskStatusV1.REJECTED else TaskStatusV1.IN_PROGRESS,
            current_status=result.task_status,
            error_code=result.error_code,
            error_message=result.error_message,
            data=terminal_data,
        )

    def _terminal_event_type(self, task_status: str) -> str:
        if task_status == TaskStatusV1.DONE:
            return "task.completed"
        if task_status == TaskStatusV1.CANCELED:
            return "task.canceled"
        if task_status == TaskStatusV1.REJECTED:
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
        }

    def _terminal_observability_data(self, result: ExecuteTaskV1.Result) -> dict[str, Any]:
        return {
            "task_status": result.task_status,
            "error_code": result.error_code,
            "has_error": bool(result.error_code or result.error_message),
            "has_result_json": bool(result.task_result_json and result.task_result_json != "{}"),
            "result_size": len(result.task_result_json or ""),
            "duration_sec": self._duration_sec(result.started_at, result.finished_at),
            "total_duration_sec": self._duration_sec(result.created_at, result.finished_at),
        }

    def _duration_sec(self, start_time: Any, finish_time: Any) -> float:
        start_sec = getattr(start_time, "sec", 0)
        start_nanosec = getattr(start_time, "nanosec", 0)
        finish_sec = getattr(finish_time, "sec", 0)
        finish_nanosec = getattr(finish_time, "nanosec", 0)
        duration = (finish_sec - start_sec) + ((finish_nanosec - start_nanosec) / 1_000_000_000.0)
        return max(0.0, duration)

    def _json_dumps(self, payload: dict[str, Any]) -> str:
        return json.dumps(payload, sort_keys=True)

    def _log_task_event(self, event: TaskEventV1, data: dict[str, Any]) -> None:
        message = self._json_dumps(self._structured_log_payload(event, data))
        if event.current_status == TaskStatusV1.ERROR:
            self.get_logger().error(message)
        elif event.current_status == TaskStatusV1.REJECTED:
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
            "previous_status": event.previous_status,
            "current_status": event.current_status,
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
        task_status: str,
        created_at: Any,
        started_at: Any,
        finished_at: Any,
        error_code: str = "",
        error_message: str = "",
        task_result_json: str = "{}",
    ) -> ExecuteTaskV1.Result:
        result = ExecuteTaskV1.Result()
        result.api_version = self.api_version
        result.task_id = task_id
        result.task_name = request.task_name
        result.source = request.source
        result.correlation_id = request.correlation_id
        result.task_status = task_status
        result.error_code = error_code
        result.error_message = error_message
        result.task_result_json = task_result_json
        result.created_at = created_at
        result.started_at = started_at
        result.finished_at = finished_at
        return result

    def _task_definition_to_msg(self, task: TaskDefinition) -> TaskSpecV1:
        msg = TaskSpecV1()
        msg.api_version = self.api_version
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
        return msg

    def _active_task_to_msg(self, task: ActiveTaskEntry) -> ActiveTaskV1:
        msg = ActiveTaskV1()
        msg.api_version = task.api_version
        msg.task_id = task.task_id
        msg.task_name = task.task_name
        msg.source = task.source
        msg.correlation_id = task.correlation_id
        msg.priority = task.priority
        msg.task_status = task.task_status
        msg.created_at = task.created_at
        msg.started_at = task.started_at
        msg.tags = list(task.tags)
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
