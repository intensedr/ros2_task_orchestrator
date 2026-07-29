import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    package_share = get_package_share_directory("task_orchestrator_ardupilot_bridge")
    task_config = os.path.join(package_share, "config", "ardupilot_tasks.yaml")
    bridge_config = os.path.join(package_share, "config", "ardupilot_bridge.yaml")
    mission_templates_path = os.path.join(package_share, "missions")

    connection_url = LaunchConfiguration("connection_url")
    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "connection_url",
                default_value="tcp:127.0.0.1:5760",
                description="pymavlink connection string (TCP/UDP/serial).",
            ),
            Node(
                package="task_orchestrator_core",
                executable="task_orchestrator_node",
                name="task_orchestrator",
                output="screen",
                parameters=[
                    {
                        "tasks_config_path": task_config,
                        "mission_templates_path": mission_templates_path,
                        "admission.battery_percent": 0.0,
                        "admission.localization_ok": False,
                        "admission.emergency_stop_active": True,
                        "admission.available_capability_tags": ["disconnected"],
                    }
                ],
            ),
            Node(
                package="task_orchestrator_ardupilot_bridge",
                executable="ardupilot_bridge_node",
                name="ardupilot_bridge",
                output="screen",
                parameters=[bridge_config, {"connection_url": connection_url}],
            ),
        ]
    )
