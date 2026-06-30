# Drone Simulation

`task_orchestrator_sim_drone` is an optional fake drone simulation package for
exercising mission orchestration without a real autopilot runtime.

It uses a local OpenRobotics Iris with Standoffs Gazebo Fuel model and fake
action servers instead of ArduPilot, PX4, MAVLink or a flight controller
runtime. This keeps the example lightweight while still exercising the public
mission API, resource locks, cancellation and timeouts.

## Build

```bash
colcon build --packages-select \
  task_orchestrator_msgs task_orchestrator_core task_orchestrator_sim_drone \
  --symlink-install
source install/setup.bash
```

## Launch

```bash
ros2 launch task_orchestrator_sim_drone sim_drone.launch.py
```

The launch starts:

- Gazebo with `worlds/drone_demo_world.sdf`;
- `fake_drone_server`, which publishes `map -> base_root` and `/drone/pose`;
- `task_orchestrator_node` with `config/drone_tasks.yaml`.

For a run without Gazebo:

```bash
ros2 launch task_orchestrator_sim_drone sim_drone.launch.py use_gazebo:=false
```

The Gazebo world is the static `worlds/drone_demo_world.sdf` file. It contains
the local `model://iris_with_standoffs` asset and a ground plane. No Gazebo
bridge or autopilot is required for this demo. The downloaded `model.sdf` stays
next to a converted `model.urdf`, and `model.config` points Gazebo at the SDF.
The visible drone geometry uses Gazebo primitives instead of Collada meshes to
avoid DAE importer artifacts.

During a mission, `fake_drone_server` mirrors its action-server pose into Gazebo
with `/world/drone_demo/set_pose`, so the visible drone follows takeoff,
waypoint and landing progress.

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
state.

Use `--no-wait` when the example should only submit the mission and exit:

```bash
ros2 run task_orchestrator_sim_drone submit_inspection_mission --no-wait
```

## Task Registry

The package registers four action-backed tasks:

- `drone/takeoff`
- `drone/go_to_waypoint`
- `drone/hover`
- `drone/land`

All tasks lock the `airframe` resource and belong to the `drone_flight` task
group, so the orchestrator serializes flight subtasks.

## Scenario Assets

- `missions/takeoff_waypoint_land.json`: simple takeoff, one waypoint, hover,
  land sequence.
- `missions/inspection_route.json`: two-zone inspection route with hover steps.
- `missions/timeout_mission.json`: intentionally short subtask timeout.
- `requests/cancel_inspection.json`: cancel request shape. Fill in the
  `lease_token` printed by `submit_inspection_mission`.
- `worlds/drone_demo_world.sdf`: optional static Gazebo scene.
- `models/iris_with_standoffs`: local copy of the OpenRobotics Iris with
  Standoffs Gazebo Fuel model, with simplified primitive visuals for Gazebo Sim.
