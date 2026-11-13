# SPDX-License-Identifier: Apache-2.0
from setuptools import setup

package_name = "fleetless_turtle_bringup"

setup(
    name=package_name,
    version="0.0.0",
    # No Python modules: this package only installs the launch file and the
    # Fast DDS profile it gives the simulator.
    packages=[],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", ["launch/turtle.launch.py"]),
        ("share/" + package_name + "/config", ["config/simulator.fastdds.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    license="Apache-2.0",
    tests_require=["pytest"],
)
