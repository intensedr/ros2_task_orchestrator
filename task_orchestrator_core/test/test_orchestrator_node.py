import json
import os
import time

from builtin_interfaces.msg import Time
import rclpy
from rclpy.action import CancelResponse
from rclpy.parameter import Parameter

from task_orchestrator_core.active_tasks import ActiveTaskEntry
from task_orchestrator_core.clients.action_task import ActionTaskCanceled, ActionTaskResult, ActionTaskServerUnavailable
from task_orchestrator_core.clients.service_task import ServiceTaskResult, ServiceTaskServerUnavailable
from task_orchestrator_core.orchestrator_node import TaskOrchestratorNode
from task_orchestrator_core.storage import SQLiteTaskStorage
from task_orchestrator_core.system_tasks.wait import WaitTaskExecutor
from task_orchestrator_core.task_models import TaskControlHook, TaskDefinition
from task_orchestrator_msgs.action import ExecuteTaskV1
from task_orchestrator_msgs.msg import ErrorCodeV1, TaskEventV1, TaskRecordV1, TaskResultV1, TaskStatusV1
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


class FakeGoalHandle:
    def __init__(self, request):
        self.request = request
        self.state = ""

    def abort(self):
        self.state = "aborted"

    def succeed(self):
        self.state = "succeeded"


class FakeServiceTaskClient:
    def __init__(self, result=None, error=None):
        self.result = result or ServiceTaskResult(result_json='{"message": "ok", "success": true}')
        self.error = error
        self.prepared = "prepared-service-task"

    def prepare(self, task, task_data_json):
        self.task = task
        self.task_data_json = task_data_json
        return self.prepared

    def execute(self, prepared):
        assert prepared == self.prepared
        if self.error is not None:
            raise self.error
        return self.result


class FakeActionTaskClient:
    def __init__(self, result=None, error=None):
        self.result = result or ActionTaskResult(result_json='{"sequence": [1, 1, 2]}')
        self.error = error
        self.prepared = "prepared-action-task"

    def prepare(self, task, task_data_json):
        self.task = task
        self.task_data_json = task_data_json
        return self.prepared

    def execute(self, prepared, task_id):
        assert prepared == self.prepared
        self.task_id = task_id
        if self.error is not None:
            raise self.error
        return self.result


class RecordingPublisher:
    def __init__(self):
        self.messages = []

    def publish(self, msg):
        self.messages.append(msg)


TERMINAL_EVENT_TYPES = {"task.completed", "task.failed", "task.rejected", "task.canceled"}


def _time(sec):
    value = Time()
    value.sec = sec
    return value


def _stored_record(task_id, status, task_data_json='{"duration_sec": 0}'):
    result = TaskResultV1()
    result.api_version = "v1beta1"
    result.task_id = task_id
    result.task_name = "system/wait"
    result.source = "test"
    result.correlation_id = f"{task_id}-corr"
    result.status = status
    result.result_json = "{}"
    result.created_at = _time(1)
    result.started_at = _time(2)
    result.finished_at = _time(3)

    record = TaskRecordV1()
    record.result = result
    record.task_data_json = task_data_json
    record.tags = ["stored"]
    return record


def _make_node(tmp_path, parameter_overrides=None):
    rclpy.try_shutdown()
    ros_log_dir = tmp_path / "ros_log"
    ros_log_dir.mkdir()
    os.environ["ROS_LOG_DIR"] = str(ros_log_dir)
    rclpy.init(args=None)
    return TaskOrchestratorNode(parameter_overrides=parameter_overrides)


def _destroy_node(node):
    node.destroy_node()
    rclpy.try_shutdown()


def test_list_tasks_returns_system_tasks_when_requested(tmp_path):
    node = _make_node(tmp_path)
    try:
        request = ListTasksV1.Request()
        response = ListTasksV1.Response()
        request.include_system_tasks = True

        response = node._list_tasks(request, response)

        assert [task.task_name for task in response.tasks] == [
            "system/cancel_task",
            "system/mission",
            "system/stop",
            "system/wait",
        ]
        assert response.tasks[0].is_system_task is True
    finally:
        _destroy_node(node)


def test_sqlite_storage_is_disabled_by_default(tmp_path):
    node = _make_node(tmp_path)
    try:
        assert node._storage is None
    finally:
        _destroy_node(node)


def test_get_task_returns_finished_task_record(tmp_path):
    node = _make_node(tmp_path)
    sleeps = []
    node._wait_task = WaitTaskExecutor(sleep=sleeps.append)
    try:
        request = ExecuteTaskV1.Goal()
        request.task_id = "wait-task"
        request.task_name = "system/wait"
        request.task_data_json = '{"duration_sec": 0}'
        request.tags = ["test"]
        goal_handle = FakeGoalHandle(request)

        node._execute_task_cb(goal_handle)

        get_request = GetTaskV1.Request()
        get_response = GetTaskV1.Response()
        get_request.task_id = "wait-task"

        get_response = node._get_task(get_request, get_response)

        assert get_response.found is True
        assert get_response.task.active is False
        assert get_response.task.result.task_id == "wait-task"
        assert get_response.task.result.status == TaskStatusV1.DONE
        assert get_response.task.task_data_json == '{"duration_sec": 0}'
        assert get_response.task.tags == ["test"]
    finally:
        _destroy_node(node)


def test_get_task_returns_active_task_record(tmp_path):
    node = _make_node(tmp_path)
    try:
        node._active_tasks.add(
            ActiveTaskEntry(
                api_version="v1beta1",
                task_id="active-task",
                task_name="example/fibonacci",
                source="test",
                correlation_id="corr-1",
                priority=0,
                status=TaskStatusV1.IN_PROGRESS,
                created_at=node.get_clock().now().to_msg(),
                started_at=node.get_clock().now().to_msg(),
                tags=("active",),
                task_server_type="action",
            )
        )
        request = GetTaskV1.Request()
        response = GetTaskV1.Response()
        request.task_id = "active-task"

        response = node._get_task(request, response)

        assert response.found is True
        assert response.task.active is True
        assert response.task.result.status == TaskStatusV1.IN_PROGRESS
        assert response.task.tags == ["active"]
    finally:
        _destroy_node(node)


def test_get_task_reports_missing_task(tmp_path):
    node = _make_node(tmp_path)
    try:
        request = GetTaskV1.Request()
        response = GetTaskV1.Response()
        request.task_id = "missing-task"

        response = node._get_task(request, response)

        assert response.found is False
    finally:
        _destroy_node(node)


def test_task_record_limit_evicts_old_terminal_records(tmp_path):
    node = _make_node(tmp_path)
    node._wait_task = WaitTaskExecutor(sleep=lambda _: None)
    node._task_record_limit = 2
    try:
        for index in range(3):
            request = ExecuteTaskV1.Goal()
            request.task_id = f"wait-task-{index}"
            request.task_name = "system/wait"
            request.task_data_json = '{"duration_sec": 0}'
            node._execute_task_cb(FakeGoalHandle(request))

        missing_request = GetTaskV1.Request()
        missing_response = GetTaskV1.Response()
        missing_request.task_id = "wait-task-0"
        present_request = GetTaskV1.Request()
        present_response = GetTaskV1.Response()
        present_request.task_id = "wait-task-2"

        missing_response = node._get_task(missing_request, missing_response)
        present_response = node._get_task(present_request, present_response)

        assert list(node._task_records) == ["wait-task-1", "wait-task-2"]
        assert missing_response.found is False
        assert present_response.found is True
        assert present_response.task.result.status == TaskStatusV1.DONE
    finally:
        _destroy_node(node)


def test_task_record_limit_preserves_active_records(tmp_path):
    node = _make_node(tmp_path)
    node._task_record_limit = 0
    try:
        active_task = ActiveTaskEntry(
            api_version="v1beta1",
            task_id="active-task",
            task_name="example/fibonacci",
            source="test",
            correlation_id="corr-1",
            priority=0,
            status=TaskStatusV1.IN_PROGRESS,
            created_at=node.get_clock().now().to_msg(),
            started_at=node.get_clock().now().to_msg(),
            tags=("active",),
            task_server_type="action",
        )

        node._store_active_task_record(active_task, task_data_json="{}")

        request = GetTaskV1.Request()
        response = GetTaskV1.Response()
        request.task_id = "active-task"
        response = node._get_task(request, response)

        assert list(node._task_records) == ["active-task"]
        assert response.found is True
        assert response.task.active is True
    finally:
        _destroy_node(node)


def test_event_record_limit_evicts_old_events(tmp_path):
    node = _make_node(tmp_path)
    node._event_record_limit = 2
    try:
        for index in range(3):
            node._publish_event(
                event_type=f"task.event-{index}",
                task_id=f"task-{index}",
                task_name="system/wait",
                source="test",
                correlation_id="corr-1",
                status=TaskStatusV1.IN_PROGRESS,
            )

        list_request = ListEventsV1.Request()
        list_response = ListEventsV1.Response()

        list_response = node._list_events(list_request, list_response)

        assert [event.event_type for event in list_response.events] == ["task.event-2", "task.event-1"]
        assert [event.event_type for event in node._event_records.values()] == ["task.event-1", "task.event-2"]
    finally:
        _destroy_node(node)


def test_event_hooks_receive_events_without_breaking_publication(tmp_path):
    class RecordingHook:
        def __init__(self):
            self.events = []

        def handle_event(self, event, data):
            self.events.append((event.event_type, data))

    class FailingHook:
        def handle_event(self, event, data):
            raise RuntimeError("hook failed")

    node = _make_node(tmp_path)
    events_pub = RecordingPublisher()
    node._events_pub = events_pub
    hook = RecordingHook()
    node.add_event_hook(hook)
    node.add_event_hook(FailingHook())
    try:
        node._publish_event(
            event_type="task.started",
            task_id="task-1",
            task_name="system/wait",
            source="test",
            correlation_id="corr-1",
            status=TaskStatusV1.IN_PROGRESS,
            data={"example": True},
        )

        assert [event.event_type for event in events_pub.messages] == ["task.started"]
        assert hook.events == [("task.started", {"example": True})]
    finally:
        _destroy_node(node)


