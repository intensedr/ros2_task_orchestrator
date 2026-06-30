cmake_minimum_required(VERSION 3.8)
project(task_orchestrator_sim_drone)

find_package(ament_cmake REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(rosidl_default_generators REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "action/GoToWaypoint.action"
  "action/Hover.action"
  "action/Land.action"
  "action/Takeoff.action"
  DEPENDENCIES geometry_msgs
)

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

ament_export_dependencies(rosidl_default_runtime)
ament_package()
