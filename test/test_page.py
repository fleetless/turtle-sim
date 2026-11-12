# SPDX-License-Identifier: Apache-2.0
"""The view's page, as far as it can be held to its word without a browser:
it loads nothing from outside, it never sends, its few numbers are the
core's, and the vendored three.js is the release NOTICE names."""

import hashlib
import re
from pathlib import Path

import pytest

from fleetless_turtle_cameras.render import constants as look
from fleetless_turtle_sim.core import constants as c

ROOT = Path(__file__).resolve().parent.parent
VIEWER = ROOT / "ros" / "fleetless_turtle_viewer"
WEB = VIEWER / "web"
VENDOR = WEB / "vendor"

OWN_FILES = sorted(path for path in WEB.iterdir() if path.is_file())

# three@0.186.1 from the npm registry, unmodified.
VENDORED = {
    "three.module.js": "9052042d676cb0fdc1ddfefe193053f34b7ac0513a616fdac4535d49987812ea",
    "three.core.js": "9edde002b066a9a05676a6127f67735b62baf399bdea529f2f7e31657da769e6",
    "OrbitControls.js": "3d79d07ecb686b4e5d93232eedab255331c1beef711e13164eaa1f68655a5f2b",
    "LICENSE": "8b378ebe60e2fe500158cb0ac71cb5e8b7d92953c2abcc63a0eb90499653b5bc",
}


def number(path: Path, name: str) -> float:
    """The value of `const NAME = <number>;` in a script."""
    found = re.search(rf"^const {name} = ([0-9.]+);", path.read_text(), re.MULTILINE)
    assert found, f"{path.name} has no constant {name}"
    return float(found[1])


def test_the_page_has_the_files_it_names():
    assert [path.name for path in OWN_FILES] == [
        "connection.js",
        "index.html",
        "main.js",
        "panel.js",
        "scan.js",
        "scene.js",
        "style.css",
        "turtle.js",
        "world.js",
    ]
    html = (WEB / "index.html").read_text()
    for reference in re.findall(r'(?:src|href)="(\./[^"]+)"', html):
        if reference != "./fleetless.yaml":  # a route of the node, not a file
            assert (WEB / reference).is_file(), reference


@pytest.mark.parametrize("path", OWN_FILES, ids=lambda path: path.name)
def test_nothing_is_loaded_from_outside_the_container(path):
    text = path.read_text()
    # The only addresses in the page are XML namespaces, which nothing fetches.
    addresses = set(re.findall(r"""https?://[^\s"'<>)]+""", text))
    assert addresses <= {"http://www.w3.org/2000/svg"}, path.name
    assert "//cdn" not in text and "@import" not in text
    for specifier in re.findall(r"""from\s+["']([^"']+)["']""", text):
        assert specifier == "three" or specifier.startswith("./"), specifier


def test_the_bare_name_three_resolves_to_the_vendored_file():
    html = (WEB / "index.html").read_text()
    assert '{ "imports": { "three": "./vendor/three.module.js" } }' in html
    assert "default-src 'self'" in html


@pytest.mark.parametrize("path", OWN_FILES, ids=lambda path: path.name)
def test_the_page_never_sends_on_the_websocket(path):
    code = re.sub(r"//.*", "", path.read_text())  # comments may speak of send()
    assert ".send(" not in code
    assert "XMLHttpRequest" not in code and "fetch(" not in code and "sendBeacon" not in code


@pytest.mark.parametrize("path", OWN_FILES, ids=lambda path: path.name)
def test_every_file_of_ours_says_its_license(path):
    assert "SPDX-License-Identifier: Apache-2.0" in "\n".join(path.read_text().splitlines()[:3])


def test_the_pages_numbers_are_the_cores():
    assert number(WEB / "turtle.js", "FOOTPRINT_RADIUS_M") == c.TURTLE_RADIUS_M
    assert number(WEB / "world.js", "SQUARE_CELLS") == c.SQUARE_CELLS
    kinds = re.search(
        r"^export const KIND = \{(.+)\};", (WEB / "world.js").read_text(), re.MULTILINE
    )
    assert dict(re.findall(r"(\w+): (\d)", kinds[1])) == {
        "FREE": str(c.FREE),
        "WALL": str(c.WALL),
        "ROCK": str(c.ROCK),
        "WATER": str(c.WATER),
        "PAD": str(c.PAD),
    }


def test_the_page_goes_quiet_when_healthz_does():
    node = (VIEWER / "fleetless_turtle_viewer" / "node.py").read_text()
    healthy_within_s = float(re.search(r"^HEALTHY_WITHIN_S = ([0-9.]+)$", node, re.MULTILINE)[1])
    assert number(WEB / "main.js", "QUIET_AFTER_MS") == healthy_within_s * 1000 == 2000


@pytest.mark.parametrize(("name", "sha256"), sorted(VENDORED.items()))
def test_the_vendored_three_js_is_untouched(name, sha256):
    assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == sha256


def test_notice_names_what_is_vendored():
    notice = (ROOT / "NOTICE").read_text()
    assert "three.js 0.186.1" in notice and "MIT License" in notice
    assert sorted(path.name for path in VENDOR.iterdir()) == sorted(VENDORED)
    for name in VENDORED:
        assert f"ros/fleetless_turtle_viewer/web/vendor/{name}" in notice


def test_the_view_draws_a_wall_and_a_rock_as_tall_as_the_eye_camera_does():
    """One world, one look: a rock is no taller on the page than in the turtle's own picture."""
    world = WEB / "world.js"
    assert number(world, "WALL_HEIGHT_M") == look.WALL_HEIGHT_M
    assert number(world, "ROCK_HEIGHT_M") == look.ROCK_HEIGHT_M
    # And the README's sentence about heights says both numbers.
    readme = " ".join((ROOT / "README.md").read_text().split())
    assert (
        f"A wall stands {look.WALL_HEIGHT_M} m tall and a rock {look.ROCK_HEIGHT_M} m, "
        "in `eye` as in the view"
    ) in readme