def test_list_events_filters_and_limits_newest_first(tmp_path):
    node = _make_node(tmp_path)
    try:
        scheduler_context = ExecuteTaskV1.Goal()
        scheduler_context.robot_id = "robot-1"
        scheduler_context.fleet_id = "fleet-1"
        scheduler_context.trace_id = "trace-1"
        scheduler_context.idempotency_key = "idem-1"
        node._publish_event(
            event_type="task.started",
            task_id="task-1",
            task_name="system/wait",
            source="scheduler",
            correlation_id="corr-1",
            status=TaskStatusV1.IN_PROGRESS,
            context=scheduler_context,
        )
        node._publish_event(
            event_type="task.completed",
            task_id="task-1",
            task_name="system/wait",
            source="scheduler",
            correlation_id="corr-1",
            status=TaskStatusV1.DONE,
            context=scheduler_context,
        )
        operator_context = ExecuteTaskV1.Goal()
        operator_context.robot_id = "robot-2"
        operator_context.fleet_id = "fleet-2"
        operator_context.trace_id = "trace-2"
        operator_context.idempotency_key = "idem-2"
        node._publish_event(
            event_type="task.rejected",
            task_id="task-2",
            task_name="missing/task",
            source="operator",
            correlation_id="corr-2",
            status=TaskStatusV1.REJECTED,
            error_code=ErrorCodeV1.UNKNOWN_TASK,
            context=operator_context,
        )

        list_request = ListEventsV1.Request()
        list_response = ListEventsV1.Response()
        list_request.task_name = "system/wait"
        list_request.source = "scheduler"
        list_request.robot_id = "robot-1"
        list_request.fleet_id = "fleet-1"
        list_request.trace_id = "trace-1"
        list_request.idempotency_key = "idem-1"
        list_request.limit = 1

        list_response = node._list_events(list_request, list_response)

        assert [event.event_type for event in list_response.events] == ["task.completed"]
        assert list_response.events[0].status == TaskStatusV1.DONE
    finally:
        _destroy_node(node)


def test_event_record_limit_zero_disables_event_history(tmp_path):
    node = _make_node(tmp_path)
    node._event_record_limit = 0
    try:
        node._publish_event(
            event_type="task.started",
            task_id="task-1",
            task_name="system/wait",
            source="test",
            correlation_id="corr-1",
            status=TaskStatusV1.IN_PROGRESS,
        )
        list_request = ListEventsV1.Request()
        list_response = ListEventsV1.Response()

        list_response = node._list_events(list_request, list_response)

        assert list_response.events == []
        assert list(node._event_records) == []
    finally:
        _destroy_node(node)


def test_sqlite_storage_persists_task_records_and_events_when_enabled(tmp_path):
    db_path = tmp_path / "task_orchestrator.sqlite3"
    node = _make_node(
        tmp_path,
        parameter_overrides=[
            Parameter("storage.enabled", value=True),
            Parameter("storage.sqlite_path", value=str(db_path)),
            Parameter("storage.retention_days", value=0),
        ],
    )
    node._wait_task = WaitTaskExecutor(sleep=lambda _: None)
    try:
        assert node._storage is not None
        request = ExecuteTaskV1.Goal()
        request.task_id = "stored-task"
        request.task_name = "system/wait"
        request.source = "storage-test"
        request.task_data_json = '{"duration_sec": 0}'

        result = node._execute_task_cb(FakeGoalHandle(request))

        assert result.status == TaskStatusV1.DONE
    finally:
        _destroy_node(node)

    storage = SQLiteTaskStorage(str(db_path), retention_days=0)
    try:
        records_request = ListTaskRecordsV1.Request()
        records_request.task_name = "system/wait"
        records = storage.list_task_records(records_request)
        events_request = ListEventsV1.Request()
        events_request.task_id = "stored-task"
        events = storage.list_events(events_request)

        assert [record.result.task_id for record in records] == ["stored-task"]
        assert records[0].result.status == TaskStatusV1.DONE
        assert records[0].task_data_json == '{"duration_sec": 0}'
        assert [event.event_type for event in events] == [
            "task.completed",
            "task.started",
            "task.received",
        ]
    finally:
        storage.close()


def test_node_recovers_sqlite_queued_task_on_startup(tmp_path):
    db_path = tmp_path / "task_orchestrator.sqlite3"
    storage = SQLiteTaskStorage(str(db_path), retention_days=0)
    try:
        queued_record = _stored_record("persisted-queued", TaskStatusV1.QUEUED)
        queued_record.queue_on_conflict = True
        queued_record.result.idempotency_key = "persisted-queued-key"
        storage.write_task_record(queued_record)
    finally:
        storage.close()

    node = _make_node(
        tmp_path,
        parameter_overrides=[
            Parameter("storage.enabled", value=True),
            Parameter("storage.sqlite_path", value=str(db_path)),
            Parameter("storage.retention_days", value=0),
        ],
    )
    try:
        record = None
        for _ in range(40):
            record = node._stored_task_record("persisted-queued")
            if record is not None and record.result.status == TaskStatusV1.DONE:
                break
            time.sleep(0.05)

        events_request = ListEventsV1.Request()
        events_request.task_id = "persisted-queued"
        events = node._stored_events(events_request)

        assert record is not None
        assert record.result.status == TaskStatusV1.DONE
        assert record.task_data_json == '{"duration_sec": 0}'
        assert "task.recovered" in [event.event_type for event in events]
        assert "task.completed" in [event.event_type for event in events]
    finally:
        _destroy_node(node)


def test_node_uses_sqlite_idempotency_record_after_restart(tmp_path):
    db_path = tmp_path / "task_orchestrator.sqlite3"
    storage = SQLiteTaskStorage(str(db_path), retention_days=0)
    try:
        done_record = _stored_record("already-done", TaskStatusV1.DONE)
        done_record.result.idempotency_key = "idem-after-restart"
        done_record.result.result_json = '{"already": true}'
        storage.write_task_record(done_record)
    finally:
        storage.close()

    node = _make_node(
        tmp_path,
        parameter_overrides=[
            Parameter("storage.enabled", value=True),
            Parameter("storage.sqlite_path", value=str(db_path)),
            Parameter("storage.retention_days", value=0),
        ],
    )
    sleeps = []
    node._wait_task = WaitTaskExecutor(sleep=sleeps.append)
    try:
        request = ExecuteTaskV1.Goal()
        request.task_id = "duplicate-submit"
        request.task_name = "system/wait"
        request.task_data_json = '{"duration_sec": 0}'
        request.idempotency_key = "idem-after-restart"
        goal_handle = FakeGoalHandle(request)

        result = node._execute_task_cb(goal_handle)

        assert goal_handle.state == "succeeded"
        assert result.task_id == "already-done"
        assert result.result_json == '{"already": true}'
        assert sleeps == []

        same_id_request = ExecuteTaskV1.Goal()
        same_id_request.task_id = "already-done"
        same_id_request.task_name = "system/wait"
        same_id_request.task_data_json = '{"duration_sec": 0}'
        same_id_request.idempotency_key = "idem-after-restart"
        same_id_goal_handle = FakeGoalHandle(same_id_request)

        same_id_result = node._execute_task_cb(same_id_goal_handle)

        assert same_id_goal_handle.state == "succeeded"
        assert same_id_result.task_id == "already-done"
        assert same_id_result.result_json == '{"already": true}'
        assert sleeps == []
    finally:
        _destroy_node(node)


def test_structured_log_payload_has_consistent_schema(tmp_path):
    node = _make_node(tmp_path)
    try:
        event = TaskEventV1()
        event.api_version = "v1beta1"
        event.event_id = "event-1"
        event.event_type = "mission.completed"
        event.task_id = "mission-task"
        event.task_name = "system/mission"
        event.source = "test"
        event.correlation_id = "corr-1"
        event.previous_status = TaskStatusV1.IN_PROGRESS
        event.status = TaskStatusV1.DONE

        payload = node._structured_log_payload(
            event,
            {
                "duration_sec": 1.25,
                "total_duration_sec": 2.5,
                "result_size": 42,
                "task_server_type": "system/mission",
            },
        )

        assert payload["event"] == "orchestrator_event"
        assert payload["schema"] == "task_orchestrator.event.v1"
        assert payload["event_category"] == "mission"
        assert payload["event_type"] == "mission.completed"
        assert payload["task_server_type"] == "system/mission"
        assert payload["duration_sec"] == 1.25
        assert payload["total_duration_sec"] == 2.5
        assert payload["result_size"] == 42
        assert payload["task_record_count"] == len(node._task_records)
        assert payload["event_record_count"] == len(node._event_records)
    finally:
        _destroy_node(node)


def test_list_task_records_returns_newest_first_with_limit(tmp_path):
    node = _make_node(tmp_path)
    node._wait_task = WaitTaskExecutor(sleep=lambda _: None)
    try:
        for index in range(3):
            request = ExecuteTaskV1.Goal()
            request.task_id = f"wait-task-{index}"
            request.task_name = "system/wait"
            request.task_data_json = '{"duration_sec": 0}'
            node._execute_task_cb(FakeGoalHandle(request))

        list_request = ListTaskRecordsV1.Request()
        list_response = ListTaskRecordsV1.Response()
        list_request.limit = 2

        list_response = node._list_task_records(list_request, list_response)

        assert [record.result.task_id for record in list_response.records] == ["wait-task-2", "wait-task-1"]
        assert all(record.result.status == TaskStatusV1.DONE for record in list_response.records)
    finally:
        _destroy_node(node)


def test_list_task_records_filters_by_status_and_source(tmp_path):
    node = _make_node(tmp_path)
    node._wait_task = WaitTaskExecutor(sleep=lambda _: None)
    try:
        done_request = ExecuteTaskV1.Goal()
        done_request.task_id = "done-task"
        done_request.task_name = "system/wait"
        done_request.source = "scheduler"
        done_request.task_data_json = '{"duration_sec": 0}'
        node._execute_task_cb(FakeGoalHandle(done_request))

        rejected_request = ExecuteTaskV1.Goal()
        rejected_request.task_id = "rejected-task"
        rejected_request.task_name = "missing/task"
        rejected_request.source = "operator"
        rejected_request.robot_id = "robot-2"
        rejected_request.fleet_id = "fleet-2"
        rejected_request.trace_id = "trace-2"
        rejected_request.idempotency_key = "idem-2"
        node._execute_task_cb(FakeGoalHandle(rejected_request))

        list_request = ListTaskRecordsV1.Request()
        list_response = ListTaskRecordsV1.Response()
        list_request.status = TaskStatusV1.REJECTED
        list_request.source = "operator"
        list_request.robot_id = "robot-2"
        list_request.fleet_id = "fleet-2"
        list_request.trace_id = "trace-2"
        list_request.idempotency_key = "idem-2"

        list_response = node._list_task_records(list_request, list_response)

        assert [record.result.task_id for record in list_response.records] == ["rejected-task"]
        assert list_response.records[0].result.error_code == ErrorCodeV1.UNKNOWN_TASK
    finally:
        _destroy_node(node)


