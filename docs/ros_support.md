# ROS2 Support Policy

ROS2 Task Orchestrator supports ROS2 Humble and Jazzy.

## Policy

- Humble is the minimum supported distribution.
- Jazzy is tested as the current modern target.
- CI builds both distributions in Docker.
- Public APIs remain source-compatible across supported distributions.

## Implementation Notes

- Python code may use Python 3.10 language features.
- Core ROS2 behavior uses stable `rclpy`, action, service and message
  APIs available in both Humble and Jazzy.
- Distribution-specific behavior must be isolated behind compatibility helpers
  when needed.
