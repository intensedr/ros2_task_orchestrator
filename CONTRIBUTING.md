# Contributing

## Local Checks

Use ROS2 Humble or Jazzy.

```bash
source /opt/ros/humble/setup.bash
colcon build --symlink-install
colcon test
```

## Contribution Requirements

- Keep changes focused.
- Update docs when public behavior changes.
- Add tests for new behavior.
- Preserve the public API versioning policy.
- Sign off commits with DCO:

```bash
git commit -s
```

## API Changes

Public message, service and action changes require an RFC or ADR update. After
`v1.0`, breaking API changes require a new versioned interface name.
