# SPDX-License-Identifier: Apache-2.0
"""The whole bringup on its ROS graph: what only shows with every node there.

The simulator says how the joints stand, robot_state_publisher turns that
into transforms and publishes the model, and the cameras draw the world the
simulator publishes. Each node's own tests play the others; here they are
the real ones, started by the one launch file.
"""

import math
import os
import signal
import socket
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import rclpy
from ament_index_python.packages import get_package_share_directory
from geometry_msgs.msg import Twist
from rclpy.executors import SingleThreadedExecutor
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image, JointState
from std_msgs.msg import String
from tf2_msgs.msg import TFMessage

GARDEN = Path(__file__).resolve().parents[3] / "maps" / "garden.png"
URDF = Path(get_package_share_directory("fleetless_turtle_description")) / "urdf" / "turtle.urdf"

LATCHED = QoSProfile(
    depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL, reliability=ReliabilityPolicy.RELIABLE
)
MOVING = {"head", "leg_front_left", "leg_front_right", "leg_hind_left", "leg_hind_right"}


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture(scope="module")
def graph():
    """The bringup, running, and a node that listens to it."""
    environment = {**os.environ, "TURTLE_MAP": str(GARDEN), "TURTLE_VIEW_PORT": str(free_port())}
    environment.pop("FLEETLESS_TOKEN", None)
    bringup = subprocess.Popen(
        ["ros2", "launch", "fleetless_turtle_bringup", "turtle.launch.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=environment,
    )
    lines = []
    threading.Thread(target=lambda: lines.extend(bringup.stdout), daemon=True).start()

    rclpy.init()
    node = rclpy.create_node("bringup_test")
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    spinning = threading.Thread(target=executor.spin, daemon=True)
    spinning.start()
    try:
        yield node, lines
    finally:
        executor.shutdown()
        spinning.join(timeout=5.0)
        node.destroy_node()
        rclpy.shutdown()
        bringup.send_signal(signal.SIGINT)
        try:
            bringup.wait(timeout=20.0)
        finally:
            if bringup.poll() is None:
                bringup.kill()
    said = "".join(lines)
    assert bringup.returncode == 0, said
    assert "Traceback" not in said, said


def heard_on(node, msg_type, topic: str, qos=10) -> list:
    """A list that fills with the topic's messages while the test goes on."""
    heard = []
    node.create_subscription(msg_type, topic, heard.append, qos)
    return heard


def wait_for(condition, timeout: float = 30.0, what: str = "the condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(f"{what} did not happen within {timeout} s.")


def yaw_of(rotation) -> float:
    return 2.0 * math.atan2(rotation.z, rotation.w)


def test_the_model_is_published_as_the_urdf_of_the_description_package(graph):
    node, _ = graph
    heard = heard_on(node, String, "/robot_description", LATCHED)
    wait_for(lambda: heard, what="/robot_description arriving")
    assert heard[0].data == URDF.read_text()
    assert ET.fromstring(heard[0].data).get("name") == "turtle"


def test_every_moving_link_has_a_transform_that_follows_the_joints(graph):
    node, _ = graph
    transforms = heard_on(node, TFMessage, "/tf", 100)
    joints = heard_on(node, JointState, "/joint_states")
    commands = node.create_publisher(Twist, "/cmd_vel", 10)

    def latest(child: str):
        for message in reversed(transforms):
            for transform in message.transforms:
                if transform.child_frame_id == child:
                    return transform
        return None

    # The simulator's own transform, and one per joint from robot_state_publisher:
    # the tree is map, odom, base_link, then the head and the four legs.
    wait_for(lambda: all(latest(child) for child in MOVING | {"base_link"}), what="the transforms")
    assert latest("base_link").header.frame_id == "odom"
    for child in MOVING:
        assert latest(child).header.frame_id == "base_link", child
    # Where the URDF puts the head's joint, and looking ahead while nothing turns.
    head = latest("head").transform
    at = (head.translation.x, head.translation.y, head.translation.z)
    assert at == pytest.approx((0.059, 0.0, 0.026), abs=1e-9)
    assert yaw_of(head.rotation) == pytest.approx(0.0, abs=1e-9)

    # Turning to the left: the head's joint follows, and so does its transform.
    wait_for(lambda: commands.get_subscription_count() > 0, what="the simulator hearing /cmd_vel")
    turn = Twist()
    turn.angular.z = 1.0
    half_of_its_30_degrees = math.radians(15.0)

    def head_joint() -> float:
        return dict(zip(joints[-1].name, joints[-1].position, strict=True))["head_joint"]

    def both_turned() -> bool:
        commands.publish(turn)  # for as long as it takes: a command is good for half a second
        joint, transform = head_joint(), yaw_of(latest("head").transform.rotation)
        return abs(joint - half_of_its_30_degrees) < 1e-6 and abs(transform - joint) < 1e-6

    wait_for(both_turned, timeout=10.0, what="the head's transform following its joint")


def test_the_cameras_draw_the_world_the_simulator_publishes(graph):
    node, _ = graph
    top = heard_on(node, Image, "/turtle/camera/top/image_raw")
    eye = heard_on(node, Image, "/turtle/camera/eye/image_raw")
    wait_for(lambda: top and eye, what="both pictures arriving")
    assert (top[-1].width, top[-1].height, top[-1].encoding) == (480, 480, "rgb8")
    assert (eye[-1].width, eye[-1].height, eye[-1].encoding) == (320, 240, "rgb8")


def test_the_models_publisher_keeps_its_list_of_links_to_itself(graph):
    _, lines = graph
    # It would print one line per link at start. Only its warnings are news.
    said = "".join(lines)
    assert "robot_state_publisher" in said  # launch names the process it started
    assert "got segment" not in said
