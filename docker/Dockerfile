ARG ROS_DISTRO=humble
FROM ros:${ROS_DISTRO}-ros-base

SHELL ["/bin/bash", "-c"]

ENV ROS_WS=/ros_ws
WORKDIR ${ROS_WS}

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      python3-colcon-common-extensions \
      python3-pip \
      python3-rosdep \
      python3-vcstool && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY task_orchestrator_msgs src/task_orchestrator_msgs
COPY task_orchestrator_core src/task_orchestrator_core

RUN . /opt/ros/${ROS_DISTRO}/setup.bash && \
    rosdep update && \
    rosdep install --from-paths src --ignore-src -r -y --rosdistro ${ROS_DISTRO} && \
    colcon build --symlink-install

COPY docker/ros_entrypoint.sh /ros_entrypoint.sh

ENTRYPOINT ["/ros_entrypoint.sh"]
CMD ["bash"]
