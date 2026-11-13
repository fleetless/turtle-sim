# SPDX-License-Identifier: Apache-2.0
"""The simulator node: a thin adapter between the core and ROS.

It steps the simulation from one 50 Hz timer, hands it every `/cmd_vel`,
every service call and every goal, and publishes what the core knows at the
rates a real robot would. It does no physics of its own; `core/simulation.py`
is the file to read for that. A timer that comes late takes the steps it
missed, up to a tenth of a second of them, so a busy machine does not slow
the turtle down.

One thread spins all of it, so no callback may wait. An action's execute
callback is a coroutine: it hands the goal to the simulation and awaits a
future, and the step timer completes that future when the goal has ended.
In between the callback is not running at all.
"""

import array
import faulthandler
import math
import os
import threading
import time
from dataclasses import dataclass

import numpy as np
import rclpy
from fleetless_turtle_msgs.action import Dock, DriveTo, DriveToSquare
from fleetless_turtle_msgs.msg import TurtleStatus
from fleetless_turtle_msgs.msg import World as WorldMsg
from fleetless_turtle_msgs.srv import SetBlock, Teleport
from geometry_msgs.msg import PoseStamped, TransformStamped, Twist
from nav_msgs.msg import OccupancyGrid, Odometry, Path
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.action.server import ServerGoalHandle
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rclpy.signals import SignalHandlerOptions
from rclpy.task import Future
from sensor_msgs.msg import BatteryState, JointState, LaserScan, Range
from std_srvs.srv import SetBool, Trigger
from tf2_ros import StaticTransformBroadcaster, TransformBroadcaster

from .core import constants as c
from .core.goals import SUCCEEDED, DockFeedback, DockResult, DriveFeedback, DriveResult, Goal
from .core.pace import Pace
from .core.simulation import Simulation
from .core.world import MapError, World

NODE_NAME = "turtle_simulator"

STOP_BOUND_S = 3.0
"""How long the node may take to end once it was asked to stop. Then it
says so and ends as it is. In the image, launch follows its interrupt with
SIGTERM a second later and with SIGKILL five seconds after that, and
`docker stop` allows ten in all: this is under both."""

STOP_LAST_RESORT_S = 4.0
"""How long until Python's faulthandler ends a stop that cannot say so
itself: one that hangs in a ROS call, which keeps Python's lock."""

# "Latched": a subscriber that joins late still gets the last message.
LATCHED = QoSProfile(
    depth=1,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
    reliability=ReliabilityPolicy.RELIABLE,
)

_SUPPLY_STATUS = {
    "charging": BatteryState.POWER_SUPPLY_STATUS_CHARGING,
    "full": BatteryState.POWER_SUPPLY_STATUS_FULL,
    "discharging": BatteryState.POWER_SUPPLY_STATUS_DISCHARGING,
}


class Refusal(Exception):
    """A start the node refuses. The message is one sentence for the user.

    It names the environment variable before the parameter: whoever started
    the container set the one and has never heard of the other.
    """


def _yaw(angle: float) -> tuple[float, float]:
    """A rotation about the vertical axis as the z and w of a quaternion."""
    return math.sin(angle / 2.0), math.cos(angle / 2.0)


@dataclass
class _Running:
    """A goal between the simulation, which runs it, and the caller, who waits for it."""

    goal: Goal
    handle: ServerGoalHandle
    ended: Future
    """Completed with the goal's result by whoever sees first that it has ended."""
    feedback: type
    """The action's feedback message."""
    feedback_due: float
    """The simulated time of the next feedback."""
    phase: str | None = None
    """The phase the last feedback named."""


