# SPDX-License-Identifier: Apache-2.0
"""The whole bringup while ROS clients come and go: the world goes on.

A node with a client of one of the turtle's services or actions that joined
the graph, a `ros2 action send_goal` from a shell for one, used to make the
world stand for a third of a second. Fast DDS opens the newcomer's
shared-memory port under the lock every send of a process takes, and where
it finds the port's listener not yet seen alive it sleeps there, a third of
a second. The simulator's thread, about to publish, waited for that lock.

The simulator's process therefore speaks UDP only: under the discovery
range LOCALHOST, which the image sets, the launch file gives it a Fast DDS
profile of its own, and tells it to leave the range to its middleware. That
is safe only where the profile is what the process then goes by: a
middleware left to itself announces a node to whoever shares the network.
The tests hold, in this order:

- what the launch file decides, by its own function under every setting
  that matters: the range, peers, ROS_LOCALHOST_ONLY, the middleware, and a
  Fast DDS profile of the user's own;
- in a bringup under five such settings, the simulator is left as ROS has
  it, and runs no thread for multicast;
- a profile that cannot be read ends the start with one sentence. Fast DDS
  would pass over it with one line and go on with its defaults, shared
  memory and multicast;
- the simulator took the profile, seen by the threads Fast DDS runs in it,
  and the cameras kept their shared memory, which the pictures travel by;
- ten clients join during a drive, and no gap between two odometry stamps
  exceeds 90 ms.

The last one can pass by luck without the cure, since the world did not
stand at every join: three times in thirty on one start, sixteen times on
another. It is the guard. The proof is a measurement,
`tools/join-probe.sh`, in fresh containers and thirty joins each.
"""

import importlib.util
import itertools
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import rclpy
from ament_index_python.packages import PackageNotFoundError, get_package_share_directory
from fleetless_turtle_msgs.srv import Teleport
from nav_msgs.msg import Odometry
from rclpy.executors import SingleThreadedExecutor

MAPS = Path(__file__).resolve().parents[3] / "maps"
GARDEN = MAPS / "garden.png"

SPEED_M_S = 0.5
JOINS = 10
GAP_S = 0.09
"""Odometry comes every 50 ms. Over 90 ms, one is missing."""

# The newcomer: a node with an action client that sees its server, stays
# half a second and leaves. Exit code 0 says it saw the server.
PASSER_BY = """
import sys, time
import rclpy
from rclpy.action import ActionClient
from fleetless_turtle_msgs.action import DriveTo
rclpy.init()
node = rclpy.create_node("passer_by")
client = ActionClient(node, DriveTo, "/turtle/drive_to")
found = client.wait_for_server(timeout_sec=20.0)
time.sleep(0.5)
client.destroy()
node.destroy_node()
rclpy.shutdown()
sys.exit(0 if found else 3)
"""

