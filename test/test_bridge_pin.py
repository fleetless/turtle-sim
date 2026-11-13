# SPDX-License-Identifier: Apache-2.0
"""The bridge in the image is one file, pinned twice.

The Dockerfile names the bridge's version and the SHA-256 of its `.deb`,
fetches that file from the bridge's GitHub release, and installs it only if
its sum is the pinned one. Fleetless's apt repository is not asked: it
serves the newest bridge and no other, so a pin on an older one would stop
building the day a newer one is published.

Two things are held here without Docker. What the Dockerfile says, read. And
what its one step does, run: the step is cut out of the Dockerfile and given
to a shell whose `PATH` holds stand-ins and nothing else, so no `apt-get`
and no `rm` of the machine this runs on is ever called.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"

VERSION = "6.5.0-0noble"
RELEASES = "https://github.com/fleetless/bridge/releases/download"
REFUSAL = "The bridge's .deb is not the one this Dockerfile pins: expected {}, got {}.\n"

# What stands in for a command of the image. It writes down what it was
# asked. `curl` then hands over the file the test prepared, or fails as the
# test wants it to; `apt-get` says which bytes it was given to install.
STAND_IN = """#!{python}
import hashlib
import json
import os
import sys
from pathlib import Path

name, arguments = Path(sys.argv[0]).name, sys.argv[1:]
asked = [[name, *arguments]]
if name == "apt-get":
    asked += [["was given", hashlib.sha256(Path(each).read_bytes()).hexdigest()]
              for each in arguments if Path(each).is_file()]
with open(os.environ["STAND_IN_LOG"], "a") as log:
    log.writelines(json.dumps(each) + "\\n" for each in asked)
if name == "curl":
    if int(os.environ["CURL_EXITS"]):
        sys.exit(int(os.environ["CURL_EXITS"]))
    target = Path(arguments[arguments.index("-o") + 1])
    target.write_bytes(Path(os.environ["CURL_HANDS_OVER"]).read_bytes())
