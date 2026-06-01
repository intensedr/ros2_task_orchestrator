#!/usr/bin/env bash
set -e

if [ -f "/opt/ros/${ROS_DISTRO}/setup.bash" ]; then
  source "/opt/ros/${ROS_DISTRO}/setup.bash"
fi

if [ -f "/ros_ws/install/setup.bash" ]; then
  source "/ros_ws/install/setup.bash"
fi

exec "$@"
