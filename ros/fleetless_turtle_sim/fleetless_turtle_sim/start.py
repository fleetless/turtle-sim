# SPDX-License-Identifier: Apache-2.0
"""Where the simulator's process begins.

Before anything else it takes the two signals that ask a process to stop.
Importing ROS and building the node takes a second or two, and a stop in
that time used to meet Python's own handler: a stack trace where "stopped"
was meant. From here on a stop sets a flag, and the node looks at it.
"""

import signal
import threading


def main() -> int:
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    # The slow part: rclpy, the messages, numpy and OpenCV.
    from .node import run

    return run(stop)
