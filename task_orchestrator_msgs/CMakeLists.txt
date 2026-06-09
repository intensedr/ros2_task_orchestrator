cmake_minimum_required(VERSION 3.8)
project(task_orchestrator_msgs)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(rosidl_default_generators REQUIRED)

set(msg_files
  "msg/ActiveTaskArrayV1.msg"
  "msg/ActiveTaskV1.msg"
  "msg/ErrorCodeV1.msg"
  "msg/SubtaskGoalV1.msg"
  "msg/SubtaskResultV1.msg"
  "msg/TaskEventV1.msg"
  "msg/TaskFeedbackV1.msg"
  "msg/TaskRecordV1.msg"
  "msg/TaskResultV1.msg"
  "msg/TaskSpecV1.msg"
  "msg/TaskStatusV1.msg"
)

set(srv_files
  "srv/CancelTasksV1.srv"
  "srv/GetTaskV1.srv"
  "srv/ListEventsV1.srv"
  "srv/ListTaskRecordsV1.srv"
  "srv/ListTasksV1.srv"
  "srv/PauseTasksV1.srv"
  "srv/ReloadConfigV1.srv"
  "srv/ResumeTasksV1.srv"
  "srv/StopTasksV1.srv"
  "srv/ValidateTaskV1.srv"
)

set(action_files
  "action/ExecuteTaskV1.action"
  "action/MissionV1.action"
  "action/WaitV1.action"
)

rosidl_generate_interfaces(${PROJECT_NAME}
  ${msg_files}
  ${srv_files}
  ${action_files}
  DEPENDENCIES builtin_interfaces
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
