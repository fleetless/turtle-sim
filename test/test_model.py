# SPDX-License-Identifier: Apache-2.0
"""The turtle's model, without ROS: the committed meshes are what
tools/make_meshes.py makes, the URDF names them and nothing else, its
joints are the ones the simulator moves, and the turtle is real size."""

import importlib.util
import itertools
import math
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

from fleetless_turtle_cameras.render import constants as look
from fleetless_turtle_sim.core import constants as c
from fleetless_turtle_sim.core import joints

ROOT = Path(__file__).resolve().parent.parent
DESCRIPTION = ROOT / "ros" / "fleetless_turtle_description"
MESHES = DESCRIPTION / "meshes"
URDF = DESCRIPTION / "urdf" / "turtle.urdf"
PACKAGE = "package://fleetless_turtle_description/meshes/"

_spec = importlib.util.spec_from_file_location("make_meshes", ROOT / "tools" / "make_meshes.py")
make_meshes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_meshes)

RECORD = np.dtype([("normal", "<f4", 3), ("corners", "<f4", (3, 3)), ("attribute", "<u2")])


def triangles_of(path: Path) -> np.ndarray:
    """A binary STL file's triangles, as [triangle, corner, xyz]."""
    data = path.read_bytes()
    (count,) = struct.unpack("<I", data[80:84])
    return np.frombuffer(data[84:], RECORD, count)["corners"].astype(float)


@pytest.fixture(scope="module")
def robot():
    return ET.parse(URDF).getroot()


def numbers(text: str) -> list[float]:
    return [float(word) for word in text.split()]


# --- The meshes -------------------------------------------------------------


def test_the_committed_meshes_are_what_the_script_makes():
    made = make_meshes.files()
    assert sorted(path.name for path in MESHES.iterdir()) == sorted(made)
    for name, content in made.items():
        assert (MESHES / name).read_bytes() == content, name


def test_the_script_makes_the_same_meshes_twice():
    assert make_meshes.files() == make_meshes.files()


@pytest.mark.parametrize("name", sorted(make_meshes.shapes()))
def test_a_mesh_is_a_binary_stl(name):
    data = (MESHES / name).read_bytes()
    (count,) = struct.unpack("<I", data[80:84])
    assert len(data) == 84 + 50 * count and count >= 100
    # The header carries the license, and does not begin as the text format does.
    assert data[:80].decode("ascii").startswith("SPDX-License-Identifier: Apache-2.0")
    assert not data.lstrip().startswith(b"solid")
    records = np.frombuffer(data[84:], RECORD)
    assert not records["attribute"].any()


@pytest.mark.parametrize("name", sorted(make_meshes.shapes()))
def test_a_mesh_is_closed_and_every_triangle_looks_outwards(name):
    data = (MESHES / name).read_bytes()
    records = np.frombuffer(data[84:], RECORD)
    corners = records["corners"].astype(float)

    # The normal a file states is the one its corners give, counter-clockwise.
    across = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    assert (np.linalg.norm(across, axis=1) > 0).all(), "a triangle without an area"
    across /= np.linalg.norm(across, axis=1, keepdims=True)
    assert np.allclose(records["normal"], across, atol=2e-6)

    # Closed: every edge is walked once in each direction, by two triangles.
    edges = {}
    for triangle in corners:
        points = [tuple(point) for point in triangle]
        for start, end in zip(points, points[1:] + points[:1], strict=True):
            edges[(start, end)] = edges.get((start, end), 0) + 1
    assert set(edges.values()) == {1}
    assert all((end, start) in edges for start, end in edges)

    # Outwards: what the triangles enclose has a volume, and a positive one.
    volume = np.einsum("ij,ij->i", corners[:, 0], np.cross(corners[:, 1], corners[:, 2])).sum() / 6
    assert volume > 1e-7


def test_the_meshes_are_under_one_megabyte_in_all():
    assert sum(path.stat().st_size for path in MESHES.iterdir()) < 1_000_000


# --- The URDF ---------------------------------------------------------------


def test_the_urdf_is_plain_and_names_the_turtle(robot):
    text = URDF.read_text()
    assert robot.tag == "robot" and robot.get("name") == "turtle"
    # No macro language: what stands in the file is what a loader reads.
    assert "xmlns:xacro" not in text and "<xacro:" not in text and "${" not in text
    assert text.splitlines()[1] == "<!-- SPDX-License-Identifier: Apache-2.0 -->"


