# ADR 0007: ROS2 Distribution Support

## Context

The project needs a support baseline that keeps the core modern enough to
maintain while still covering widely deployed ROS2 robots.

## Decision

Support ROS2 Humble and Jazzy.

Humble is the minimum supported distribution. Jazzy is the current modern target
in CI.

The core may use Python 3.10 language features. ROS2 behavior uses stable
`rclpy`, action, service and message APIs available in both supported
distributions.

## Consequences

- The codebase avoids legacy constraints from older ROS2 distributions.
- CI remains practical through Docker builds for Humble and Jazzy.
- Distribution-specific behavior is isolated behind compatibility helpers.
