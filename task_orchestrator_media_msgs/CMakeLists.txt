cmake_minimum_required(VERSION 3.8)
project(task_orchestrator_media_msgs)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(rosidl_default_generators REQUIRED)

set(msg_files
  "msg/AudioChunkV1.msg"
  "msg/MediaArtifactV1.msg"
  "msg/MediaDeviceV1.msg"
  "msg/MediaDeviceStateV1.msg"
  "msg/MediaErrorCodeV1.msg"
  "msg/MediaSessionV1.msg"
  "msg/MediaSessionStateV1.msg"
  "msg/MediaUploadStateV1.msg"
)

set(srv_files
  "srv/GetMediaArtifactAccessV1.srv"
  "srv/GetMediaSessionV1.srv"
  "srv/ListMediaDevicesV1.srv"
  "srv/StartRecordingV1.srv"
  "srv/StopRecordingV1.srv"
)

set(action_files
  "action/CaptureMediaV1.action"
)

rosidl_generate_interfaces(${PROJECT_NAME}
  ${msg_files}
  ${srv_files}
  ${action_files}
  DEPENDENCIES builtin_interfaces
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
