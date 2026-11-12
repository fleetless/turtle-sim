# SPDX-License-Identifier: Apache-2.0
"""Where the simulator's process begins.

Before anything else it takes the two signals that ask a process to stop.
Importing ROS and building the node takes a second or two, and a stop in
that time used to meet Python's own handler: a stack trace where "stopped"
was meant. From here on a stop does what fits the moment: while the
imports run, nothing exists yet that would want shutting down, and the
process leaves at once; after that it sets a flag, and the node looks at it.
"""

import os
import signal
import threading


def main() -> int:
    stop = threading.Event()
    running = False

    def on_stop(*_) -> None:
        if not running:
            os._exit(0)
        stop.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, on_stop)
    # The slow part: rclpy, the messages, numpy and OpenCV.
    from .node import run

    running = True
    return run(stop)
