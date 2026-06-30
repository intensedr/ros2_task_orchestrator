# task_orchestrator_sim_drone

Optional fake drone simulation package for ROS2 Task Orchestrator.

This package is intentionally lightweight. It does not start ArduPilot, PX4,
MAVLink or a flight controller runtime. It provides:

- a local OpenRobotics Iris with Standoffs Gazebo Fuel model;
- fake ROS 2 action servers for takeoff, waypoint, hover and land;
- a static Gazebo world that includes the local Iris model;
- task registry and mission JSON examples for orchestrator demos.

## Build

```bash
colcon build --packages-select \
  task_orchestrator_msgs task_orchestrator_core task_orchestrator_sim_drone \
  --symlink-install
source install/setup.bash
```

## Run

Start Gazebo, the fake drone action servers and the orchestrator:

```bash
ros2 launch task_orchestrator_sim_drone sim_drone.launch.py
```

The launch starts Gazebo with `worlds/drone_demo_world.sdf`, starts
`fake_drone_server`, and starts `task_orchestrator_node` with
`config/drone_tasks.yaml`. The Gazebo world includes the local
`model://iris_with_standoffs` asset, so Gazebo does not need to download it from
Fuel at launch time. The model keeps the downloaded `model.sdf` as source data
and loads that SDF through `model.config`. Its visible geometry is built from
Gazebo primitives instead of Collada meshes, avoiding DAE importer artifacts in
Gazebo Sim.

The fake drone server also mirrors its `/drone/pose` state into Gazebo through
the `/world/drone_demo/set_pose` service, so the visible Iris model moves during
takeoff, waypoint and landing tasks. Adjust the update rate with
`gazebo_pose_sync_rate_hz:=...` if needed.

For runs without Gazebo:

```bash
ros2 launch task_orchestrator_sim_drone sim_drone.launch.py use_gazebo:=false
```

To start only the orchestrator against an already running drone backend:

```bash
ros2 launch task_orchestrator_sim_drone orchestrator_drone.launch.py
```

## Drive a Mission

Submit the inspection route from another sourced terminal:

```bash
ros2 run task_orchestrator_sim_drone submit_inspection_mission
```

The helper registers a demo agent, submits `missions/inspection_route.json`
through `/task_orchestrator/submit_mission` and polls
`/task_orchestrator/get_mission_state` until the mission reaches a terminal
state. Each helper run generates a fresh mission id to avoid conflicts with an
active lease from a previous demo run.

To submit and return immediately:

```bash
ros2 run task_orchestrator_sim_drone submit_inspection_mission --no-wait
```

## Task Registry

The package registers four fake drone action-backed tasks:

- `drone/takeoff`
- `drone/go_to_waypoint`
- `drone/hover`
- `drone/land`

All tasks use the `airframe` resource and `drone_flight` task group, so mission
subtasks run serially and demonstrate resource locking.

## Scenario Assets

- `missions/takeoff_waypoint_land.json`: simple takeoff, one waypoint, hover,
  land sequence.
- `missions/inspection_route.json`: two-zone inspection route with hover steps.
- `missions/timeout_mission.json`: intentionally short subtask timeout.
- `requests/cancel_inspection.json`: cancel request shape. Fill in the
  `lease_token` printed by `submit_inspection_mission`.
- `models/iris_with_standoffs`: local copy of the OpenRobotics Iris with
  Standoffs Gazebo Fuel model, with simplified primitive visuals for Gazebo Sim.
- `worlds/drone_demo_world.sdf`: static Gazebo world with the local Iris model.
