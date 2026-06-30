import json
from pathlib import Path
import xml.etree.ElementTree as ET

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


def test_agent_connection_example_mission_payload_parses():
    mission_path = _workspace_root() / "task_orchestrator_examples" / "missions" / "agent_wait_mission.json"
    parser = MissionTaskParser()

    mission = parser.parse(mission_path.read_text(encoding="utf-8"), default_mission_id="fallback")
    wait_payload = json.loads(mission.subtasks[0].task_data_json)

    assert mission.mission_id == "agent-demo-wait"
    assert [subtask.task_name for subtask in mission.subtasks] == ["system/wait"]
    assert wait_payload["duration_sec"] == 1.0


def test_agent_connection_helper_is_installed_and_uses_public_mission_api():
    package_path = _workspace_root() / "task_orchestrator_examples"
    cmake_text = (package_path / "CMakeLists.txt").read_text(encoding="utf-8")
    package_xml = ET.parse(package_path / "package.xml").getroot()
    script_path = package_path / "scripts" / "demo_agent_client"
    script_text = script_path.read_text(encoding="utf-8")
    runtime_deps = {element.text for element in package_xml.findall("exec_depend")}

    assert "PROGRAMS scripts/demo_agent_client" in cmake_text
    assert script_path.stat().st_mode & 0o111
    assert {"ament_index_python", "rclpy", "task_orchestrator_msgs"}.issubset(runtime_deps)
    for service_name in (
        "RegisterAgentV1",
        "ListAgentsV1",
        "ValidateMissionV1",
        "ClaimMissionV1",
        "SubmitMissionV1",
        "GetMissionStateV1",
        "CancelMissionV1",
        "PauseMissionV1",
        "ResumeMissionV1",
        "RetryMissionV1",
        "ReleaseMissionV1",
    ):
        assert service_name in script_text
    assert "task_orchestrator_sim_" not in script_text


def test_agent_connection_docs_are_in_nav():
    docs_text = (_workspace_root() / "docs" / "examples" / "agent_connection.md").read_text(encoding="utf-8")
    mkdocs_text = (_workspace_root() / "mkdocs.yml").read_text(encoding="utf-8")

    assert "examples/agent_connection.md" in mkdocs_text
    assert "demo_agent_client run" in docs_text
    assert "/task_orchestrator/submit_mission" in docs_text


def test_sim_nav2_task_config_loads():
    config_path = _workspace_root() / "task_orchestrator_sim_nav2" / "config" / "nav2_tasks.yaml"

    registry = TaskRegistry.from_yaml_file(config_path)

    task = registry.get("navigation/navigate_to_pose")
    assert task is not None
    assert task.topic == "/navigate_to_pose"
    assert task.msg_interface == "nav2_msgs/action/NavigateToPose"
    assert task.task_server_type == "action"
    assert task.blocking is True
    assert task.cancel_on_stop is True
    assert task.reentrant is False
    assert task.resources == ("base",)
    assert task.task_group == "navigation"
    assert task.capability_tags == ("localization", "motion")
    assert task.zone_locked is True


def test_sim_nav2_mission_payloads_parse():
    parser = MissionTaskParser()

    navigate_mission = parser.parse(
        (_workspace_root() / "task_orchestrator_sim_nav2" / "missions" / "navigate_to_pose.json").read_text(
            encoding="utf-8"
        ),
        default_mission_id="fallback",
    )
    route_mission = parser.parse(
        (_workspace_root() / "task_orchestrator_sim_nav2" / "missions" / "route_mission.json").read_text(
            encoding="utf-8"
        ),
        default_mission_id="fallback",
    )

    assert navigate_mission.mission_id == "sim-nav2-navigate"
    assert [subtask.task_name for subtask in navigate_mission.subtasks] == ["navigation/navigate_to_pose"]
    assert route_mission.mission_id == "sim-nav2-route"
    assert [subtask.subtask_id for subtask in route_mission.subtasks] == [
        "waypoint-1",
        "waypoint-2",
        "waypoint-3",
    ]
    assert route_mission.subtasks[1].depends_on == ("waypoint-1",)
    assert route_mission.subtasks[2].depends_on == ("waypoint-2",)


