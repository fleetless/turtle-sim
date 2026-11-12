# SPDX-License-Identifier: Apache-2.0
from glob import glob

from setuptools import setup

package_name = "fleetless_turtle_description"

setup(
    name=package_name,
    version="0.0.0",
    # No Python modules: this package only installs the model.
    packages=[],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/urdf", ["urdf/turtle.urdf"]),
        # Where `package://fleetless_turtle_description/meshes/...` leads.
        ("share/" + package_name + "/meshes", sorted(glob("meshes/*.stl"))),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    license="Apache-2.0",
    tests_require=["pytest"],
)
