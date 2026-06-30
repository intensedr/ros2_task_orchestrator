import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    task_config = LaunchConfiguration("task_config")
    mission_templates_path = LaunchConfiguration("mission_templates_path")

    package_share = get_package_share_directory("task_orchestrator_sim_nav2")
    default_task_config = os.path.join(package_share, "config", "nav2_tasks.yaml")
    default_mission_templates_path = os.path.join(package_share, "missions")

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "task_config",
                default_value=default_task_config,
                description="Task registry YAML file for the Nav2 simulation.",
            ),
            DeclareLaunchArgument(
                "mission_templates_path",
                default_value=default_mission_templates_path,
                description="Mission template directory for the Nav2 simulation.",
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
                        "admission.available_capability_tags": ["localization", "motion"],
                    }
                ],
            ),
        ]
    )
