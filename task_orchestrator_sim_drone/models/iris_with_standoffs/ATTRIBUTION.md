# Iris with Standoffs model attribution

Source: https://fuel.gazebosim.org/1.0/OpenRobotics/models/Iris%20with%20Standoffs

This package contains a local copy of version 3 of the Gazebo Fuel model
`Iris with Standoffs` from OpenRobotics.

The upstream `model.config` describes it as a copy of the 3DR Iris model from:

https://github.com/PX4/sitl_gazebo/tree/master/models

Authors listed by the upstream model:

- Fadri Furrer
- Michael Burri
- Mina Kamel
- Janosch Nikolic
- Markus Achtelik

Maintainer listed by the upstream model: john hsu.

Local changes:

- Upstream DAE mesh assets are retained under `meshes/` for traceability and
  offline reference.
- `model.sdf` and `model.urdf` use simplified primitive visual geometry instead
  of the upstream DAE visuals to avoid Collada importer artifacts in Gazebo Sim.
- `model.urdf` was converted from `model.sdf`; `model.config` points Gazebo at
  the SDF while keeping the URDF copy next to it.
