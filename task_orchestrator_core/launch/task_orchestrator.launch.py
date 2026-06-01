import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    params_file = LaunchConfiguration("params_file")

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "params_file",
                default_value=os.path.join(
                    get_package_share_directory("task_orchestrator_core"),
                    "params",
                    "task_orchestrator_defaults.yaml",
                ),
                description="Optional task orchestrator parameter file.",
            ),
            Node(
                package="task_orchestrator_core",
                executable="task_orchestrator_node",
                name="task_orchestrator",
                output="screen",
                parameters=[params_file],
            ),
        ]
    )
