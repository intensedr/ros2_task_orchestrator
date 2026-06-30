import os

from ament_index_python.packages import PackageNotFoundError, get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, OpaqueFunction, SetEnvironmentVariable
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import EnvironmentVariable, LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def _is_true(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "on"}


def _gazebo_actions(context, *_args, **_kwargs) -> list[IncludeLaunchDescription]:
    if not _is_true(LaunchConfiguration("use_gazebo").perform(context)):
        return []

    try:
        ros_gz_sim_share = get_package_share_directory("ros_gz_sim")
    except PackageNotFoundError as exc:
        raise RuntimeError(
            "use_gazebo:=true requires ros_gz_sim. "
            "Install it with: sudo apt install ros-$ROS_DISTRO-ros-gz-sim"
        ) from exc

    gazebo_world = LaunchConfiguration("gazebo_world")
    return [
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(os.path.join(ros_gz_sim_share, "launch", "gz_sim.launch.py")),
            launch_arguments={"gz_args": ["-r -v4 ", gazebo_world]}.items(),
        )
    ]


def generate_launch_description() -> LaunchDescription:
    task_config = LaunchConfiguration("task_config")
    mission_templates_path = LaunchConfiguration("mission_templates_path")
    frame_id = LaunchConfiguration("frame_id")
    base_frame_id = LaunchConfiguration("base_frame_id")
    initial_x = LaunchConfiguration("initial_x")
    initial_y = LaunchConfiguration("initial_y")
    initial_z = LaunchConfiguration("initial_z")
    initial_yaw = LaunchConfiguration("initial_yaw")
    update_rate_hz = LaunchConfiguration("update_rate_hz")
    gazebo_pose_sync_rate_hz = LaunchConfiguration("gazebo_pose_sync_rate_hz")

    package_share = get_package_share_directory("task_orchestrator_sim_drone")
    default_task_config = os.path.join(package_share, "config", "drone_tasks.yaml")
    default_mission_templates_path = os.path.join(package_share, "missions")
    default_gazebo_world = os.path.join(package_share, "worlds", "drone_demo_world.sdf")
    gazebo_model_path = os.path.join(package_share, "models")

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "task_config",
                default_value=default_task_config,
                description="Task registry YAML file for the drone simulation.",
            ),
            DeclareLaunchArgument(
                "mission_templates_path",
                default_value=default_mission_templates_path,
                description="Mission template directory for the drone simulation.",
            ),
            DeclareLaunchArgument("frame_id", default_value="map"),
            DeclareLaunchArgument("base_frame_id", default_value="base_root"),
            DeclareLaunchArgument(
                "use_gazebo",
                default_value="true",
                description="Start Gazebo Sim with the local Iris drone model.",
            ),
            DeclareLaunchArgument(
                "gazebo_world",
                default_value=default_gazebo_world,
                description="Gazebo SDF world file used when use_gazebo is true.",
            ),
            DeclareLaunchArgument("initial_x", default_value="0.0"),
            DeclareLaunchArgument("initial_y", default_value="0.0"),
            DeclareLaunchArgument("initial_z", default_value="0.10"),
            DeclareLaunchArgument("initial_yaw", default_value="0.0"),
            DeclareLaunchArgument("update_rate_hz", default_value="20.0"),
            DeclareLaunchArgument(
                "gazebo_pose_sync_rate_hz",
                default_value="10.0",
                description="Rate used by the fake drone server to move the Gazebo model.",
            ),
            SetEnvironmentVariable(
                "GZ_SIM_RESOURCE_PATH",
                [gazebo_model_path, os.pathsep, EnvironmentVariable("GZ_SIM_RESOURCE_PATH", default_value="")],
            ),
            Node(
                package="task_orchestrator_sim_drone",
                executable="fake_drone_server",
                name="fake_drone_server",
                output="screen",
                parameters=[
                    {
                        "frame_id": frame_id,
                        "base_frame_id": base_frame_id,
                        "initial_x": initial_x,
                        "initial_y": initial_y,
                        "initial_z": initial_z,
                        "initial_yaw": initial_yaw,
                        "update_rate_hz": update_rate_hz,
                        "gazebo_pose_sync_enabled": ParameterValue(
                            LaunchConfiguration("use_gazebo"),
                            value_type=bool,
                        ),
                        "gazebo_world_name": "drone_demo",
                        "gazebo_model_name": "iris_reference",
                        "gazebo_pose_sync_rate_hz": gazebo_pose_sync_rate_hz,
                    }
                ],
            ),
            OpaqueFunction(function=_gazebo_actions),
            Node(
                package="task_orchestrator_core",
                executable="task_orchestrator_node",
                name="task_orchestrator",
                output="screen",
                parameters=[
                    {
                        "tasks_config_path": task_config,
                        "mission_templates_path": mission_templates_path,
                        "admission.available_capability_tags": ["flight", "localization"],
                    }
                ],
            ),
        ]
    )
