import json

from task_orchestrator_core.system_tasks.mission import MissionTaskParser, MissionTaskValidationError
from task_orchestrator_msgs.msg import TaskStatusV1


def _assert_parse_error(payload, expected_fragment):
    parser = MissionTaskParser()
    try:
        parser.parse(json.dumps(payload), default_mission_id="fallback")
    except MissionTaskValidationError as exc:
        assert expected_fragment in str(exc)
    else:
        raise AssertionError("expected MissionTaskValidationError")


def test_mission_task_parser_accepts_linear_subtasks():
    parser = MissionTaskParser()

    mission = parser.parse(
        json.dumps(
            {
                "mission_id": "mission-1",
                "subtasks": [
                    {
                        "subtask_id": "wait-1",
                        "task_name": "system/wait",
                        "task_data_json": {"duration_sec": 0},
                        "allow_skipping": True,
                        "max_attempts": 2,
                        "retry_backoff_sec": 0.5,
                        "retry_backoff_type": "exponential",
                        "retry_max_backoff_sec": 1.0,
                        "retry_error_codes": ["TASK_TIMEOUT"],
                        "timeout_sec": 3.0,
                    }
                ],
            }
        ),
        default_mission_id="fallback",
    )

    assert mission.mission_id == "mission-1"
    assert len(mission.subtasks) == 1
    assert mission.subtasks[0].task_id == "mission-1/wait-1"
    assert mission.subtasks[0].task_data_json == '{"duration_sec": 0}'
    assert mission.subtasks[0].allow_skipping is True
    assert mission.subtasks[0].max_attempts == 2
    assert mission.subtasks[0].retry_backoff_sec == 0.5
    assert mission.subtasks[0].retry_backoff_type == "exponential"
    assert mission.subtasks[0].retry_max_backoff_sec == 1.0
    assert mission.subtasks[0].retry_error_codes == ("TASK_TIMEOUT",)
    assert mission.subtasks[0].timeout_sec == 3.0


def test_mission_task_parser_accepts_graph_retry_policy_and_condition():
    parser = MissionTaskParser()

    mission = parser.parse(
        json.dumps(
            {
                "mission_id": "mission-graph",
                "subtasks": [
                    {
                        "subtask_id": "inspect",
                        "task_name": "system/wait",
                        "task_data_json": {"duration_sec": 0},
                        "retry_policy": {
                            "max_attempts": 3,
                            "backoff_sec": 0.5,
                            "backoff_type": "exponential",
                            "max_backoff_sec": 2.0,
                            "error_codes": ["TASK_TIMEOUT"],
                        },
                        "condition": {"action": "retry"},
                    },
                    {
                        "subtask_id": "dock",
                        "task_name": "system/wait",
                        "depends_on": ["inspect"],
                        "condition_json": {"action": "skip"},
                    },
                ],
            }
        ),
        default_mission_id="fallback",
    )

    inspect = mission.subtasks[0]
    dock = mission.subtasks[1]
    assert inspect.max_attempts == 3
    assert inspect.retry_backoff_sec == 0.5
    assert inspect.retry_backoff_type == "exponential"
    assert inspect.retry_max_backoff_sec == 2.0
    assert inspect.retry_error_codes == ("TASK_TIMEOUT",)
    assert parser.condition_action(inspect) == "retry"
    assert dock.depends_on == ("inspect",)
    assert parser.condition_action(dock) == "skip"


def test_mission_task_parser_rejects_missing_task_name():
    parser = MissionTaskParser()

    try:
        parser.parse('{"subtasks": [{}]}', default_mission_id="mission-1")
    except MissionTaskValidationError as exc:
        assert "task_name" in str(exc)
    else:
        raise AssertionError("expected MissionTaskValidationError")


def test_mission_task_parser_rejects_duplicate_subtask_ids():
    _assert_parse_error(
        {
            "subtasks": [
                {"subtask_id": "same", "task_name": "system/wait"},
                {"subtask_id": "same", "task_name": "system/wait"},
            ]
        },
        "duplicate subtask_id",
    )


def test_mission_task_parser_rejects_unknown_dependency():
    _assert_parse_error(
        {
            "subtasks": [
                {
                    "subtask_id": "wait-1",
                    "task_name": "system/wait",
                    "depends_on": ["missing"],
                }
            ]
        },
        "depends on unknown subtasks",
    )


def test_mission_task_parser_rejects_dependency_cycle():
    _assert_parse_error(
        {
            "subtasks": [
                {"subtask_id": "a", "task_name": "system/wait", "depends_on": ["b"]},
                {"subtask_id": "b", "task_name": "system/wait", "depends_on": ["a"]},
            ]
        },
        "dependency cycle",
    )


def test_mission_task_parser_rejects_invalid_condition_action():
    _assert_parse_error(
        {
            "subtasks": [
                {
                    "subtask_id": "wait-1",
                    "task_name": "system/wait",
                    "condition": {"action": "hold"},
                }
            ]
        },
        "condition_json.action",
    )


def test_mission_result_json_is_stable():
    parser = MissionTaskParser()

    result_json = parser.result_json(
        mission_id="mission-1",
        status=TaskStatusV1.DONE,
        mission_results=[],
    )

    assert json.loads(result_json) == {
        "mission_id": "mission-1",
        "status": "DONE",
        "error_code": "",
        "error_message": "",
        "mission_results": [],
    }


def test_mission_result_json_includes_structured_error_when_failed():
    parser = MissionTaskParser()

    result_json = parser.result_json(
        mission_id="mission-1",
        status=TaskStatusV1.ERROR,
        mission_results=[],
        error_code="UNKNOWN_TASK",
        error_message="Unknown task_name: missing/task",
    )

    assert json.loads(result_json)["error"] == {
        "code": "UNKNOWN_TASK",
        "message": "Unknown task_name: missing/task",
        "details": {},
    }
