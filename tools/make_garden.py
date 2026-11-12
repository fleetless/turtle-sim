#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Draws the shipped map, deterministically.

    python3 tools/make_garden.py

writes maps/garden.png (the map: one pixel per cell, north up),
maps/garden.places.json (a handful of named cells, so no test and no check
hard-codes a coordinate of a map that may be redrawn) and docs/garden.png
(the picture in the README). A test compares all three with what this
script draws, so change the garden here and run it again.

Needs numpy and OpenCV, nothing else. Without them on the host:

    docker build -t turtle-sim:test-unit -f docker/unit.Dockerfile docker
    docker run --rm -u "$(id -u):$(id -g)" -v "$PWD:/src" -w /src \
        turtle-sim:test-unit python tools/make_garden.py
"""

import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ros" / "fleetless_turtle_sim"))

from fleetless_turtle_sim.core.constants import (  # noqa: E402
    FREE,
    GRID_CELLS,
    MAP_COLOURS,
    PAD,
    ROCK,
    SQUARE_CELLS,
    SQUARES,
    WALL,
    WATER,
)

MAP_PATH = ROOT / "maps" / "garden.png"
PLACES_PATH = ROOT / "maps" / "garden.places.json"
PICTURE_PATH = ROOT / "docs" / "garden.png"

# The cells the tests and infra/sim-check.mjs drive to. `open` is a free cell
# a few metres from the start, `far` a free cell across the map, `enclosed` a
# cell in the walled courtyard nothing leads into, `wall` a wall cell.
PLACES = {
    "open": {"x": 40, "y": 24},
    "far": {"x": 240, "y": 240},
    "enclosed": {"x": 213, "y": 193},
    "wall": {"x": 24, "y": 200},
}


def draw() -> np.ndarray:
    """The garden as `kinds[cy, cx]`, row 0 the south edge."""
    kinds = np.full((GRID_CELLS, GRID_CELLS), FREE, np.uint8)

    def box(kind, x_min, y_min, x_max, y_max):
        """A filled rectangle of cells, both corners included."""
        kinds[y_min : y_max + 1, x_min : x_max + 1] = kind

    def frame(kind, x_min, y_min, x_max, y_max, thickness=2):
        """The outline of a rectangle, `thickness` cells thick, drawn inwards."""
        t = thickness - 1
        box(kind, x_min, y_min, x_max, y_min + t)
        box(kind, x_min, y_max - t, x_max, y_max)
        box(kind, x_min, y_min, x_min + t, y_max)
        box(kind, x_max - t, y_min, x_max, y_max)

    def ellipse(kind, centre_x, centre_y, radius_x, radius_y):
        cy, cx = np.mgrid[0:GRID_CELLS, 0:GRID_CELLS]
        inside = ((cx - centre_x) / radius_x) ** 2 + ((cy - centre_y) / radius_y) ** 2 <= 1.0
        kinds[inside] = kind

    last = GRID_CELLS - 1

    # The garden wall, two cells thick.
    frame(WALL, 0, 0, last, last)

    # The charging pad, 12 x 12, in square A1, one cell clear of the wall.
    box(PAD, 3, 3, 14, 14)

    # The rock alley: a lane between two hedges, with rocks left and right in
    # turn. They reach past the middle, so there is no straight line through.
    box(WALL, 48, 46, 160, 47)
    box(WALL, 48, 64, 160, 65)
    for i in range(6):
        x = 56 + 16 * i
        y = 48 if i % 2 == 0 else 55
        box(ROCK, x, y, x + 5, y + 8)

    # The orchard: trees on a grid, wide enough apart to drive between.
    for x in range(150, 235, 12):
        for y in (16, 28, 40):
            box(ROCK, x, y, x + 1, y + 1)

    # The pond, and a smaller one by the house.
    ellipse(WATER, 200, 72, 30.5, 20.5)
    ellipse(WATER, 44, 112, 12.5, 12.5)

    # Three rock beds with lanes between them.
    for x in (96, 112, 128):
        box(ROCK, x, 88, x + 9, 111)

    # The dead end: a lane closed on three sides, open to the west.
    box(WALL, 150, 120, 210, 121)
    box(WALL, 150, 136, 210, 137)
    box(WALL, 209, 120, 210, 137)

    # The hedge in front of the house, open at both ends.
    box(WALL, 16, 140, 90, 142)

    # The house: two rooms, a doorway to the south, a door between the rooms.
    frame(WALL, 24, 168, 103, 231)
    box(FREE, 44, 168, 51, 169)
    box(WALL, 70, 170, 71, 229)
    box(FREE, 70, 196, 71, 203)

    # The shed: solid, nothing to see inside.
    box(WALL, 120, 200, 147, 219)

    # The courtyard: free inside, walled all the way round. No way in.
    frame(WALL, 196, 176, 231, 211)

    return kinds


def to_pixels(kinds: np.ndarray) -> np.ndarray:
    """`kinds` as an image, north up, red-green-blue."""
    pixels = np.zeros((GRID_CELLS, GRID_CELLS, 3), np.uint8)
    for colour, kind in MAP_COLOURS.items():
        pixels[kinds == kind] = colour
    return np.flipud(pixels)


def picture(kinds: np.ndarray, scale: int = 3, margin: int = 28) -> np.ndarray:
    """The README's picture: the map enlarged, with the squares and their names."""
    size = GRID_CELLS * scale
    image = np.full((size + margin, size + margin, 3), 0xFF, np.uint8)
    enlarged = np.repeat(np.repeat(to_pixels(kinds), scale, axis=0), scale, axis=1)
    image[:size, margin:] = enlarged
    grey = (0xB0, 0xB0, 0xB0)
    ink = (0x40, 0x40, 0x40)
    step = SQUARE_CELLS * scale
    for i in range(1, SQUARES):
        image[:size, margin + i * step] = grey
        image[size - i * step, margin:] = grey
    for i in range(SQUARES):
        letter = chr(ord("A") + i)
        number = str(i + 1)
        cv2.putText(
            image,
            letter,
            (margin + i * step + step // 2 - 5, size + 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            ink,
            1,
            cv2.LINE_8,
        )
        cv2.putText(
            image,
            number,
            (4 if i < 9 else 0, size - i * step - step // 2 + 5),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            ink,
            1,
            cv2.LINE_8,
        )
    return image


def places_text() -> str:
    return json.dumps(PLACES, indent=2) + "\n"


def main() -> None:
    kinds = draw()
    MAP_PATH.parent.mkdir(exist_ok=True)
    PICTURE_PATH.parent.mkdir(exist_ok=True)
    cv2.imwrite(str(MAP_PATH), cv2.cvtColor(to_pixels(kinds), cv2.COLOR_RGB2BGR))
    PLACES_PATH.write_text(places_text())
    cv2.imwrite(str(PICTURE_PATH), cv2.cvtColor(picture(kinds), cv2.COLOR_RGB2BGR))
    blocked = int((kinds != FREE).sum() - (kinds == PAD).sum())
    print(
        f"{MAP_PATH.relative_to(ROOT)}: {blocked} of {kinds.size} cells block "
        f"({100 * blocked / kinds.size:.1f} %), {int((kinds == WATER).sum())} of them water."
    )


if __name__ == "__main__":
    main()
