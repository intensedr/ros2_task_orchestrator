cmake_minimum_required(VERSION 3.8)
project(task_orchestrator_sim_drone)

find_package(ament_cmake REQUIRED)

install(
  DIRECTORY config launch missions models requests worlds
  DESTINATION share/${PROJECT_NAME}
)

install(
  PROGRAMS
    scripts/fake_drone_server
    scripts/submit_inspection_mission
  DESTINATION lib/${PROJECT_NAME}
)

ament_package()
