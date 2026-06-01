![ROS2 Task Orchestrator banner](assets/banner.png)

![ROS2](https://img.shields.io/badge/ROS2-Humble_|_Jazzy-22314E?logo=ros&logoColor=white)
![License](https://img.shields.io/badge/License-Apache_2.0-blue)

# ROS2 Task Orchestrator

ROS2 Task Orchestrator is an open-source ROS2 package for starting, tracking,
cancelling and composing robot tasks through a stable ROS2-native API.

The project is designed as an edge task layer. Existing ROS2 actions and
services keep implementing robot behavior; the orchestrator provides a common
entry point, task state, results, feedback and events.

## ROS2 Distribution Support

| Distribution | Status | Notes |
|---|---|---|
| Humble | Supported | Minimum supported distribution. |
| Jazzy | Supported | Current CI target. |
| Lyrical | Planned | Added after build and CI validation. |
| Foxy | Planned legacy | Best-effort source compatibility for legacy robots. |

Foxy is end-of-life upstream and will not be part of the default CI matrix.
Lyrical is the current ROS2 release line and is tracked as the next support
target.

## Packages

- `task_orchestrator_msgs`: public ROS2 messages, services and actions.
- `task_orchestrator_core`: core orchestrator node and task lifecycle logic.
- `task_orchestrator_examples`: example configs and launch files.

## Build

```bash
source /opt/ros/humble/setup.bash
colcon build --symlink-install
source install/setup.bash
```

## Run

```bash
ros2 launch task_orchestrator_core task_orchestrator.launch.py
```

## Docker

```bash
docker build --build-arg ROS_DISTRO=humble -f docker/Dockerfile -t ros-task-orchestrator:humble .
docker run --rm --network host ros-task-orchestrator:humble \
  ros2 launch task_orchestrator_core task_orchestrator.launch.py
```

For Jazzy:

```bash
docker build --build-arg ROS_DISTRO=jazzy -f docker/Dockerfile -t ros-task-orchestrator:jazzy .
```

## Documentation

- [Docs Home](docs/index.md): overview and navigation.
- [Getting Started](docs/getting-started.md): build, run and first task.
- [Task YAML](docs/configuration/task-yaml.md): declare service/action-backed tasks.
- [Public API](docs/api/public-api.md): actions, topics and services.
- [Observability](docs/concepts/observability.md): events, feedback and structured logs.
- [SQLite Storage](docs/operations/sqlite-storage.md): optional durability.
- [Recovery](docs/operations/recovery.md): late-client and restart recovery.
- [Nav2 Example](docs/examples/nav2.md): navigation task config.
- [Architecture](docs/architecture.md): core boundaries and runtime shape.

## License

Apache-2.0
