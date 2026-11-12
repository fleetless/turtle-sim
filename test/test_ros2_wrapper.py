# SPDX-License-Identifier: Apache-2.0
"""The `ros2` wrapper waits for every node the launch file starts.

In a young container it asks the daemon whether it has heard each node, by
one topic the node publishes or listens to. A node the launch file gains
and the wrapper does not know of would be the one whose topics the first
`ros2 topic list` lacks, now and then, on a machine with two cores. So the
two lists are held against each other here.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WRAPPER = ROOT / "docker" / "ros2"
LAUNCH = ROOT / "ros" / "fleetless_turtle_bringup" / "launch" / "turtle.launch.py"
SMOKE = ROOT / "docker" / "smoke-test.sh"
NODES = ROOT / "ros"


def listed(name: str, script: Path) -> list[str]:
    """The words of a shell list, `NAME="a b c"`, which may run over several lines."""
    found = re.search(rf'^{name}="([^"]*)"', script.read_text(), re.MULTILINE)
    assert found, f"{script.name} has no {name}"
    return found[1].split()


def source_of(package: str) -> str:
    return (NODES / package / package / "node.py").read_text()


def test_the_wrapper_knows_one_topic_of_every_node_the_launch_file_starts():
    published, listened_to = listed("PUBLISHED", WRAPPER), listed("LISTENED_TO", WRAPPER)
    # The simulator, the view, the cameras and the model's publisher. The
    # bridge is the bridge's own launch file and comes with a token only.
    nodes = re.findall(r"\b(\w+) = Node\(", LAUNCH.read_text())
    assert nodes == ["simulator", "viewer", "cameras", "model_publisher"]
    assert len(published) + len(listened_to) == len(nodes)

    # Each topic is a thing of the node it stands for.
    assert 'create_publisher(TurtleStatus, "turtle/status"' in source_of("fleetless_turtle_sim")
    viewer = source_of("fleetless_turtle_viewer")
    assert 'create_subscription(TurtleStatus, "turtle/status"' in viewer
    assert '"turtle/camera/top/image_raw"' in source_of("fleetless_turtle_cameras")
    assert published == ["/turtle/status", "/turtle/camera/top/image_raw", "/robot_description"]
    assert listened_to == ["/turtle/status"]


def test_every_topic_the_wrapper_waits_for_is_one_the_smoke_test_asks_for():
    asked_for = listed("TOPICS", SMOKE)
    for topic in listed("PUBLISHED", WRAPPER) + listed("LISTENED_TO", WRAPPER):
        assert topic in asked_for, topic
    # And the smoke test asks its first question for all four nodes.
    assert sorted(listed("NODES", SMOKE)) == [
        "/robot_state_publisher",
        "/turtle_cameras",
        "/turtle_simulator",
        "/turtle_viewer",
    ]


def test_the_wrapper_waits_in_a_young_container_only():
    script = WRAPPER.read_text()
    # Thirty seconds after the turtle started nobody is asked and nothing waits.
    assert re.search(r"^YOUNG_FOR_S=30$", script, re.MULTILINE)
    assert 'if [ "$(date +%s)" -lt "$young_until" ] && ! heard; then' in script
    # The file that says when the turtle started is written for the turtle
    # only: a command in the turtle's place has no turtle to wait for.
    start = (ROOT / "docker" / "start.sh").read_text()
    command, turtle = start.index('exec "$@"'), start.index("date +%s > /tmp/turtle-started-at")
    assert command < turtle < start.index("exec python3 /opt/turtle/launcher.py")
