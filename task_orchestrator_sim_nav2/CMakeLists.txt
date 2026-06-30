cmake_minimum_required(VERSION 3.8)
project(task_orchestrator_sim_nav2 NONE)

find_package(ament_cmake REQUIRED)

install(
  DIRECTORY config launch missions requests
  DESTINATION share/${PROJECT_NAME}
)

install(
  PROGRAMS scripts/submit_route_mission
  DESTINATION lib/${PROJECT_NAME}
)

ament_package()
