# SPDX-License-Identifier: Apache-2.0
"""The test's side of the ROS graph: a node that listens, measures and publishes."""

import threading
import time

import rclpy
from rclpy.action import ActionClient
from rclpy.executors import SingleThreadedExecutor


class Sent:
    """A goal the probe has sent: its feedback so far, its result, and a way to cancel it."""

    def __init__(self, handle, feedback: list):
        self.handle = handle
        self.feedback = feedback
        """(arrival time, feedback message), in the order they came."""
        self._result = handle.get_result_async()

    def result(self, timeout: float = 60.0):
        """The goal's final status and its result message."""
        response = Probe.wait(self._result, timeout, "the goal's result")
        return response.status, response.result

    @property
    def ended(self) -> bool:
        return self._result.done()

    def cancel(self, timeout: float = 5.0):
        """Ask for a cancel and return the server's answer to the request."""
        return Probe.wait(self.handle.cancel_goal_async(), timeout, "the answer to the cancel")

    def phases(self) -> list[str]:
        """The phases the feedback named, each once, in the order they first came."""
        return list(dict.fromkeys(feedback.phase for _, feedback in self.feedback))


class Probe:
    def __init__(self, name: str = "probe"):
        self.node = rclpy.create_node(name)
        self._clients = {}
        self._executor = SingleThreadedExecutor()
        self._executor.add_node(self.node)
        self._thread = threading.Thread(target=self._executor.spin, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._executor.shutdown()
        self._thread.join(timeout=5.0)
        self.node.destroy_node()

    @staticmethod
    def wait(future, timeout: float, what: str):
        """The result of a future the probe's own thread completes."""
        deadline = time.monotonic() + timeout
        while not future.done():
            if time.monotonic() > deadline:
                raise AssertionError(f"{what} did not come within {timeout} s.")
            time.sleep(0.002)
        return future.result()

    def call(self, srv_type, name: str, timeout: float = 10.0, **fields):
        """Call a service and return its response."""
        if name not in self._clients:
            self._clients[name] = self.node.create_client(srv_type, name)
        client = self._clients[name]
        if not client.wait_for_service(timeout_sec=timeout):
            raise AssertionError(f"The service {name} did not appear within {timeout} s.")
        return self.wait(client.call_async(srv_type.Request(**fields)), timeout, name)

    def send(self, action_type, name: str, timeout: float = 10.0, **fields) -> Sent:
        """Send a goal. Every goal is accepted, so that is asserted here."""
        if name not in self._clients:
            self._clients[name] = ActionClient(self.node, action_type, name)
        client = self._clients[name]
        if not client.wait_for_server(timeout_sec=timeout):
            raise AssertionError(f"The action {name} did not appear within {timeout} s.")
        feedback = []
        handle = self.wait(
            client.send_goal_async(
                action_type.Goal(**fields),
                feedback_callback=lambda message: feedback.append(
                    (time.monotonic(), message.feedback)
                ),
            ),
            timeout,
            f"the answer to a goal on {name}",
        )
        assert handle.accepted, f"{name} did not accept {fields}"
        return Sent(handle, feedback)

    def listen(self, msg_type, topic: str, qos=10) -> list:
        """A list that fills with (arrival time, message) while the test goes on."""
        heard = []
        self.node.create_subscription(
            msg_type, topic, lambda message: heard.append((time.monotonic(), message)), qos
        )
        return heard

    def first(self, msg_type, topic: str, where=lambda message: True, timeout=10.0, qos=10):
        """The first message on a topic that satisfies `where`."""
        return self.until(self.listen(msg_type, topic, qos), where, timeout, topic)

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
    def newest(heard: list, where, timeout: float = 10.0, what: str = "a message"):
        """The newest message in a `listen` list, once it satisfies `where`.

        For a latched topic: what counts is what it says now, whether that
        arrived a moment ago or is still on its way.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if heard and where(heard[-1][1]):
                return heard[-1][1]
            time.sleep(0.01)
        raise AssertionError(f"The newest message on {what} did not fit within {timeout} s.")

    @staticmethod
    def rate(heard: list) -> float:
        """Messages per second, between the first and the last arrival."""
        if len(heard) < 2:
            return 0.0
        return (len(heard) - 1) / (heard[-1][0] - heard[0][0])
