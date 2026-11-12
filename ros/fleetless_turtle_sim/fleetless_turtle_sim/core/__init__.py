# SPDX-License-Identifier: Apache-2.0
"""The simulation core: the world, the drive, the sensors, the battery.

Nothing in here imports ROS, reads a clock or rolls a die. The node in
`fleetless_turtle_sim.node` is a thin adapter around it, and plain pytest is
enough to test it.
"""
