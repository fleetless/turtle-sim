# SPDX-License-Identifier: Apache-2.0
from setuptools import find_packages, setup

package_name = "fleetless_turtle_sim"

setup(
    name=package_name,
    version="0.0.0",
    packages=find_packages(exclude=["test", "test.*"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    # The dependencies are in package.xml, which is what rosdep reads.
    install_requires=["setuptools"],
    zip_safe=True,
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={"console_scripts": ["simulator = fleetless_turtle_sim.start:main"]},
)
