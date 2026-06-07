from builtin_interfaces.msg import Time

from task_orchestrator_core.storage import SQLiteStorageError, SQLiteTaskStorage
from task_orchestrator_msgs.msg import TaskEventV1, TaskRecordV1, TaskResultV1, TaskStatusV1
from task_orchestrator_msgs.srv import ListEventsV1, ListTaskRecordsV1


def _time(sec: int) -> Time:
    value = Time()
    value.sec = sec
    return value


def _record(task_id: str, status: str, source: str = "test") -> TaskRecordV1:
    result = TaskResultV1()
    result.api_version = "v1beta1"
    result.task_id = task_id
    result.task_name = "system/wait"
    result.source = source
    result.correlation_id = f"{task_id}-corr"
    result.status = status
    result.result_json = "{}"
    result.duration_sec = 1.0
    result.total_duration_sec = 2.0
    result.metadata_json = '{"work_order": "wo-1"}'
    result.robot_id = "robot-1"
    result.trace_id = "trace-1"
    result.created_at = _time(1)
    result.started_at = _time(2)
    result.finished_at = _time(3)

    record = TaskRecordV1()
    record.result = result
    record.task_data_json = '{"duration_sec": 0}'
    record.tags = ["storage"]
    return record


def _event(event_id: str, event_type: str, task_id: str, status: str) -> TaskEventV1:
    event = TaskEventV1()
    event.api_version = "v1beta1"
    event.event_id = event_id
    event.event_type = event_type
    event.task_id = task_id
    event.task_name = "system/wait"
    event.source = "test"
    event.correlation_id = f"{task_id}-corr"
    event.status = status
    event.data_json = "{}"
    event.duration_sec = 1.0
    event.total_duration_sec = 2.0
    event.robot_id = "robot-1"
    event.trace_id = "trace-1"
    event.stamp = _time(4)
    return event


def test_sqlite_storage_requires_path():
    try:
        SQLiteTaskStorage("", retention_days=0)
    except SQLiteStorageError as exc:
        assert "storage.sqlite_path" in str(exc)
    else:
        raise AssertionError("SQLiteTaskStorage accepted an empty path")


def test_sqlite_storage_round_trips_task_records_and_events(tmp_path):
    storage = SQLiteTaskStorage(str(tmp_path / "tasks.sqlite3"), retention_days=0)
    try:
        storage.write_task_record(_record("task-1", TaskStatusV1.DONE, source="scheduler"))
        storage.write_task_record(_record("task-2", TaskStatusV1.REJECTED, source="operator"))
        storage.write_event(_event("event-1", "task.started", "task-1", TaskStatusV1.IN_PROGRESS))
        storage.write_event(_event("event-2", "task.completed", "task-1", TaskStatusV1.DONE))

        get_record = storage.get_task_record("task-1")
        records_request = ListTaskRecordsV1.Request()
        records_request.status = TaskStatusV1.REJECTED
        records_request.source = "operator"
        events_request = ListEventsV1.Request()
        events_request.task_id = "task-1"

        records = storage.list_task_records(records_request)
        events = storage.list_events(events_request)

        assert get_record is not None
        assert get_record.result.task_id == "task-1"
        assert get_record.result.finished_at.sec == 3
        assert get_record.result.duration_sec == 1.0
        assert get_record.result.robot_id == "robot-1"
        assert get_record.result.trace_id == "trace-1"
        assert get_record.tags == ["storage"]
        assert [record.result.task_id for record in records] == ["task-2"]
        assert [event.event_type for event in events] == ["task.completed", "task.started"]
        assert events[0].robot_id == "robot-1"
        assert events[0].trace_id == "trace-1"
    finally:
        storage.close()
