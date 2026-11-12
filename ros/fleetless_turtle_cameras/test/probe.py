# SPDX-License-Identifier: Apache-2.0
"""The test's side of the ROS graph: a node that listens, measures and publishes.

The simulator's tests have the same helper. A package's tests are not
installed, so each package that needs one keeps its own.
"""

import threading
import time

import rclpy
from rclpy.executors import SingleThreadedExecutor


class Probe:
    def __init__(self, name: str = "probe"):
        self.node = rclpy.create_node(name)
        self._executor = SingleThreadedExecutor()
        self._executor.add_node(self.node)
        self._thread = threading.Thread(target=self._executor.spin, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._executor.shutdown()
        self._thread.join(timeout=5.0)
        self.node.destroy_node()

    def listen(self, msg_type, topic: str, qos=10) -> list:
        """A list that fills with (arrival time, message) while the test goes on."""
        heard = []
        self.node.create_subscription(
            msg_type, topic, lambda message: heard.append((time.monotonic(), message)), qos
        )
        return heard

    @staticmethod
    def until(heard: list, where, timeout: float = 10.0, what: str = "a message"):
        """The first message in a `listen` list, from now on, that satisfies `where`."""
        deadline = time.monotonic() + timeout
        seen = len(heard)
        while time.monotonic() < deadline:
            now = len(heard)
            for _, message in heard[seen:now]:
                if where(message):
                    return message
            seen = now
            time.sleep(0.01)
        raise AssertionError(f"Nothing fitting arrived on {what} within {timeout} s.")

    @staticmethod
    def rate(heard: list) -> float:
        """Messages per second, between the first and the last arrival."""
        if len(heard) < 2:
            return 0.0
        return (len(heard) - 1) / (heard[-1][0] - heard[0][0])
