import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    task_config = LaunchConfiguration("task_config")

    default_task_config = os.path.join(
        get_package_share_directory("task_orchestrator_examples"),
        "config",
        "nav2_tasks.yaml",
    )

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "task_config",
                default_value=default_task_config,
                description="Task registry YAML file for the Nav2 example.",
            ),
            Node(
                package="task_orchestrator_core",
                executable="task_orchestrator_node",
                name="task_orchestrator",
                output="screen",
                parameters=[{"tasks_config_path": task_config}],
            ),
        ]
    )