def test_list_task_records_includes_active_records_missing_from_cache(tmp_path):
    node = _make_node(tmp_path)
    try:
        node._active_tasks.add(
            ActiveTaskEntry(
                api_version="v1beta1",
                task_id="active-task",
                task_name="example/fibonacci",
                source="test",
                correlation_id="corr-1",
                priority=0,
                status=TaskStatusV1.IN_PROGRESS,
                created_at=node.get_clock().now().to_msg(),
                started_at=node.get_clock().now().to_msg(),
                tags=("active",),
                task_server_type="action",
            )
        )
        list_request = ListTaskRecordsV1.Request()
        list_response = ListTaskRecordsV1.Response()
        list_request.status = TaskStatusV1.IN_PROGRESS

        list_response = node._list_task_records(list_request, list_response)

        assert [record.result.task_id for record in list_response.records] == ["active-task"]
        assert list_response.records[0].active is True
        assert list_response.records[0].tags == ["active"]
    finally:
        _destroy_node(node)


def test_reload_config_replaces_registry_from_parameter_path(tmp_path):
    node = _make_node(tmp_path)
    events_pub = RecordingPublisher()
    node._events_pub = events_pub
    config_path = tmp_path / "tasks.yaml"
    config_path.write_text(
        """
tasks:
  - task_name: example/set_bool
    topic: /example/set_bool
    msg_interface: std_srvs/srv/SetBool
    task_server_type: service
""",
        encoding="utf-8",
    )
    try:
        node.set_parameters([Parameter("tasks_config_path", value=str(config_path))])
        request = ReloadConfigV1.Request()
        response = ReloadConfigV1.Response()

        response = node._reload_config(request, response)

        assert response.success is True
        assert response.error_code == ""
        assert node._task_registry.get("example/set_bool") is not None
        assert events_pub.messages[-1].event_type == "system.config.reloaded"
        event_data = json.loads(events_pub.messages[-1].data_json)
        assert event_data["success"] is True
        assert event_data["task_count"] >= 1
    finally:
        _destroy_node(node)


def test_reload_config_reports_errors_without_replacing_registry(tmp_path):
    node = _make_node(tmp_path)
    events_pub = RecordingPublisher()
    node._events_pub = events_pub
    bad_config_path = tmp_path / "bad_tasks.yaml"
    bad_config_path.write_text(
        """
tasks:
  - task_name: example/old
    task_server_type: service
  - task_name: example/old
    task_server_type: action
""",
        encoding="utf-8",
    )
    node._task_registry.add(TaskDefinition(task_name="example/existing", task_server_type="service"))
    try:
        node.set_parameters([Parameter("tasks_config_path", value=str(bad_config_path))])
        request = ReloadConfigV1.Request()
        response = ReloadConfigV1.Response()

        response = node._reload_config(request, response)

        assert response.success is False
        assert response.error_code == ErrorCodeV1.TASK_DATA_PARSING_FAILED
        assert "duplicate task_name" in response.error_message
        assert node._task_registry.get("example/existing") is not None
        assert node._task_registry.get("example/old") is None
        assert events_pub.messages[-1].event_type == "system.config.reload_failed"
        event_data = json.loads(events_pub.messages[-1].data_json)
        assert event_data["success"] is False
    finally:
        _destroy_node(node)


def test_validate_task_accepts_system_wait_payload_and_returns_schema(tmp_path):
    node = _make_node(tmp_path)
    try:
        request = ValidateTaskV1.Request()
        response = ValidateTaskV1.Response()
        request.task_name = "system/wait"
        request.task_data_json = '{"duration_sec": 0}'
        request.include_schema = True

        response = node._validate_task(request, response)
        schema = json.loads(response.schema_json)

        assert response.valid is True
        assert response.error_code == ""
        assert response.normalized_task_data_json == '{"duration_sec": 0}'
        assert schema["title"] == "system/wait"
        assert schema["properties"]["duration_sec"]["minimum"] == 0
    finally:
        _destroy_node(node)


def test_validate_task_rejects_invalid_payload(tmp_path):
    node = _make_node(tmp_path)
    try:
        request = ValidateTaskV1.Request()
        response = ValidateTaskV1.Response()
        request.task_name = "system/wait"
        request.task_data_json = '{"duration_sec": -1}'

        response = node._validate_task(request, response)

        assert response.valid is False
        assert response.error_code == ErrorCodeV1.TASK_DATA_PARSING_FAILED
        assert "duration_sec" in response.error_message
    finally:
        _destroy_node(node)


def test_validate_task_rejects_unknown_task(tmp_path):
    node = _make_node(tmp_path)
    try:
        request = ValidateTaskV1.Request()
        response = ValidateTaskV1.Response()
        request.task_name = "missing/task"
        request.task_data_json = "{}"

        response = node._validate_task(request, response)

        assert response.valid is False
        assert response.error_code == ErrorCodeV1.UNKNOWN_TASK
    finally:
        _destroy_node(node)


def test_validate_task_resolves_mission_template(tmp_path):
    templates_dir = tmp_path / "mission_templates"
    templates_dir.mkdir()
    (templates_dir / "wait.yaml").write_text(
        """
parameters:
  mission_id: template-mission
  duration_sec: 0
mission_id: "${mission_id}"
subtasks:
  - subtask_id: wait-1
    task_name: system/wait
    task_data_json:
      duration_sec: "${duration_sec}"
""",
        encoding="utf-8",
    )
    node = _make_node(tmp_path)
    node.set_parameters([Parameter("mission_templates_path", value=str(templates_dir))])
    try:
        request = ValidateTaskV1.Request()
        response = ValidateTaskV1.Response()
        request.task_id = "mission-validation"
        request.task_name = "system/mission"
        request.task_data_json = json.dumps(
            {
                "template_id": "wait",
                "params": {
                    "mission_id": "mission-from-template",
                    "duration_sec": 0,
                },
            }
        )

        response = node._validate_task(request, response)
        normalized = json.loads(response.normalized_task_data_json)

        assert response.valid is True
        assert normalized["mission_id"] == "mission-from-template"
        assert normalized["subtasks"][0]["task_name"] == "system/wait"
    finally:
        _destroy_node(node)


def test_execute_unknown_task_is_rejected(tmp_path):
    node = _make_node(tmp_path)
    events_pub = RecordingPublisher()
    feedback_pub = RecordingPublisher()
    node._events_pub = events_pub
    node._feedback_pub = feedback_pub
    try:
        request = ExecuteTaskV1.Goal()
        request.task_name = "missing/task"
        goal_handle = FakeGoalHandle(request)

        result = node._execute_task_cb(goal_handle)
        result_payload = json.loads(result.result_json)
        rejected_event_data = json.loads(events_pub.messages[-1].data_json)
        terminal_feedback = json.loads(feedback_pub.messages[-1].feedback_json)

        assert goal_handle.state == "aborted"
        assert result.status == TaskStatusV1.REJECTED
        assert result.error_code == ErrorCodeV1.UNKNOWN_TASK
        assert result_payload["error"]["code"] == ErrorCodeV1.UNKNOWN_TASK
        assert "Unknown task" in result_payload["error"]["message"]
        assert result.task_id
        assert rejected_event_data["status"] == TaskStatusV1.REJECTED
        assert rejected_event_data["has_error"] is True
        assert terminal_feedback["status"] == TaskStatusV1.REJECTED
        assert terminal_feedback["error_code"] == ErrorCodeV1.UNKNOWN_TASK
    finally:
        _destroy_node(node)


def test_execute_system_wait_succeeds_and_clears_active_task(tmp_path):
    node = _make_node(tmp_path)
    sleeps = []
    events_pub = RecordingPublisher()
    feedback_pub = RecordingPublisher()
    node._wait_task = WaitTaskExecutor(sleep=sleeps.append)
    node._events_pub = events_pub
    node._feedback_pub = feedback_pub
    try:
        request = ExecuteTaskV1.Goal()
        request.task_name = "system/wait"
        request.task_data_json = '{"duration_sec": 2.0}'
        request.tags = ["observable"]
        goal_handle = FakeGoalHandle(request)

        result = node._execute_task_cb(goal_handle)

        assert goal_handle.state == "succeeded"
        assert result.status == TaskStatusV1.DONE
        assert result.result_json == '{"duration_sec": 2.0}'
        assert sleeps == [2.0]
        assert len(node._active_tasks) == 0

        assert [event.event_type for event in events_pub.messages] == [
            "task.received",
            "task.started",
            "task.completed",
        ]
        received_data = json.loads(events_pub.messages[0].data_json)
        started_data = json.loads(events_pub.messages[1].data_json)
        completed_data = json.loads(events_pub.messages[2].data_json)
        assert received_data["task_id_generated"] is True
        assert received_data["tags"] == ["observable"]
        assert started_data["task_server_type"] == "system/wait"
        assert started_data["reentrant"] is True
        assert completed_data["status"] == TaskStatusV1.DONE
        assert completed_data["has_result_json"] is True
        assert completed_data["duration_sec"] >= 0.0

        assert [feedback.progress for feedback in feedback_pub.messages] == [0.0, 1.0]
        assert json.loads(feedback_pub.messages[0].feedback_json)["status"] == TaskStatusV1.IN_PROGRESS
        assert json.loads(feedback_pub.messages[1].feedback_json)["status"] == TaskStatusV1.DONE
    finally:
        _destroy_node(node)


def test_execute_wait_respects_request_timeout(tmp_path):
    node = _make_node(tmp_path)
    sleeps = []
    node._wait_task = WaitTaskExecutor(sleep=sleeps.append)
    try:
        request = ExecuteTaskV1.Goal()
        request.task_id = "timeout-wait"
        request.task_name = "system/wait"
        request.task_data_json = '{"duration_sec": 2.0}'
        request.timeout_sec = 1.0

        result = node._execute_task_cb(FakeGoalHandle(request))

        assert result.status == TaskStatusV1.ERROR
        assert result.error_code == ErrorCodeV1.TASK_TIMEOUT
        assert result.duration_sec >= 0.0
        assert sleeps == []
    finally:
        _destroy_node(node)


def test_execute_task_copies_fleet_metadata_to_result_record_and_event(tmp_path):
    node = _make_node(tmp_path)
    events_pub = RecordingPublisher()
    node._events_pub = events_pub
    node._wait_task = WaitTaskExecutor(sleep=lambda _: None)
    try:
        request = ExecuteTaskV1.Goal()
        request.task_id = "metadata-task"
        request.task_name = "system/wait"
        request.task_data_json = '{"duration_sec": 0}'
        request.metadata_json = '{"work_order": "wo-1"}'
        request.robot_id = "robot-1"
        request.fleet_id = "fleet-1"
        request.site_id = "site-1"
        request.zone_id = "zone-1"
        request.operator_id = "operator-1"
        request.trace_id = "trace-1"

        result = node._execute_task_cb(FakeGoalHandle(request))

        assert result.status == TaskStatusV1.DONE
        assert result.robot_id == "robot-1"
        assert result.trace_id == "trace-1"
        assert result.metadata_json == '{"work_order": "wo-1"}'
        assert node._task_records["metadata-task"].robot_id == "robot-1"
        assert events_pub.messages[-1].robot_id == "robot-1"
        assert events_pub.messages[-1].trace_id == "trace-1"
    finally:
        _destroy_node(node)


