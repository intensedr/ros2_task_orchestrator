from glob import glob
import os

from setuptools import find_packages, setup


package_name = "task_orchestrator_ardupilot_bridge"

setup(
    name=package_name,
    version="1.0.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml", "requirements-ardupilot.txt"]),
        (os.path.join("share", package_name, "config"), glob("config/*.yaml")),
        (os.path.join("share", package_name, "launch"), glob("launch/*.launch.py")),
        (os.path.join("share", package_name, "missions"), glob("missions/*.json")),
    ],
    install_requires=[
        "setuptools>=59.6,<83",
        "pymavlink>=2.4.49,<2.5",
    ],
    zip_safe=True,
    maintainer="Dmitriy Romanov",
    maintainer_email="intense.dr@gmail.com",
    description="ROS2 action and telemetry bridge from Task Orchestrator to ArduPilot.",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "ardupilot_bridge_node = task_orchestrator_ardupilot_bridge.bridge_node:main",
        ],
    },
)
