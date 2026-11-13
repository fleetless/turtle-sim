# SPDX-License-Identifier: Apache-2.0
"""The one launch file: the turtle's own nodes and, with a token, the Fleetless Bridge.

    ros2 launch fleetless_turtle_bringup turtle.launch.py

The turtle's own are the simulator, the cameras, the view and
robot_state_publisher, which publishes the model. The bridge is the bridge's
own launch file, included unchanged: this is how any ROS 2 robot starts it.
Everything a user may set comes from the environment, because that is what
`docker run -e` reaches.

    FLEETLESS_TOKEN       the robot's token; unset or empty, no bridge starts
    FLEETLESS_CLOUD_URL   read by the bridge itself
    TURTLE_MAP            the map file
    TURTLE_BATTERY_DRAIN  factor on the battery's discharge rates
    TURTLE_VIEW_PORT      the port the view listens on
"""

import os
from xml.etree import ElementTree

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


def model() -> str:
    """The turtle's URDF, as the text robot_state_publisher is given."""
    path = os.path.join(
        get_package_share_directory("fleetless_turtle_description"), "urdf", "turtle.urdf"
    )
    with open(path) as urdf:
        return urdf.read()


def without_shared_memory() -> dict[str, str]:
    """The simulator's own environment: a Fast DDS that speaks UDP only.

    When a node with a client of one of the turtle's services or actions
    joins, Fast DDS opens the newcomer's shared-memory port under the lock
    that every send of a process takes. Where it finds the port's listener
    not yet seen alive, it sleeps there for a third of a second. The
    simulator's one thread steps the world and publishes, and it waited for
    that lock: the world stood, three times in thirty joins on one start
    and sixteen times on another.

    A process without the shared-memory transport opens no such port. What
    the simulator sends is small beside a picture: the world and the map
    are 65 kB each and go out when they change, a full trail is 144 kB,
    once a second while it grows. UDP on the loopback carries that at no
    cost that shows. The cameras keep shared memory: a picture is 691 kB,
    and ten go out a second.

    Where the discovery range is LOCALHOST, as the image sets it, ROS names
    both transports itself, and FASTDDS_BUILTIN_TRANSPORTS is not asked. So
    this one process is told to leave the range to Fast DDS, and a profile
    says what LOCALHOST says, with the one transport.

    Only where that is all ROS would have said. Under another range, which
    somebody chose, the simulator is left as ROS has it. So it is with
    peers named in ROS_STATIC_PEERS: ROS has every node look for them, and
    the profile knows none. And with ROS_LOCALHOST_ONLY=1, the older way to
    say LOCALHOST: ROS then passes over the range this process would be
    given, and the shared memory would be back with the profile for show.

    A profile that Fast DDS cannot read it passes over with one line, and
    goes on with its defaults: shared memory, and multicast to whoever
    shares the network. So the file is read here first, and a start
    without it is no start.
    """
    said = os.environ.get
    if (
        said("ROS_AUTOMATIC_DISCOVERY_RANGE") != "LOCALHOST"
        or said("ROS_STATIC_PEERS")
        # ROS reads the first character: "1" and whatever begins with it.
        or said("ROS_LOCALHOST_ONLY", "").startswith("1")
    ):
        return {}
    profile = os.path.join(
        get_package_share_directory("fleetless_turtle_bringup"), "config", "simulator.fastdds.xml"
    )
    try:
        ElementTree.parse(profile)
    except (OSError, ElementTree.ParseError) as error:
        raise RuntimeError(
            f"The simulator's Fast DDS profile cannot be read: {error}. Without it the "
            "simulator would announce itself beyond this machine. The turtle does not start."
        ) from None
    return {
        "ROS_AUTOMATIC_DISCOVERY_RANGE": "SYSTEM_DEFAULT",
        "FASTRTPS_DEFAULT_PROFILES_FILE": profile,
    }


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
        additional_env=without_shared_memory(),
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
    cameras = Node(package="fleetless_turtle_cameras", executable="cameras", output="screen")
    # ROS's own node for a robot's model. It publishes the URDF on
    # /robot_description, where the bridge reads it, and turns the simulator's
    # /joint_states into the transforms of the head and the legs. It lists
    # every link it read at start; only its warnings are the turtle's news.
    model_publisher = Node(
        package="robot_state_publisher",
        executable="robot_state_publisher",
        output="screen",
        parameters=[{"robot_description": ParameterValue(model(), value_type=str)}],
        ros_arguments=["--log-level", "warn"],
    )
    parts = [
        simulator,
        cameras,
        model_publisher,
        viewer,
        needed(simulator, "The simulator"),
        needed(cameras, "The cameras"),
        needed(model_publisher, "The model's publisher"),
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
