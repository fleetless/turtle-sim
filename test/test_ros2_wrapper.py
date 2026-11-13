# SPDX-License-Identifier: Apache-2.0
"""The `ros2` wrapper waits for every node the launch file starts.

In a young container it asks the daemon whether it has heard each node, by
one topic the node publishes or listens to. A node the launch file gains
and the wrapper does not know of would be the one whose topics the first
`ros2 topic list` lacks, now and then, on a machine with two cores. So the
two lists are held against each other here.

And the wrapper itself is run, in a copy whose three paths of the image
point into a directory of the test: with a daemon that never hears the
nodes, with one that hears them after a moment, and where nothing waits.
"""

import os
import re
import subprocess
import time
from pathlib import Path

import pytest

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
    launch = LAUNCH.read_text()
    nodes = re.findall(r"\b(\w+) = Node\(", launch)
    assert nodes == ["simulator", "viewer", "cameras", "model_publisher"]
    # And these four are every node it starts: one written straight into the
    # list, or of another kind of node, has no name here and is counted.
    assert len(re.findall(r"\w*Node\w*\(", launch)) == len(nodes)
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


# --- The wrapper, run ----------------------------------------------------------

RAN_OUT = (
    "The turtle's nodes were not all heard within 30 s of its start: this answer may lack some."
)
NEVER = 1_000_000

# In place of the daemon's port: no answer to the first DEAF_FOR questions,
# then "one" to every question.
CURL = """#!/bin/sh
asked=$(($(cat "$ASKED" 2> /dev/null || echo 0) + 1))
echo "$asked" > "$ASKED"
[ "$asked" -gt "$DEAF_FOR" ] || exit 7
echo '<methodResponse><params><param><value><int>1</int></value></param></params></methodResponse>'
"""
# In place of ros2: it says what it was asked and ends with a code of its own.
ROS2 = """#!/bin/sh
echo "the answer to $*"
exit 3
"""


def run_a_copy(directory: Path, started_s_ago: int | None, deaf_for: int):
    """`ros2 topic list` through the wrapper: what it said, and how often it asked the daemon."""
    script = WRAPPER.read_text()
    (directory / "environment.sh").write_text("")
    for fixed, here in (
        ("source /opt/turtle/environment.sh", f"source {directory}/environment.sh"),
        ("started_at=/tmp/turtle-started-at", f"started_at={directory}/turtle-started-at"),
        ('exec "/opt/ros/$ROS_DISTRO/bin/ros2" "$@"', f'exec "{directory}/bin/ros2-itself" "$@"'),
    ):
        assert script.count(fixed) == 1, fixed
        script = script.replace(fixed, here)
    (directory / "bin").mkdir()
    for name, text in (("ros2", script), ("curl", CURL), ("ros2-itself", ROS2)):
        (directory / "bin" / name).write_text(text)
        (directory / "bin" / name).chmod(0o755)
    if started_s_ago is not None:
        (directory / "turtle-started-at").write_text(f"{int(time.time()) - started_s_ago}\n")
    asked = directory / "asked"
    ran = subprocess.run(
        ["bash", str(directory / "bin" / "ros2"), "topic", "list"],
        env={
            **os.environ,
            "PATH": f"{directory}/bin:{os.environ['PATH']}",
            "ASKED": str(asked),
            "DEAF_FOR": str(deaf_for),
        },
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return ran, int(asked.read_text()) if asked.exists() else 0


def test_a_wait_that_runs_out_says_so_and_still_gives_the_answer(tmp_path):
    # Three seconds of its thirty are left, and the daemon never hears the nodes.
    ran, asked = run_a_copy(tmp_path, started_s_ago=27, deaf_for=NEVER)
    assert ran.stderr == RAN_OUT + "\n"
    # ros2 was asked all the same: its answer, and its exit code.
    assert ran.stdout == "the answer to topic list\n"
    assert ran.returncode == 3
    assert asked > 3


def test_a_wait_that_ends_because_every_node_was_heard_says_nothing(tmp_path):
    ran, asked = run_a_copy(tmp_path, started_s_ago=5, deaf_for=6)
    assert (ran.stdout, ran.stderr, ran.returncode) == ("the answer to topic list\n", "", 3)
    # Six questions without an answer, then one for each of the four nodes, twice:
    # the round that ends the wait and the look at why it ended.
    assert asked == 6 + 2 * 4


@pytest.mark.parametrize("started_s_ago", [31, None], ids=["an old turtle", "no turtle"])
def test_where_nothing_waits_the_daemon_is_not_asked_and_nothing_is_said(tmp_path, started_s_ago):
    ran, asked = run_a_copy(tmp_path, started_s_ago, deaf_for=NEVER)
    assert (ran.stdout, ran.stderr, ran.returncode) == ("the answer to topic list\n", "", 3)
    assert asked == 0
