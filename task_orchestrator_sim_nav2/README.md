# task_orchestrator_sim_nav2

Optional Nav2 simulation package for ROS2 Task Orchestrator.

This package intentionally reuses the upstream Nav2 TurtleBot simulation assets
instead of maintaining a robot model or world locally. On Jazzy and newer, use
the Nav2 minimal TurtleBot packages installed with `nav2_bringup`. On Humble,
use the TurtleBot3 Gazebo path supported by Nav2.

## Install Prerequisites

This package does not vendor or install Nav2 simulation assets. Install them
with the ROS distribution package manager before launching the simulation:

```bash
sudo apt install ros-$ROS_DISTRO-navigation2 ros-$ROS_DISTRO-nav2-bringup
```

For Jazzy and newer:

```bash
sudo apt install ros-$ROS_DISTRO-nav2-minimal-tb3-sim
```

For Humble or other Gazebo Classic setups:

```bash
sudo apt install ros-$ROS_DISTRO-turtlebot3-gazebo
```

Build and source this workspace after adding or updating the package:

```bash
colcon build --packages-select \
  task_orchestrator_msgs task_orchestrator_core task_orchestrator_sim_nav2 \
  --symlink-install
source install/setup.bash
ros2 pkg prefix task_orchestrator_sim_nav2
```

## Run

```bash
ros2 launch task_orchestrator_sim_nav2 sim_nav2.launch.py
```

The wrapper accepts lowercase `headless:=false` and normalizes it before
including the upstream Nav2 launch file.
RViz is disabled by default so the simulation can run on headless machines.
Each full simulation launch uses a fresh Gazebo transport partition by default,
so a stale `gz sim` server from a previous run does not keep the robot at its
old pose. The initial robot pose is explicit and can be overridden:

```bash
ros2 launch task_orchestrator_sim_nav2 sim_nav2.launch.py \
  x_pose:=-2.00 y_pose:=-0.50 yaw:=0.00
```

Use `gz_partition:=task_orchestrator_nav2` when you intentionally want multiple
Gazebo tools or terminals to attach to the same simulation instance.

For the Gazebo GUI and RViz:

```bash
ros2 launch task_orchestrator_sim_nav2 sim_nav2.launch.py headless:=false use_rviz:=true
```

The wrapper sets `TURTLEBOT3_MODEL=waffle` by default. Override it with
`turtlebot3_model:=burger` or `turtlebot3_model:=waffle_pi` only if the matching
upstream assets are installed.

To start only the orchestrator against an already running Nav2 simulation:

```bash
ros2 launch task_orchestrator_sim_nav2 orchestrator_nav2.launch.py
```

This orchestrator-only launch does not reset Gazebo or respawn the robot.

## Drive a Route Mission

Start the full simulation in one terminal:

```bash
ros2 launch task_orchestrator_sim_nav2 sim_nav2.launch.py headless:=false
```

Submit the example route mission from another sourced terminal:

```bash
ros2 run task_orchestrator_sim_nav2 submit_route_mission
```

The helper registers a demo agent, submits `missions/route_mission.json` through
`/task_orchestrator/submit_mission` and polls
`/task_orchestrator/get_mission_state` until the mission reaches a terminal
state. It does not add a second action server; Nav2 movement still goes through
the existing `/navigate_to_pose` action. By default, each helper run generates a
fresh mission id to avoid conflicts with an active lease from a previous demo
run; pass `--mission-id` when a fixed id is required.

To submit and return immediately:

```bash
ros2 run task_orchestrator_sim_nav2 submit_route_mission --no-wait
```

## Scenarios

- `missions/navigate_to_pose.json`: one navigation subtask.
- `missions/route_mission.json`: ordered waypoint route.
- `missions/nav2_route_template.yaml`: parameterized route template.
- `requests/queued_navigation_goal.json`: execute-task request with
  `queue_on_conflict`.
- `requests/cancel_navigation.json`: cancel request for an active navigation
  task.