class SimulatorNode(Node):
    def __init__(self):
        super().__init__(NODE_NAME)
        self.declare_parameter(
            "map",
            "",
            ParameterDescriptor(
                description="The map file: an 8-bit RGB PNG of 256 x 256 pixels.", read_only=True
            ),
        )
        # Any type is accepted and checked below: a launch file hands over
        # whatever an environment variable held, and the answer to "abc"
        # is a sentence, not a stack trace.
        self.declare_parameter(
            "battery_drain",
            1.0,
            ParameterDescriptor(
                description="Factor on the battery's discharge rates; 0 turns discharge off.",
                dynamic_typing=True,
                read_only=True,
            ),
        )
        self._sim = Simulation(self._load_world(), battery_drain=self._battery_drain())
        # One lock around the simulation: every callback takes it.
        self._lock = threading.Lock()
        # The core reads no clock. This node does, and asks how many steps
        # are due whenever its timer gets round to it.
        self._pace = Pace()
        self._warned_about_non_finite = False
        self._published_world_version = None
        self._published_plan_version = None
        self._published_trail_version = None
        self._published_trail_number = None
        self._trail_published_at = -math.inf
        # The goals whose callers still wait for a result.
        self._running: list[_Running] = []

        self._odometry = self.create_publisher(Odometry, "odom", 10)
        self._scan = self.create_publisher(LaserScan, "scan", 10)
        self._ranges = {
            name: self.create_publisher(Range, f"range/{name}", 10)
            for name in c.RANGE_SENSOR_ANGLES
        }
        self._battery = self.create_publisher(BatteryState, "battery_state", 10)
        self._status = self.create_publisher(TurtleStatus, "turtle/status", 10)
        self._world = self.create_publisher(WorldMsg, "turtle/world", LATCHED)
        self._map = self.create_publisher(OccupancyGrid, "map", LATCHED)
        self._plan = self.create_publisher(Path, "turtle/plan", LATCHED)
        self._trail = self.create_publisher(Path, "turtle/trail", LATCHED)
        self._tf = TransformBroadcaster(self)
        self._tf_static = StaticTransformBroadcaster(self)
        self._publish_static_transforms()
        # The latched topics say something from the start: the world as
        # loaded, no path, no trail.
        self._publish_what_changed()

        self.create_subscription(Twist, "cmd_vel", self._on_cmd_vel, 10)

        self.create_service(Trigger, "turtle/reset", self._on_reset)
        self.create_service(Teleport, "turtle/teleport", self._on_teleport)
        self.create_service(SetBlock, "turtle/set_block", self._on_set_block)
        self.create_service(SetBool, "turtle/set_trail", self._on_set_trail)

        # Three servers, one navigator. Every goal is accepted and answered
        # through its result, and so is every cancel.
        self._actions = [
            ActionServer(
                self,
                action,
                name,
                execute_callback=execute,
                goal_callback=lambda _request: GoalResponse.ACCEPT,
                cancel_callback=lambda _handle: CancelResponse.ACCEPT,
            )
            for action, name, execute in (
                (DriveTo, "turtle/drive_to", self._drive_to),
                (DriveToSquare, "turtle/drive_to_square", self._drive_to_square),
                (Dock, "turtle/dock", self._dock),
            )
        ]

        self.create_timer(c.STEP_S, self._step)
        self.create_timer(1.0 / c.ODOMETRY_RATE_HZ, self._publish_odometry)
        self.create_timer(1.0 / c.LASER_RATE_HZ, self._publish_scan)
        self.create_timer(1.0 / c.RANGE_RATE_HZ, self._publish_ranges)
        self.create_timer(1.0 / c.BATTERY_RATE_HZ, self._publish_battery)
        self.create_timer(1.0 / c.STATUS_RATE_HZ, self._publish_status)

        # The model's legs and head, which robot_state_publisher turns into transforms.
        self._joints = self.create_publisher(JointState, "joint_states", 10)
        self.create_timer(1.0 / c.JOINT_RATE_HZ, self._publish_joints)

        x, y = self._sim.status().cell_x, self._sim.status().cell_y
        self.get_logger().info(
            f"The turtle is on the pad, cell ({x}, {y}), in {self.get_parameter('map').value}."
        )

    # --- Parameters ---------------------------------------------------------

    def _load_world(self) -> World:
        path = self.get_parameter("map").value
        if not path:
            raise Refusal("TURTLE_MAP (the parameter map) is empty: it names the map file.")
        try:
            return World.load(path)
        except MapError as error:
            raise Refusal(str(error)) from None

    def _battery_drain(self) -> float:
        value = self.get_parameter("battery_drain").value
        try:
            if isinstance(value, bool):
                raise ValueError
            drain = float(value)
            if not math.isfinite(drain) or drain < 0.0:
                raise ValueError
        except (TypeError, ValueError):
            raise Refusal(
                f'TURTLE_BATTERY_DRAIN (the parameter battery_drain) is "{value}": '
                "the battery drain factor is a number of 0 or more."
            ) from None
        return drain

    # --- In ------------------------------------------------------------------

    def _on_cmd_vel(self, twist: Twist) -> None:
        """A velocity command. Only linear.x and angular.z mean anything to two wheels."""
        with self._lock:
            self._sim.command(twist.linear.x, twist.angular.z)
        self._settle()  # a command other than zero ends a goal

    def _step(self) -> None:
        with self._lock:
            for _ in range(self._pace.due(time.monotonic())):
                self._sim.step()
            non_finite = self._sim.drive.non_finite_commands
        if non_finite and not self._warned_about_non_finite:
            self._warned_about_non_finite = True
            self.get_logger().warning(
                "A /cmd_vel held a value that is not a number. It counted as 0, "
                "and so will the next ones. This is logged once."
            )
        self._settle()
        self._publish_what_changed()

    # --- Services -------------------------------------------------------------
    # The core checks the arguments and words the answer; a call from inside
    # ROS has no cloud in front of it.

    def _on_reset(self, _request: Trigger.Request, response: Trigger.Response):
        with self._lock:
            response.success, response.message = self._sim.reset()
        return self._answered(response)

    def _on_teleport(self, request: Teleport.Request, response: Teleport.Response):
        with self._lock:
            response.success, response.message = self._sim.teleport(
                request.x, request.y, request.heading_deg
            )
        return self._answered(response)

    def _on_set_block(self, request: SetBlock.Request, response: SetBlock.Response):
        with self._lock:
            response.success, response.message = self._sim.set_block(
                request.x, request.y, request.occupied
            )
        return self._answered(response)

    def _on_set_trail(self, request: SetBool.Request, response: SetBool.Response):
        with self._lock:
            response.success, response.message = self._sim.set_trail(request.data)
        return self._answered(response)

    def _answered(self, response):
        """What a service changed is out before its answer is: a goal ended, a cell, a path."""
        self._settle()
        self._publish_what_changed()
        return response

    # --- Actions --------------------------------------------------------------

    async def _drive_to(self, handle: ServerGoalHandle) -> DriveTo.Result:
        request = handle.request
        return await self._run(handle, DriveTo, lambda: self._sim.drive_to(request.x, request.y))

    async def _drive_to_square(self, handle: ServerGoalHandle) -> DriveToSquare.Result:
        square = handle.request.square
        return await self._run(handle, DriveToSquare, lambda: self._sim.drive_to_square(square))

    async def _dock(self, handle: ServerGoalHandle) -> Dock.Result:
        return await self._run(handle, Dock, self._sim.dock)

    async def _run(self, handle: ServerGoalHandle, action, start):
        """One goal, from the caller's hands into the simulation's and back.

        It does not wait in the sense a thread waits: `await` hands the one
        thread back to the executor, and this coroutine goes on when the
        step timer has completed `ended`.
        """
        with self._lock:
            goal = start()
            due = self._sim.time
        ended = Future()
        self._running.append(_Running(goal, handle, ended, action.Feedback, due))
        # The new goal has ended the one before it, and a goal that asked
        # for the impossible has ended itself: both are answered here.
        self._settle()
        result = await ended

        if result.reason in SUCCEEDED:
            handle.succeed()
        elif result.reason == "canceled":
            handle.canceled()
        else:
            handle.abort()
        return _message(action.Result(), result)

    def _settle(self) -> None:
        """Between the callers and their goals: cancels in, feedback and results out."""
        with self._lock:
            now = self._sim.time
        for running in list(self._running):
            goal = running.goal
            if goal.result is None and running.handle.is_cancel_requested:
                goal.cancel()  # the turtle brakes; the result follows once it stands
            if goal.result is not None:
                self._running.remove(running)
                running.ended.set_result(goal.result)
                continue
            # Feedback twice a second, and at once when the phase changes: a
            # turtle that plans again does so in less than half a second.
            feedback = goal.feedback()
            if now >= running.feedback_due - c.TIME_TOLERANCE_S or feedback.phase != running.phase:
                running.feedback_due = now + 1.0 / c.GOAL_FEEDBACK_RATE_HZ
                running.phase = feedback.phase
                running.handle.publish_feedback(_message(running.feedback(), feedback))

    # --- Out -----------------------------------------------------------------

    def _publish_odometry(self) -> None:
        with self._lock:
            drive = self._sim.drive
            x, y, heading = drive.x, drive.y, drive.heading
            linear, angular = drive.linear, drive.angular
        stamp = self.get_clock().now().to_msg()
        z, w = _yaw(heading)

        # Exact odometry: the pose published is the pose simulated.
        odometry = Odometry()
        odometry.header.stamp = stamp
        odometry.header.frame_id = "odom"
        odometry.child_frame_id = "base_link"
        odometry.pose.pose.position.x = x
        odometry.pose.pose.position.y = y
        odometry.pose.pose.orientation.z = z
        odometry.pose.pose.orientation.w = w
        odometry.twist.twist.linear.x = linear
        odometry.twist.twist.angular.z = angular
        self._odometry.publish(odometry)

        transform = TransformStamped()
        transform.header.stamp = stamp
        transform.header.frame_id = "odom"
        transform.child_frame_id = "base_link"
        transform.transform.translation.x = x
        transform.transform.translation.y = y
        transform.transform.rotation.z = z
        transform.transform.rotation.w = w
        self._tf.sendTransform(transform)

    def _publish_scan(self) -> None:
        with self._lock:
            ranges = self._sim.scan()
        scan = LaserScan()
        scan.header.stamp = self.get_clock().now().to_msg()
        scan.header.frame_id = "base_link"
        scan.angle_min = c.LASER_ANGLE_MIN
        scan.angle_max = c.LASER_ANGLE_MIN + (c.LASER_RAYS - 1) * c.LASER_ANGLE_INCREMENT
        scan.angle_increment = c.LASER_ANGLE_INCREMENT
        scan.scan_time = 1.0 / c.LASER_RATE_HZ
        scan.range_min = c.LASER_RANGE_MIN_M
        scan.range_max = c.LASER_RANGE_MAX_M
        # A ray that hit nothing is inf (REP 117).
        scan.ranges = ranges
        self._scan.publish(scan)

    def _publish_ranges(self) -> None:
        with self._lock:
            readings = self._sim.ranges()
        stamp = self.get_clock().now().to_msg()
        for name, reading in readings.items():
            message = Range()
            message.header.stamp = stamp
            message.header.frame_id = f"range_{name}"
            message.radiation_type = Range.INFRARED
            message.field_of_view = 0.0  # one ray
            message.min_range = c.RANGE_MIN_M
            message.max_range = c.RANGE_MAX_M
            # Nothing in range reports max_range, a number, so a chart has no holes.
            message.range = reading
            self._ranges[name].publish(message)

    def _publish_battery(self) -> None:
        with self._lock:
            battery = self._sim.battery
            percent, voltage = battery.percent, battery.voltage
            supply = battery.supply(self._sim.on_charger)
        state = BatteryState()
        state.header.stamp = self.get_clock().now().to_msg()
        state.voltage = voltage
        state.percentage = percent / 100.0
        state.power_supply_status = _SUPPLY_STATUS[supply]
        state.present = True
        # What this battery does not measure is NaN, as the message asks.
        state.temperature = math.nan
        state.current = math.nan
        state.charge = math.nan
        state.capacity = math.nan
        state.design_capacity = math.nan
        self._battery.publish(state)

    def _publish_status(self) -> None:
        with self._lock:
            status = self._sim.status()
        message = TurtleStatus()
        message.header.stamp = self.get_clock().now().to_msg()
        message.header.frame_id = "map"
        message.cell_x = status.cell_x
        message.cell_y = status.cell_y
        message.square = status.square
        message.heading_deg = status.heading_deg
        message.speed = status.speed
        message.turn_rate = status.turn_rate
        message.mode = status.mode
        message.crawling = status.crawling
        message.on_charger = status.on_charger
        message.bumper = status.bumper
        message.collisions = status.collisions
        message.odometer_m = status.odometer_m
        message.trail = status.trail
        self._status.publish(message)

    def _publish_joints(self) -> None:
        with self._lock:
            positions = self._sim.joints()
        joints = JointState()
        joints.header.stamp = self.get_clock().now().to_msg()
        joints.name = list(positions)
        joints.position = list(positions.values())
        self._joints.publish(joints)

    def _publish_what_changed(self) -> None:
        """The latched topics: the world and the path on change, the trail as it grows.

        The trail grows a point every tenth of a metre, and its growth is
        published at most once a second, whoever asks: a service that did
        nothing to the trail does not send it out early. A trail that was
        emptied or switched, by `reset` or `set_trail`, is a new trail and
        does not wait for that second: it is out before the service answers.
        """
        with self._lock:
            world = self._sim.world_version != self._published_world_version
            plan = self._sim.plan_version != self._published_plan_version
            trail = self._sim.trail_version != self._published_trail_version
            a_new_trail = self._sim.trail_number != self._published_trail_number
        if world:
            self._publish_world()
        if plan:
            with self._lock:
                points = self._sim.plan
                self._published_plan_version = self._sim.plan_version
            self._plan.publish(self._path(points))
        due = time.monotonic() - self._trail_published_at >= 1.0 / c.TRAIL_RATE_HZ
        if trail and (a_new_trail or due):
            with self._lock:
                points = tuple(self._sim.trail)
                self._published_trail_version = self._sim.trail_version
                self._published_trail_number = self._sim.trail_number
            self._trail_published_at = time.monotonic()
            self._trail.publish(self._path(points))

    def _path(self, points) -> Path:
        """Points in metres as a path in the map frame, each pose looking along the path."""
        path = Path()
        path.header.stamp = self.get_clock().now().to_msg()
        path.header.frame_id = "map"
        z, w = 0.0, 1.0
        for index, (x, y) in enumerate(points):
            if index + 1 < len(points):
                ahead_x, ahead_y = points[index + 1]
                if (ahead_x, ahead_y) != (x, y):
                    z, w = _yaw(math.atan2(ahead_y - y, ahead_x - x))
            pose = PoseStamped()
            pose.header = path.header
            pose.pose.position.x = x
            pose.pose.position.y = y
            pose.pose.orientation.z = z
            pose.pose.orientation.w = w
            path.poses.append(pose)
        return path

    def _publish_world(self) -> None:
        with self._lock:
            kinds = self._sim.world.kinds.copy()
            self._published_world_version = self._sim.world_version
        stamp = self.get_clock().now().to_msg()

        world = WorldMsg()
        world.header.stamp = stamp
        world.header.frame_id = "map"
        world.width = c.GRID_CELLS
        world.height = c.GRID_CELLS
        world.resolution = c.CELL_SIZE_M
        # Row-major from cell (0, 0), which is how `kinds` lies in memory.
        world.kinds = array.array("B", kinds.tobytes())
        self._world.publish(world)

        # The same cells for ROS tools: 100 where a cell blocks, 0 where not.
        grid = OccupancyGrid()
        grid.header.stamp = stamp
        grid.header.frame_id = "map"
        grid.info.map_load_time = stamp
        grid.info.resolution = c.CELL_SIZE_M
        grid.info.width = c.GRID_CELLS
        grid.info.height = c.GRID_CELLS
        grid.info.origin.orientation.w = 1.0
        occupied = np.where(np.isin(kinds, c.BLOCKING), 100, 0).astype(np.int8)
        grid.data = array.array("b", occupied.tobytes())
        self._map.publish(grid)

    def _publish_static_transforms(self) -> None:
        stamp = self.get_clock().now().to_msg()

        def transform(parent: str, child: str, angle: float = 0.0) -> TransformStamped:
            message = TransformStamped()
            message.header.stamp = stamp
            message.header.frame_id = parent
            message.child_frame_id = child
            message.transform.rotation.z, message.transform.rotation.w = _yaw(angle)
            return message

        # map -> odom is the identity, because the odometry is exact. Each
        # range sensor gets a frame that looks along its ray.
        self._tf_static.sendTransform(
            [transform("map", "odom")]
            + [
                transform("base_link", f"range_{name}", angle)
                for name, angle in c.RANGE_SENSOR_ANGLES.items()
            ]
        )

    def destroy_node(self) -> None:
        # The action servers first: they hold on to the node.
        for server in self._actions:
            server.destroy()
        super().destroy_node()


