from task_orchestrator_core.constants import (
    ACTION_EXECUTE_TASK,
    SERVICE_CANCEL_TASKS,
    SERVICE_GET_TASK,
    SERVICE_LIST_TASKS,
    SERVICE_PAUSE_TASKS,
    SERVICE_RESUME_TASKS,
    TOPIC_ACTIVE_TASKS,
    TOPIC_EVENTS,
    TOPIC_FEEDBACK,
    TOPIC_RESULTS,
)
from task_orchestrator_msgs.action import ExecuteTaskV1
from task_orchestrator_msgs.msg import (
    ActiveTaskArrayV1,
    ActiveTaskV1,
    ErrorCodeV1,
    SubtaskGoalV1,
    SubtaskResultV1,
    TaskEventV1,
    TaskFeedbackV1,
    TaskRecordV1,
    TaskResultV1,
    TaskSpecV1,
    TaskStatusV1,
)
from task_orchestrator_msgs.srv import ListEventsV1, ListTaskRecordsV1, ListTasksV1


PUBLIC_METADATA_FIELDS = (
    "api_version",
    "task_id",
    "task_name",
    "source",
    "priority",
    "correlation_id",
    "created_at",
    "started_at",
    "finished_at",
    "status",
    "error_code",
    "error_message",
    "result_json",
)


def _assert_metadata_fields(message_type):
    fields = message_type.get_fields_and_field_types()
    for field_name in PUBLIC_METADATA_FIELDS:
        assert field_name in fields


def test_public_ros_names_contract():
    assert ACTION_EXECUTE_TASK == "/task_orchestrator/execute_task"
    assert TOPIC_ACTIVE_TASKS == "/task_orchestrator/active_tasks"
    assert TOPIC_RESULTS == "/task_orchestrator/results"
    assert TOPIC_EVENTS == "/task_orchestrator/events"
    assert TOPIC_FEEDBACK == "/task_orchestrator/feedback"
    assert SERVICE_LIST_TASKS == "/task_orchestrator/list_tasks"
    assert SERVICE_GET_TASK == "/task_orchestrator/get_task"
    assert SERVICE_CANCEL_TASKS == "/task_orchestrator/cancel_tasks"
    assert SERVICE_PAUSE_TASKS == "/task_orchestrator/pause_tasks"
    assert SERVICE_RESUME_TASKS == "/task_orchestrator/resume_tasks"


def test_public_message_metadata_contract():
    for message_type in (
        ExecuteTaskV1.Goal,
        ExecuteTaskV1.Result,
        ExecuteTaskV1.Feedback,
        ActiveTaskArrayV1,
        ActiveTaskV1,
        SubtaskGoalV1,
        SubtaskResultV1,
        TaskEventV1,
        TaskFeedbackV1,
        TaskRecordV1,
        TaskResultV1,
        TaskSpecV1,
    ):
        _assert_metadata_fields(message_type)


def test_execute_task_action_contract():
    goal = ExecuteTaskV1.Goal()
    goal.api_version = "v1beta1"
    goal.task_name = "example/task"
    goal.task_data_json = "{}"
    goal.delay_sec = 1.0
    goal.timeout_sec = 5.0
    goal.queue_on_conflict = True
    goal.robot_id = "robot-1"
    goal.fleet_id = "fleet-1"
    goal.trace_id = "trace-1"

    assert goal.api_version == "v1beta1"
    assert goal.task_name == "example/task"
    assert goal.task_data_json == "{}"
    assert goal.delay_sec == 1.0
    assert goal.timeout_sec == 5.0
    assert goal.queue_on_conflict is True
    assert goal.robot_id == "robot-1"
    assert goal.fleet_id == "fleet-1"
    assert goal.trace_id == "trace-1"


def test_status_and_error_constants():
    assert TaskStatusV1.RECEIVED == "RECEIVED"
    assert TaskStatusV1.ERROR == "ERROR"
    assert ErrorCodeV1.DEADLINE_EXCEEDED == "DEADLINE_EXCEEDED"
    assert ErrorCodeV1.UNSUPPORTED == "UNSUPPORTED"


def test_event_message_contract():
    event = TaskEventV1()
    event.event_type = "task.started"
    event.data_json = "{}"

    assert event.event_type == "task.started"
    assert event.data_json == "{}"


def test_list_tasks_service_contract():
    request = ListTasksV1.Request()
    response = ListTasksV1.Response()

    request.include_system_tasks = True

    assert request.include_system_tasks is True
    assert response.tasks == []


def test_list_events_service_contract():
    request = ListEventsV1.Request()
    response = ListEventsV1.Response()

    request.event_type = "task.completed"
    request.status = TaskStatusV1.DONE
    request.limit = 10

    assert request.event_type == "task.completed"
    assert request.status == TaskStatusV1.DONE
    assert request.limit == 10
    assert response.events == []


def test_list_task_records_service_contract():
    request = ListTaskRecordsV1.Request()
    response = ListTaskRecordsV1.Response()

    request.status = TaskStatusV1.DONE
    request.limit = 10

    assert request.status == TaskStatusV1.DONE
    assert request.limit == 10
    assert response.records == []
