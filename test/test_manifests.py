# SPDX-License-Identifier: Apache-2.0
"""Every ROS package declares what its Python imports.

The base image ships half of ROS, so an import that no manifest names works
anyway, until the day the base image changes. This reads every import of
every package and looks for it in that package's own package.xml.
"""

import ast
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = sorted(path.parent for path in (ROOT / "ros").glob("*/package.xml"))

# Where the module a line imports is not named like the rosdep key that
# brings it. Everything else is a ROS package imported under its own name.
KEY_OF_MODULE = {
    "aiohttp": "python3-aiohttp",
    "cv2": "python3-opencv",
    "numpy": "python3-numpy",
    "pytest": "python3-pytest",
    "tf2_ros": "tf2_ros_py",
}

# What a package needs to run, and what it may also use in its tests.
RUN = {"depend", "exec_depend"}
ANY = RUN | {"test_depend", "build_depend", "buildtool_depend", "build_export_depend"}


def declared(package: Path, kinds: set[str]) -> set[str]:
    manifest = ET.parse(package / "package.xml").getroot()
    return {element.text for element in manifest if element.tag in kinds}


def imported(files: list[Path]) -> dict[str, Path]:
    """Top-level modules the files import, each with one file that does."""
    modules = {}
    for path in files:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module]
            else:
                continue  # a relative import stays inside the package
            for name in names:
                modules.setdefault(name.split(".")[0], path)
    return modules


def missing(package: Path, files: list[Path], kinds: set[str]) -> list[str]:
    known = declared(package, kinds)
    # Its own modules, and the helpers that lie beside its tests.
    own = {package.name} | {path.stem for path in package.rglob("*.py")}
    return [
        f"{path.relative_to(ROOT)} imports {module}: no {key} in package.xml"
        for module, path in sorted(imported(files).items())
        if module not in sys.stdlib_module_names
        and module not in own
        and (key := KEY_OF_MODULE.get(module, module)) not in known
    ]


@pytest.mark.parametrize("package", PACKAGES, ids=lambda package: package.name)
def test_what_a_package_runs_on_is_declared(package):
    code = [*(package / package.name).rglob("*.py"), *(package / "launch").glob("*.py")]
    assert missing(package, code, RUN) == []


@pytest.mark.parametrize("package", PACKAGES, ids=lambda package: package.name)
def test_what_a_packages_tests_use_is_declared(package):
    assert missing(package, list((package / "test").rglob("*.py")), ANY) == []


def test_all_four_packages_are_read():
    assert [package.name for package in PACKAGES] == [
        "fleetless_turtle_bringup",
        "fleetless_turtle_msgs",
        "fleetless_turtle_sim",
        "fleetless_turtle_viewer",
    ]
    # And the reading finds what is there: the simulator's node imports ROS.
    simulator = ROOT / "ros" / "fleetless_turtle_sim" / "fleetless_turtle_sim"
    assert {"rclpy", "tf2_ros", "numpy"} <= set(imported(list(simulator.rglob("*.py"))))
