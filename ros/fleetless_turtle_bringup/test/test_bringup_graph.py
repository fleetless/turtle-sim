# SPDX-License-Identifier: Apache-2.0
"""The whole bringup on its ROS graph: what only shows with every node there.

The simulator says how the joints stand, robot_state_publisher turns that
into transforms and publishes the model, and the cameras draw the world the
simulator publishes, with the path of a goal that really runs and the trail
the turtle really leaves. Each node's own tests play the others; here they
are the real ones, started by the one launch file.
"""

import itertools
import math
import os
import signal
import socket
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest
import rclpy
from ament_index_python.packages import get_package_share_directory
from fleetless_turtle_msgs.action import DriveTo
from fleetless_turtle_msgs.srv import Teleport
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.action import ActionClient
from rclpy.executors import SingleThreadedExecutor
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image, JointState
from std_msgs.msg import String
from std_srvs.srv import SetBool
from tf2_msgs.msg import TFMessage

from fleetless_turtle_cameras.render import constants as look

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


# --- A goal, seen by both cameras -------------------------------------------


def answer(future, timeout: float = 30.0, what: str = "an answer"):
    """The result of a future that the fixture's own thread completes."""
    wait_for(future.done, timeout, what)
    return future.result()


def plan_coloured(pixels: np.ndarray) -> np.ndarray:
    """Where a picture is violet, as the path is: blue over red over green."""
    red, green, blue = (pixels[..., i].astype(int) for i in range(3))
    return (blue > red) & (red > green) & (blue - green >= 30)


def trail_coloured(pixels: np.ndarray) -> np.ndarray:
    """Where a picture is orange, as the trail is: red well over green, green over blue."""
    red, green, blue = (pixels[..., i].astype(int) for i in range(3))
    return (red > green + 10) & (red - blue >= 60)


def test_both_pictures_flow_while_a_goal_runs_and_top_shows_where_it_leads(graph):
    """A drive of eleven metres along the lawn, the trail on, both cameras watched.

    The cameras node hears the simulator's own /turtle/plan and
    /turtle/trail. `top` draws the path from the turtle on and the trail
    behind it, so on a drive due east the picture is orange west of the
    turtle and violet east of it, and never violet behind it. And the
    simulator keeps its pace while it is being watched: judged by the
    odometry's own stamps, as in the simulator's own launch tests.
    """
    node, _ = graph
    # The colours this test looks for are the ones the renderer draws with.
    assert plan_coloured(np.array([look.PLAN], np.uint8))[0]
    assert trail_coloured(np.array([look.TRAIL], np.uint8))[0]
    assert not plan_coloured(np.array([look.TRAIL], np.uint8))[0]

    start, target = (20, 30), (130, 30)
    teleport = node.create_client(Teleport, "/turtle/teleport")
    set_trail = node.create_client(SetBool, "/turtle/set_trail")
    drive_to = ActionClient(node, DriveTo, "/turtle/drive_to")
    assert teleport.wait_for_service(timeout_sec=30.0)
    assert set_trail.wait_for_service(timeout_sec=30.0)
    assert drive_to.wait_for_server(timeout_sec=30.0)
    request = Teleport.Request(x=start[0], y=start[1], heading_deg=0.0)
    assert answer(teleport.call_async(request)).success
    assert answer(set_trail.call_async(SetBool.Request(data=True))).success

    # A picture is 700 kB and ten come a second: only what the test asks of
    # each one is kept. For `top`, how much of the turtle's own row is
    # violet and how much orange, west of the turtle and east of it. The
    # turtle is the middle of the picture; its plate and arrowhead are left out.
    half, plate = look.TOP_SIZE_PX // 2, 24
    tops, eyes = [], []

    def on_top(image: Image) -> None:
        pixels = np.frombuffer(bytes(image.data), np.uint8).reshape(image.height, image.width, 3)
        row = pixels[half - 4 : half + 4]
        west, east = row[:, : half - plate], row[:, half + plate :]
        tops.append(
            {
                "at": time.monotonic(),
                "size": (image.width, image.height, image.encoding),
                "path_ahead": int(plan_coloured(east).sum()),
                "path_behind": int(plan_coloured(west).sum()),
                "trail_behind": int(trail_coloured(west).sum()),
            }
        )

    def on_eye(image: Image) -> None:
        eyes.append({"at": time.monotonic(), "size": (image.width, image.height, image.encoding)})

    node.create_subscription(Image, "/turtle/camera/top/image_raw", on_top, 10)
    node.create_subscription(Image, "/turtle/camera/eye/image_raw", on_eye, 10)
    odometry = heard_on(node, Odometry, "/odom", 500)
    wait_for(lambda: tops and eyes, what="both pictures arriving")
    assert tops[-1]["path_ahead"] == 0  # no goal, no path

    handle = answer(drive_to.send_goal_async(DriveTo.Goal(x=target[0], y=target[1])))
    assert handle.accepted
    began = time.monotonic()
    result = answer(handle.get_result_async(), timeout=90.0, what="the goal's result").result
    ended = time.monotonic()
    assert (result.success, result.reason) == (True, "reached")

    # Both pictures flowed for as long as the goal ran: at their size, some
    # of them a second at the least, and never a second without one.
    for name, pictures, size in (
        ("top", tops, (480, 480, "rgb8")),
        ("eye", eyes, (320, 240, "rgb8")),
    ):
        during = [picture for picture in pictures if began <= picture["at"] <= ended]
        assert {picture["size"] for picture in during} == {size}, name
        rate = len(during) / (ended - began)
        gaps = [b["at"] - a["at"] for a, b in itertools.pairwise(during)]
        print(f"bringup: {name}: {rate:.1f} pictures a second, longest gap {max(gaps):.2f} s")
        assert rate > 5.0, f"{name}: {rate:.1f} pictures a second"
        assert max(gaps) < 1.0, f"{name}: {max(gaps):.2f} s without a picture"

    # Well under way, between the fifth second and the fifteenth: the path
    # ahead, the trail behind, and no path behind.
    under_way = [picture for picture in tops if began + 5.0 <= picture["at"] <= began + 15.0]
    assert len(under_way) > 30
    assert all(picture["path_ahead"] > 100 for picture in under_way)
    assert all(picture["trail_behind"] > 50 for picture in under_way)
    assert all(picture["path_behind"] == 0 for picture in under_way)
    # The goal has ended: the path is gone from the picture, the trail stays.
    wait_for(lambda: tops[-1]["at"] > ended + 0.5, what="a picture after the goal")
    assert tops[-1]["path_ahead"] == 0 and tops[-1]["trail_behind"] > 50

    # The simulator's pace while it was watched, by its own stamps.
    def stamp(message) -> float:
        return message.header.stamp.sec + message.header.stamp.nanosec / 1e9

    speed = 0.5
    full = [message for message in odometry if abs(message.twist.twist.linear.x - speed) < 1e-6]
    assert len(full) > 300, "the turtle hardly reached full speed"
    seconds = stamp(full[-1]) - stamp(full[0])
    metres = full[-1].pose.pose.position.x - full[0].pose.pose.position.x
    pace = metres / (speed * seconds)
    gaps = [stamp(b) - stamp(a) for a, b in itertools.pairwise(full)]
    long_gaps = [round(gap * 1000) for gap in gaps if gap > 0.09]
    measured = (
        f"pace {pace:.3f} over {seconds:.1f} s with both cameras watched, longest gap between "
        f"two odometry stamps {max(gaps) * 1000:.0f} ms, gaps over 90 ms: {long_gaps}"
    )
    print(f"bringup: {measured}")
    assert pace == pytest.approx(1.0, abs=0.03), measured
