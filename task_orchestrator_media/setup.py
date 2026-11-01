import os
import warnings
from glob import glob

from setuptools import find_packages, setup

package_name = "task_orchestrator_media"

warnings.filterwarnings(
    "ignore",
    message=r"Unbuilt egg for pytest-repeat.*",
    category=UserWarning,
    module=r"setuptools\.command\.easy_install",
)

setup(
    name=package_name,
    version="1.0.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
        (os.path.join("share", package_name, "config"), glob("config/*.yaml")),
        (os.path.join("share", package_name, "launch"), glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools>=59.6,<83"],
    zip_safe=True,
    maintainer="Dmitriy Romanov",
    maintainer_email="intense.dr@gmail.com",
    description="Local media capture and artifact spool for ROS2 Task Orchestrator.",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "media_manager_node = task_orchestrator_media.manager_node:main",
        ],
    },
)