def test_every_mesh_the_urdf_names_is_there_and_every_mesh_is_named(robot):
    named = [mesh.get("filename") for mesh in robot.iter("mesh")]
    assert all(name.startswith(PACKAGE) for name in named)
    files = {name.removeprefix(PACKAGE) for name in named}
    assert files == {path.name for path in MESHES.iterdir()}
    assert all(name.endswith(".stl") for name in files)


def test_the_meshes_are_coloured_by_materials_and_none_has_a_texture(robot):
    colours = {
        material.get("name"): numbers(material.find("color").get("rgba"))
        for material in robot.findall("material")
    }
    assert all(len(rgba) == 4 and rgba[3] == 1.0 for rgba in colours.values())
    for visual in robot.iter("visual"):
        assert visual.find("material").get("name") in colours
        # A material is named, never defined in passing.
        assert len(visual.find("material")) == 0
    assert not list(robot.iter("texture"))

    # One turtle looks like one: these are the colours the `top` camera draws it in.
    def bytes_of(name: str) -> tuple[int, ...]:
        return tuple(round(channel * 255) for channel in colours[name][:3])

    assert bytes_of("shell") == look.TURTLE_SHELL
    assert bytes_of("shell_top") == look.TURTLE_SHELL_TOP
    assert bytes_of("rim") == look.TURTLE_RIM
    assert bytes_of("skin") == look.TURTLE_SKIN


def test_the_joints_are_the_ones_the_simulator_moves(robot):
    found = {joint.get("name"): joint for joint in robot.findall("joint")}
    assert set(found) == set(joints.NAMES)
    for name, joint in found.items():
        assert joint.get("type") == "revolute", name
        assert joint.find("parent").get("link") == "base_link", name
        assert joint.find("child").get("link") == name.removesuffix("_joint"), name
        limit = joint.find("limit")
        assert float(limit.get("lower")) == -float(limit.get("upper")), name
    links = {link.get("name") for link in robot.findall("link")}
    assert links == {"base_link"} | {name.removesuffix("_joint") for name in joints.NAMES}

    # The head turns as far as the simulator turns it, a leg at least as far as it swings.
    assert float(found[joints.HEAD].find("limit").get("upper")) == pytest.approx(
        c.HEAD_TURN_LIMIT, abs=1e-4
    )
    for name in joints.NAMES[1:]:
        assert float(found[name].find("limit").get("upper")) >= c.LEG_SWING, name


def test_the_collision_shape_is_the_footprint(robot):
    shapes = list(robot.iter("collision"))
    assert len(shapes) == 1
    assert float(shapes[0].find("geometry/cylinder").get("radius")) == c.TURTLE_RADIUS_M
    assert not list(shapes[0].iter("mesh"))


# --- The turtle the URDF builds ---------------------------------------------


def rotation(roll: float, pitch: float, yaw: float) -> np.ndarray:
    """URDF's `rpy`: a turn about x, then y, then z, of the fixed axes."""
    cr, sr, cp, sp = math.cos(roll), math.sin(roll), math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return np.array(
        [
            [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
            [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
            [-sp, cp * sr, cp * cr],
        ]
    )


def about(axis: np.ndarray, angle: float) -> np.ndarray:
    """A turn by `angle` about an axis."""
    x, y, z = axis / np.linalg.norm(axis)
    cross = np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])
    return np.eye(3) + math.sin(angle) * cross + (1.0 - math.cos(angle)) * cross @ cross


def placed(element) -> tuple[np.ndarray, np.ndarray]:
    """The rotation and the offset of an element's `<origin>`; none is no move."""
    origin = element.find("origin")
    if origin is None:
        return np.eye(3), np.zeros(3)
    return (
        rotation(*numbers(origin.get("rpy", "0 0 0"))),
        np.array(numbers(origin.get("xyz", "0 0 0"))),
    )