def test_queue_on_conflict_request_publishes_queued_and_dequeued_events(tmp_path):
    node = _make_node(tmp_path)
    events_pub = RecordingPublisher()
    node._events_pub = events_pub
    node._wait_task = WaitTaskExecutor(sleep=lambda _: None)
    try:
        request = ExecuteTaskV1.Goal()
        request.task_id = "queued-task"
        request.task_name = "system/wait"
        request.task_data_json = '{"duration_sec": 0}'
        request.queue_on_conflict = True
        request.priority = 5

        result = node._execute_task_cb(FakeGoalHandle(request))

        assert result.status == TaskStatusV1.DONE
        assert [event.event_type for event in events_pub.messages] == [
            "task.received",
            "task.queued",
            "task.dequeued",
            "task.started",
            "task.completed",
        ]
        queued_data = json.loads(events_pub.messages[1].data_json)
        assert queued_data["queue_position"] == 1
        assert events_pub.messages[3].previous_status == TaskStatusV1.QUEUED
    finally:
        _destroy_node(node)


def test_done_terminal_result_and_event_are_published_once(tmp_path):
    node = _make_node(tmp_path)
    events_pub = RecordingPublisher()
    results_pub = RecordingPublisher()
    node._events_pub = events_pub
    node._results_pub = results_pub
    node._wait_task = WaitTaskExecutor(sleep=lambda _: None)
    try:
        request = ExecuteTaskV1.Goal()
        request.task_id = "done-task"
        request.task_name = "system/wait"
        request.task_data_json = '{"duration_sec": 0}'

        result = node._execute_task_cb(FakeGoalHandle(request))

        terminal_events = [event for event in events_pub.messages if event.event_type in TERMINAL_EVENT_TYPES]
        assert result.status == TaskStatusV1.DONE
        assert [msg.task_id for msg in results_pub.messages] == ["done-task"]
        assert [event.event_type for event in terminal_events] == ["task.completed"]
    finally:
        _destroy_node(node)


def test_error_terminal_result_and_event_are_published_once(tmp_path):
    node = _make_node(tmp_path)
    events_pub = RecordingPublisher()
    results_pub = RecordingPublisher()
    node._events_pub = events_pub
    node._results_pub = results_pub
    node._service_task_client = FakeServiceTaskClient(
        error=ServiceTaskServerUnavailable("service is not available: /example/set_bool")
    )
    node._task_registry.add(
        TaskDefinition(
            task_name="example/set_bool",
            topic="/example/set_bool",
            msg_interface="std_srvs/srv/SetBool",
            task_server_type="service",
        )
    )
    try:
        request = ExecuteTaskV1.Goal()
        request.task_id = "error-task"
        request.task_name = "example/set_bool"
        request.task_data_json = '{"data": true}'

        result = node._execute_task_cb(FakeGoalHandle(request))

        terminal_events = [event for event in events_pub.messages if event.event_type in TERMINAL_EVENT_TYPES]
        assert result.status == TaskStatusV1.ERROR
        assert [msg.task_id for msg in results_pub.messages] == ["error-task"]
        assert [event.event_type for event in terminal_events] == ["task.failed"]
    finally:
        _destroy_node(node)


def test_rejected_terminal_result_and_event_are_published_once(tmp_path):
    node = _make_node(tmp_path)
    events_pub = RecordingPublisher()
    results_pub = RecordingPublisher()
    node._events_pub = events_pub
    node._results_pub = results_pub
    try:
        request = ExecuteTaskV1.Goal()
        request.task_id = "rejected-task"
        request.task_name = "missing/task"

        result = node._execute_task_cb(FakeGoalHandle(request))

        terminal_events = [event for event in events_pub.messages if event.event_type in TERMINAL_EVENT_TYPES]
        assert result.status == TaskStatusV1.REJECTED
        assert [msg.task_id for msg in results_pub.messages] == ["rejected-task"]
        assert [event.event_type for event in terminal_events] == ["task.rejected"]
    finally:
        _destroy_node(node)


def test_canceled_terminal_result_and_event_are_published_once(tmp_path):
    node = _make_node(tmp_path)
    events_pub = RecordingPublisher()
    results_pub = RecordingPublisher()
    node._events_pub = events_pub
    node._results_pub = results_pub
    node._action_task_client = FakeActionTaskClient(error=ActionTaskCanceled("action goal was canceled"))
    node._task_registry.add(
        TaskDefinition(
            task_name="example/fibonacci",
            topic="/example/fibonacci",
            msg_interface="example_interfaces/action/Fibonacci",
            task_server_type="action",
        )
    )
    try:
        request = ExecuteTaskV1.Goal()
        request.task_id = "canceled-task"
        request.task_name = "example/fibonacci"
        request.task_data_json = '{"order": 5}'

        result = node._execute_task_cb(FakeGoalHandle(request))

        terminal_events = [event for event in events_pub.messages if event.event_type in TERMINAL_EVENT_TYPES]
        assert result.status == TaskStatusV1.CANCELED
        assert [msg.task_id for msg in results_pub.messages] == ["canceled-task"]
        assert [event.event_type for event in terminal_events] == ["task.canceled"]
    finally:
        _destroy_node(node)


def test_execute_service_task_succeeds_and_clears_active_task(tmp_path):
    node = _make_node(tmp_path)
    node._service_task_client = FakeServiceTaskClient()
    node._task_registry.add(
        TaskDefinition(
            task_name="example/set_bool",
            topic="/example/set_bool",
            msg_interface="std_srvs/srv/SetBool",
            task_server_type="service",
        )
    )
    try:
        request = ExecuteTaskV1.Goal()
        request.task_name = "example/set_bool"
        request.task_data_json = '{"data": true}'
        goal_handle = FakeGoalHandle(request)

        result = node._execute_task_cb(goal_handle)

        assert goal_handle.state == "succeeded"
        assert result.status == TaskStatusV1.DONE
        assert result.result_json == '{"message": "ok", "success": true}'
        assert len(node._active_tasks) == 0
    finally:
        _destroy_node(node)


def test_execute_service_task_reports_unavailable_server(tmp_path):
    node = _make_node(tmp_path)
    node._service_task_client = FakeServiceTaskClient(
        error=ServiceTaskServerUnavailable("service is not available: /example/set_bool")
    )
    node._task_registry.add(
        TaskDefinition(
            task_name="example/set_bool",
            topic="/example/set_bool",
            msg_interface="std_srvs/srv/SetBool",
            task_server_type="service",
        )
    )
    try:
        request = ExecuteTaskV1.Goal()
        request.task_name = "example/set_bool"
        request.task_data_json = '{"data": true}'
        goal_handle = FakeGoalHandle(request)

        result = node._execute_task_cb(goal_handle)

        assert goal_handle.state == "aborted"
        assert result.status == TaskStatusV1.ERROR
        assert result.error_code == ErrorCodeV1.SERVER_UNAVAILABLE
        assert len(node._active_tasks) == 0
    finally:
        _destroy_node(node)


def test_execute_action_task_succeeds_and_clears_active_task(tmp_path):
    node = _make_node(tmp_path)
    node._action_task_client = FakeActionTaskClient()
    node._task_registry.add(
        TaskDefinition(
            task_name="example/fibonacci",
            topic="/example/fibonacci",
            msg_interface="example_interfaces/action/Fibonacci",
            task_server_type="action",
        )
    )
    try:
        request = ExecuteTaskV1.Goal()
        request.task_name = "example/fibonacci"
        request.task_data_json = '{"order": 5}'
        goal_handle = FakeGoalHandle(request)

        result = node._execute_task_cb(goal_handle)

        assert goal_handle.state == "succeeded"
        assert result.status == TaskStatusV1.DONE
        assert result.result_json == '{"sequence": [1, 1, 2]}'
        assert len(node._active_tasks) == 0
    finally:
        _destroy_node(node)


def test_execute_action_task_reports_unavailable_server(tmp_path):
    node = _make_node(tmp_path)
    node._action_task_client = FakeActionTaskClient(
        error=ActionTaskServerUnavailable("action server is not available: /example/fibonacci")
    )
    node._task_registry.add(
        TaskDefinition(
            task_name="example/fibonacci",
            topic="/example/fibonacci",
            msg_interface="example_interfaces/action/Fibonacci",
            task_server_type="action",
        )
    )
    try:
        request = ExecuteTaskV1.Goal()
        request.task_name = "example/fibonacci"
        request.task_data_json = '{"order": 5}'
        goal_handle = FakeGoalHandle(request)

        result = node._execute_task_cb(goal_handle)

        assert goal_handle.state == "aborted"
        assert result.status == TaskStatusV1.ERROR
        assert result.error_code == ErrorCodeV1.SERVER_UNAVAILABLE
        assert len(node._active_tasks) == 0
    finally:
        _destroy_node(node)


def test_execute_action_task_reports_canceled_by_default(tmp_path):
    node = _make_node(tmp_path)
    node._action_task_client = FakeActionTaskClient(error=ActionTaskCanceled("action goal was canceled"))
    node._task_registry.add(
        TaskDefinition(
            task_name="example/fibonacci",
            topic="/example/fibonacci",
            msg_interface="example_interfaces/action/Fibonacci",
            task_server_type="action",
        )
    )
    try:
        request = ExecuteTaskV1.Goal()
        request.task_name = "example/fibonacci"
        request.task_data_json = '{"order": 5}'
        goal_handle = FakeGoalHandle(request)

        result = node._execute_task_cb(goal_handle)

        assert goal_handle.state == "aborted"
        assert result.status == TaskStatusV1.CANCELED
        assert result.error_message == "action goal was canceled"
        assert len(node._active_tasks) == 0
    finally:
        _destroy_node(node)


