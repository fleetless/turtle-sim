# SPDX-License-Identifier: Apache-2.0
cmake_minimum_required(VERSION 3.8)
project(fleetless_turtle_msgs)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(std_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/TurtleStatus.msg"
  "msg/World.msg"
  "srv/Teleport.srv"
  "srv/SetBlock.srv"
  "action/DriveTo.action"
  "action/DriveToSquare.action"
  "action/Dock.action"
  DEPENDENCIES std_msgs
)

if(BUILD_TESTING)
  find_package(ament_cmake_pytest REQUIRED)
  ament_add_pytest_test(test_interfaces test/test_interfaces.py)
endif()

ament_export_dependencies(rosidl_default_runtime)
ament_package()
