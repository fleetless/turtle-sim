# SPDX-License-Identifier: Apache-2.0
import os

from setuptools import find_packages, setup

package_name = "fleetless_turtle_viewer"


def tree(source: str, target: str) -> list[tuple[str, list[str]]]:
    """Every file below `source`, installed below `target` in the same layout."""
    return [
        (
            os.path.join(target, os.path.relpath(folder, source)),
            [os.path.join(folder, f) for f in files],
        )
        for folder, _, files in os.walk(source)
        if files
    ]


setup(
    name=package_name,
    version="0.0.0",
    packages=find_packages(exclude=["test", "test.*"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        # The page, which the node serves from the installed package.
        *tree("web", "share/" + package_name + "/web"),
    ],
    # The dependencies are in package.xml, which is what rosdep reads.
    install_requires=["setuptools"],
    zip_safe=True,
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={"console_scripts": ["viewer = fleetless_turtle_viewer.node:main"]},
)