def _message(message, snapshot: DriveResult | DockResult | DriveFeedback | DockFeedback):
    """A result or a feedback of the core as its ROS message: the fields have the same names."""
    for name in message.get_fields_and_field_types():
        setattr(message, name, getattr(snapshot, name))
    return message


def _hold_a_stop_to_its_bound(
    stop: threading.Event, failures: list, ended: threading.Event
) -> None:
    """Wait for the stop, then give the node `STOP_BOUND_S` to end. A thread of its own runs this.

    What ends a node is ROS's to do, and its calls take no bound: one that
    does not return would keep the process until launch or Docker kills it.
    So from the moment a stop is asked for, two things watch.

    This thread. When the bound runs out it says one sentence and ends the
    process with exit code 0: it was asked to stop, and it stops. It writes
    the sentence to stderr itself, because ROS's logger may be what hangs.

    And Python's faulthandler, a second later. A ROS call that hangs keeps
    Python's lock, and then no Python thread runs again, this one included.
    The faulthandler's own thread needs no lock: it writes where every
    thread stands, which names the call, and ends the process with exit
    code 1.

    A stop that ends in time sets `ended`, and neither says anything.
    """
    stop.wait()
    if failures:
        return  # no stop that was asked for: the failure ends the node, in its own words
    faulthandler.dump_traceback_later(STOP_LAST_RESORT_S, exit=True, file=2)
    if ended.wait(STOP_BOUND_S):
        faulthandler.cancel_dump_traceback_later()
        return
    words = f"The simulator did not finish stopping within {STOP_BOUND_S:g} s and ends without it."
    os.write(2, f"{words}\n".encode())
    os._exit(0)


