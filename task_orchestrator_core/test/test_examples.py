import json
from pathlib import Path

from task_orchestrator_core.registry import TaskRegistry
from task_orchestrator_core.system_tasks.mission import MissionTaskParser


def _workspace_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_nav2_example_task_config_loads():
    config_path = _workspace_root() / "task_orchestrator_examples" / "config" / "nav2_tasks.yaml"

    registry = TaskRegistry.from_yaml_file(config_path)

    task = registry.get("nav2/navigate_to_pose")
    assert task is not None
    assert task.topic == "/navigate_to_pose"
    assert task.msg_interface == "nav2_msgs/action/NavigateToPose"
    assert task.task_server_type == "action"
    assert task.blocking is True
    assert task.cancel_on_stop is True
    assert task.cancel_reported_as_success is False
    assert task.reentrant is False
    assert task.resources == ("base", "map")
    assert task.tags == ("nav2", "navigation", "motion")


def test_nav2_example_mission_payload_parses():
    mission_path = _workspace_root() / "task_orchestrator_examples" / "missions" / "nav2_wait_mission.json"
    parser = MissionTaskParser()

    mission = parser.parse(mission_path.read_text(encoding="utf-8"), default_mission_id="fallback")
    navigate_payload = json.loads(mission.subtasks[0].task_data_json)

    assert mission.mission_id == "nav2-demo-mission"
    assert [subtask.task_name for subtask in mission.subtasks] == ["nav2/navigate_to_pose", "system/wait"]
    assert navigate_payload["pose"]["header"]["frame_id"] == "map"
    assert navigate_payload["pose"]["pose"]["position"]["x"] == 1.0
