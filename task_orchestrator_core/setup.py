from glob import glob
import os
import warnings

from setuptools import find_packages, setup


package_name = "task_orchestrator_core"

warnings.filterwarnings(
    "ignore",
    message=r"Unbuilt egg for pytest-repeat.*",
    category=UserWarning,
    module=r"setuptools\.command\.easy_install",
)

setup(
    name=package_name,
    version="0.3.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
        (os.path.join("share", package_name, "launch"), glob("launch/*.launch.py")),
        (os.path.join("share", package_name, "params"), glob("params/*.yaml")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Dmitriy Romanov",
    maintainer_email="intense.dr@gmail.com",
    description="Core ROS2 node for ROS2 Task Orchestrator.",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "task_orchestrator_node = task_orchestrator_core.orchestrator_node:main",
        ],
    },
)
