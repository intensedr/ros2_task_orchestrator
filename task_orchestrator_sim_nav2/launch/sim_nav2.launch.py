import os
import uuid

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, SetEnvironmentVariable
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    headless = LaunchConfiguration("headless")
    use_rviz = LaunchConfiguration("use_rviz")
    use_sim_time = LaunchConfiguration("use_sim_time")
    autostart = LaunchConfiguration("autostart")
    turtlebot3_model = LaunchConfiguration("turtlebot3_model")
    gz_partition = LaunchConfiguration("gz_partition")
    x_pose = LaunchConfiguration("x_pose")
    y_pose = LaunchConfiguration("y_pose")
    z_pose = LaunchConfiguration("z_pose")
    roll = LaunchConfiguration("roll")
    pitch = LaunchConfiguration("pitch")
    yaw = LaunchConfiguration("yaw")
    task_config = LaunchConfiguration("task_config")
    mission_templates_path = LaunchConfiguration("mission_templates_path")

    sim_package_share = get_package_share_directory("task_orchestrator_sim_nav2")
    nav2_bringup_share = get_package_share_directory("nav2_bringup")
    default_task_config = os.path.join(sim_package_share, "config", "nav2_tasks.yaml")
    default_mission_templates_path = os.path.join(sim_package_share, "missions")
    default_gz_partition = f"task_orchestrator_nav2_{uuid.uuid4().hex[:8]}"
    nav2_headless = PythonExpression(["'True' if '", headless, "'.lower() == 'true' else 'False'"])

    nav2_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(nav2_bringup_share, "launch", "tb3_simulation_launch.py")
        ),
        launch_arguments={
            "headless": nav2_headless,
            "use_rviz": use_rviz,
            "use_sim_time": use_sim_time,
            "autostart": autostart,
            "x_pose": x_pose,
            "y_pose": y_pose,
            "z_pose": z_pose,
            "roll": roll,
            "pitch": pitch,
            "yaw": yaw,
        }.items(),
    )

    orchestrator = Node(
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
    )

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "headless",
                default_value="true",
                description="Run Gazebo without the 3D client.",
            ),
            DeclareLaunchArgument(
                "use_rviz",
                default_value="false",
                description="Start RViz from the upstream Nav2 launch file.",
            ),
            DeclareLaunchArgument(
                "use_sim_time",
                default_value="true",
                description="Use simulation time for Nav2.",
            ),
            DeclareLaunchArgument(
                "autostart",
                default_value="true",
                description="Automatically activate Nav2 lifecycle nodes.",
            ),
            DeclareLaunchArgument(
                "turtlebot3_model",
                default_value="waffle",
                description="TurtleBot3 model for Nav2/TurtleBot simulation assets.",
            ),
            DeclareLaunchArgument(
                "gz_partition",
                default_value=default_gz_partition,
                description="Gazebo transport partition. Defaults to a fresh partition for each launch.",
            ),
            DeclareLaunchArgument(
                "x_pose",
                default_value="-2.00",
                description="Initial robot x pose in the map/world frame.",
            ),
            DeclareLaunchArgument(
                "y_pose",
                default_value="-0.50",
                description="Initial robot y pose in the map/world frame.",
            ),
            DeclareLaunchArgument(
                "z_pose",
                default_value="0.01",
                description="Initial robot z pose in the map/world frame.",
            ),
            DeclareLaunchArgument(
                "roll",
                default_value="0.00",
                description="Initial robot roll.",
            ),
            DeclareLaunchArgument(
                "pitch",
                default_value="0.00",
                description="Initial robot pitch.",
            ),
            DeclareLaunchArgument(
                "yaw",
                default_value="0.00",
                description="Initial robot yaw.",
            ),
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
            SetEnvironmentVariable("GZ_PARTITION", gz_partition),
            SetEnvironmentVariable("TURTLEBOT3_MODEL", turtlebot3_model),
            nav2_launch,
            orchestrator,
        ]
    )
