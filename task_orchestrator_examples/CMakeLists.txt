cmake_minimum_required(VERSION 3.8)
project(task_orchestrator_examples NONE)

find_package(ament_cmake REQUIRED)

install(
  DIRECTORY config launch missions
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