def run(stop: threading.Event, args=None) -> int:
    """Run the node until `stop` is set. `start.py` sets it on SIGINT and SIGTERM.

    This process takes the signals itself. Left to ROS, a signal closes the
    context at once, under a timer that is about to publish into it.
    """
    if stop.is_set():
        return 0  # asked to stop during the imports: there is nothing to undo
    failures = []
    ended = threading.Event()
    threading.Thread(
        target=_hold_a_stop_to_its_bound, args=(stop, failures, ended), name="stop", daemon=True
    ).start()
    rclpy.init(args=args, signal_handler_options=SignalHandlerOptions.NO)
    try:
        node = SimulatorNode()
    except Refusal as refusal:
        rclpy.logging.get_logger(NODE_NAME).fatal(str(refusal))
        rclpy.try_shutdown()
        return 2
    if stop.is_set():
        # Asked to stop while it was still starting, by a person or by a
        # launch whose other node gave up. Nothing is wrong: leave quietly.
        node.destroy_node()
        rclpy.try_shutdown()
        ended.set()
        return 0

    # One thread. Under rclpy's multi-threaded executor the step timer
    # stalled for 90 to 560 ms whenever /cmd_vel arrived, and `Pace` drops a
    # lag of more than 0.1 s: on a busy machine the turtle drove at 87 % of
    # the commanded speed. No callback of this node waits for another one:
    # a goal's callback awaits a future and is off the thread meanwhile.
    executor = SingleThreadedExecutor()
    executor.add_node(node)

    def spin() -> None:
        try:
            executor.spin()
        except Exception as failure:
            failures.append(failure)
        finally:
            stop.set()

    spinning = threading.Thread(target=spin, name="ros", daemon=True)
    spinning.start()
    stop.wait()

    # In this order: let the running callbacks finish, then take ROS away.
    executor.shutdown()
    spinning.join(timeout=5.0)
    node.destroy_node()
    rclpy.try_shutdown()
    ended.set()
    if failures:
        raise failures[0]
    return 0
