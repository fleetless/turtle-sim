#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Makes the turtle's meshes, deterministically.

    python3 tools/make_meshes.py

writes the binary STL files of ros/fleetless_turtle_description/meshes/: a
shell, its rim, a head, an eye, a leg and a tail. Each is an ellipsoid or
half of one, so nobody needs a modelling program. The URDF beside them puts
them together, colours them and uses the leg four times and the eye twice.
A test compares the files with what this script makes, so change the turtle
here and run it again.

Needs numpy, nothing else. Without it on the host:

    docker build -t turtle-sim:test-unit -f docker/unit.Dockerfile docker
    docker run --rm -u "$(id -u):$(id -g)" -v "$PWD:/src" -w /src \
        turtle-sim:test-unit python tools/make_meshes.py

Metres, in each mesh's own frame: x ahead, y to the left, z up. The turtle
is real size: with the URDF's placements nothing reaches past the
simulator's footprint, a circle of 0.1 m radius.
"""

import math
import struct
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
MESHES = ROOT / "ros" / "fleetless_turtle_description" / "meshes"

# A vertex is written to the micrometre. That is finer than anything here
# needs, and coarse enough that every machine's sine rounds to the same file.
DECIMALS = 6


def _point(radii, centre, around: float, up: float) -> tuple[float, float, float]:
    """A point of an ellipsoid: `around` its vertical axis, `up` from its equator."""
    return (
        centre[0] + radii[0] * math.cos(up) * math.cos(around),
        centre[1] + radii[1] * math.cos(up) * math.sin(around),
        centre[2] + radii[2] * math.sin(up),
    )


def _bands(radii, centre, segments: int, rings: int, lowest: float) -> list:
    """An ellipsoid's surface from the latitude `lowest` to its top, as triangles.

    Seen from outside, every triangle's corners run counter-clockwise: that
    is how a mesh says which side is out.
    """
    triangles = []
    for ring in range(rings):
        south = lowest + (math.pi / 2 - lowest) * ring / rings
        north = lowest + (math.pi / 2 - lowest) * (ring + 1) / rings
        for segment in range(segments):
            west = 2 * math.pi * segment / segments
            east = 2 * math.pi * (segment + 1) / segments
            a = _point(radii, centre, west, south)
            b = _point(radii, centre, east, south)
            c = _point(radii, centre, east, north)
            d = _point(radii, centre, west, north)
            # At a pole two corners of the band are one point, and one
            # triangle is all there is.
            if south > -math.pi / 2:
                triangles.append((a, b, c))
            if ring < rings - 1:
                triangles.append((a, c, d))
    return triangles


def ellipsoid(radii, centre=(0.0, 0.0, 0.0), segments: int = 24, rings: int = 12) -> list:
    """A whole ellipsoid: `rings` bands from pole to pole, `segments` around."""
    return _bands(radii, centre, segments, rings, lowest=-math.pi / 2)


def dome(radii, segments: int = 32, rings: int = 8) -> list:
    """The upper half of an ellipsoid on the plane z = 0, closed underneath."""
    triangles = _bands(radii, (0.0, 0.0, 0.0), segments, rings, lowest=0.0)
    for segment in range(segments):
        west = 2 * math.pi * segment / segments
        east = 2 * math.pi * (segment + 1) / segments
        # The floor looks down: clockwise, seen from above.
        triangles.append(
            (
                (0.0, 0.0, 0.0),
                _point(radii, (0.0, 0.0, 0.0), east, 0.0),
                _point(radii, (0.0, 0.0, 0.0), west, 0.0),
            )
        )
    return triangles


def shapes() -> dict[str, list]:
    """Every mesh by its file's name, as triangles of three points each."""
    return {
        # The shell, a dome that stands on its flat side.
        "shell.stl": dome((0.074, 0.066, 0.042)),
        # The rim under it, a little wider: what shows of the turtle's belly.
        "rim.stl": ellipsoid((0.080, 0.072, 0.009), segments=32, rings=6),
        # The head, from its joint forwards. Its hind part is the neck, in the shell.
        "head.stl": ellipsoid(
            (0.024, 0.017, 0.015), centre=(0.016, 0.0, 0.002), segments=20, rings=10
        ),
        "eye.stl": ellipsoid((0.0045, 0.0045, 0.0045), segments=10, rings=6),
        # A leg, flat as a flipper, from its hip outwards along x.
        "leg.stl": ellipsoid((0.019, 0.011, 0.007), centre=(0.013, 0.0, 0.0), segments=16, rings=8),
        "tail.stl": ellipsoid((0.013, 0.005, 0.004), segments=10, rings=6),
    }


def stl(name: str, triangles: list) -> bytes:
    """A binary STL file: 80 bytes of header, a count, then 50 bytes a triangle."""
    corners = np.round(np.array(triangles, dtype=np.float64), DECIMALS)
    normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    normals /= np.linalg.norm(normals, axis=1, keepdims=True)

    # Not "solid ...": a file that starts so is taken for the text format.
    header = f"SPDX-License-Identifier: Apache-2.0. turtle-sim {name}, tools/make_meshes.py"
    assert len(header) <= 80, name
    records = np.zeros(
        len(triangles),
        dtype=[("normal", "<f4", 3), ("corners", "<f4", (3, 3)), ("attribute", "<u2")],
    )
    records["normal"] = np.round(normals, DECIMALS)
    records["corners"] = corners
    return header.encode("ascii").ljust(80) + struct.pack("<I", len(triangles)) + records.tobytes()


def files() -> dict[str, bytes]:
    """Every mesh file's content by its name."""
    return {name: stl(name, triangles) for name, triangles in shapes().items()}


def main() -> None:
    MESHES.mkdir(parents=True, exist_ok=True)
    total = 0
    for name, content in files().items():
        (MESHES / name).write_bytes(content)
        total += len(content)
        print(f"{(MESHES / name).relative_to(ROOT)}: {(len(content) - 84) // 50} triangles")
    print(f"{total / 1024:.0f} kB in all.")


if __name__ == "__main__":
    main()
