"""Generate the demo Iris wrapper with a fixed forward / downward RGB camera."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET


SDF_VERSION = "1.11"
MODEL_NAME = "iris_inspection"
BASE_MODEL_URI = "model://iris_with_standoffs"
BASE_MODEL_INSTANCE_NAME = "airframe"
BASE_MODEL_Z_OFFSET_M = 0.194923

# Demo mount assumption: the camera sits just ahead of the simplified Iris body
# and is pitched down by 30 degrees. Gazebo cameras look along the sensor's +X
# axis. All values use SI units and are expressed in base_link.
CAMERA_XYZ_IN_BASE_M = (0.17, 0.0, -0.035)
CAMERA_XYZ_IN_MODEL_M = (
    CAMERA_XYZ_IN_BASE_M[0],
    CAMERA_XYZ_IN_BASE_M[1],
    BASE_MODEL_Z_OFFSET_M + CAMERA_XYZ_IN_BASE_M[2],
)
CAMERA_RPY_RAD = (0.0, math.radians(30.0), 0.0)
CAMERA_TOPIC = "/drone/front_camera"
CAMERA_WIDTH_PX = 1280
CAMERA_HEIGHT_PX = 720
CAMERA_UPDATE_RATE_HZ = 20.0
CAMERA_HORIZONTAL_FOV_RAD = math.radians(80.0)


def _text(parent: ET.Element, tag: str, value: object, attrib: dict[str, str] | None = None) -> ET.Element:
    child = ET.SubElement(parent, tag, attrib or {})
    child.text = str(value)
    return child


def _values(values: tuple[float, ...]) -> str:
    return " ".join(f"{value:.9g}" for value in values)


def _pose(
    parent: ET.Element,
    xyz: tuple[float, float, float],
    rpy: tuple[float, float, float],
    *,
    relative_to: str,
) -> ET.Element:
    return _text(parent, "pose", _values((*xyz, *rpy)), {"relative_to": relative_to})


def _material(parent: ET.Element, color: str) -> None:
    material = ET.SubElement(parent, "material")
    _text(material, "ambient", color)
    _text(material, "diffuse", color)
    _text(material, "specular", "0.18 0.18 0.18 1")


def _box_visual(
    link: ET.Element,
    name: str,
    size: str,
    xyz: tuple[float, float, float],
    rpy: tuple[float, float, float],
    color: str,
) -> None:
    visual = ET.SubElement(link, "visual", {"name": name})
    _pose(visual, xyz, rpy, relative_to="front_camera_link")
    geometry = ET.SubElement(visual, "geometry")
    box = ET.SubElement(geometry, "box")
    _text(box, "size", size)
    _material(visual, color)


def _cylinder_visual(
    link: ET.Element,
    name: str,
    radius: float,
    length: float,
    xyz: tuple[float, float, float],
    rpy: tuple[float, float, float],
    color: str,
) -> None:
    visual = ET.SubElement(link, "visual", {"name": name})
    _pose(visual, xyz, rpy, relative_to="front_camera_link")
    geometry = ET.SubElement(visual, "geometry")
    cylinder = ET.SubElement(geometry, "cylinder")
    _text(cylinder, "radius", radius)
    _text(cylinder, "length", length)
    _material(visual, color)


def gen_sdf() -> dict[str, object]:
    sdf = ET.Element("sdf", {"version": SDF_VERSION})
    model = ET.SubElement(sdf, "model", {"name": MODEL_NAME})
    _text(model, "static", "true")

    include = ET.SubElement(model, "include")
    _text(include, "name", BASE_MODEL_INSTANCE_NAME)
    _text(include, "uri", BASE_MODEL_URI)

    camera_link = ET.SubElement(model, "link", {"name": "front_camera_link"})
    _pose(camera_link, CAMERA_XYZ_IN_MODEL_M, CAMERA_RPY_RAD, relative_to="__model__")

    _box_visual(
        camera_link,
        "camera_housing_visual",
        "0.09 0.075 0.05",
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
        "0.025 0.03 0.035 1",
    )
    _cylinder_visual(
        camera_link,
        "camera_lens_visual",
        0.022,
        0.022,
        (0.055, 0.0, 0.0),
        (0.0, math.pi / 2.0, 0.0),
        "0.04 0.10 0.22 1",
    )

    sensor = ET.SubElement(camera_link, "sensor", {"name": "front_rgb", "type": "camera"})
    _pose(sensor, (0.058, 0.0, 0.0), (0.0, 0.0, 0.0), relative_to="front_camera_link")
    camera = ET.SubElement(sensor, "camera")
    _text(camera, "horizontal_fov", f"{CAMERA_HORIZONTAL_FOV_RAD:.9g}")
    image = ET.SubElement(camera, "image")
    _text(image, "width", CAMERA_WIDTH_PX)
    _text(image, "height", CAMERA_HEIGHT_PX)
    _text(image, "format", "R8G8B8")
    clip = ET.SubElement(camera, "clip")
    _text(clip, "near", 0.08)
    _text(clip, "far", 80.0)
    _text(sensor, "always_on", "true")
    _text(sensor, "update_rate", CAMERA_UPDATE_RATE_HZ)
    _text(sensor, "visualize", "false")
    _text(sensor, "topic", CAMERA_TOPIC)

    optical = ET.SubElement(
        model,
        "frame",
        {"name": "front_camera_optical_frame", "attached_to": "front_camera_link"},
    )
    _pose(
        optical,
        (0.058, 0.0, 0.0),
        (-math.pi / 2.0, 0.0, -math.pi / 2.0),
        relative_to="front_camera_link",
    )

    return {
        "xml": sdf,
        "assumptions": [
            {
                "code": "demo_camera_mount",
                "message": "Camera mount pose is a demo assumption, not measured vehicle CAD.",
            },
            {
                "code": "static_pose_driven_vehicle",
                "message": "The static wrapper and its nested airframe are moved together by fake_drone_server through Gazebo set_pose.",
            },
        ],
        "metadata": {
            "target_consumer": "Gazebo Harmonic / gz-sim8",
            "sdf_version": SDF_VERSION,
        },
    }
