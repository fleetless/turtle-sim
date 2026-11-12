# SPDX-License-Identifier: Apache-2.0
"""Every number and every colour of the two cameras, named, and nowhere else.

What the world is made of (the grid, the cell, the turtle's size) is the
simulator's to say and stays in `fleetless_turtle_sim.core.constants`. The
shipped fleetless.yaml repeats the pictures' sizes and their rate; a test
keeps the two in step.
"""

import math

FRAME_RATE_HZ = 10.0
"""Pictures per second, from both cameras."""

# --- What the cameras draw from ----------------------------------------------

WORLD_CELLS_LIMIT = 512
"""The most cells along one side of a world that is drawn: twice the
simulator's grid. `top` keeps the whole world as one picture, and a picture
has to fit in memory."""

POSE_LIMIT_M = 100.0
"""How far from the map's origin, along x or y, the turtle or a point of a
path may lie and still be drawn. The largest world ends at 51.2 m; much
further out a number is no place, and a pixel no whole number."""

# --- `top`: the map around the turtle, north up ------------------------------

TOP_SIZE_PX = 480
"""The picture is square."""

TOP_WINDOW_M = 12.0
"""What it shows, edge to edge, centred on the turtle: 0.025 m a pixel."""

TOP_WALL_SHADOW_PX = 3
TOP_ROCK_SHADOW_PX = 1
"""The hint of height: how far a block's shadow falls, to the north-east."""

TOP_SHADOW = 0.8
"""What is left of a floor's colour in a shadow."""

TOP_LIT_EDGE = 1.08
TOP_DARK_EDGE = 0.78
"""A block's rim towards the sun, and the one away from it."""

TOP_LINE_OPACITY = 0.55
"""Of the lines between the squares and of their names."""

TOP_NAME_SCALE = 0.36
"""Size of a square's name, in OpenCV's font scale: letters 8 px high."""

TOP_MARKER_RADIUS_M = 0.2
"""The ring around the turtle, so it is found at four pixels a cell."""

TOP_TARGET_RADIUS_M = 0.15
"""The ring around the target."""

# --- `eye`: the turtle's own view --------------------------------------------

EYE_WIDTH_PX = 320
EYE_HEIGHT_PX = 240

EYE_FIELD_OF_VIEW = math.radians(70.0)
"""From the picture's left edge to its right one."""

EYE_HEIGHT_M = 0.1
"""How far above the floor the turtle looks from."""

EYE_RANGE_M = 8.0
"""Beyond this there is haze."""

EYE_NEAR_M = 0.01
"""No face is nearer than this: a distance is divided by."""

WALL_HEIGHT_M = 0.3
"""A wall is a tall block."""

ROCK_HEIGHT_M = 0.15
"""A rock is a low block: half a wall, and still above the turtle's eye, so
it hides the floor behind it and a wall behind it rises over it.

The view draws both as tall as this; a test keeps the two in step."""

FOG_POWER = 2.0
"""How haze grows with distance: little nearby, all of it at the range."""

FACE_LIGHT = (0.93, 1.0, 0.84, 0.78)
"""How bright a block's face is, by where it looks: west, south, east,
north. The sun stands in the south-west, as in the view."""

CELL_CONTRAST = 0.025
"""Every other cell is this much lighter, the others this much darker, on
the floor and on a block's faces. It is what moves when the turtle does."""

# --- Colours, as (red, green, blue). The view's, so one world looks like one --

LAWN = (0xA6, 0xC4, 0x8D)
LAWN_OTHER_SQUARE = (0x9D, 0xBD, 0x85)
"""The squares as on a chess board, only quieter."""

WATER = (0x4B, 0x9C, 0xD3)
WATER_SHORE = (0x6C, 0xB4, 0xDF)
PAD = (0xF2, 0xDC, 0x6B)
PAD_EDGE = (0xD6, 0xB4, 0x3A)
PAD_MARK = (0x4A, 0x43, 0x26)
UNDER_BLOCKS = (0x7D, 0x8A, 0x72)
WALL = (0xE9, 0xE2, 0xD4)
ROCK = (0x8F, 0x91, 0x8C)

OUTSIDE = (0x3F, 0x5B, 0x45)
"""Beyond the grid: the frame around the view's floor. Seen from the eye
it is the face of the world's edge, as tall as a wall."""

SQUARE_LINES = (0x4C, 0x6A, 0x45)

SKY = (0x8F, 0xBF, 0xE6)
HAZE = (0xE9, 0xEF, 0xE6)
"""The sky overhead, and what it and everything far away fade into."""

TRAIL = (0xD9, 0x7A, 0x2B)
PLAN = (0x7A, 0x3F, 0xD0)

TURTLE_SHELL = (0x2F, 0x6F, 0x4F)
TURTLE_SHELL_TOP = (0x3F, 0x8A, 0x61)
TURTLE_RIM = (0x86, 0xB8, 0x6B)
TURTLE_SKIN = (0xA3, 0xC9, 0x7D)
TURTLE_MARKER = (0xFF, 0xF6, 0xD6)