def test_execute_action_task_can_report_cancel_as_success(tmp_path):
    node = _make_node(tmp_path)
    node._action_task_client = FakeActionTaskClient(error=ActionTaskCanceled("action goal was canceled"))
    node._task_registry.add(
        TaskDefinition(
            task_name="example/fibonacci",
            topic="/example/fibonacci",
            msg_interface="example_interfaces/action/Fibonacci",
            task_server_type="action",
            cancel_reported_as_success=True,
        )
    )
    try:
        request = ExecuteTaskV1.Goal()
        request.task_name = "example/fibonacci"
        request.task_data_json = '{"order": 5}'
        goal_handle = FakeGoalHandle(request)

        result = node._execute_task_cb(goal_handle)

        assert goal_handle.state == "succeeded"
        assert result.status == TaskStatusV1.DONE
        assert result.error_message == ""
        assert len(node._active_tasks) == 0
    finally:
        _destroy_node(node)


def test_cancel_tasks_cancels_matching_active_task(tmp_path):
    node = _make_node(tmp_path)
    events_pub = RecordingPublisher()
    node._events_pub = events_pub
    canceled = []
    try:
        node._active_tasks.add(
            ActiveTaskEntry(
                api_version="v1beta1",
                task_id="task-1",
                task_name="example/fibonacci",
                source="test",
                correlation_id="corr-1",
                priority=0,
                status=TaskStatusV1.IN_PROGRESS,
                created_at=node.get_clock().now().to_msg(),
                started_at=node.get_clock().now().to_msg(),
                tags=(),
                task_server_type="action",
                cancel_on_stop=True,
                cancel_callback=lambda: not canceled.append("task-1"),
            )
        )
        request = CancelTasksV1.Request()
        response = CancelTasksV1.Response()
        request.task_ids = ["task-1"]

        response = node._cancel_tasks(request, response)

        assert response.success is True
        assert response.canceled_task_ids == ["task-1"]
        assert response.failed_task_ids == []
        assert canceled == ["task-1"]
        system_events = [event for event in events_pub.messages if event.event_type.startswith("system.cancel")]
        assert [event.event_type for event in system_events] == [
            "system.cancel.requested",
            "system.cancel.completed",
        ]
        completed_data = json.loads(system_events[-1].data_json)
        assert completed_data["success"] is True
        assert completed_data["canceled_task_ids"] == ["task-1"]
    finally:
        _destroy_node(node)


def test_execute_task_action_cancel_callback_cancels_active_task(tmp_path):
    node = _make_node(tmp_path)
    canceled = []
    try:
        node._active_tasks.add(
            ActiveTaskEntry(
                api_version="v1beta1",
                task_id="task-1",
                task_name="example/fibonacci",
                source="test",
                correlation_id="corr-1",
                priority=0,
                status=TaskStatusV1.IN_PROGRESS,
                created_at=node.get_clock().now().to_msg(),
                started_at=node.get_clock().now().to_msg(),
                tags=(),
                task_server_type="action",
                cancel_on_stop=True,
                cancel_callback=lambda: not canceled.append("task-1"),
            )
        )
        request = ExecuteTaskV1.Goal()
        request.task_id = "task-1"
        goal_handle = FakeGoalHandle(request)

        response = node._cancel_execute_task_goal(goal_handle)

        assert response == CancelResponse.ACCEPT
        assert canceled == ["task-1"]
    finally:
        _destroy_node(node)


def test_execute_task_action_cancel_callback_rejects_noncancelable_task(tmp_path):
    node = _make_node(tmp_path)
    try:
        node._active_tasks.add(
            ActiveTaskEntry(
                api_version="v1beta1",
                task_id="task-1",
                task_name="example/set_bool",
                source="test",
                correlation_id="corr-1",
                priority=0,
                status=TaskStatusV1.IN_PROGRESS,
                created_at=node.get_clock().now().to_msg(),
                started_at=node.get_clock().now().to_msg(),
                tags=(),
                task_server_type="service",
                cancel_on_stop=True,
                cancel_callback=None,
            )
        )
        request = ExecuteTaskV1.Goal()
        request.task_id = "task-1"
        goal_handle = FakeGoalHandle(request)

        response = node._cancel_execute_task_goal(goal_handle)

        assert response == CancelResponse.REJECT
    finally:
        _destroy_node(node)


def test_cancel_tasks_reports_noncancelable_task(tmp_path):
    node = _make_node(tmp_path)
    try:
        node._active_tasks.add(
            ActiveTaskEntry(
                api_version="v1beta1",
                task_id="task-1",
                task_name="example/set_bool",
                source="test",
                correlation_id="corr-1",
                priority=0,
                status=TaskStatusV1.IN_PROGRESS,
                created_at=node.get_clock().now().to_msg(),
                started_at=node.get_clock().now().to_msg(),
                tags=(),
                task_server_type="service",
                cancel_on_stop=True,
                cancel_callback=None,
            )
        )
        request = CancelTasksV1.Request()
        response = CancelTasksV1.Response()
        request.task_ids = ["task-1"]

        response = node._cancel_tasks(request, response)

        assert response.success is False
        assert response.canceled_task_ids == []
        assert response.failed_task_ids == ["task-1"]
        assert response.error_code == ErrorCodeV1.TASK_CANCEL_FAILED
    finally:
        _destroy_node(node)


def test_pause_and_resume_tasks_use_active_control_callbacks(tmp_path):
    node = _make_node(tmp_path)
    events_pub = RecordingPublisher()
    node._events_pub = events_pub
    calls = []
    try:
        node._active_tasks.add(
            ActiveTaskEntry(
                api_version="v1beta1",
                task_id="task-1",
                task_name="example/pauseable",
                source="test",
                correlation_id="corr-1",
                priority=0,
                status=TaskStatusV1.IN_PROGRESS,
                created_at=node.get_clock().now().to_msg(),
                started_at=node.get_clock().now().to_msg(),
                tags=(),
                task_server_type="action",
                pause_callback=lambda: not calls.append("pause"),
                resume_callback=lambda: not calls.append("resume"),
            )
        )
        pause_request = PauseTasksV1.Request()
        pause_response = PauseTasksV1.Response()
        pause_request.task_ids = ["task-1"]
        resume_request = ResumeTasksV1.Request()
        resume_response = ResumeTasksV1.Response()
        resume_request.task_ids = ["task-1"]

        pause_response = node._pause_tasks(pause_request, pause_response)
        resume_response = node._resume_tasks(resume_request, resume_response)

        assert pause_response.success is True
        assert pause_response.paused_task_ids == ["task-1"]
        assert resume_response.success is True
        assert resume_response.resumed_task_ids == ["task-1"]
        assert calls == ["pause", "resume"]
        assert node._active_tasks.get("task-1").status == TaskStatusV1.IN_PROGRESS
        assert [event.event_type for event in events_pub.messages] == [
            "system.pause.requested",
            "task.paused",
            "system.pause.completed",
            "system.resume.requested",
            "task.resumed",
            "system.resume.completed",
        ]
    finally:
        _destroy_node(node)


def test_pause_tasks_reports_unsupported_without_hook(tmp_path):
    node = _make_node(tmp_path)
    try:
        node._active_tasks.add(
            ActiveTaskEntry(
                api_version="v1beta1",
                task_id="task-1",
                task_name="example/not-pauseable",
                source="test",
                correlation_id="corr-1",
                priority=0,
                status=TaskStatusV1.IN_PROGRESS,
                created_at=node.get_clock().now().to_msg(),
                started_at=node.get_clock().now().to_msg(),
                tags=(),
                task_server_type="service",
            )
        )
        request = PauseTasksV1.Request()
        response = PauseTasksV1.Response()
        request.task_ids = ["task-1"]

        response = node._pause_tasks(request, response)

        assert response.success is False
        assert response.paused_task_ids == []
        assert response.failed_task_ids == ["task-1"]
        assert response.error_code == ErrorCodeV1.UNSUPPORTED
    finally:
        _destroy_node(node)


def test_configured_service_control_hook_executes_through_service_client(tmp_path):
    node = _make_node(tmp_path)
    node._service_task_client = FakeServiceTaskClient()
    task = TaskDefinition(
        task_name="example/pauseable",
        pause_hook=TaskControlHook(
            task_server_type="service",
            topic="/example/pause",
            msg_interface="std_srvs/srv/Trigger",
            task_data_json="{}",
            timeout_sec=2.0,
        ),
    )
    try:
        callback = node._make_task_control_callback(task, "task-1", "pause", task.pause_hook)

        assert callback is not None
        assert callback() is True
        assert node._service_task_client.task.task_name == "example/pauseable/pause"
        assert node._service_task_client.task.topic == "/example/pause"
        assert node._service_task_client.task.cancel_timeout == 2.0
    finally:
        _destroy_node(node)


def test_stop_tasks_uses_cancel_on_stop(tmp_path):
    node = _make_node(tmp_path)
    events_pub = RecordingPublisher()
    node._events_pub = events_pub
    stopped = []
    try:
        node._active_tasks.add(
            ActiveTaskEntry(
                api_version="v1beta1",
                task_id="stop-me",
                task_name="example/fibonacci",
                source="test",
                correlation_id="corr-1",
                priority=0,
                status=TaskStatusV1.IN_PROGRESS,
                created_at=node.get_clock().now().to_msg(),
                started_at=node.get_clock().now().to_msg(),
                tags=(),
                task_server_type="action",
                cancel_on_stop=True,
                cancel_callback=lambda: not stopped.append("stop-me"),
            )
        )
        node._active_tasks.add(
            ActiveTaskEntry(
                api_version="v1beta1",
                task_id="keep-me",
                task_name="example/fibonacci",
                source="test",
                correlation_id="corr-2",
                priority=0,
                status=TaskStatusV1.IN_PROGRESS,
                created_at=node.get_clock().now().to_msg(),
                started_at=node.get_clock().now().to_msg(),
                tags=(),
                task_server_type="action",
                cancel_on_stop=False,
                cancel_callback=lambda: not stopped.append("keep-me"),
            )
        )
        request = StopTasksV1.Request()
        response = StopTasksV1.Response()

        response = node._stop_tasks(request, response)

        assert response.success is True
        assert response.stopped_task_ids == ["stop-me"]
        assert stopped == ["stop-me"]
        system_events = [event for event in events_pub.messages if event.event_type.startswith("system.stop")]
        assert [event.event_type for event in system_events] == [
            "system.stop.requested",
            "system.stop.completed",
        ]
        completed_data = json.loads(system_events[-1].data_json)
        assert completed_data["success"] is True
        assert completed_data["stopped_task_ids"] == ["stop-me"]
    finally:
        _destroy_node(node)


