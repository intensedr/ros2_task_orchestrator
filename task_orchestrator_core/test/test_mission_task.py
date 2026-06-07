import json

from task_orchestrator_core.system_tasks.mission import MissionTaskParser, MissionTaskValidationError
from task_orchestrator_msgs.msg import TaskStatusV1


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
    assert mission.subtasks[0].timeout_sec == 3.0


def test_mission_task_parser_rejects_missing_task_name():
    parser = MissionTaskParser()

    try:
        parser.parse('{"subtasks": [{}]}', default_mission_id="mission-1")
    except MissionTaskValidationError as exc:
        assert "task_name" in str(exc)
    else:
        raise AssertionError("expected MissionTaskValidationError")


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
