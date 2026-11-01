# Drone demo world design ledger

| Field | Value |
|---|---|
| Generated output | `drone_demo_world.sdf` |
| Source | `gen_drone_demo_world.py` |
| Document kind | Gazebo world |
| Target consumer | Gazebo Harmonic / gz-sim8 |
| SDFormat | 1.11, matching the existing project world |
| Units | metres, kilograms, seconds, radians |
| Coordinates | ROS-like: +X forward/east, +Y left/north, +Z up |

The existing inspection route is the spatial source of truth:

| Frame / object | Pose in `world` | Purpose |
|---|---:|---|
| `home` | `(0, 0, 0)` | Take-off and landing pad |
| waypoint A | `(1.5, 1.2, 1.8)`, yaw `0` | Looks toward inspection cabinet A |
| station A | `(3.25, 1.2, 0)` | Visible from waypoint A |
| waypoint B | `(2.2, -1.0, 1.8)`, yaw `-0.7854` | Looks toward inspection tank B |
| station B | `(3.45, -2.25, 0)` | Visible from waypoint B |

The camera mount is defined in `../models/iris_inspection/gen_model.py`. The
static wrapper places it at `(0.17, 0, -0.035)` relative to the nested Iris
`base_link` and pitches it down by 30 degrees. The placement is a demo
assumption, not a measurement from vehicle CAD.

The world uses primitive geometry only, has no external Fuel resources, and is
intentionally compact for real-time camera rendering. Static collision is kept
on the ground and large inspection objects; small signs and markings are visual
only.
