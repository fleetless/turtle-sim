# SPDX-License-Identifier: Apache-2.0
"""The two renderers: `top.render` and `eye.render`.

Each is a function from a `Scene` to a picture, an array of red, green and
blue bytes. Nothing in here imports ROS or reads a clock: the node in
`fleetless_turtle_cameras.node` is a thin adapter around them, and plain
pytest is enough to test them.
"""