def test_execute_system_cancel_task_cancels_active_task_despite_blocking_policy(tmp_path):
    node = _make_node(tmp_path)
    events_pub = RecordingPublisher()
    node._events_pub = events_pub
    canceled = []
    try:
        node._active_tasks.add(
            ActiveTaskEntry(
                api_version="v1beta1",
                task_id="blocking-task",
                task_name="example/fibonacci",
                source="test",
                correlation_id="corr-1",
                priority=0,
                status=TaskStatusV1.IN_PROGRESS,
                created_at=node.get_clock().now().to_msg(),
                started_at=node.get_clock().now().to_msg(),
                tags=(),
                task_server_type="action",
                blocking=True,
                cancel_on_stop=True,
                cancel_callback=lambda: not canceled.append("blocking-task"),
            )
        )
        request = ExecuteTaskV1.Goal()
        request.task_id = "cancel-request"
        request.task_name = "system/cancel_task"
        request.task_data_json = '{"task_ids": ["blocking-task"]}'
        goal_handle = FakeGoalHandle(request)

        result = node._execute_task_cb(goal_handle)
        payload = json.loads(result.result_json)

        assert goal_handle.state == "succeeded"
        assert result.status == TaskStatusV1.DONE
        assert payload["success"] is True
        assert payload["canceled_task_ids"] == ["blocking-task"]
        assert payload["failed_task_ids"] == []
        assert canceled == ["blocking-task"]
        assert node._active_tasks.get("cancel-request") is None
        system_events = [event for event in events_pub.messages if event.event_type.startswith("system.cancel")]
        assert [event.event_type for event in system_events] == [
            "system.cancel.requested",
            "system.cancel.completed",
        ]
        assert system_events[0].task_id == "cancel-request"
    finally:
        _destroy_node(node)


def test_execute_system_cancel_task_reports_noncancelable_active_task(tmp_path):
    node = _make_node(tmp_path)
    try:
        node._active_tasks.add(
            ActiveTaskEntry(
                api_version="v1beta1",
                task_id="service-task",
                task_name="example/set_bool",
                source="test",
                correlation_id="corr-1",
                priority=0,
                status=TaskStatusV1.IN_PROGRESS,
                created_at=node.get_clock().now().to_msg(),
                started_at=node.get_clock().now().to_msg(),
                tags=(),
                task_server_type="service",
                cancel_on_stop=True,
                cancel_callback=None,
            )
        )
        request = ExecuteTaskV1.Goal()
        request.task_id = "cancel-request"
        request.task_name = "system/cancel_task"
        request.task_data_json = '{"task_ids": ["service-task"]}'
        goal_handle = FakeGoalHandle(request)

        result = node._execute_task_cb(goal_handle)
        payload = json.loads(result.result_json)

        assert goal_handle.state == "aborted"
        assert result.status == TaskStatusV1.ERROR
        assert result.error_code == ErrorCodeV1.TASK_CANCEL_FAILED
        assert payload["success"] is False
        assert payload["canceled_task_ids"] == []
        assert payload["failed_task_ids"] == ["service-task"]
    finally:
        _destroy_node(node)


def test_execute_system_stop_task_stops_cancel_on_stop_tasks(tmp_path):
    node = _make_node(tmp_path)
    events_pub = RecordingPublisher()
    node._events_pub = events_pub
    stopped = []
    try:
        node._active_tasks.add(
            ActiveTaskEntry(
                api_version="v1beta1",
                task_id="stop-me",
                task_name="example/fibonacci",
                source="test",
                correlation_id="corr-1",
                priority=0,
                status=TaskStatusV1.IN_PROGRESS,
                created_at=node.get_clock().now().to_msg(),
                started_at=node.get_clock().now().to_msg(),
                tags=(),
                task_server_type="action",
                blocking=True,
                cancel_on_stop=True,
                cancel_callback=lambda: not stopped.append("stop-me"),
            )
        )
        node._active_tasks.add(
            ActiveTaskEntry(
                api_version="v1beta1",
                task_id="keep-me",
                task_name="example/fibonacci",
                source="test",
                correlation_id="corr-2",
                priority=0,
                status=TaskStatusV1.IN_PROGRESS,
                created_at=node.get_clock().now().to_msg(),
                started_at=node.get_clock().now().to_msg(),
                tags=(),
                task_server_type="action",
                cancel_on_stop=False,
                cancel_callback=lambda: not stopped.append("keep-me"),
            )
        )
        request = ExecuteTaskV1.Goal()
        request.task_id = "stop-request"
        request.task_name = "system/stop"
        request.task_data_json = "{}"
        goal_handle = FakeGoalHandle(request)

        result = node._execute_task_cb(goal_handle)
        payload = json.loads(result.result_json)

        assert goal_handle.state == "succeeded"
        assert result.status == TaskStatusV1.DONE
        assert payload["success"] is True
        assert payload["stopped_task_ids"] == ["stop-me"]
        assert stopped == ["stop-me"]
        assert node._active_tasks.get("stop-request") is None
        system_events = [event for event in events_pub.messages if event.event_type.startswith("system.stop")]
        assert [event.event_type for event in system_events] == [
            "system.stop.requested",
            "system.stop.completed",
        ]
        assert system_events[0].task_id == "stop-request"
    finally:
        _destroy_node(node)


def test_blocking_active_task_rejects_new_task(tmp_path):
    node = _make_node(tmp_path)
    try:
        node._active_tasks.add(
            ActiveTaskEntry(
                api_version="v1beta1",
                task_id="blocking-task",
                task_name="example/blocking",
                source="test",
                correlation_id="corr-1",
                priority=0,
                status=TaskStatusV1.IN_PROGRESS,
                created_at=node.get_clock().now().to_msg(),
                started_at=node.get_clock().now().to_msg(),
                tags=(),
                task_server_type="action",
                blocking=True,
                cancel_on_stop=True,
                cancel_callback=lambda: True,
            )
        )
        request = ExecuteTaskV1.Goal()
        request.task_name = "system/wait"
        request.task_data_json = '{"duration_sec": 0}'
        goal_handle = FakeGoalHandle(request)

        result = node._execute_task_cb(goal_handle)

        assert goal_handle.state == "aborted"
        assert result.status == TaskStatusV1.REJECTED
        assert result.error_code == ErrorCodeV1.RESOURCE_CONFLICT
    finally:
        _destroy_node(node)


def test_resource_lock_rejects_conflicting_task(tmp_path):
    node = _make_node(tmp_path)
    node._task_registry.add(
        TaskDefinition(
            task_name="example/use-base",
            task_server_type="system/wait",
            resources=("base",),
        )
    )
    try:
        node._active_tasks.add(
            ActiveTaskEntry(
                api_version="v1beta1",
                task_id="active-base-task",
                task_name="example/active-base",
                source="test",
                correlation_id="corr-1",
                priority=0,
                status=TaskStatusV1.IN_PROGRESS,
                created_at=node.get_clock().now().to_msg(),
                started_at=node.get_clock().now().to_msg(),
                tags=(),
                task_server_type="action",
                resources=("base",),
            )
        )
        request = ExecuteTaskV1.Goal()
        request.task_id = "resource-conflict"
        request.task_name = "example/use-base"
        request.task_data_json = '{"duration_sec": 0}'

        result = node._execute_task_cb(FakeGoalHandle(request))

        assert result.status == TaskStatusV1.REJECTED
        assert result.error_code == ErrorCodeV1.RESOURCE_CONFLICT
        assert "resources: base" in result.error_message
    finally:
        _destroy_node(node)


def test_zone_lock_rejects_conflicting_zone(tmp_path):
    node = _make_node(tmp_path)
    node._task_registry.add(
        TaskDefinition(
            task_name="example/zone-task",
            task_server_type="system/wait",
            zone_locked=True,
        )
    )
    try:
        node._active_tasks.add(
            ActiveTaskEntry(
                api_version="v1beta1",
                task_id="active-zone-task",
                task_name="example/active-zone-task",
                source="test",
                correlation_id="corr-1",
                priority=0,
                status=TaskStatusV1.IN_PROGRESS,
                created_at=node.get_clock().now().to_msg(),
                started_at=node.get_clock().now().to_msg(),
                tags=(),
                task_server_type="action",
                zone_id="zone-1",
                zone_locked=True,
            )
        )
        request = ExecuteTaskV1.Goal()
        request.task_id = "zone-conflict"
        request.task_name = "example/zone-task"
        request.task_data_json = '{"duration_sec": 0}'
        request.zone_id = "zone-1"

        result = node._execute_task_cb(FakeGoalHandle(request))

        assert result.status == TaskStatusV1.REJECTED
        assert result.error_code == ErrorCodeV1.RESOURCE_CONFLICT
        assert "zone-1" in result.error_message
    finally:
        _destroy_node(node)


def test_admission_provider_parameters_reject_task(tmp_path):
    node = _make_node(tmp_path)
    node._task_registry.add(
        TaskDefinition(
            task_name="example/needs-battery",
            task_server_type="system/wait",
            min_battery_percent=50.0,
        )
    )
    node.set_parameters([Parameter("admission.battery_percent", value=20.0)])
    try:
        request = ExecuteTaskV1.Goal()
        request.task_id = "battery-rejected"
        request.task_name = "example/needs-battery"
        request.task_data_json = '{"duration_sec": 0}'

        result = node._execute_task_cb(FakeGoalHandle(request))

        assert result.status == TaskStatusV1.REJECTED
        assert result.error_code == ErrorCodeV1.POLICY_REJECTED
        assert "Battery level" in result.error_message
    finally:
        _destroy_node(node)


def test_reentrant_task_allows_same_task_name(tmp_path):
    node = _make_node(tmp_path)
    sleeps = []
    node._wait_task = WaitTaskExecutor(sleep=sleeps.append)
    try:
        node._active_tasks.add(
            ActiveTaskEntry(
                api_version="v1beta1",
                task_id="existing-wait",
                task_name="system/wait",
                source="test",
                correlation_id="corr-1",
                priority=0,
                status=TaskStatusV1.IN_PROGRESS,
                created_at=node.get_clock().now().to_msg(),
                started_at=node.get_clock().now().to_msg(),
                tags=(),
                task_server_type="system/wait",
                blocking=False,
                cancel_on_stop=True,
                cancel_callback=None,
            )
        )
        request = ExecuteTaskV1.Goal()
        request.task_name = "system/wait"
        request.task_data_json = '{"duration_sec": 0}'
        goal_handle = FakeGoalHandle(request)

        result = node._execute_task_cb(goal_handle)

        assert goal_handle.state == "succeeded"
        assert result.status == TaskStatusV1.DONE
        assert sleeps == [0.0]
        assert node._active_tasks.get("existing-wait") is not None
    finally:
        _destroy_node(node)


