# SPDX-License-Identifier: Apache-2.0
"""What holds for the repository as a whole, whatever file it is in."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# What a checkout holds that git does not track.
UNTRACKED = {".git", "node_modules", "build", "install", "log", "__pycache__"}
UNTRACKED |= {".pytest_cache", ".ruff_cache"}


def tracked_files():
    """Every file of the checkout that is not build output or a cache."""
    for path in sorted(ROOT.rglob("*")):
        if path.is_file() and not UNTRACKED.intersection(path.relative_to(ROOT).parts):
            yield path


def test_no_file_sends_a_reader_to_the_private_ops_repository():
    """This repository is meant to become public; fleetless/fleetless is not.

    An issue number or a link into it is a dead end for every reader without
    access, so nothing a user reads may carry one. The workflows are the one
    place that may name the organisation's other repositories.
    """
    # Spelt in two halves, or this file would be the first to hold them.
    needles = ["fleetless/" + "fleetless#", "github.com/fleetless/" + "fleetless/"]
    found = []
    for path in tracked_files():
        relative = path.relative_to(ROOT)
        if relative.parts[0] == ".github":
            continue
        text = path.read_bytes().decode("utf-8", errors="ignore")
        found += [f"{relative}: {needle}" for needle in needles if needle in text]
    assert found == []


def test_the_walk_sees_the_files_it_is_meant_to_guard():
    names = {str(path.relative_to(ROOT)) for path in tracked_files()}
    assert {"README.md", "Dockerfile", "fleetless/turtle.fleetless.yaml"} <= names
    assert "ros/fleetless_turtle_viewer/web/index.html" in names
    assert not [name for name in names if "node_modules" in name or name.startswith(".git/")]
