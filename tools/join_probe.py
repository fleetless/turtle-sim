#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Does the world stand when a ROS client joins? Measured from inside a running turtle.

    docker cp tools/join_probe.py turtle:/tmp/
    docker exec turtle /opt/turtle/entrypoint.sh python3 -u /tmp/join_probe.py action 30

`tools/join-probe.sh` does that in fresh containers and under a CPU limit.

The probe puts the turtle on open ground and drives it in a circle by
`/cmd_vel` at 0.5 m/s, twenty commands a second. Meanwhile, N times, it
starts a short-lived process: a node with a client of the turtle that waits
for its server, sleeps half a second and ends. Then it reads its own record
of `/odom`:

  - every gap of more than 90 ms between two header stamps, with how far the
    turtle moved across it. The simulator stamps a message when it publishes
    it, 20 times a second, so a gap is the simulator's own thread coming
    late, not a late delivery. Where the world stood, the turtle moved a
    centimetre across the gap; where the thread was only late and made the
    time up, it moved the whole way;
  - the pace: the distance driven against 0.5 m/s times the time it took. A
    world that stood for longer than a tenth of a second does not make that
    time up, so the pace says what the gaps cost in sum;
  - the longest wait between two arrivals and the longest time a message
    took from its stamp to the probe: when the simulator sent it.

The probe must not measure itself. Any ROS process with Fast DDS's
shared-memory transport can be held up by the same newcomer, in its own
sending and in its own receiving. So the process that listens has no such
transport: it gives itself a Fast DDS profile with UDP only. And it sends
nothing: the commands come from a process of their own. Both the driver and
the passers-by are ROS processes as anybody starts them.

What joins:

    action    an action client of /turtle/drive_to (the default)
    service   a service client of /turtle/set_trail
    nobody    no process at all, for the same length of time: what the
              machine does to the pace by itself

The last line is the summary. With --json one more line follows, the same
as JSON. Exit code 0 means it measured, not that it found nothing.
"""

import argparse
import atexit
import itertools
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

SPEED_M_S = 0.5
TURN_RAD_S = 0.5
"""A circle of one metre radius."""
COMMAND_PERIOD_S = 0.05
GAP_S = 0.09
"""Odometry comes every 50 ms. Over 90 ms, one is missing."""
PASSER_BY_STAYS_S = 0.5
BETWEEN_JOINS_S = 0.7
NOBODY_TAKES_S = 2.0
"""About what a join takes, so that a run with nobody drives as long."""
UDP_ONLY = """<?xml version="1.0" encoding="UTF-8"?>
<dds xmlns="http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles">
  <profiles>
    <transport_descriptors>
      <transport_descriptor>
        <transport_id>udp</transport_id>
        <type>UDPv4</type>
        <maxInitialPeersRange>32</maxInitialPeersRange>
      </transport_descriptor>
    </transport_descriptors>
    <participant profile_name="join_probe" is_default_profile="true">
      <rtps>
        <builtin>
          <metatrafficUnicastLocatorList>
            <locator><udpv4/></locator>
          </metatrafficUnicastLocatorList>
          <initialPeersList>
            <locator><udpv4><address>127.0.0.1</address></udpv4></locator>
          </initialPeersList>
        </builtin>
        <userTransports>
          <transport_id>udp</transport_id>
        </userTransports>
        <useBuiltinTransports>false</useBuiltinTransports>
      </rtps>
    </participant>
  </profiles>
</dds>
"""
"""What ROS sets up under the discovery range LOCALHOST, without the shared memory."""
PLACES = Path("/opt/turtle/maps/garden.places.json")
OPEN_GROUND = (40, 24)
"""A cell of the shipped garden with room for the circle, if the image names none."""


def passer_by(kind: str) -> int:
    """The short-lived process: join the graph with one client, see the server, leave."""
    import rclpy

    rclpy.init()
    node = rclpy.create_node("passer_by")
    if kind == "action":
        from fleetless_turtle_msgs.action import DriveTo
        from rclpy.action import ActionClient

        client = ActionClient(node, DriveTo, "/turtle/drive_to")
        found = client.wait_for_server(timeout_sec=10.0)
    else:
        from std_srvs.srv import SetBool

        client = node.create_client(SetBool, "/turtle/set_trail")
        found = client.wait_for_service(timeout_sec=10.0)
    time.sleep(PASSER_BY_STAYS_S)
    client.destroy()
    node.destroy_node()
    rclpy.shutdown()
    return 0 if found else 3


def driver() -> int:
    """The process at the controls: the circle's command, twenty times a second, until ended."""
    import rclpy
    from geometry_msgs.msg import Twist
    from rclpy.signals import SignalHandlerOptions

    # The signals stay with Python: the probe ends this process with
    # SIGTERM, and it then ends on the spot, without a word.
    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    node = rclpy.create_node("join_probe_driver")
    commands = node.create_publisher(Twist, "/cmd_vel", 10)
    twist = Twist()
    twist.linear.x = SPEED_M_S
    twist.angular.z = TURN_RAD_S
    while True:
        commands.publish(twist)
        time.sleep(COMMAND_PERIOD_S)


