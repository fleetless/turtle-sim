# SPDX-License-Identifier: Apache-2.0
"""The cameras node alone, against a turtle this test plays itself.

What it does before it knows the world, with a world it cannot draw and
with a pose that is no number, and how it ends when it is asked to.
"""

import math
import signal
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import rclpy
from ament_index_python.packages import get_package_prefix
from fleetless_turtle_msgs.msg import World
from nav_msgs.msg import Odometry
from probe import Probe
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image

from fleetless_turtle_cameras.render import constants as look

CAMERAS = (
    Path(get_package_prefix("fleetless_turtle_cameras")) / "lib/fleetless_turtle_cameras/cameras"
)
TOP = "/turtle/camera/top/image_raw"
EYE = "/turtle/camera/eye/image_raw"
STARTED = "The cameras draw top (480 x 480) and eye (320 x 240), 10 pictures a second each."

LATCHED = QoSProfile(
    depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL, reliability=ReliabilityPolicy.RELIABLE
)


def started() -> tuple[subprocess.Popen, list[str]]:
    """Start the node and wait until it says what it draws."""
    process = subprocess.Popen(
        [str(CAMERAS)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    lines = []
    threading.Thread(target=lambda: lines.extend(process.stdout), daemon=True).start()
    deadline = time.monotonic() + 60.0
    while not any(STARTED in line for line in lines):
        assert process.poll() is None, "".join(lines)
        assert time.monotonic() < deadline, "".join(lines)
        time.sleep(0.05)
    return process, lines


def stopped(process: subprocess.Popen, lines: list[str], how=signal.SIGINT) -> str:
    """Send the signal, wait for the end and return everything the node said."""
    assert process.poll() is None, "".join(lines)  # it keeps running until told
    process.send_signal(how)
    try:
        process.wait(timeout=20.0)
    finally:
        if process.poll() is None:
            process.kill()
    time.sleep(0.2)  # the reader takes the last lines
    return "".join(lines)


def world_of(kinds: np.ndarray) -> World:
    world = World()
    world.header.frame_id = "map"
    world.height, world.width = kinds.shape
    world.resolution = 0.1
    world.kinds = kinds.tobytes()
    return world


def odometry_at(x: float, y: float, heading: float = 0.0) -> Odometry:
    odometry = Odometry()
    odometry.pose.pose.position.x, odometry.pose.pose.position.y = x, y
    odometry.pose.pose.orientation.z = math.sin(heading / 2)
    odometry.pose.pose.orientation.w = math.cos(heading / 2)
    return odometry


def picture_of(image: Image) -> np.ndarray:
    return np.frombuffer(image.data, np.uint8).reshape(image.height, image.width, 3)


@pytest.fixture(scope="module")
def probe():
    rclpy.init()
    probe = Probe("fake_turtle")
    yield probe
    probe.close()
    rclpy.shutdown()


@pytest.fixture(scope="module")
def cameras(probe):
    """A running cameras node, and what it said once it has ended."""
    process, lines = started()
    try:
        yield process, lines
    finally:
        said = stopped(process, lines) if process.poll() is None else "".join(lines)
    assert process.returncode == 0, said
    assert "Traceback" not in said, said


def found_by(publisher, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while publisher.get_subscription_count() == 0:
        assert time.monotonic() < deadline, "the cameras node did not subscribe"
        time.sleep(0.05)


# The tests run in the order they stand in: the node learns the pose, then a
# world it cannot draw, then one it can.


def test_without_a_world_no_picture_is_published(probe, cameras):
    top = probe.listen(Image, TOP)
    eye = probe.listen(Image, EYE)
    odometry = probe.node.create_publisher(Odometry, "/odom", 10)
    found_by(odometry)
    for _ in range(15):
        odometry.publish(odometry_at(3.25, 3.25))
        time.sleep(0.1)
    assert (len(top), len(eye)) == (0, 0)


def test_a_world_with_the_wrong_number_of_cells_is_not_drawn_and_logged_once(probe, cameras):
    _, lines = cameras
    top = probe.listen(Image, TOP)
    world = probe.node.create_publisher(World, "/turtle/world", LATCHED)
    found_by(world)
    broken = world_of(np.zeros((8, 8), np.uint8))
    broken.kinds = bytes(60)
    for _ in range(3):
        world.publish(broken)
        time.sleep(0.4)
    warning = "A /turtle/world of 8 x 8 cells came with 60 of them. It is not drawn."
    assert sum(warning in line for line in lines) == 1, "".join(lines)
    assert len(top) == 0
    probe.node.destroy_publisher(world)


def test_a_world_of_any_size_is_drawn_once_world_and_pose_are_known(probe, cameras):
    top = probe.listen(Image, TOP)
    eye = probe.listen(Image, EYE)
    world = probe.node.create_publisher(World, "/turtle/world", LATCHED)
    odometry = probe.node.create_publisher(Odometry, "/odom", 10)
    found_by(world)
    found_by(odometry)
    kinds = np.zeros((48, 64), np.uint8)
    kinds[30:36, 42:44] = World.WALL  # one metre east of the turtle
    world.publish(world_of(kinds))
    odometry.publish(odometry_at(3.25, 3.25))

    seen = picture_of(Probe.until(top, lambda _: True, what=TOP))
    assert seen.shape == (480, 480, 3)
    middle = look.TOP_SIZE_PX // 2
    assert (seen[middle, middle] == look.TURTLE_SHELL_TOP).all()
    assert (seen[middle, middle + 42] == look.WALL).all()
    # The grid ends 1.55 m north of this turtle.
    assert (seen[middle - 80, middle] == look.OUTSIDE).all()
    ahead = picture_of(Probe.until(eye, lambda _: True, what=EYE))
    assert ahead.shape == (240, 320, 3)
    horizon = look.EYE_HEIGHT_PX // 2
    assert np.abs(ahead[horizon, look.EYE_WIDTH_PX // 2].astype(int) - look.WALL).max() < 30


def test_a_pose_that_is_no_number_is_not_taken(probe, cameras):
    top = probe.listen(Image, TOP)
    odometry = probe.node.create_publisher(Odometry, "/odom", 10)
    found_by(odometry)
    for x, y in ((math.nan, 3.25), (3.25, math.inf)):
        odometry.publish(odometry_at(x, y))
    time.sleep(0.5)
    middle = look.TOP_SIZE_PX // 2
    seen = picture_of(Probe.until(top, lambda _: True, what=TOP))
    # Still the picture from where the turtle was last known to be.
    assert (seen[middle, middle + 42] == look.WALL).all()
    process, lines = cameras
    assert process.poll() is None, "".join(lines)


@pytest.mark.parametrize("how", [signal.SIGINT, signal.SIGTERM], ids=["SIGINT", "SIGTERM"])
def test_a_signal_ends_the_node_cleanly(how):
    process, lines = started()
    time.sleep(0.5)
    said = stopped(process, lines, how)
    assert process.returncode == 0, said
    assert "Traceback" not in said, said


@pytest.mark.parametrize("after_s", [0.05, 0.3, 0.8, 1.5])
def test_a_stop_while_the_node_is_still_starting_leaves_no_stack_trace(after_s):
    """A stop as launch sends it in the container: an interrupt, and SIGTERM a second later.

    There every node is born with SIGINT ignored (docker/entrypoint.sh says
    why), so an interrupt passes it by until `start.py` has asked for the
    signal: Python's own start, before a line of ours runs, would answer it
    with a stack trace. From `start.py` on, the interrupt alone ends the
    node, with exit 0. A node that had not got that far ends by the SIGTERM,
    which is what launch then sends.
    """
    born_ignoring_interrupts = ["bash", "-c", "trap '' INT; exec \"$0\"", str(CAMERAS)]
    process = subprocess.Popen(
        born_ignoring_interrupts, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    time.sleep(after_s)
    process.send_signal(signal.SIGINT)
    try:
        try:
            said, _ = process.communicate(timeout=1.0)
        except subprocess.TimeoutExpired:
            process.send_signal(signal.SIGTERM)
            said, _ = process.communicate(timeout=20.0)
    finally:
        if process.poll() is None:
            process.kill()
    assert "Traceback" not in said and "KeyboardInterrupt" not in said, said
    assert process.returncode in (0, -signal.SIGTERM), said