# The process at the controls: a circle of one metre radius at full speed,
# twenty commands a second. A process of its own, so that the test's own
# process sends nothing while it listens.
DRIVER = f"""
import time
import rclpy
from geometry_msgs.msg import Twist
from rclpy.signals import SignalHandlerOptions
rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
node = rclpy.create_node("driver")
commands = node.create_publisher(Twist, "/cmd_vel", 10)
twist = Twist()
twist.linear.x = {SPEED_M_S}
twist.angular.z = 0.5
while True:
    commands.publish(twist)
    time.sleep(0.05)
"""


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_for(condition, timeout: float = 30.0, what: str = "the condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(f"{what} did not happen within {timeout} s.")


def stamp(message) -> float:
    return message.header.stamp.sec + message.header.stamp.nanosec / 1e9


WHAT_ROS_IS_TOLD = (
    "ROS_AUTOMATIC_DISCOVERY_RANGE",
    "ROS_STATIC_PEERS",
    "ROS_LOCALHOST_ONLY",
    "RMW_IMPLEMENTATION",
    "FASTRTPS_DEFAULT_PROFILES_FILE",
)
"""Every variable the launch file asks before it gives the simulator its profile."""

A_PROFILE_OF_THE_USERS_OWN = """<?xml version="1.0" encoding="UTF-8"?>
<dds xmlns="http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles">
  <profiles>
    <participant profile_name="a_users_own" is_default_profile="true">
      <rtps>
        <sendSocketBufferSize>1048576</sendSocketBufferSize>
      </rtps>
    </participant>
  </profiles>
</dds>
"""
"""A profile as somebody tunes Fast DDS with: it says nothing about discovery."""


def environment(discovery_range: str | None, **more: str) -> dict[str, str]:
    """The bringup's environment: the garden, a free port, no token, and what ROS is told."""
    variables = {**os.environ, "TURTLE_MAP": str(GARDEN), "TURTLE_VIEW_PORT": str(free_port())}
    for name in ("FLEETLESS_TOKEN", *WHAT_ROS_IS_TOLD):
        variables.pop(name, None)
    if discovery_range is not None:
        variables["ROS_AUTOMATIC_DISCOVERY_RANGE"] = discovery_range
    return {**variables, **more}


def launch(variables: dict[str, str], file: str | None = None, cwd=None) -> subprocess.Popen:
    """`ros2 launch` of the turtle's launch file, or of a copy of it at a path."""
    which = [file] if file else ["fleetless_turtle_bringup", "turtle.launch.py"]
    return subprocess.Popen(
        ["ros2", "launch", *which],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=variables,
        cwd=cwd,
    )


def stopped(bringup: subprocess.Popen) -> None:
    bringup.send_signal(signal.SIGINT)
    try:
        bringup.wait(timeout=20.0)
    finally:
        if bringup.poll() is None:
            bringup.kill()


def descends_from(pid: int, ancestor: int) -> bool:
    while pid > 1:
        if pid == ancestor:
            return True
        # The parent is the second field after the name, which stands in brackets.
        pid = int(Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[1])
    return False


def process_of(executable: str, bringup: subprocess.Popen) -> Path | None:
    """`/proc/<pid>` of the one process under the launch that runs `executable`, once there."""
    found = []
    for entry in Path("/proc").iterdir():
        try:
            if not entry.name.isdigit() or not descends_from(int(entry.name), bringup.pid):
                continue
            if executable in (entry / "cmdline").read_bytes().decode():
                found.append(entry)
        except (FileNotFoundError, ProcessLookupError):
            continue  # a process that ended while it was looked at
    assert len(found) <= 1, f"{executable} runs {len(found)} times under the launch"
    return found[0] if found else None


def dds_threads(executable: str, bringup: subprocess.Popen) -> list[str]:
    """The names of Fast DDS's threads in that process.

    Fast DDS names a thread after each port it listens on: `dds.udp.<port>`,
    `dds.shm.<port>`. That is how a process shows which transports it has.
    """
    process = process_of(executable, bringup)
    if process is None:
        return []
    try:
        names = [(task / "comm").read_text().strip() for task in (process / "task").iterdir()]
    except (FileNotFoundError, ProcessLookupError):
        return []
    return sorted(name for name in names if name.startswith("dds."))


def given(executable: str, bringup: subprocess.Popen) -> dict[str, str]:
    """What that process was started with, of the two variables the launch file may set for it."""
    variables = (process_of(executable, bringup) / "environ").read_bytes().decode().split("\0")
    ours = ("ROS_AUTOMATIC_DISCOVERY_RANGE", "FASTRTPS_DEFAULT_PROFILES_FILE")
    return dict(v.split("=", 1) for v in variables if v.split("=", 1)[0] in ours)


SIMULATOR = "lib/fleetless_turtle_sim/simulator"
CAMERAS = "lib/fleetless_turtle_cameras/cameras"

# What each node says once it is built. Fast DDS has started every thread of
# a process by then; one look earlier could miss the last of them.
UP = {SIMULATOR: "The turtle is on the pad", CAMERAS: "The cameras draw"}


def threads_once_up(executable: str, bringup: subprocess.Popen, lines: list[str]) -> list[str]:
    """Fast DDS's threads in that process, looked at after the node said it is up."""
    wait_for(lambda: any(UP[executable] in line for line in lines), what=f"{UP[executable]} …")
    threads = dds_threads(executable, bringup)
    assert any(name.startswith("dds.udp.") for name in threads), threads
    return threads


# Where every participant of a domain listens for multicast, by the RTPS
# port rule. A process that has this thread is found by whoever shares its
# network; under LOCALHOST no process has it.
MULTICAST = f"dds.udp.{7400 + 250 * int(os.environ.get('ROS_DOMAIN_ID', '0'))}"


def with_shared_memory(threads: list[str]) -> list[str]:
    return [name for name in threads if name.startswith("dds.shm")]


# --- What the launch file decides, by its own function -------------------------------


@pytest.fixture
def decide(monkeypatch, tmp_path):
    """The launch file's `without_shared_memory`, under the image's settings plus what a test names.

    It is given a working directory of its own, with no profile in it, and
    a clean slate of the variables it asks.
    """
    path = Path(get_package_share_directory("fleetless_turtle_bringup")) / "launch"
    spec = importlib.util.spec_from_file_location("turtle_launch", path / "turtle.launch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.chdir(tmp_path)

    def decide(**told: str | None) -> dict[str, str]:
        for name in WHAT_ROS_IS_TOLD:
            monkeypatch.delenv(name, raising=False)
        for name, value in {"ROS_AUTOMATIC_DISCOVERY_RANGE": "LOCALHOST", **told}.items():
            if value is not None:
                monkeypatch.setenv(name, value)
        return module.without_shared_memory()

    decide.module = module
    return decide


def handed_out(given: dict[str, str]) -> bool:
    """Whether that is the profile and the range that goes with it, or nothing at all."""
    if not given:
        return False
    assert given["ROS_AUTOMATIC_DISCOVERY_RANGE"] == "SYSTEM_DEFAULT"
    assert Path(given["FASTRTPS_DEFAULT_PROFILES_FILE"]).name == "simulator.fastdds.xml"
    assert len(given) == 2
    return True


def test_under_the_images_own_settings_the_profile_is_handed_out(decide):
    assert handed_out(decide())


OTHER_RANGES = [None, "", "SUBNET", "OFF", "SYSTEM_DEFAULT", "localhost"]


@pytest.mark.parametrize("discovery_range", OTHER_RANGES)
def test_under_another_range_it_is_not(decide, discovery_range):
    assert not handed_out(decide(ROS_AUTOMATIC_DISCOVERY_RANGE=discovery_range))


def test_with_peers_named_it_is_not_and_an_empty_list_names_none(decide):
    assert not handed_out(decide(ROS_STATIC_PEERS="192.0.2.7"))
    assert handed_out(decide(ROS_STATIC_PEERS=""))


@pytest.mark.parametrize(("value", "localhost_only"), [("1", True), ("10", True), ("0", False)])
def test_ros_localhost_only_counts_as_ros_reads_it(decide, value, localhost_only):
    """By its first character. Where it counts, the profile is not handed out."""
    assert handed_out(decide(ROS_LOCALHOST_ONLY=value)) != localhost_only


@pytest.mark.parametrize("middleware", [None, "", "rmw_fastrtps_cpp", "rmw_fastrtps_dynamic_cpp"])
def test_where_fast_dds_is_the_middleware_it_is_handed_out(decide, middleware):
    """Named, or not named at all: ROS then loads rmw_fastrtps_cpp, which is installed."""
    assert handed_out(decide(RMW_IMPLEMENTATION=middleware))


@pytest.mark.parametrize(
    "middleware", ["rmw_cyclonedds_cpp", "rmw_zenoh_cpp", "rmw_connextdds", "RMW_FASTRTPS_CPP"]
)
def test_under_another_middleware_it_is_not(decide, middleware):
    """Another middleware reads no Fast DDS profile.

    Given `SYSTEM_DEFAULT` all the same, the simulator had that
    middleware's own defaults: under Cyclone DDS it announced itself by
    multicast, and a stranger on the Docker network drove the turtle.
    """
    assert not handed_out(decide(RMW_IMPLEMENTATION=middleware))


@pytest.mark.parametrize("middleware", [None, ""])
def test_where_none_is_named_and_fast_dds_is_not_installed_it_is_not(
    decide, monkeypatch, middleware
):
    """ROS then loads whichever middleware it finds."""

    def not_installed(name: str):
        raise PackageNotFoundError(name)

    monkeypatch.setattr(decide.module, "get_package_prefix", not_installed)
    assert not handed_out(decide(RMW_IMPLEMENTATION=middleware))


def test_with_a_profile_named_by_the_user_it_is_not_and_an_empty_name_names_none(decide, tmp_path):
    own = tmp_path / "own.xml"
    own.write_text(A_PROFILE_OF_THE_USERS_OWN)
    assert not handed_out(decide(FASTRTPS_DEFAULT_PROFILES_FILE=str(own)))
    assert handed_out(decide(FASTRTPS_DEFAULT_PROFILES_FILE=""))


def test_with_a_profile_in_the_working_directory_it_is_not(decide, tmp_path):
    """Fast DDS reads that file after the one it was named, and the later one wins."""
    (tmp_path / "DEFAULT_FASTRTPS_PROFILES.xml").write_text(A_PROFILE_OF_THE_USERS_OWN)
    assert not handed_out(decide())


# --- Before the bringup that the other tests share: starts of their own -------


@pytest.mark.parametrize(
    ("discovery_range", "more", "own_profile"),
    [
        # Another range, and one that keeps the test's nodes to themselves:
        # under OFF a node looks for nobody and answers nobody.
        pytest.param("OFF", {}, None, id="another range"),
        # ROS has every node look for these peers. The profile knows none.
        pytest.param("LOCALHOST", {"ROS_STATIC_PEERS": "127.0.0.1"}, None, id="peers"),
        # The older way to say LOCALHOST. ROS then passes over the range
        # the simulator would be given, and the profile would be for show.
        pytest.param("LOCALHOST", {"ROS_LOCALHOST_ONLY": "1"}, None, id="localhost only"),
        # A profile of the user's own, by either way Fast DDS finds one.
        pytest.param("LOCALHOST", {}, "FASTRTPS_DEFAULT_PROFILES_FILE", id="a profile, named"),
        pytest.param("LOCALHOST", {}, "DEFAULT_FASTRTPS_PROFILES.xml", id="a profile, found"),
    ],
)
def test_where_ros_would_say_otherwise_the_simulator_is_left_as_ros_has_it(
    discovery_range, more, own_profile, tmp_path
):
    """Started with what the launch itself was given, with shared memory, and without multicast.

    Whoever names another range, peers or a profile of their own chose
    that. The simulator's profile says LOCALHOST and nothing else, and
    under `SYSTEM_DEFAULT` whatever takes its place decides who finds the
    simulator: the second profile case, on a head that handed the profile
    out regardless, ran the simulator with a thread for multicast.
    """
    variables = environment(discovery_range, **more)
    as_given = {"ROS_AUTOMATIC_DISCOVERY_RANGE": discovery_range}
    if own_profile == "FASTRTPS_DEFAULT_PROFILES_FILE":
        named = tmp_path / "own.xml"
        named.write_text(A_PROFILE_OF_THE_USERS_OWN)
        variables[own_profile] = as_given[own_profile] = str(named)
    elif own_profile:
        (tmp_path / own_profile).write_text(A_PROFILE_OF_THE_USERS_OWN)
    bringup = launch(variables, cwd=tmp_path)
    lines = []
    threading.Thread(target=lambda: lines.extend(bringup.stdout), daemon=True).start()
    try:
        simulator = threads_once_up(SIMULATOR, bringup, lines)
        started_with = given(SIMULATOR, bringup)
    finally:
        stopped(bringup)
    # One comparison, so that a failure says all three and not the first.
    found = {
        "started with": started_with,
        "shared memory": bool(with_shared_memory(simulator)),
        "a thread for multicast": MULTICAST in simulator,
    }
    as_ros_has_it = {
        "started with": as_given,
        "shared memory": True,
        "a thread for multicast": False,
    }
    assert found == as_ros_has_it, simulator


@pytest.mark.parametrize("profile", ["missing", "garbled"])
def test_a_profile_that_cannot_be_read_ends_the_start_with_one_sentence(profile, tmp_path):
    """A copy of the installed package whose profile is gone, or is no XML.

    The copy stands first on the path ROS finds packages by, so the launch
    file, started from the copy, looks for its profile there.
    """
    installed = Path(get_package_share_directory("fleetless_turtle_bringup"))
    share = tmp_path / "share" / "fleetless_turtle_bringup"
    shutil.copytree(installed, share)
    marker = tmp_path / "share" / "ament_index" / "resource_index" / "packages"
    marker.mkdir(parents=True)
    (marker / "fleetless_turtle_bringup").touch()
    if profile == "missing":
        (share / "config" / "simulator.fastdds.xml").unlink()
    else:
        (share / "config" / "simulator.fastdds.xml").write_text("<dds><profiles><participant")

    variables = environment("LOCALHOST")
    variables["AMENT_PREFIX_PATH"] = f"{tmp_path}:{variables['AMENT_PREFIX_PATH']}"
    bringup = launch(variables, str(share / "launch" / "turtle.launch.py"))
    try:
        said, _ = bringup.communicate(timeout=60.0)
    finally:
        if bringup.poll() is None:
            bringup.kill()
    assert bringup.returncode != 0, said
    assert "The simulator's Fast DDS profile cannot be read" in said, said
    assert "The turtle does not start." in said, said
    assert "process started" not in said, said


# --- The bringup as the image starts it ---------------------------------------


@pytest.fixture(scope="module")
def graph():
    """The bringup, running under LOCALHOST as in the image, and a node that listens to it."""
    bringup = launch(environment("LOCALHOST"))
    lines = []
    threading.Thread(target=lambda: lines.extend(bringup.stdout), daemon=True).start()

    rclpy.init()
    node = rclpy.create_node("bringup_join_test")
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    spinning = threading.Thread(target=executor.spin, daemon=True)
    spinning.start()
    try:
        yield node, bringup, lines
    finally:
        executor.shutdown()
        spinning.join(timeout=5.0)
        node.destroy_node()
        rclpy.shutdown()
        stopped(bringup)
    said = "".join(lines)
    assert bringup.returncode == 0, said
    assert "Traceback" not in said, said


def test_the_simulator_speaks_udp_only_and_the_cameras_keep_shared_memory(graph):
    """The setting, by what it does.

    A profile that Fast DDS did not take changes nothing and says one line
    at most, and a variable ROS does not read says nothing at all. So this
    looks at the processes themselves.
    """
    _, bringup, lines = graph
    simulator = threads_once_up(SIMULATOR, bringup, lines)
    cameras = threads_once_up(CAMERAS, bringup, lines)
    assert given(SIMULATOR, bringup)["ROS_AUTOMATIC_DISCOVERY_RANGE"] == "SYSTEM_DEFAULT"
    assert given(CAMERAS, bringup) == {"ROS_AUTOMATIC_DISCOVERY_RANGE": "LOCALHOST"}
    assert not with_shared_memory(simulator), simulator
    assert MULTICAST not in simulator, simulator
    assert with_shared_memory(cameras), cameras
    assert MULTICAST not in cameras, cameras


def test_ten_clients_join_during_a_drive_and_the_world_goes_on(graph):
    """Judged by the odometry's own stamps: the simulator stamps a message when it publishes it."""
    node, _, _ = graph
    odometry = []
    node.create_subscription(Odometry, "/odom", odometry.append, 1000)
    wait_for(lambda: odometry, what="odometry arriving")

    # Onto open ground, so that the circle touches nothing. This test's own
    # client is gone again before the first passer-by comes.
    place = json.loads((MAPS / "garden.places.json").read_text())["open"]
    teleport = node.create_client(Teleport, "/turtle/teleport")
    assert teleport.wait_for_service(timeout_sec=30.0)
    answer = teleport.call_async(Teleport.Request(x=place["x"], y=place["y"], heading_deg=0.0))
    wait_for(answer.done, what="the answer to the teleport")
    assert answer.result().success, answer.result().message
    node.destroy_client(teleport)

    driver = subprocess.Popen([sys.executable, "-c", DRIVER])
    try:
        wait_for(
            lambda: abs(odometry[-1].twist.twist.linear.x - SPEED_M_S) < 1e-6,
            what="the turtle reaching its speed",
        )
        time.sleep(1.0)
        first = len(odometry)
        for _ in range(JOINS):
            passer_by = [sys.executable, "-c", PASSER_BY]
            came_and_went = subprocess.run(passer_by, capture_output=True, timeout=60.0)
            assert came_and_went.returncode == 0, came_and_went.stderr.decode()
            time.sleep(0.7)
        last = len(odometry)
    finally:
        driver.terminate()
        driver.wait(timeout=10.0)

    during = odometry[first:last]
    assert len(during) > 20 * JOINS, "the drive was shorter than ten joins take"
    gaps = [stamp(b) - stamp(a) for a, b in itertools.pairwise(during)]
    long_gaps = [round(gap * 1000) for gap in gaps if gap > GAP_S]
    seconds = stamp(during[-1]) - stamp(during[0])
    print(
        f"bringup: {JOINS} clients joined during a drive of {seconds:.1f} s; longest gap between "
        f"two odometry stamps {max(gaps) * 1000:.0f} ms, gaps over 90 ms: {long_gaps}"
    )
    assert long_gaps == []