def stamp_s(message) -> float:
    return message.header.stamp.sec + message.header.stamp.nanosec / 1e9


def listen_without_shared_memory() -> dict[str, str]:
    """Give this process a Fast DDS with UDP only. Returns the environment as it was.

    Under the range LOCALHOST ROS names shared memory and UDP itself, so
    this process leaves the range to Fast DDS and says the rest in a
    profile. The processes the probe starts get the environment as it was.
    """
    as_it_was = dict(os.environ)
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as profile:
        profile.write(UDP_ONLY)
    atexit.register(os.unlink, profile.name)
    os.environ["ROS_AUTOMATIC_DISCOVERY_RANGE"] = "SYSTEM_DEFAULT"
    os.environ["FASTRTPS_DEFAULT_PROFILES_FILE"] = profile.name
    return as_it_was


def has_shared_memory() -> bool:
    """Whether Fast DDS runs a shared-memory thread in this process. It names them `dds.shm.…`."""
    names = [(task / "comm").read_text() for task in Path("/proc/self/task").iterdir()]
    return any(name.startswith("dds.shm") for name in names)


def measure(kind: str, joins: int):
    """The measurement, and a function that takes the probe's node away again."""
    environment = listen_without_shared_memory()
    import rclpy
    from fleetless_turtle_msgs.msg import TurtleStatus
    from fleetless_turtle_msgs.srv import Teleport
    from nav_msgs.msg import Odometry
    from rclpy.executors import SingleThreadedExecutor

    rclpy.init()
    node = rclpy.create_node("join_probe")
    odometry = []  # (arrival on the steady clock, arrival on the clock of the stamps, message)
    status = []
    node.create_subscription(
        Odometry, "/odom", lambda m: odometry.append((time.monotonic(), time.time(), m)), 1000
    )
    node.create_subscription(TurtleStatus, "/turtle/status", status.append, 10)
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    spinning = threading.Thread(target=executor.spin, daemon=True)
    spinning.start()
    if has_shared_memory():
        raise SystemExit("join_probe: Fast DDS did not take the probe's UDP-only profile.")

    def close() -> None:
        # In this order, or the thread spins a node that is being taken away.
        executor.shutdown()
        spinning.join(timeout=5.0)
        node.destroy_node()
        rclpy.shutdown()

    def wait_for(what: str, there, seconds: float = 60.0) -> None:
        deadline = time.monotonic() + seconds
        while not there():
            if time.monotonic() > deadline:
                raise SystemExit(f"join_probe: {what} did not come within {seconds:g} s.")
            time.sleep(0.05)

    wait_for("odometry", lambda: odometry)
    wait_for("the turtle's status", lambda: status)

    # Onto open ground, so that the circle touches nothing. The probe's own
    # client is gone again before the first passer-by comes.
    x, y = OPEN_GROUND
    if PLACES.exists():
        place = json.loads(PLACES.read_text())["open"]
        x, y = place["x"], place["y"]
    teleport = node.create_client(Teleport, "/turtle/teleport")
    if not teleport.wait_for_service(timeout_sec=30.0):
        raise SystemExit("join_probe: /turtle/teleport did not appear within 30 s.")
    answer = teleport.call_async(Teleport.Request(x=x, y=y, heading_deg=0.0))
    wait_for("the answer to the teleport", answer.done, 10.0)
    if not answer.result().success:
        raise SystemExit(f"join_probe: {answer.result().message}")
    node.destroy_client(teleport)
    collisions_before = status[-1].collisions

    # The driver's node joins here, once, and is settled before the first
    # passer-by: the turtle is up to speed, and a second more.
    at_the_controls = subprocess.Popen([sys.executable, __file__, "--driver"], env=environment)
    try:
        wait_for(
            "the turtle reaching its speed",
            lambda: abs(odometry[-1][2].twist.twist.linear.x - SPEED_M_S) < 1e-6,
        )
        time.sleep(1.0)

        first = len(odometry)
        marks = []  # (start, end) of every passer-by, on the steady clock
        found = 0
        for _ in range(joins):
            start = time.monotonic()
            if kind == "nobody":
                time.sleep(NOBODY_TAKES_S)
            else:
                passer = [sys.executable, __file__, "--passer-by", kind]
                ended = subprocess.run(passer, capture_output=True, timeout=120, env=environment)
                found += ended.returncode == 0
            marks.append((start, time.monotonic()))
            time.sleep(BETWEEN_JOINS_S)
        last = len(odometry)
    finally:
        # However this ends, nobody is left at the controls.
        at_the_controls.terminate()
        at_the_controls.wait(timeout=10.0)
    time.sleep(0.5)
    window = odometry[first:last]
    crawled = any(message.crawling for message in status)
    touched = status[-1].collisions - collisions_before

    def where(arrival: float) -> str:
        for number, (start, end) in enumerate(marks, 1):
            if start - 0.2 <= arrival <= end + 0.5:
                took = f"join {number}, which took {end - start:.2f} s"
                if arrival <= end:
                    return f"{arrival - start:+.2f} s into {took}"
                return f"{arrival - end:.2f} s after {took}"
        return "between two joins"

    gaps = []
    distance = 0.0
    longest_wait = 0.0
    longest_delay = max((on_the_clock - stamp_s(m) for _, on_the_clock, m in window), default=0.0)
    for (arrived, _, a), (arrived_next, _, b) in itertools.pairwise(window):
        moved = math.hypot(
            b.pose.pose.position.x - a.pose.pose.position.x,
            b.pose.pose.position.y - a.pose.pose.position.y,
        )
        distance += moved
        longest_wait = max(longest_wait, arrived_next - arrived)
        gap = stamp_s(b) - stamp_s(a)
        if gap > GAP_S:
            gaps.append(
                {"ms": round(gap * 1000), "moved_cm": round(moved * 100, 1), "at": where(arrived)}
            )
    seconds = stamp_s(window[-1][2]) - stamp_s(window[0][2]) if len(window) > 1 else 0.0
    result = {
        "kind": kind,
        "joins": joins,
        "found_the_server": found,
        "drive_s": round(seconds, 1),
        "odometry_messages": len(window),
        "gaps": gaps,
        "longest_gap_ms": max((gap["ms"] for gap in gaps), default=0),
        "pace_percent": round(100.0 * distance / (SPEED_M_S * seconds), 2) if seconds else None,
        "longest_wait_ms": round(longest_wait * 1000),
        "longest_delay_ms": round(longest_delay * 1000),
        "collisions": touched,
        "crawled": crawled,
    }
    return result, close


