"""Launch the task orchestrator media manager."""

from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    default_params = str(
        Path(get_package_share_directory("task_orchestrator_media")) / "config" / "media_manager.yaml"
    )
    params_file = LaunchConfiguration("params_file")
    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "params_file",
                default_value=default_params,
                description="Media manager parameter file.",
            ),
            Node(
                package="task_orchestrator_media",
                executable="media_manager_node",
                name="media_manager",
                output="screen",
                parameters=[params_file],
            ),
        ]
    )
