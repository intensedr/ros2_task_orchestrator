from task_orchestrator_core.constants import (
    ACTION_EXECUTE_TASK,
    SERVICE_CANCEL_MISSION,
    SERVICE_CANCEL_TASKS,
    SERVICE_CLAIM_MISSION,
    SERVICE_GET_MISSION_STATE,
    SERVICE_GET_TASK,
    SERVICE_LIST_AGENTS,
    SERVICE_LIST_TASKS,
    SERVICE_PAUSE_MISSION,
    SERVICE_PAUSE_TASKS,
    SERVICE_REGISTER_AGENT,
    SERVICE_RELEASE_MISSION,
    SERVICE_RESUME_MISSION,
    SERVICE_RESUME_TASKS,
    SERVICE_RETRY_MISSION,
    SERVICE_SUBMIT_MISSION,
    SERVICE_VALIDATE_MISSION,
    SERVICE_VALIDATE_TASK,
    TOPIC_ACTIVE_TASKS,
    TOPIC_EVENTS,
    TOPIC_FEEDBACK,
    TOPIC_RESULTS,
)
from task_orchestrator_msgs.action import ExecuteTaskV1
from task_orchestrator_msgs.msg import (
    ActiveTaskArrayV1,
    ActiveTaskV1,
    AgentRecordV1,
    AgentStatusV1,
    ErrorCodeV1,
    MissionLeaseStatusV1,
    MissionLeaseV1,
    SubtaskGoalV1,
    SubtaskResultV1,
    TaskEventV1,
    TaskFeedbackV1,
    TaskRecordV1,
    TaskResultV1,
    TaskSpecV1,
    TaskStatusV1,
)
from task_orchestrator_msgs.srv import (
    ClaimMissionV1,
    ListEventsV1,
    ListTaskRecordsV1,
    ListTasksV1,
    RegisterAgentV1,
    SubmitMissionV1,
    ValidateMissionV1,
    ValidateTaskV1,
)


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
    assert SERVICE_VALIDATE_TASK == "/task_orchestrator/validate_task"
    assert SERVICE_REGISTER_AGENT == "/task_orchestrator/register_agent"
    assert SERVICE_LIST_AGENTS == "/task_orchestrator/list_agents"
    assert SERVICE_CLAIM_MISSION == "/task_orchestrator/claim_mission"
    assert SERVICE_RELEASE_MISSION == "/task_orchestrator/release_mission"
    assert SERVICE_VALIDATE_MISSION == "/task_orchestrator/validate_mission"
    assert SERVICE_SUBMIT_MISSION == "/task_orchestrator/submit_mission"
    assert SERVICE_CANCEL_MISSION == "/task_orchestrator/cancel_mission"
    assert SERVICE_PAUSE_MISSION == "/task_orchestrator/pause_mission"
    assert SERVICE_RESUME_MISSION == "/task_orchestrator/resume_mission"
    assert SERVICE_RETRY_MISSION == "/task_orchestrator/retry_mission"
    assert SERVICE_GET_MISSION_STATE == "/task_orchestrator/get_mission_state"


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
    goal.api_version = "v1"
    goal.task_name = "example/task"
    goal.task_data_json = "{}"
    goal.delay_sec = 1.0
    goal.timeout_sec = 5.0
    goal.queue_on_conflict = True
    goal.robot_id = "robot-1"
    goal.fleet_id = "fleet-1"
    goal.trace_id = "trace-1"

    assert goal.api_version == "v1"
    assert goal.task_name == "example/task"
    assert goal.task_data_json == "{}"
    assert goal.delay_sec == 1.0
    assert goal.timeout_sec == 5.0
    assert goal.queue_on_conflict is True
    assert goal.robot_id == "robot-1"
    assert goal.fleet_id == "fleet-1"
    assert goal.trace_id == "trace-1"


def test_agent_record_message_contract():
    agent = AgentRecordV1()
    fields = AgentRecordV1.get_fields_and_field_types()

    agent.api_version = "v1"
    agent.agent_id = "agent-1"
    agent.heartbeat_status = AgentStatusV1.ONLINE
    agent.capabilities = ["mission.compose"]
    agent.current_mission_id = "mission-1"

    assert "last_heartbeat_at" in fields
    assert "stale_at" in fields
    assert agent.agent_id == "agent-1"
    assert agent.heartbeat_status == "ONLINE"
    assert agent.current_mission_id == "mission-1"


def test_mission_lease_message_contract():
    lease = MissionLeaseV1()
    fields = MissionLeaseV1.get_fields_and_field_types()

    lease.api_version = "v1"
    lease.mission_id = "mission-1"
    lease.agent_id = "agent-1"
    lease.lease_token = "lease-token"
    lease.lease_status = MissionLeaseStatusV1.ACTIVE

    assert "lease_expires_at" in fields
    assert lease.mission_id == "mission-1"
    assert lease.lease_status == "ACTIVE"