def report(result: dict) -> None:
    who = {"action": "action clients", "service": "service clients", "nobody": "nobody"}
    line = f"join_probe: {who[result['kind']]}, {result['joins']} joins"
    if result["kind"] != "nobody":
        line += f", {result['found_the_server']} of them found the server"
    print(f"{line}; a drive of {result['drive_s']} s, {result['odometry_messages']} odometry.")
    for gap in result["gaps"]:
        print(f"  gap {gap['ms']} ms, the turtle moved {gap['moved_cm']} cm, {gap['at']}")
    if result["collisions"] or result["crawled"]:
        # The pace below then says nothing: the turtle was held up by something else.
        print(
            f"  the drive was not clean: {result['collisions']} collisions, "
            f"{'an empty battery' if result['crawled'] else 'the battery held'}."
        )
    gaps = result["gaps"]
    print(
        f"gaps over {GAP_S * 1000:.0f} ms: {len(gaps)}"
        + (f" (longest {result['longest_gap_ms']} ms)" if gaps else "")
        + f"; pace {result['pace_percent']} %"
        + f"; arrivals: longest wait {result['longest_wait_ms']} ms"
        + f", longest delay {result['longest_delay_ms']} ms"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    kinds = ["action", "service", "nobody"]
    parser.add_argument("kind", nargs="?", default="action", choices=kinds)
    parser.add_argument("joins", nargs="?", default=30, type=int)
    parser.add_argument("--json", action="store_true", help="one more line, the summary as JSON")
    parser.add_argument("--passer-by", choices=["action", "service"], help=argparse.SUPPRESS)
    parser.add_argument("--driver", action="store_true", help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.passer_by:
        return passer_by(arguments.passer_by)
    if arguments.driver:
        return driver()
    result, close = measure(arguments.kind, arguments.joins)
    # Said first: what was measured stands, whatever ROS does on its way out.
    report(result)
    if arguments.json:
        print(json.dumps(result))
    sys.stdout.flush()
    close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
