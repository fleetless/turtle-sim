# SPDX-License-Identifier: Apache-2.0
"""The model as it is installed: ROS's own parser accepts the URDF, and
every `package://` in it leads to a file.

What the meshes are and whether the turtle is real size is held without
ROS, by test/test_model.py at the repository's root.
"""

import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from ament_index_python.packages import get_package_share_directory

from fleetless_turtle_sim.core import joints

SHARE = Path(get_package_share_directory("fleetless_turtle_description"))
URDF = SHARE / "urdf" / "turtle.urdf"


def test_check_urdf_accepts_the_file():
    done = subprocess.run(["check_urdf", str(URDF)], capture_output=True, text=True, timeout=60)
    said = done.stdout + done.stderr
    assert done.returncode == 0, said
    assert "robot name is: turtle" in said
    assert "Successfully Parsed XML" in said
    # The tree it read: the shell, and five children that hang on it.
    assert "root Link: base_link has 5 child(ren)" in said
    children = set(re.findall(r"child\(\d+\):\s+(\w+)", said))
    assert children == {name.removesuffix("_joint") for name in joints.NAMES}, said


def test_every_package_reference_resolves_to_an_installed_file():
    references = [mesh.get("filename") for mesh in ET.parse(URDF).getroot().iter("mesh")]
    assert len(references) == 11
    for reference in references:
        scheme, _, rest = reference.partition("://")
        package, _, path = rest.partition("/")
        assert scheme == "package", reference
        # As the bridge resolves it: the package's share directory, then the path.
        found = Path(get_package_share_directory(package)) / path
        assert found.is_file() and found.stat().st_size > 84, reference


def test_the_installed_model_is_the_one_in_the_source():
    source = Path(__file__).resolve().parent.parent
    assert URDF.read_bytes() == (source / "urdf" / "turtle.urdf").read_bytes()
    installed = sorted(path.name for path in (SHARE / "meshes").iterdir())
    assert installed == sorted(path.name for path in (source / "meshes").iterdir())
    for name in installed:
        assert (SHARE / "meshes" / name).read_bytes() == (source / "meshes" / name).read_bytes()
