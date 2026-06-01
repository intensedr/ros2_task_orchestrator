# ROS2 Support Policy

ROS2 Task Orchestrator supports ROS2 Humble and Jazzy.

Lyrical support is planned after build and CI validation. Foxy support is
planned as best-effort legacy source compatibility for robots that cannot move
off Foxy yet.

## Policy

- Humble is the minimum supported distribution.
- Jazzy is tested as the current modern target.
- Lyrical is tracked as the next support target.
- Foxy is end-of-life upstream and is not part of the default CI matrix.
- CI builds supported distributions in Docker.
- Public APIs remain source-compatible across supported distributions.

## Implementation Notes

- Python code may use Python 3.10 language features in the supported baseline.
- Core ROS2 behavior uses stable `rclpy`, action, service and message
  APIs available in both Humble and Jazzy.
- Distribution-specific behavior must be isolated behind compatibility helpers
  when needed.

## Distribution Matrix

| Distribution | Upstream Status | Project Status |
|---|---|---|
| Foxy Fitzroy | EOL since June 20, 2023 | Planned legacy best-effort source support. |
| Humble Hawksbill | Supported until May 2027 | Supported. |
| Jazzy Jalisco | Supported until May 2029 | Supported and tested. |
| Lyrical Luth | Released May 22, 2026, supported until May 2031 | Planned. |

The upstream dates follow the official ROS2 distributions list.
