"""Generate the compact drone inspection world used by video demos."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET


SDF_VERSION = "1.11"
WORLD_NAME = "drone_demo"
DRONE_MODEL_URI = "model://iris_inspection"
DRONE_INSTANCE_NAME = "iris_reference"
GROUND_CENTER_XY_M = (2.0, 0.0)
GROUND_SIZE_XY_M = (14.0, 10.0)

HOME_XY_M = (0.0, 0.0)
WAYPOINT_A_XY_M = (1.5, 1.2)
WAYPOINT_B_XY_M = (2.2, -1.0)
STATION_A_XY_M = (3.25, 1.2)
STATION_B_XY_M = (3.45, -2.25)


def _text(parent: ET.Element, tag: str, value: object, attrib: dict[str, str] | None = None) -> ET.Element:
    child = ET.SubElement(parent, tag, attrib or {})
    child.text = str(value)
    return child


def _values(values: tuple[float, ...]) -> str:
    return " ".join(f"{value:.9g}" for value in values)


def _pose(
    parent: ET.Element,
    xyz: tuple[float, float, float],
    rpy: tuple[float, float, float] = (0.0, 0.0, 0.0),
    *,
    relative_to: str,
) -> ET.Element:
    return _text(parent, "pose", _values((*xyz, *rpy)), {"relative_to": relative_to})


def _material(parent: ET.Element, color: str, *, emissive: str | None = None) -> None:
    material = ET.SubElement(parent, "material")
    _text(material, "ambient", color)
    _text(material, "diffuse", color)
    _text(material, "specular", "0.16 0.16 0.16 1")
    if emissive is not None:
        _text(material, "emissive", emissive)


def _geometry(parent: ET.Element, shape: str, dimensions: tuple[float, ...]) -> None:
    geometry = ET.SubElement(parent, "geometry")
    primitive = ET.SubElement(geometry, shape)
    if shape == "box":
        _text(primitive, "size", _values(dimensions))
    elif shape == "cylinder":
        _text(primitive, "radius", dimensions[0])
        _text(primitive, "length", dimensions[1])
    elif shape == "sphere":
        _text(primitive, "radius", dimensions[0])
    elif shape == "plane":
        _text(primitive, "normal", "0 0 1")
        _text(primitive, "size", _values(dimensions))
    else:
        raise ValueError(f"unsupported primitive: {shape}")


def _visual(
    link: ET.Element,
    name: str,
    shape: str,
    dimensions: tuple[float, ...],
    xyz: tuple[float, float, float],
    color: str,
    *,
    rpy: tuple[float, float, float] = (0.0, 0.0, 0.0),
    emissive: str | None = None,
) -> None:
    visual = ET.SubElement(link, "visual", {"name": name})
    _pose(visual, xyz, rpy, relative_to="link")
    _geometry(visual, shape, dimensions)
    _material(visual, color, emissive=emissive)


def _collision(
    link: ET.Element,
    name: str,
    shape: str,
    dimensions: tuple[float, ...],
    xyz: tuple[float, float, float],
    *,
    rpy: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> None:
    collision = ET.SubElement(link, "collision", {"name": name})
    _pose(collision, xyz, rpy, relative_to="link")
    _geometry(collision, shape, dimensions)


def _static_model(world: ET.Element, name: str, xyz: tuple[float, float, float]) -> tuple[ET.Element, ET.Element]:
    model = ET.SubElement(world, "model", {"name": name})
    _text(model, "static", "true")
    _pose(model, xyz, relative_to="world")
    return model, ET.SubElement(model, "link", {"name": "link"})


def _add_ground(world: ET.Element) -> None:
    _, link = _static_model(world, "demo_ground", (*GROUND_CENTER_XY_M, 0.0))
    _collision(link, "ground_collision", "plane", GROUND_SIZE_XY_M, (0.0, 0.0, 0.0))
    _visual(link, "ground_visual", "plane", GROUND_SIZE_XY_M, (0.0, 0.0, 0.0), "0.18 0.22 0.19 1")

    # A simple service lane makes motion visible in both the scene and camera.
    _visual(link, "service_lane", "box", (8.2, 2.0, 0.018), (1.7, 0.0, 0.009), "0.12 0.13 0.14 1")
    for index, x_m in enumerate((-1.2, 0.0, 1.2, 2.4, 3.6, 4.8)):
        _visual(
            link,
            f"lane_marker_{index}",
            "box",
            (0.55, 0.055, 0.025),
            (x_m, 0.0, 0.022),
            "0.92 0.84 0.18 1",
        )


def _add_landing_pad(world: ET.Element) -> None:
    _, link = _static_model(world, "home_landing_pad", (*HOME_XY_M, 0.0))
    _collision(link, "pad_collision", "cylinder", (0.9, 0.05), (0.0, 0.0, 0.025))
    _visual(link, "pad_outer", "cylinder", (0.9, 0.05), (0.0, 0.0, 0.025), "0.92 0.36 0.05 1")
    _visual(link, "pad_inner", "cylinder", (0.72, 0.018), (0.0, 0.0, 0.059), "0.08 0.09 0.10 1")
    _visual(link, "h_left", "box", (0.42, 0.075, 0.014), (0.0, 0.18, 0.075), "0.96 0.96 0.94 1")
    _visual(link, "h_right", "box", (0.42, 0.075, 0.014), (0.0, -0.18, 0.075), "0.96 0.96 0.94 1")
    _visual(link, "h_crossbar", "box", (0.075, 0.36, 0.014), (0.0, 0.0, 0.075), "0.96 0.96 0.94 1")


def _add_route_markers(world: ET.Element) -> None:
    for name, xy_m, color in (
        ("waypoint_a_marker", WAYPOINT_A_XY_M, "0.05 0.65 0.85 1"),
        ("waypoint_b_marker", WAYPOINT_B_XY_M, "0.95 0.55 0.04 1"),
    ):
        _, link = _static_model(world, name, (*xy_m, 0.0))
        _visual(link, "marker", "cylinder", (0.32, 0.018), (0.0, 0.0, 0.012), color, emissive=color)


def _add_station_a(world: ET.Element) -> None:
    _, link = _static_model(world, "inspection_station_a", (*STATION_A_XY_M, 0.0))
    _collision(link, "cabinet_collision", "box", (0.85, 1.05, 1.2), (0.0, 0.0, 0.6))
    _visual(link, "cabinet", "box", (0.85, 1.05, 1.2), (0.0, 0.0, 0.6), "0.50 0.54 0.56 1")
    _visual(link, "front_panel", "box", (0.025, 0.58, 0.58), (-0.438, 0.0, 0.66), "0.05 0.08 0.10 1")
    _visual(
        link,
        "status_screen",
        "box",
        (0.014, 0.34, 0.21),
        (-0.455, 0.0, 0.78),
        "0.03 0.38 0.60 1",
        emissive="0.02 0.24 0.50 1",
    )
    _visual(link, "warning_strip", "box", (0.014, 0.52, 0.08), (-0.456, 0.0, 0.35), "0.96 0.66 0.04 1")
    _visual(link, "beacon", "cylinder", (0.09, 0.14), (0.0, 0.0, 1.29), "0.04 0.70 0.95 1", emissive="0.02 0.38 0.75 1")
    for index, y_m in enumerate((-0.38, 0.38)):
        _visual(link, f"pipe_{index}", "cylinder", (0.055, 1.0), (0.32, y_m, 0.5), "0.88 0.46 0.05 1")


def _add_station_b(world: ET.Element) -> None:
    _, link = _static_model(world, "inspection_station_b", (*STATION_B_XY_M, 0.0))
    _collision(link, "tank_collision", "cylinder", (0.65, 1.5), (0.0, 0.0, 0.75))
    _visual(link, "tank", "cylinder", (0.65, 1.5), (0.0, 0.0, 0.75), "0.62 0.65 0.66 1")
    _visual(link, "inspection_band", "cylinder", (0.67, 0.14), (0.0, 0.0, 0.92), "0.74 0.08 0.05 1")
    _visual(link, "tank_cap", "cylinder", (0.68, 0.08), (0.0, 0.0, 1.52), "0.22 0.24 0.25 1")
    _visual(
        link,
        "hotspot_marker",
        "box",
        (0.035, 0.30, 0.26),
        (-0.47, 0.47, 0.82),
        "0.95 0.16 0.03 1",
        rpy=(0.0, 0.0, -math.pi / 4.0),
        emissive="0.55 0.03 0.01 1",
    )
    for index, local_y in enumerate((-0.34, 0.34)):
        _visual(link, f"support_{index}", "box", (0.18, 0.18, 0.45), (0.0, local_y, 0.225), "0.18 0.19 0.20 1")


def _add_warehouse(world: ET.Element) -> None:
    _, link = _static_model(world, "operations_warehouse", (-1.6, 3.55, 0.0))
    _collision(link, "building_collision", "box", (3.8, 1.8, 2.35), (0.0, 0.0, 1.175))
    _visual(link, "building", "box", (3.8, 1.8, 2.35), (0.0, 0.0, 1.175), "0.25 0.29 0.32 1")
    _visual(link, "roof", "box", (4.1, 2.05, 0.14), (0.0, 0.0, 2.42), "0.06 0.08 0.10 1")
    for index, x_m in enumerate((-1.2, 0.0, 1.2)):
        _visual(link, f"door_{index}", "box", (0.82, 0.035, 1.55), (x_m, -0.918, 0.79), "0.09 0.12 0.14 1")
        _visual(
            link,
            f"door_light_{index}",
            "box",
            (0.18, 0.04, 0.09),
            (x_m, -0.94, 1.82),
            "0.10 0.75 0.92 1",
            emissive="0.05 0.42 0.70 1",
        )


def _add_safety_barriers(world: ET.Element) -> None:
    for index, (x_m, y_m, yaw) in enumerate(
        ((2.7, 2.25, 0.0), (3.7, 2.25, 0.0), (4.5, -1.15, math.pi / 2.0), (4.5, -2.1, math.pi / 2.0))
    ):
        _, link = _static_model(world, f"safety_barrier_{index}", (x_m, y_m, 0.0))
        _visual(link, "beam", "box", (0.9, 0.08, 0.14), (0.0, 0.0, 0.42), "0.95 0.35 0.03 1", rpy=(0.0, 0.0, yaw))
        for post_index, offset in enumerate((-0.34, 0.34)):
            local_x = offset if yaw == 0.0 else 0.0
            local_y = 0.0 if yaw == 0.0 else offset
            _visual(
                link,
                f"post_{post_index}",
                "box",
                (0.07, 0.07, 0.72),
                (local_x, local_y, 0.36),
                "0.88 0.88 0.84 1",
            )


def _add_drone(world: ET.Element) -> None:
    include = ET.SubElement(world, "include")
    _text(include, "name", DRONE_INSTANCE_NAME)
    _text(include, "uri", DRONE_MODEL_URI)
    _pose(include, (0.0, 0.0, 0.4), relative_to="world")


def gen_sdf() -> dict[str, object]:
    sdf = ET.Element("sdf", {"version": SDF_VERSION})
    world = ET.SubElement(sdf, "world", {"name": WORLD_NAME})

    for filename, name in (
        ("gz-sim-physics-system", "gz::sim::systems::Physics"),
        ("gz-sim-sensors-system", "gz::sim::systems::Sensors"),
        ("gz-sim-user-commands-system", "gz::sim::systems::UserCommands"),
        ("gz-sim-scene-broadcaster-system", "gz::sim::systems::SceneBroadcaster"),
    ):
        plugin = ET.SubElement(world, "plugin", {"filename": filename, "name": name})
        if filename == "gz-sim-sensors-system":
            _text(plugin, "render_engine", "ogre2")

    scene = ET.SubElement(world, "scene")
    _text(scene, "ambient", "0.46 0.49 0.52 1")
    _text(scene, "background", "0.58 0.72 0.84 1")
    _text(scene, "shadows", "true")
    _text(scene, "grid", "true")

    sun = ET.SubElement(world, "light", {"name": "sun", "type": "directional"})
    _pose(sun, (0.0, 0.0, 8.0), relative_to="world")
    _text(sun, "cast_shadows", "true")
    _text(sun, "intensity", 1.0)
    _text(sun, "direction", "-0.45 0.20 -0.88")
    _text(sun, "diffuse", "0.92 0.90 0.82 1")
    _text(sun, "specular", "0.25 0.25 0.25 1")

    fill = ET.SubElement(world, "light", {"name": "inspection_fill", "type": "point"})
    _pose(fill, (3.2, -0.4, 4.0), relative_to="world")
    _text(fill, "cast_shadows", "false")
    _text(fill, "intensity", 1.4)
    _text(fill, "diffuse", "0.70 0.82 1.0 1")
    attenuation = ET.SubElement(fill, "attenuation")
    _text(attenuation, "range", 12.0)
    _text(attenuation, "constant", 0.5)
    _text(attenuation, "linear", 0.04)
    _text(attenuation, "quadratic", 0.02)

    _add_ground(world)
    _add_landing_pad(world)
    _add_route_markers(world)
    _add_station_a(world)
    _add_station_b(world)
    _add_warehouse(world)
    _add_safety_barriers(world)
    _add_drone(world)

    return {
        "xml": sdf,
        "assumptions": [
            {
                "code": "route_aligned_layout",
                "message": "Inspection assets are positioned for the existing two-waypoint mission and the assumed 30 degree camera pitch.",
            },
            {
                "code": "primitive_demo_geometry",
                "message": "All environment geometry is a lightweight visual approximation for demonstrations.",
            },
        ],
        "metadata": {
            "target_consumer": "Gazebo Harmonic / gz-sim8",
            "sdf_version": SDF_VERSION,
        },
    }