def test_nonreentrant_task_replaces_cancelable_same_type_task(tmp_path):
    node = _make_node(tmp_path)
    node._action_task_client = FakeActionTaskClient()
    canceled = []
    node._task_registry.add(
        TaskDefinition(
            task_name="example/fibonacci",
            topic="/example/fibonacci",
            msg_interface="example_interfaces/action/Fibonacci",
            task_server_type="action",
            reentrant=False,
        )
    )
    try:
        node._active_tasks.add(
            ActiveTaskEntry(
                api_version="v1beta1",
                task_id="old-task",
                task_name="example/fibonacci",
                source="test",
                correlation_id="corr-1",
                priority=0,
                status=TaskStatusV1.IN_PROGRESS,
                created_at=node.get_clock().now().to_msg(),
                started_at=node.get_clock().now().to_msg(),
                tags=(),
                task_server_type="action",
                blocking=True,
                cancel_on_stop=True,
                cancel_callback=lambda: not canceled.append("old-task"),
            )
        )
        request = ExecuteTaskV1.Goal()
        request.task_id = "new-task"
        request.task_name = "example/fibonacci"
        request.task_data_json = '{"order": 5}'
        goal_handle = FakeGoalHandle(request)

        result = node._execute_task_cb(goal_handle)

        assert goal_handle.state == "succeeded"
        assert result.status == TaskStatusV1.DONE
        assert canceled == ["old-task"]
        assert node._active_tasks.get("old-task") is not None
        assert node._active_tasks.get("new-task") is None
    finally:
        _destroy_node(node)


def test_nonreentrant_task_rejects_noncancelable_same_type_task(tmp_path):
    node = _make_node(tmp_path)
    node._task_registry.add(
        TaskDefinition(
            task_name="example/set_bool",
            topic="/example/set_bool",
            msg_interface="std_srvs/srv/SetBool",
            task_server_type="service",
            reentrant=False,
        )
    )
    try:
        node._active_tasks.add(
            ActiveTaskEntry(
                api_version="v1beta1",
                task_id="old-task",
                task_name="example/set_bool",
                source="test",
                correlation_id="corr-1",
                priority=0,
                status=TaskStatusV1.IN_PROGRESS,
                created_at=node.get_clock().now().to_msg(),
                started_at=node.get_clock().now().to_msg(),
                tags=(),
                task_server_type="service",
                blocking=False,
                cancel_on_stop=True,
                cancel_callback=None,
            )
        )
        request = ExecuteTaskV1.Goal()
        request.task_id = "new-task"
        request.task_name = "example/set_bool"
        request.task_data_json = '{"data": true}'
        goal_handle = FakeGoalHandle(request)

        result = node._execute_task_cb(goal_handle)

        assert goal_handle.state == "aborted"
        assert result.status == TaskStatusV1.REJECTED
        assert result.error_code == ErrorCodeV1.RESOURCE_CONFLICT
        assert node._active_tasks.get("old-task") is not None
        assert node._active_tasks.get("new-task") is None
    finally:
        _destroy_node(node)


def test_execute_linear_mission_succeeds(tmp_path):
    node = _make_node(tmp_path)
    events_pub = RecordingPublisher()
    node._events_pub = events_pub
    sleeps = []
    node._wait_task = WaitTaskExecutor(sleep=sleeps.append)
    try:
        request = ExecuteTaskV1.Goal()
        request.task_id = "mission-task"
        request.task_name = "system/mission"
        request.task_data_json = json.dumps(
            {
                "mission_id": "mission-1",
                "subtasks": [
                    {
                        "subtask_id": "wait-1",
                        "task_name": "system/wait",
                        "task_data_json": {"duration_sec": 0},
                    },
                    {
                        "subtask_id": "wait-2",
                        "task_name": "system/wait",
                        "task_data_json": {"duration_sec": 0},
                    },
                ],
            }
        )
        goal_handle = FakeGoalHandle(request)

        result = node._execute_task_cb(goal_handle)
        result_payload = json.loads(result.result_json)

        assert goal_handle.state == "succeeded"
        assert result.status == TaskStatusV1.DONE
        assert result_payload["mission_id"] == "mission-1"
        assert result_payload["status"] == TaskStatusV1.DONE
        assert [item["subtask_id"] for item in result_payload["mission_results"]] == ["wait-1", "wait-2"]
        assert sleeps == [0.0, 0.0]
        mission_events = [event for event in events_pub.messages if event.event_type.startswith("mission.")]
        assert [event.event_type for event in mission_events] == [
            "mission.started",
            "mission.subtask.started",
            "mission.subtask.completed",
            "mission.subtask.started",
            "mission.subtask.completed",
            "mission.completed",
        ]
        assert json.loads(mission_events[0].data_json)["mission_id"] == "mission-1"
        assert json.loads(mission_events[-1].data_json)["completed_subtasks"] == 2
    finally:
        _destroy_node(node)


def test_execute_mission_graph_dependencies_use_ready_waves(tmp_path):
    node = _make_node(tmp_path)
    events_pub = RecordingPublisher()
    node._events_pub = events_pub
    sleeps = []
    node._wait_task = WaitTaskExecutor(sleep=sleeps.append)
    try:
        request = ExecuteTaskV1.Goal()
        request.task_id = "mission-task"
        request.task_name = "system/mission"
        request.task_data_json = json.dumps(
            {
                "mission_id": "mission-graph",
                "subtasks": [
                    {
                        "subtask_id": "wait-2",
                        "task_name": "system/wait",
                        "task_data_json": {"duration_sec": 0},
                        "depends_on": ["wait-1"],
                    },
                    {
                        "subtask_id": "wait-1",
                        "task_name": "system/wait",
                        "task_data_json": {"duration_sec": 0},
                    },
                    {
                        "subtask_id": "wait-3",
                        "task_name": "system/wait",
                        "task_data_json": {"duration_sec": 0},
                        "depends_on": ["wait-1"],
                    },
                ],
            }
        )

        result = node._execute_task_cb(FakeGoalHandle(request))
        result_payload = json.loads(result.result_json)

        assert result.status == TaskStatusV1.DONE
        assert [item["subtask_id"] for item in result_payload["mission_results"]] == [
            "wait-1",
            "wait-2",
            "wait-3",
        ]
        assert sleeps == [0.0, 0.0, 0.0]

        mission_events = [event for event in events_pub.messages if event.event_type.startswith("mission.")]
        started_events = [event for event in mission_events if event.event_type == "mission.subtask.started"]
        started_payloads = [json.loads(event.data_json) for event in started_events]
        assert [payload["subtask_id"] for payload in started_payloads] == ["wait-1", "wait-2", "wait-3"]
        assert started_payloads[0]["graph_wave_index"] == 1
        assert started_payloads[0]["ready_subtask_ids"] == ["wait-1"]
        assert started_payloads[1]["graph_wave_index"] == 2
        assert started_payloads[1]["ready_subtask_ids"] == ["wait-2", "wait-3"]
        assert json.loads(mission_events[-1].data_json)["graph_waves"] == 2
    finally:
        _destroy_node(node)


def test_execute_mission_condition_skip_satisfies_dependency(tmp_path):
    node = _make_node(tmp_path)
    events_pub = RecordingPublisher()
    node._events_pub = events_pub
    sleeps = []
    node._wait_task = WaitTaskExecutor(sleep=sleeps.append)
    try:
        request = ExecuteTaskV1.Goal()
        request.task_id = "mission-task"
        request.task_name = "system/mission"
        request.task_data_json = json.dumps(
            {
                "mission_id": "mission-conditions",
                "subtasks": [
                    {
                        "subtask_id": "skip-me",
                        "task_name": "system/wait",
                        "task_data_json": {"duration_sec": 10},
                        "condition": {"action": "skip"},
                    },
                    {
                        "subtask_id": "after-skip",
                        "task_name": "system/wait",
                        "task_data_json": {"duration_sec": 0},
                        "depends_on": ["skip-me"],
                    },
                ],
            }
        )

        result = node._execute_task_cb(FakeGoalHandle(request))
        result_payload = json.loads(result.result_json)

        assert result.status == TaskStatusV1.DONE
        assert [item["status"] for item in result_payload["mission_results"]] == [
            TaskStatusV1.SKIPPED,
            TaskStatusV1.DONE,
        ]
        assert [item["attempts"] for item in result_payload["mission_results"]] == [0, 1]
        assert sleeps == [0.0]
        mission_events = [event for event in events_pub.messages if event.event_type.startswith("mission.")]
        assert [event.event_type for event in mission_events] == [
            "mission.started",
            "mission.subtask.started",
            "mission.subtask.skipped",
            "mission.subtask.started",
            "mission.subtask.completed",
            "mission.completed",
        ]
    finally:
        _destroy_node(node)


def test_execute_mission_condition_abort_fails_and_marks_remaining_pending(tmp_path):
    node = _make_node(tmp_path)
    events_pub = RecordingPublisher()
    node._events_pub = events_pub
    sleeps = []
    node._wait_task = WaitTaskExecutor(sleep=sleeps.append)
    try:
        request = ExecuteTaskV1.Goal()
        request.task_id = "mission-task"
        request.task_name = "system/mission"
        request.task_data_json = json.dumps(
            {
                "mission_id": "mission-conditions",
                "subtasks": [
                    {
                        "subtask_id": "abort-me",
                        "task_name": "system/wait",
                        "condition": {"action": "abort", "reason": "blocked by condition"},
                    },
                    {
                        "subtask_id": "after-abort",
                        "task_name": "system/wait",
                        "task_data_json": {"duration_sec": 0},
                        "depends_on": ["abort-me"],
                    },
                ],
            }
        )
        goal_handle = FakeGoalHandle(request)

        result = node._execute_task_cb(goal_handle)
        result_payload = json.loads(result.result_json)

        assert goal_handle.state == "aborted"
        assert result.status == TaskStatusV1.ERROR
        assert result.error_code == ErrorCodeV1.POLICY_REJECTED
        assert result.error_message == "blocked by condition"
        assert [item["status"] for item in result_payload["mission_results"]] == [
            TaskStatusV1.ERROR,
            TaskStatusV1.PENDING,
        ]
        assert [item["attempts"] for item in result_payload["mission_results"]] == [0, 0]
        assert sleeps == []
        mission_events = [event for event in events_pub.messages if event.event_type.startswith("mission.")]
        assert mission_events[-1].event_type == "mission.failed"
        failed_data = json.loads(mission_events[-1].data_json)
        assert failed_data["failed_subtask_id"] == "abort-me"
        assert failed_data["blocked_subtask_ids"] == ["after-abort"]
    finally:
        _destroy_node(node)