def test_status_and_error_constants():
    assert TaskStatusV1.RECEIVED == "RECEIVED"
    assert TaskStatusV1.PENDING == "PENDING"
    assert TaskStatusV1.ERROR == "ERROR"
    assert AgentStatusV1.ONLINE == "ONLINE"
    assert MissionLeaseStatusV1.ACTIVE == "ACTIVE"
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
    task = TaskSpecV1()

    request.include_system_tasks = True
    task.zone_locked = True
    task.min_battery_percent = 30.0
    task.allowed_robot_modes = ["AUTO"]
    task.requires_localization = True
    task.supports_pause = True
    task.supports_resume = True

    assert request.include_system_tasks is True
    assert task.zone_locked is True
    assert task.min_battery_percent == 30.0
    assert task.allowed_robot_modes == ["AUTO"]
    assert task.requires_localization is True
    assert task.supports_pause is True
    assert task.supports_resume is True
    assert response.tasks == []


def test_list_events_service_contract():
    request = ListEventsV1.Request()
    response = ListEventsV1.Response()

    request.event_type = "task.completed"
    request.status = TaskStatusV1.DONE
    request.robot_id = "robot-1"
    request.trace_id = "trace-1"
    request.idempotency_key = "idem-1"
    request.limit = 10

    assert request.event_type == "task.completed"
    assert request.status == TaskStatusV1.DONE
    assert request.robot_id == "robot-1"
    assert request.trace_id == "trace-1"
    assert request.idempotency_key == "idem-1"
    assert request.limit == 10
    assert response.events == []


def test_list_task_records_service_contract():
    request = ListTaskRecordsV1.Request()
    response = ListTaskRecordsV1.Response()

    request.status = TaskStatusV1.DONE
    request.robot_id = "robot-1"
    request.trace_id = "trace-1"
    request.idempotency_key = "idem-1"
    request.limit = 10

    assert request.status == TaskStatusV1.DONE
    assert request.robot_id == "robot-1"
    assert request.trace_id == "trace-1"
    assert request.idempotency_key == "idem-1"
    assert request.limit == 10
    assert response.records == []


def test_validate_task_service_contract():
    request = ValidateTaskV1.Request()
    response = ValidateTaskV1.Response()

    request.task_id = "validation-task"
    request.task_name = "system/wait"
    request.task_data_json = '{"duration_sec": 0}'
    request.include_schema = True
    response.valid = True
    response.normalized_task_data_json = '{"duration_sec": 0}'
    response.schema_json = "{}"

    assert request.task_name == "system/wait"
    assert request.include_schema is True
    assert response.valid is True
    assert response.normalized_task_data_json == '{"duration_sec": 0}'


def test_agent_registry_service_contract():
    register_request = RegisterAgentV1.Request()
    register_response = RegisterAgentV1.Response()

    register_request.agent_id = "agent-1"
    register_request.display_name = "Planner"
    register_request.agent_type = "planner"
    register_request.capabilities = ["mission.compose"]
    register_request.heartbeat_timeout_sec = 30.0
    register_request.metadata_json = '{"runtime": "external"}'
    register_response.success = True
    register_response.agent.agent_id = "agent-1"
    register_response.agent.heartbeat_status = AgentStatusV1.ONLINE

    assert register_request.agent_id == "agent-1"
    assert register_request.capabilities == ["mission.compose"]
    assert register_response.success is True
    assert register_response.agent.heartbeat_status == "ONLINE"


def test_mission_lease_service_contract():
    claim_request = ClaimMissionV1.Request()
    claim_response = ClaimMissionV1.Response()

    claim_request.agent_id = "agent-1"
    claim_request.mission_id = "mission-1"
    claim_request.lease_duration_sec = 120.0
    claim_response.success = True
    claim_response.lease.mission_id = "mission-1"
    claim_response.lease.agent_id = "agent-1"
    claim_response.lease.lease_status = MissionLeaseStatusV1.ACTIVE

    assert claim_request.mission_id == "mission-1"
    assert claim_request.lease_duration_sec == 120.0
    assert claim_response.lease.lease_status == "ACTIVE"


def test_agent_mission_command_service_contract():
    validate_request = ValidateMissionV1.Request()
    submit_request = SubmitMissionV1.Request()
    submit_response = SubmitMissionV1.Response()

    validate_request.agent_id = "agent-1"
    validate_request.mission_id = "mission-1"
    validate_request.mission_json = '{"mission_id": "mission-1", "subtasks": []}'
    validate_request.include_schema = True
    submit_request.agent_id = "agent-1"
    submit_request.mission_id = "mission-1"
    submit_request.mission_json = validate_request.mission_json
    submit_request.lease_token = "lease-token"
    submit_request.queue_on_conflict = True
    submit_response.success = True
    submit_response.task_id = "mission-task-1"

    assert validate_request.include_schema is True
    assert submit_request.queue_on_conflict is True
    assert submit_response.task_id == "mission-task-1"


def test_task_record_scheduling_contract():
    record = TaskRecordV1()
    record.delay_sec = 1.5
    record.timeout_sec = 5.0
    record.queue_on_conflict = True

    fields = TaskRecordV1.get_fields_and_field_types()

    assert "scheduled_at" in fields
    assert "delay_sec" in fields
    assert "deadline_at" in fields
    assert "timeout_sec" in fields
    assert "queue_on_conflict" in fields
    assert record.delay_sec == 1.5
    assert record.timeout_sec == 5.0
    assert record.queue_on_conflict is True