def test_sim_nav2_request_examples_are_structured_json():
    queued_request_path = (
        _workspace_root() / "task_orchestrator_sim_nav2" / "requests" / "queued_navigation_goal.json"
    )
    cancel_request_path = _workspace_root() / "task_orchestrator_sim_nav2" / "requests" / "cancel_navigation.json"

    queued_request = json.loads(queued_request_path.read_text(encoding="utf-8"))
    cancel_request = json.loads(cancel_request_path.read_text(encoding="utf-8"))

    assert queued_request["api_version"] == "v1"
    assert queued_request["task_name"] == "navigation/navigate_to_pose"
    assert queued_request["queue_on_conflict"] is True
    assert queued_request["task_data_json"]["pose"]["header"]["frame_id"] == "map"
    assert cancel_request["task_ids"] == ["sim-nav2-queued-navigation"]


def test_sim_nav2_launch_isolates_gazebo_and_exposes_initial_pose():
    launch_text = (
        _workspace_root() / "task_orchestrator_sim_nav2" / "launch" / "sim_nav2.launch.py"
    ).read_text(encoding="utf-8")

    assert 'SetEnvironmentVariable("GZ_PARTITION", gz_partition)' in launch_text
    assert '"x_pose": x_pose' in launch_text
    assert '"y_pose": y_pose' in launch_text
    assert '"yaw": yaw' in launch_text


def test_sim_nav2_route_mission_helper_is_installed_and_uses_public_mission_api():
    package_path = _workspace_root() / "task_orchestrator_sim_nav2"
    cmake_text = (package_path / "CMakeLists.txt").read_text(encoding="utf-8")
    script_text = (package_path / "scripts" / "submit_route_mission").read_text(encoding="utf-8")

    assert "PROGRAMS scripts/submit_route_mission" in cmake_text
    assert "RegisterAgentV1" in script_text
    assert "SubmitMissionV1" in script_text
    assert "GetMissionStateV1" in script_text
    assert "/navigate_to_pose" not in script_text


def test_sim_drone_task_config_loads():
    config_path = _workspace_root() / "task_orchestrator_sim_drone" / "config" / "drone_tasks.yaml"

    registry = TaskRegistry.from_yaml_file(config_path)

    takeoff = registry.get("drone/takeoff")
    waypoint = registry.get("drone/go_to_waypoint")
    land = registry.get("drone/land")

    assert takeoff is not None
    assert takeoff.topic == "/drone/takeoff"
    assert takeoff.msg_interface == "task_orchestrator_sim_drone/action/Takeoff"
    assert takeoff.task_server_type == "action"
    assert takeoff.resources == ("airframe",)
    assert takeoff.task_group == "drone_flight"
    assert takeoff.capability_tags == ("flight", "localization")
    assert waypoint is not None
    assert waypoint.zone_locked is True
    assert land is not None
    assert land.topic == "/drone/land"


def test_sim_drone_mission_payloads_parse():
    parser = MissionTaskParser()

    simple_mission = parser.parse(
        (_workspace_root() / "task_orchestrator_sim_drone" / "missions" / "takeoff_waypoint_land.json").read_text(
            encoding="utf-8"
        ),
        default_mission_id="fallback",
    )
    inspection_mission = parser.parse(
        (_workspace_root() / "task_orchestrator_sim_drone" / "missions" / "inspection_route.json").read_text(
            encoding="utf-8"
        ),
        default_mission_id="fallback",
    )
    timeout_mission = parser.parse(
        (_workspace_root() / "task_orchestrator_sim_drone" / "missions" / "timeout_mission.json").read_text(
            encoding="utf-8"
        ),
        default_mission_id="fallback",
    )

    assert simple_mission.mission_id == "sim-drone-takeoff-waypoint-land"
    assert [subtask.task_name for subtask in simple_mission.subtasks] == [
        "drone/takeoff",
        "drone/go_to_waypoint",
        "drone/hover",
        "drone/land",
    ]
    assert inspection_mission.subtasks[3].depends_on == ("inspect-a",)
    assert timeout_mission.subtasks[0].timeout_sec == 1.0


