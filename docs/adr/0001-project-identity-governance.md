# ADR 0001: Project Identity And Governance

## Context

The project must be open source, ROS2-native and usable by external fleet
systems without being owned by product-specific cloud assumptions. It also needs
a clear package naming scheme before messages and source packages are created.

## Decision

Use **ROS2 Task Orchestrator** as the display name and `ros_task_orchestrator` as
the repository name.

Use these ROS2 packages:

- `task_orchestrator_msgs`
- `task_orchestrator_core`
- `task_orchestrator_bridge`
- `task_orchestrator_examples`

Use Apache-2.0 as the project license.

Use maintainer-led governance:

- Maintainers approve releases and API changes.
- Contributions use DCO sign-off.
- Releases follow semantic versioning.
- Public API stability starts at `v1.0`.
- Security reports are handled through a documented private disclosure path.

## Consequences

- The project stays generic and discoverable in the ROS2 ecosystem.
- Apache-2.0 is friendly to commercial and open-source users.
- External systems can depend on the project without making the project
  product-specific.
- Governance remains maintainer-led.