def test_execute_mission_retry_policy_uses_error_codes_and_exponential_backoff(tmp_path):
    node = _make_node(tmp_path)
    retry_sleeps = []
    node._retry_sleep = retry_sleeps.append
    try:
        request = ExecuteTaskV1.Goal()
        request.task_id = "mission-task"
        request.task_name = "system/mission"
        request.task_data_json = json.dumps(
            {
                "mission_id": "mission-retry",
                "subtasks": [
                    {
                        "subtask_id": "missing",
                        "task_name": "missing/task",
                        "allow_skipping": True,
                        "retry_policy": {
                            "max_attempts": 3,
                            "backoff_sec": 1.0,
                            "backoff_type": "exponential",
                            "max_backoff_sec": 1.5,
                            "error_codes": [ErrorCodeV1.UNKNOWN_TASK],
                        },
                    }
                ],
            }
        )

        result = node._execute_task_cb(FakeGoalHandle(request))
        result_payload = json.loads(result.result_json)

        assert result.status == TaskStatusV1.DONE
        assert retry_sleeps == [1.0, 1.5]
        assert result_payload["mission_results"][0]["status"] == TaskStatusV1.SKIPPED
        assert result_payload["mission_results"][0]["attempts"] == 3
        assert result_payload["mission_results"][0]["error_code"] == ErrorCodeV1.UNKNOWN_TASK
    finally:
        _destroy_node(node)


def test_execute_mission_retry_policy_skips_non_matching_error_code(tmp_path):
    node = _make_node(tmp_path)
    retry_sleeps = []
    node._retry_sleep = retry_sleeps.append
    try:
        request = ExecuteTaskV1.Goal()
        request.task_id = "mission-task"
        request.task_name = "system/mission"
        request.task_data_json = json.dumps(
            {
                "mission_id": "mission-retry",
                "subtasks": [
                    {
                        "subtask_id": "missing",
                        "task_name": "missing/task",
                        "allow_skipping": True,
                        "retry_policy": {
                            "max_attempts": 3,
                            "backoff_sec": 1.0,
                            "error_codes": [ErrorCodeV1.TASK_TIMEOUT],
                        },
                    }
                ],
            }
        )

        result = node._execute_task_cb(FakeGoalHandle(request))
        result_payload = json.loads(result.result_json)

        assert result.status == TaskStatusV1.DONE
        assert retry_sleeps == []
        assert result_payload["mission_results"][0]["attempts"] == 1
        assert result_payload["mission_results"][0]["error_code"] == ErrorCodeV1.UNKNOWN_TASK
    finally:
        _destroy_node(node)


def test_execute_mission_timeout_publishes_timeout_event(tmp_path):
    node = _make_node(tmp_path)
    events_pub = RecordingPublisher()
    node._events_pub = events_pub
    sleeps = []
    node._wait_task = WaitTaskExecutor(sleep=sleeps.append)
    try:
        request = ExecuteTaskV1.Goal()
        request.task_id = "mission-task"
        request.task_name = "system/mission"
        request.task_data_json = json.dumps(
            {
                "mission_id": "mission-timeout",
                "subtasks": [
                    {
                        "subtask_id": "slow-wait",
                        "task_name": "system/wait",
                        "task_data_json": {"duration_sec": 2.0},
                        "timeout_sec": 1.0,
                    }
                ],
            }
        )
        goal_handle = FakeGoalHandle(request)

        result = node._execute_task_cb(goal_handle)
        result_payload = json.loads(result.result_json)

        assert goal_handle.state == "aborted"
        assert result.status == TaskStatusV1.ERROR
        assert result.error_code == ErrorCodeV1.TASK_TIMEOUT
        assert result_payload["mission_results"][0]["error_code"] == ErrorCodeV1.TASK_TIMEOUT
        assert sleeps == []
        mission_events = [event for event in events_pub.messages if event.event_type.startswith("mission.")]
        assert mission_events[-1].event_type == "mission.timeout"
        assert json.loads(mission_events[-1].data_json)["failed_subtask_id"] == "slow-wait"
    finally:
        _destroy_node(node)


def test_execute_mission_from_yaml_template(tmp_path):
    templates_dir = tmp_path / "mission_templates"
    templates_dir.mkdir()
    template_path = templates_dir / "wait.yaml"
    template_path.write_text(
        """
parameters:
  mission_id: template-mission
  duration_sec: 0
mission_id: "${mission_id}"
subtasks:
  - subtask_id: wait-1
    task_name: system/wait
    task_data_json:
      duration_sec: "${duration_sec}"
""",
        encoding="utf-8",
    )
    node = _make_node(tmp_path)
    node.set_parameters([Parameter("mission_templates_path", value=str(templates_dir))])
    sleeps = []
    node._wait_task = WaitTaskExecutor(sleep=sleeps.append)
    try:
        request = ExecuteTaskV1.Goal()
        request.task_id = "mission-template-task"
        request.task_name = "system/mission"
        request.task_data_json = json.dumps(
            {
                "template_id": "wait",
                "params": {
                    "mission_id": "mission-from-template",
                    "duration_sec": 0,
                },
            }
        )

        result = node._execute_task_cb(FakeGoalHandle(request))
        result_payload = json.loads(result.result_json)

        assert result.status == TaskStatusV1.DONE
        assert result_payload["mission_id"] == "mission-from-template"
        assert [item["subtask_id"] for item in result_payload["mission_results"]] == ["wait-1"]
        assert sleeps == [0.0]
    finally:
        _destroy_node(node)


def test_execute_mission_fails_on_nonskippable_subtask_failure(tmp_path):
    node = _make_node(tmp_path)
    events_pub = RecordingPublisher()
    node._events_pub = events_pub
    try:
        request = ExecuteTaskV1.Goal()
        request.task_id = "mission-task"
        request.task_name = "system/mission"
        request.task_data_json = json.dumps(
            {
                "mission_id": "mission-1",
                "subtasks": [
                    {
                        "subtask_id": "missing",
                        "task_name": "missing/task",
                        "task_data_json": {},
                    },
                    {
                        "subtask_id": "not-started",
                        "task_name": "system/wait",
                        "task_data_json": {"duration_sec": 0},
                    }
                ],
            }
        )
        goal_handle = FakeGoalHandle(request)

        result = node._execute_task_cb(goal_handle)
        result_payload = json.loads(result.result_json)

        assert goal_handle.state == "aborted"
        assert result.status == TaskStatusV1.ERROR
        assert result.error_code == ErrorCodeV1.UNKNOWN_TASK
        assert result_payload["status"] == TaskStatusV1.ERROR
        assert result_payload["error"]["code"] == ErrorCodeV1.UNKNOWN_TASK
        assert result_payload["mission_results"][0]["subtask_id"] == "missing"
        assert result_payload["mission_results"][0]["status"] == TaskStatusV1.REJECTED
        assert result_payload["mission_results"][1]["subtask_id"] == "not-started"
        assert result_payload["mission_results"][1]["status"] == TaskStatusV1.PENDING
        assert result_payload["mission_results"][1]["attempts"] == 0
        mission_events = [event for event in events_pub.messages if event.event_type.startswith("mission.")]
        assert [event.event_type for event in mission_events] == [
            "mission.started",
            "mission.subtask.started",
            "mission.subtask.failed",
            "mission.failed",
        ]
        failed_data = json.loads(mission_events[-1].data_json)
        assert failed_data["failed_subtask_id"] == "missing"
        assert failed_data["completed_subtasks"] == 1
        assert failed_data["total_subtasks"] == 2
    finally:
        _destroy_node(node)


def test_execute_mission_cancellation_marks_remaining_subtasks_canceled(tmp_path):
    node = _make_node(tmp_path)
    events_pub = RecordingPublisher()
    node._events_pub = events_pub
    node._action_task_client = FakeActionTaskClient(error=ActionTaskCanceled("action goal was canceled"))
    node._task_registry.add(
        TaskDefinition(
            task_name="example/fibonacci",
            topic="/example/fibonacci",
            msg_interface="example_interfaces/action/Fibonacci",
            task_server_type="action",
        )
    )
    try:
        request = ExecuteTaskV1.Goal()
        request.task_id = "mission-task"
        request.task_name = "system/mission"
        request.task_data_json = json.dumps(
            {
                "mission_id": "mission-1",
                "subtasks": [
                    {
                        "subtask_id": "action-1",
                        "task_name": "example/fibonacci",
                        "task_data_json": {"order": 5},
                    },
                    {
                        "subtask_id": "wait-1",
                        "task_name": "system/wait",
                        "task_data_json": {"duration_sec": 0},
                    },
                ],
            }
        )
        goal_handle = FakeGoalHandle(request)

        result = node._execute_task_cb(goal_handle)
        result_payload = json.loads(result.result_json)

        assert goal_handle.state == "aborted"
        assert result.status == TaskStatusV1.CANCELED
        assert result_payload["status"] == TaskStatusV1.CANCELED
        assert result_payload["error"]["message"] == "action goal was canceled"
        assert [item["status"] for item in result_payload["mission_results"]] == [
            TaskStatusV1.CANCELED,
            TaskStatusV1.CANCELED,
        ]
        assert result_payload["mission_results"][1]["attempts"] == 0
        mission_events = [event for event in events_pub.messages if event.event_type.startswith("mission.")]
        assert mission_events[-1].event_type == "mission.canceled"
        canceled_data = json.loads(mission_events[-1].data_json)
        assert canceled_data["completed_subtasks"] == 1
        assert canceled_data["total_subtasks"] == 2
    finally:
        _destroy_node(node)


def test_execute_mission_skips_allowed_subtask_failure(tmp_path):
    node = _make_node(tmp_path)
    events_pub = RecordingPublisher()
    node._events_pub = events_pub
    try:
        request = ExecuteTaskV1.Goal()
        request.task_id = "mission-task"
        request.task_name = "system/mission"
        request.task_data_json = json.dumps(
            {
                "mission_id": "mission-1",
                "subtasks": [
                    {
                        "subtask_id": "missing",
                        "task_name": "missing/task",
                        "task_data_json": {},
                        "allow_skipping": True,
                    }
                ],
            }
        )
        goal_handle = FakeGoalHandle(request)

        result = node._execute_task_cb(goal_handle)
        result_payload = json.loads(result.result_json)

        assert goal_handle.state == "succeeded"
        assert result.status == TaskStatusV1.DONE
        assert result_payload["status"] == TaskStatusV1.DONE
        assert result_payload["mission_results"][0]["status"] == TaskStatusV1.SKIPPED
        assert result_payload["mission_results"][0]["skipped"] is True
        mission_events = [event for event in events_pub.messages if event.event_type.startswith("mission.")]
        assert [event.event_type for event in mission_events] == [
            "mission.started",
            "mission.subtask.started",
            "mission.subtask.skipped",
            "mission.completed",
        ]
        skipped_data = json.loads(mission_events[2].data_json)
        assert skipped_data["skipped"] is True
    finally:
        _destroy_node(node)
