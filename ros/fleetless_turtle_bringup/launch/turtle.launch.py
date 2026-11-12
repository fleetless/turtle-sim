# SPDX-License-Identifier: Apache-2.0
"""The one launch file: the simulator, the view and, with a token, the Fleetless Bridge.

    ros2 launch fleetless_turtle_bringup turtle.launch.py

The bridge is the bridge's own launch file, included unchanged: this is how
any ROS 2 robot starts it. Everything a user may set comes from the
environment, because that is what `docker run -e` reaches.

    FLEETLESS_TOKEN       the robot's token; unset or empty, no bridge starts
    FLEETLESS_CLOUD_URL   read by the bridge itself
    TURTLE_MAP            the map file
    TURTLE_BATTERY_DRAIN  factor on the battery's discharge rates
    TURTLE_VIEW_PORT      the port the view listens on
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription, LogInfo, RegisterEventHandler
from launch.event_handlers import OnProcessExit
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import EnvironmentVariable
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

NO_TOKEN = "FLEETLESS_TOKEN is not set: simulator and view are running, no bridge was started."


def from_environment(name: str, default: str) -> ParameterValue:
    """An environment variable as a parameter, as the text it is.

    The node checks it. A value it cannot use ends it with one sentence;
    letting launch guess a type first would end it with a stack trace.
    """
    return ParameterValue(EnvironmentVariable(name, default_value=default), value_type=str)


def needed(node: Node, what: str) -> RegisterEventHandler:
    """When this node ends by itself, everything ends, and `ros2 launch` exits non-zero."""

    def give_up(event, context):
        if context.is_shutdown:
            return None  # everything is stopping anyway: Ctrl-C, `docker stop`
        # An exception is the one way a launch file has to make `ros2 launch`
        # return something other than 0. Launch logs the sentence, stops the
        # other processes and exits 1.
        raise RuntimeError(f"{what} ended with exit code {event.returncode}. Stopping the rest.")

    return RegisterEventHandler(OnProcessExit(target_action=node, on_exit=give_up))


def generate_launch_description() -> LaunchDescription:
    simulator = Node(
        package="fleetless_turtle_sim",
        executable="simulator",
        output="screen",
        parameters=[
            {
                "map": from_environment("TURTLE_MAP", "/opt/turtle/maps/garden.png"),
                "battery_drain": from_environment("TURTLE_BATTERY_DRAIN", "1.0"),
            }
        ],
    )
    viewer = Node(
        package="fleetless_turtle_viewer",
        executable="viewer",
        output="screen",
        parameters=[{"port": from_environment("TURTLE_VIEW_PORT", "8765")}],
    )
    parts = [
        simulator,
        viewer,
        needed(simulator, "The simulator"),
        needed(viewer, "The view"),
    ]

    if os.environ.get("FLEETLESS_TOKEN"):
        bridge = os.path.join(
            get_package_share_directory("fleetless_bridge"), "launch", "bridge.launch.py"
        )
        parts.append(IncludeLaunchDescription(PythonLaunchDescriptionSource(bridge)))
    else:
        parts.append(LogInfo(msg=NO_TOKEN))

    return LaunchDescription(parts)