def test_sim_drone_launch_defaults_to_local_gazebo_model():
    launch_text = (
        _workspace_root() / "task_orchestrator_sim_drone" / "launch" / "sim_drone.launch.py"
    ).read_text(encoding="utf-8")

    assert 'LaunchConfiguration("use_gazebo")' in launch_text
    assert 'default_value="true"' in launch_text
    assert '"gz_sim.launch.py"' in launch_text
    assert "SetEnvironmentVariable" in launch_text
    assert "GZ_SIM_RESOURCE_PATH" in launch_text
    assert "use_gazebo:=true requires ros_gz_sim" in launch_text


def test_sim_drone_gazebo_world_includes_local_iris_model():
    world_path = _workspace_root() / "task_orchestrator_sim_drone" / "worlds" / "drone_demo_world.sdf"

    sdf = ET.parse(world_path).getroot()
    world = sdf.find("world")

    assert sdf.tag == "sdf"
    assert sdf.attrib["version"] == "1.11"
    assert world is not None
    assert world.attrib["name"] == "drone_demo"
    include = world.find("include")
    assert include is not None
    assert include.findtext("name") == "iris_reference"
    assert include.findtext("uri") == "model://iris_with_standoffs"
    assert include.findtext("pose") == "0 0 0.4 0 0 0"
    model_names = {model.attrib["name"] for model in world.findall("model")}
    assert model_names == {"demo_ground"}


def test_sim_drone_iris_model_assets_are_local_and_attributed():
    model_path = _workspace_root() / "task_orchestrator_sim_drone" / "models" / "iris_with_standoffs"
    model_sdf = ET.parse(model_path / "model.sdf").getroot()
    model_urdf = ET.parse(model_path / "model.urdf").getroot()
    model_config = ET.parse(model_path / "model.config").getroot()
    attribution = (model_path / "ATTRIBUTION.md").read_text(encoding="utf-8")

    assert (model_path / "meshes" / "iris.dae").is_file()
    assert (model_path / "meshes" / "iris_prop_ccw.dae").is_file()
    assert (model_path / "meshes" / "iris_prop_cw.dae").is_file()
    assert model_config.findtext("sdf") == "model.sdf"
    assert model_sdf.tag == "sdf"
    assert model_sdf.find("model").attrib["name"] == "iris"
    assert model_urdf.tag == "robot"
    assert model_urdf.attrib["name"] == "iris_with_standoffs"
    assert model_sdf.find(".//box") is not None
    assert model_sdf.find(".//cylinder") is not None
    assert model_urdf.find(".//box") is not None
    assert model_urdf.find(".//cylinder") is not None
    assert not model_sdf.findall(".//mesh")
    assert not model_urdf.findall(".//mesh")
    assert "https://fuel.gazebosim.org/1.0/OpenRobotics/models/Iris%20with%20Standoffs" in attribution
    assert "https://github.com/PX4/sitl_gazebo/tree/master/models" in attribution


def test_sim_drone_helper_is_installed_and_uses_public_mission_api():
    package_path = _workspace_root() / "task_orchestrator_sim_drone"
    cmake_text = (package_path / "CMakeLists.txt").read_text(encoding="utf-8")
    script_text = (package_path / "scripts" / "submit_inspection_mission").read_text(encoding="utf-8")

    assert "scripts/fake_drone_server" in cmake_text
    assert "scripts/submit_inspection_mission" in cmake_text
    assert "models requests worlds" in cmake_text
    assert "RegisterAgentV1" in script_text
    assert "SubmitMissionV1" in script_text
    assert "GetMissionStateV1" in script_text
    assert "/drone/go_to_waypoint" not in script_text
