# Getting Started

## Requirements

- ROS2 Humble or Jazzy.
- `colcon`.
- Python dependencies available through ROS2 package dependencies.

## Build

```bash
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install
source install/setup.bash
```

For Humble, source `/opt/ros/humble/setup.bash` instead.

## Run

```bash
ros2 launch task_orchestrator_core task_orchestrator.launch.py
```

The default parameter file is
`task_orchestrator_core/params/task_orchestrator_defaults.yaml`.

## Execute A Wait Task

Send a built-in wait task through the public action:

```bash
ros2 action send_goal /task_orchestrator/execute_task \
  task_orchestrator_msgs/action/ExecuteTaskV1 \
  "{task_name: system/wait, task_data_json: '{\"duration_sec\": 1.0}'}"
```

## Inspect State

```bash
ros2 topic echo /task_orchestrator/active_tasks
ros2 topic echo /task_orchestrator/results
ros2 topic echo /task_orchestrator/events
ros2 topic echo /task_orchestrator/feedback
```

## Query Recent Records

```bash
ros2 service call /task_orchestrator/list_task_records \
  task_orchestrator_msgs/srv/ListTaskRecordsV1 "{}"

ros2 service call /task_orchestrator/list_events \
  task_orchestrator_msgs/srv/ListEventsV1 "{}"
```
