# SPDX-License-Identifier: Apache-2.0
"""What holds for the repository as a whole, whatever file it is in."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# What a checkout holds that git does not track.
UNTRACKED = {".git", "node_modules", "build", "install", "log", "__pycache__"}
UNTRACKED |= {".pytest_cache", ".ruff_cache"}

# The organisation's private ops repository, spelt in two halves, or this
# file would be the first to name it.
OPS = "fleetless/" + "fleetless"
# The name itself, wherever it stands: with an issue number, in a link, in
# plain words. What only begins like it is another name: a repository
# called fleetless-something, or this repository's own packages, whose
# names go on with an underscore. In any case of letters: GitHub reads the
# name with capitals just the same.
NAMES_THE_OPS_REPOSITORY = re.compile(re.escape(OPS) + r"(?![\w-])", re.IGNORECASE)


def tracked_files():
    """Every file of the checkout that is not build output or a cache."""
    for path in sorted(ROOT.rglob("*")):
        if path.is_file() and not UNTRACKED.intersection(path.relative_to(ROOT).parts):
            yield path


def test_no_file_sends_a_reader_to_the_private_ops_repository():
    """This repository is meant to become public; the ops repository is not.

    Its name, an issue number or a link into it is a dead end for every
    reader without access, so nothing a user reads may carry one. The
    workflows are the one place that may name the organisation's other
    repositories.
    """
    found = []
    for path in tracked_files():
        relative = path.relative_to(ROOT)
        if relative.parts[0] == ".github":
            continue
        text = path.read_bytes().decode("utf-8", errors="ignore")
        found += [
            f"{relative}:{text.count(chr(10), 0, match.start()) + 1}: {match.group()}"
            for match in NAMES_THE_OPS_REPOSITORY.finditer(text)
        ]
    assert found == []


@pytest.mark.parametrize(
    "text",
    [
        f"see {OPS}#898",
        f"[an issue](https://github.com/{OPS}/issues/898)",
        f"[the ops repository](https://github.com/{OPS})",
        f"https://github.com/{OPS}?tab=issues",
        f"{OPS} issue 898",
        f"git@github.com:{OPS}.git",
        f"as {OPS} has it.",
        OPS,
        f"see {OPS.title()}#898",
        f"https://github.com/{OPS.upper()}/issues/898",
    ],
)
def test_the_guard_finds_the_name_however_it_is_written(text):
    assert NAMES_THE_OPS_REPOSITORY.search(text)


@pytest.mark.parametrize(
    "text",
    [
        f"https://github.com/{OPS}-bridge",
        f"{OPS}_turtle_msgs/msg/TurtleStatus",
        f"ros/{OPS}_turtle_sim/package.xml",
        "ghcr.io/fleetless/turtle-sim",
        "https://github.com/fleetless/turtle-sim",
        "fleetless/turtle.fleetless.yaml",
        "ros/fleetless_turtle_sim/fleetless_turtle_sim/node.py",
        f"https://github.com/{OPS.title()}-Bridge",
        "GHCR.IO/FLEETLESS/TURTLE-SIM",
    ],
)
def test_the_guard_leaves_other_names_alone(text):
    assert not NAMES_THE_OPS_REPOSITORY.search(text)


def test_the_walk_sees_the_files_it_is_meant_to_guard():
    names = {str(path.relative_to(ROOT)) for path in tracked_files()}
    assert {"README.md", "Dockerfile", "fleetless/turtle.fleetless.yaml"} <= names
    assert "ros/fleetless_turtle_viewer/web/index.html" in names
    assert not [name for name in names if "node_modules" in name or name.startswith(".git/")]