"""
STOOD_IN_FOR = ["apt-get", "curl", "rm"]
# The real ones the step may use: they read and write the test's own files.
REAL = ["sha256sum", "mktemp", "cut"]


def instructions(dockerfile: str) -> list[str]:
    """A Dockerfile's instructions, each as the one line Docker makes of it.

    A backslash at the end of a line joins the next one to it. A comment is
    no part of an instruction, between two of its lines either.
    """
    found, begun = [], ""
    for line in dockerfile.splitlines():
        if line.lstrip().startswith("#") or not (begun or line.strip()):
            continue
        begun += line
        if begun.endswith("\\"):
            begun = begun[:-1]
        else:
            found.append(begun)
            begun = ""
    assert not begun, "the Dockerfile ends in the middle of an instruction"
    return found


def the_bridge_step(dockerfile: str) -> str:
    """The shell of the one `RUN` that names the bridge's `.deb`."""
    steps = [each for each in instructions(dockerfile) if "fleetless-bridge_" in each]
    assert len(steps) == 1, steps
    assert steps[0].startswith("RUN "), steps[0]
    return steps[0].removeprefix("RUN ")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_the_step(
    directory: Path,
    *,
    version: str = VERSION,
    pinned: str,
    handed_over: bytes = b"the bridge",
    curl_exits: int = 0,
) -> tuple[subprocess.CompletedProcess, list[list[str]]]:
    """Runs the Dockerfile's bridge step as a build would. Returns it and what it asked for."""
    commands = directory / "commands"
    commands.mkdir()
    for name in STOOD_IN_FOR:
        (commands / name).write_text(STAND_IN.format(python=sys.executable))
        (commands / name).chmod(0o755)
    for name in REAL:
        real = shutil.which(name)
        assert real, f"this test needs {name}"
        (commands / name).symlink_to(real)
    (directory / "tmp").mkdir()
    (directory / "handed-over").write_bytes(handed_over)
    log = directory / "asked"
    log.touch()
    ran = subprocess.run(
        ["/bin/sh", "-c", the_bridge_step(DOCKERFILE.read_text())],
        env={
            "PATH": str(commands),
            "TMPDIR": str(directory / "tmp"),
            # What a build gives the step: the base image's variable and the two arguments.
            "ROS_DISTRO": "jazzy",
            "BRIDGE_VERSION": version,
            "BRIDGE_DEB_SHA256": pinned,
            "STAND_IN_LOG": str(log),
            "CURL_HANDS_OVER": str(directory / "handed-over"),
            "CURL_EXITS": str(curl_exits),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    return ran, [json.loads(line) for line in log.read_text().splitlines()]


def asked_of(command: str, asked: list[list[str]]) -> list[list[str]]:
    return [each[1:] for each in asked if each[0] == command]


# --- The Dockerfile, read ------------------------------------------------------


def test_the_pin_is_two_lines_one_after_the_other():
    """The workflow that moves the pin rewrites exactly these two, by their beginnings."""
    text = DOCKERFILE.read_text()
    pin = re.findall(
        r"^ARG BRIDGE_VERSION=([A-Za-z0-9.+~:-]+)\nARG BRIDGE_DEB_SHA256=([0-9a-f]{64})$",
        text,
        re.MULTILINE,
    )
    assert len(pin) == 1, pin
    assert len(re.findall(r"^ARG BRIDGE_VERSION=", text, re.MULTILINE)) == 1
    assert len(re.findall(r"^ARG BRIDGE_DEB_SHA256=", text, re.MULTILINE)) == 1
    # Before the first stage, so every stage that asks for them gets them.
    assert text.index("ARG BRIDGE_DEB_SHA256=") < text.index("\nFROM ")


def test_the_stage_that_installs_the_bridge_asks_for_both():
    """An argument set before the first `FROM` is empty in a stage that does not name it."""
    stage = instructions(DOCKERFILE.read_text())
    stage = stage[max(at for at, each in enumerate(stage) if each.startswith("FROM ")) :]
    step = next(at for at, each in enumerate(stage) if "fleetless-bridge_" in each)
    assert "ARG BRIDGE_VERSION" in stage[:step]
    assert "ARG BRIDGE_DEB_SHA256" in stage[:step]


def test_the_image_says_which_bridge_it_carries():
    labels = [each for each in instructions(DOCKERFILE.read_text()) if each.startswith("LABEL ")]
    assert len(labels) == 1
    assert 'dev.fleetless.bridge.version="${BRIDGE_VERSION}"' in labels[0]
    assert 'dev.fleetless.bridge.deb.sha256="${BRIDGE_DEB_SHA256}"' in labels[0]


def test_the_build_asks_nothing_of_fleetless_s_apt_repository():
    """No source, no key: an image builds where the repository does not answer."""
    text = DOCKERFILE.read_text()
    assert "registry.fleetless.dev" not in text
    for instruction in instructions(text):
        assert not re.search(r"sources\.list|signed-by|keyrings|\.gpg\b|gnupg", instruction)


def test_a_comment_is_no_part_of_an_instruction_and_a_backslash_joins_two_lines():
    dockerfile = "# first\nFROM a\nRUN one \\\n# between\n && two\n\nLABEL x=1\n"
    assert instructions(dockerfile) == ["FROM a", "RUN one  && two", "LABEL x=1"]


# --- The step, run -------------------------------------------------------------


def test_the_file_of_the_pinned_sum_is_fetched_from_the_release_and_installed(tmp_path):
    bridge = b"the bridge, as released"
    ran, asked = run_the_step(tmp_path, pinned=sha256(bridge), handed_over=bridge)
    assert (ran.returncode, ran.stderr) == (0, ""), ran

    # One download, of the release's own file, and a failed one is an error.
    [download] = asked_of("curl", asked)
    name = f"ros-jazzy-fleetless-bridge_{VERSION}_all.deb"
    assert download[-1] == f"{RELEASES}/v6.5.0/{name}"
    assert "-fsSL" in download
    file = Path(download[download.index("-o") + 1])
    assert file.name == name

    # apt installs that one file, by a path, and nothing of the bridge by name.
    installs = [each for each in asked_of("apt-get", asked) if each[0] == "install"]
    assert installs[-1] == ["install", "-y", str(file)]
    assert file.is_absolute() or str(file).startswith("./")
    assert asked_of("was given", asked) == [[sha256(bridge)]]
    assert not [word for each in installs for word in each if word.startswith("ros-")]
    # apt's lists are there when it resolves what the bridge depends on.
    assert asked.index(["apt-get", "update"]) < asked.index(["apt-get", *installs[-1]])

    # And the layer keeps neither the file nor apt's lists.
    [removal] = asked_of("rm", asked)
    assert removal[0] == "-rf"
    assert str(file.parent) in removal or str(file) in removal
    assert [word for word in removal if word.startswith("/var/lib/apt/lists/")]


@pytest.mark.parametrize("at", [0, 31, 63], ids=["first digit", "a middle digit", "last digit"])
def test_a_file_of_another_sum_ends_the_build_with_one_sentence(tmp_path, at):
    bridge = b"the bridge, as released"
    right = sha256(bridge)
    wrong = right[:at] + ("0" if right[at] != "0" else "1") + right[at + 1 :]
    ran, asked = run_the_step(tmp_path, pinned=wrong, handed_over=bridge)
    assert ran.returncode != 0
    assert ran.stderr == REFUSAL.format(wrong, right)
    # Nothing was installed from it: apt never saw the file.
    assert asked_of("was given", asked) == []
    assert not [each for each in asked_of("apt-get", asked) if any(".deb" in word for word in each)]


def test_another_file_under_the_pinned_name_is_refused(tmp_path):
    """The pin names the bytes. A release whose file was replaced does not get in."""
    pinned = sha256(b"the bridge, as released")
    ran, asked = run_the_step(tmp_path, pinned=pinned, handed_over=b"something else")
    assert ran.returncode != 0
    assert ran.stderr == REFUSAL.format(pinned, sha256(b"something else"))
    assert asked_of("was given", asked) == []


@pytest.mark.parametrize(
    "pinned",
    ["", "0573a954", "not a sum at all", "z" * 64],
    ids=["empty", "too short", "words", "no hex"],
)
def test_a_pin_that_is_no_sum_is_refused_like_a_wrong_one(tmp_path, pinned):
    bridge = b"the bridge, as released"
    ran, asked = run_the_step(tmp_path, pinned=pinned, handed_over=bridge)
    assert ran.returncode != 0
    assert ran.stderr == REFUSAL.format(pinned, sha256(bridge))
    assert asked_of("was given", asked) == []


@pytest.mark.parametrize(
    ("version", "tag"),
    [
        ("6.5.0-0noble", "v6.5.0"),
        ("6.4.0-0noble", "v6.4.0"),
        ("10.0.12-0noble", "v10.0.12"),
        # The pin up to its last hyphen, whatever stands before it.
        ("7.0.0~rc1-2-0noble", "v7.0.0~rc1-2"),
    ],
)
def test_the_release_s_tag_is_the_pin_up_to_its_last_hyphen(tmp_path, version, tag):
    bridge = b"a bridge"
    ran, asked = run_the_step(tmp_path, version=version, pinned=sha256(bridge), handed_over=bridge)
    assert ran.returncode == 0, ran
    [download] = asked_of("curl", asked)
    assert download[-1] == f"{RELEASES}/{tag}/ros-jazzy-fleetless-bridge_{version}_all.deb"


def test_a_download_that_fails_ends_the_build_and_installs_nothing(tmp_path):
    """curl's own error is the sentence; there is no second place to ask."""
    ran, asked = run_the_step(tmp_path, pinned=sha256(b"the bridge"), curl_exits=22)
    assert ran.returncode != 0
    assert len(asked_of("curl", asked)) == 1
    assert asked_of("was given", asked) == []
    assert not [each for each in asked_of("apt-get", asked) if any(".deb" in word for word in each)]


def test_the_stand_ins_are_all_the_step_can_reach():
    """The step is run with these commands and no others: a new one has to be named here."""
    assert not set(STOOD_IN_FOR) & set(REAL)
    assert os.pathsep not in "".join(STOOD_IN_FOR + REAL)
