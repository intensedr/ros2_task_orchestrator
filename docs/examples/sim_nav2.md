# Nav2 Simulation

`task_orchestrator_sim_nav2` is an optional simulation package for exercising
the orchestrator against Nav2.

It uses the upstream Nav2 TurtleBot simulation instead of owning robot or world
assets locally:

- Robot: TurtleBot3 Waffle.
- World: the Nav2 TurtleBot simulation world from `nav2_bringup`.
- Jazzy and newer: Nav2 minimal TurtleBot packages for modern Gazebo.
- Humble fallback: the TurtleBot3 Gazebo path supported by Nav2.

## Install Prerequisites

Install Nav2 and bringup:

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

## Launch

```bash
ros2 launch task_orchestrator_sim_nav2 sim_nav2.launch.py
```

The wrapper accepts lowercase `headless:=false`, normalizes it for the upstream
Nav2 launch file and sets `TURTLEBOT3_MODEL=waffle` by default. RViz is disabled
by default so the simulation can run on headless machines.

Each full simulation launch uses a fresh Gazebo transport partition by default,
which avoids reusing a stale `gz sim` server and seeing the robot at its old
pose. The initial robot pose is explicit:

```bash
ros2 launch task_orchestrator_sim_nav2 sim_nav2.launch.py \
  x_pose:=-2.00 y_pose:=-0.50 yaw:=0.00
```

Pass a fixed partition when you intentionally want several Gazebo tools or
terminals to attach to the same simulation:

```bash
ros2 launch task_orchestrator_sim_nav2 sim_nav2.launch.py \
  gz_partition:=task_orchestrator_nav2
```

For the Gazebo GUI and RViz:

```bash
ros2 launch task_orchestrator_sim_nav2 sim_nav2.launch.py headless:=false use_rviz:=true
```

If Nav2 is already running, start only the orchestrator:

```bash
ros2 launch task_orchestrator_sim_nav2 orchestrator_nav2.launch.py
```

The orchestrator-only launch does not reset Gazebo or respawn the robot.

## Drive a Route Mission

Start the full simulation in one terminal:

```bash
ros2 launch task_orchestrator_sim_nav2 sim_nav2.launch.py headless:=false
```

Submit the route mission from another sourced terminal:

```bash
ros2 run task_orchestrator_sim_nav2 submit_route_mission
```

The helper registers a demo agent, submits `missions/route_mission.json` through
`/task_orchestrator/submit_mission` and polls
`/task_orchestrator/get_mission_state` until the mission reaches a terminal
state. No extra action server is needed: Nav2 movement still goes through the
existing `/navigate_to_pose` action. Each helper run generates a fresh mission
id to avoid conflicts with an active lease from a previous demo run; pass
`--mission-id` when a fixed id is required.

Use `--no-wait` when the example should only submit the mission and exit:

```bash
ros2 run task_orchestrator_sim_nav2 submit_route_mission --no-wait
```

## Task Registry

The package registers one Nav2-backed task:

- `navigation/navigate_to_pose`
- action: `/navigate_to_pose`
- interface: `nav2_msgs/action/NavigateToPose`
- resource lock: `base`
- task group: `navigation`

## Scenario Assets

- `missions/navigate_to_pose.json`: one navigation subtask.
- `missions/route_mission.json`: ordered route with three waypoints.
- `missions/nav2_route_template.yaml`: parameterized route mission template.
- `requests/queued_navigation_goal.json`: execute-task request with
  `queue_on_conflict`.
- `requests/cancel_navigation.json`: cancel request for an active navigation
  task.

Simulation tests should run manually or in a nightly profile. Core CI should
continue selecting only lightweight packages that do not require Gazebo/Nav2.
