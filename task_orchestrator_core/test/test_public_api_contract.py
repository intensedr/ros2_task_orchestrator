from task_orchestrator_msgs.action import ExecuteTaskV1
from task_orchestrator_msgs.msg import ErrorCodeV1, TaskEventV1, TaskStatusV1
from task_orchestrator_msgs.srv import ListEventsV1, ListTaskRecordsV1, ListTasksV1


def test_execute_task_action_contract():
    goal = ExecuteTaskV1.Goal()
    goal.api_version = "v1alpha1"
    goal.task_name = "example/task"
    goal.task_data_json = "{}"

    assert goal.api_version == "v1alpha1"
    assert goal.task_name == "example/task"
    assert goal.task_data_json == "{}"


def test_status_and_error_constants():
    assert TaskStatusV1.RECEIVED == "RECEIVED"
    assert TaskStatusV1.ERROR == "ERROR"
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
    request.current_status = TaskStatusV1.DONE
    request.limit = 10

    assert request.event_type == "task.completed"
    assert request.current_status == TaskStatusV1.DONE
    assert request.limit == 10
    assert response.events == []


def test_list_task_records_service_contract():
    request = ListTaskRecordsV1.Request()
    response = ListTaskRecordsV1.Response()

    request.task_status = TaskStatusV1.DONE
    request.limit = 10

    assert request.task_status == TaskStatusV1.DONE
    assert request.limit == 10
    assert response.records == []
