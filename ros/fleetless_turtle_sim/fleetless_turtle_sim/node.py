# SPDX-License-Identifier: Apache-2.0
"""The simulator node: a thin adapter between the core and ROS.

It steps the simulation from one 50 Hz timer, hands it every `/cmd_vel`, and
publishes what the core knows at the rates a real robot would. It does no
physics of its own; `core/simulation.py` is the file to read for that. A
timer that comes late takes the steps it missed, up to a tenth of a second
of them, so a busy machine does not slow the turtle down.
"""

import array
import math
import threading
import time

import numpy as np
import rclpy
from fleetless_turtle_msgs.msg import TurtleStatus
from fleetless_turtle_msgs.msg import World as WorldMsg
from geometry_msgs.msg import TransformStamped, Twist
from nav_msgs.msg import OccupancyGrid, Odometry
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rclpy.signals import SignalHandlerOptions
from sensor_msgs.msg import BatteryState, LaserScan, Range
from tf2_ros import StaticTransformBroadcaster, TransformBroadcaster

from .core import constants as c
from .core.pace import Pace
from .core.simulation import Simulation
from .core.world import MapError, World

NODE_NAME = "turtle_simulator"

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
        self._tf = TransformBroadcaster(self)
        self._tf_static = StaticTransformBroadcaster(self)
        self._publish_static_transforms()
        self._publish_world()

        self.create_subscription(Twist, "cmd_vel", self._on_cmd_vel, 10)

        self.create_timer(c.STEP_S, self._step)
        self.create_timer(1.0 / c.ODOMETRY_RATE_HZ, self._publish_odometry)
        self.create_timer(1.0 / c.LASER_RATE_HZ, self._publish_scan)
        self.create_timer(1.0 / c.RANGE_RATE_HZ, self._publish_ranges)
        self.create_timer(1.0 / c.BATTERY_RATE_HZ, self._publish_battery)
        self.create_timer(1.0 / c.STATUS_RATE_HZ, self._publish_status)

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

    def _step(self) -> None:
        with self._lock:
            for _ in range(self._pace.due(time.monotonic())):
                self._sim.step()
            non_finite = self._sim.drive.non_finite_commands
            world_changed = self._sim.world_version != self._published_world_version
        if non_finite and not self._warned_about_non_finite:
            self._warned_about_non_finite = True
            self.get_logger().warning(
                "A /cmd_vel held a value that is not a number. It counted as 0, "
                "and so will the next ones. This is logged once."
            )
        if world_changed:
            self._publish_world()

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


def run(stop: threading.Event, args=None) -> int:
    """Run the node until `stop` is set. `start.py` sets it on SIGINT and SIGTERM.

    This process takes the signals itself. Left to ROS, a signal closes the
    context at once, under a timer that is about to publish into it.
    """
    if stop.is_set():
        return 0  # asked to stop during the imports: there is nothing to undo
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
        return 0

    # One thread. Under rclpy's multi-threaded executor the step timer
    # stalled for 90 to 560 ms whenever /cmd_vel arrived, and `Pace` drops a
    # lag of more than 0.1 s: on a busy machine the turtle drove at 87 % of
    # the commanded speed. No callback of this node waits for another one,
    # so one thread is all they need.
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    failures = []

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
    if failures:
        raise failures[0]
    return 0