def points_of(robot, angles: dict[str, float]) -> dict[str, np.ndarray]:
    """Every link's mesh points in `base_link`, with the joints at `angles`."""
    frames = {"base_link": (np.eye(3), np.zeros(3))}
    for joint in robot.findall("joint"):
        turn, offset = placed(joint)
        swing = about(np.array(numbers(joint.find("axis").get("xyz"))), angles[joint.get("name")])
        frames[joint.find("child").get("link")] = (turn @ swing, offset)
    points = {}
    for link in robot.findall("link"):
        turn, offset = frames[link.get("name")]
        parts = []
        for visual in link.findall("visual"):
            mesh = visual.find("geometry/mesh")
            corners = triangles_of(MESHES / mesh.get("filename").removeprefix(PACKAGE))
            corners = corners.reshape(-1, 3) * numbers(mesh.get("scale", "1 1 1"))
            visual_turn, visual_offset = placed(visual)
            parts.append((corners @ visual_turn.T + visual_offset) @ turn.T + offset)
        points[link.get("name")] = np.vstack(parts)
    return points


STRAIGHT = dict.fromkeys(joints.NAMES, 0.0)


def limits_of(robot) -> dict[str, float]:
    return {
        joint.get("name"): float(joint.find("limit").get("upper"))
        for joint in robot.findall("joint")
    }


def test_the_turtle_is_real_size_however_its_joints_stand(robot):
    """Nothing reaches past the simulator's footprint or below the floor.

    What touches a wall in the model is then what touches it in the simulation.
    """
    limits = limits_of(robot)
    for signs in itertools.product((-1.0, 0.0, 1.0), repeat=len(joints.NAMES)):
        angles = {name: sign * limits[name] for name, sign in zip(joints.NAMES, signs, strict=True)}
        for link, points in points_of(robot, angles).items():
            reach = np.hypot(points[:, 0], points[:, 1]).max()
            assert reach <= c.TURTLE_RADIUS_M + 1e-9, (link, angles)
            assert points[:, 2].min() >= 0.0, (link, angles)
    # And it fills the footprint: the head reaches almost to its edge, the shell is most of it.
    points = points_of(robot, STRAIGHT)
    assert points["head"][:, 0].max() > 0.95 * c.TURTLE_RADIUS_M
    assert points["base_link"][:, 1].max() > 0.7 * c.TURTLE_RADIUS_M
    assert 0.05 < points["base_link"][:, 2].max() < c.TURTLE_RADIUS_M


def test_the_turtle_looks_along_x_and_is_the_same_on_both_sides(robot):
    points = points_of(robot, STRAIGHT)
    # The head is in front, the tail behind, and neither leans to a side.
    assert points["head"][:, 0].min() > 0.04
    assert points["base_link"][:, 0].min() < -0.095
    for link in ("head", "base_link"):
        assert points[link][:, 1].max() == pytest.approx(-points[link][:, 1].min(), abs=1e-6)
    # Each left leg is its right leg, mirrored.
    for left, right in (("leg_front_left", "leg_front_right"), ("leg_hind_left", "leg_hind_right")):
        assert points[left][:, 1].min() > 0.0 > points[right][:, 1].max()
        assert points[left][:, 0].mean() == pytest.approx(points[right][:, 0].mean(), abs=1e-4)
        assert points[left][:, 1].mean() == pytest.approx(-points[right][:, 1].mean(), abs=1e-4)
    assert points["leg_front_left"][:, 0].mean() > 0.0 > points["leg_hind_left"][:, 0].mean()


@pytest.mark.parametrize("name", joints.NAMES[1:])
def test_a_positive_angle_swings_a_leg_forwards(robot, name):
    link = name.removesuffix("_joint")
    at_rest = points_of(robot, STRAIGHT)[link][:, 0].mean()
    swung = points_of(robot, {**STRAIGHT, name: c.LEG_SWING})[link][:, 0].mean()
    back = points_of(robot, {**STRAIGHT, name: -c.LEG_SWING})[link][:, 0].mean()
    assert swung > at_rest + 0.003 and back < at_rest - 0.003


def test_a_positive_angle_turns_the_head_to_the_left(robot):
    turned = points_of(robot, {**STRAIGHT, joints.HEAD: c.HEAD_TURN_LIMIT})["head"]
    assert turned[:, 1].mean() > 0.005
    other_way = points_of(robot, {**STRAIGHT, joints.HEAD: -c.HEAD_TURN_LIMIT})["head"]
    assert other_way[:, 1].mean() == pytest.approx(-turned[:, 1].mean(), abs=1e-6)


def test_the_legs_stand_on_the_floor(robot):
    points = points_of(robot, STRAIGHT)
    for link in ("leg_front_left", "leg_front_right", "leg_hind_left", "leg_hind_right"):
        assert points[link][:, 2].min() < 0.002, link
