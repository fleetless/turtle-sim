# SPDX-License-Identifier: Apache-2.0
"""docker-compose.yaml against the container it starts.

It promises the same container as the README's one `docker run` line, and
every variable that container reads, each with its default. This file holds
both without Docker: the service and nothing beside it, and one list of
variables in four places, so a new one cannot be forgotten in any of them.
docker/compose-test.sh brings the file up.
"""

import ast
import re
import shlex
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "docker-compose.yaml"
EXAMPLE = ROOT / ".env.example"
README = ROOT / "README.md"
DOCKERFILE = ROOT / "Dockerfile"
LAUNCH_FILES = sorted((ROOT / "ros").glob("*/launch/*.py"))

# What a user may set is named like this, and nothing else of ours is.
VARIABLE = r"(?:FLEETLESS|TURTLE)_[A-Z0-9_]+"
# Written with a default, so nobody has to edit the file: ${NAME:-default}.
WITH_DEFAULT = re.compile(r"\$\{(" + VARIABLE + r"):-([^}]*)\}")


def service() -> dict:
    return yaml.safe_load(COMPOSE.read_text())["services"]["turtle"]


def scripts_in_the_image() -> list[Path]:
    """The files of docker/ that the Dockerfile copies into the image."""
    copied = re.findall(r"^COPY (.+) \S+$", DOCKERFILE.read_text(), re.MULTILINE)
    return sorted(
        {
            ROOT / source
            for line in copied
            for source in line.split()
            if source.startswith("docker/")
        }
    )


def names_in_python(path: Path) -> set[str]:
    """Every name a Python file spells out as a text of its own."""
    return {
        node.value
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and re.fullmatch(VARIABLE, node.value)
    }


def names_in_shell(path: Path) -> set[str]:
    """Every name a shell file expands and does not set itself."""
    text = path.read_text()
    expanded = set(re.findall(r"\$\{?(" + VARIABLE + r")", text))
    its_own = set(re.findall(r"^\s*(" + VARIABLE + r")=", text, re.MULTILINE))
    return expanded - its_own


def read_by_the_container() -> set[str]:
    """The variables the container reads from whoever starts it.

    From the code, not from a list: the image's own lines, the scripts it
    starts with and the launch file. FLEETLESS_CLOUD_URL is the bridge's to
    read; it is here because the entrypoint looks at it first.
    """
    names = names_in_shell(DOCKERFILE)
    for path in [*scripts_in_the_image(), *LAUNCH_FILES]:
        names |= names_in_python(path) if path.suffix == ".py" else names_in_shell(path)
    return names


def defaults_in_the_launch_file() -> dict[str, str]:
    """Variable and default, wherever a launch file gives the two to a call."""
    defaults = {}
    for path in LAUNCH_FILES:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and len(node.args) == 2:
                name, default = node.args
                if (
                    isinstance(name, ast.Constant)
                    and isinstance(default, ast.Constant)
                    and isinstance(name.value, str)
                    and re.fullmatch(VARIABLE, name.value)
                ):
                    defaults[name.value] = default.value
    return defaults


def defaults_in_the_compose_file() -> dict[str, str]:
    defaults = {}
    for name, value in service()["environment"].items():
        written = WITH_DEFAULT.fullmatch(value)
        assert written and written[1] == name, f"{name}: {value} is not ${{{name}:-default}}"
        defaults[name] = written[2]
    return defaults


def test_the_reading_finds_what_the_container_reads():
    assert [path.name for path in scripts_in_the_image()] == [
        "entrypoint.sh",
        "environment.sh",
        "launcher.py",
        "ros2",
        "start.sh",
    ]
    assert len(LAUNCH_FILES) == 1
    # The entrypoint's one and the launch file's four.
    assert read_by_the_container() == {
        "FLEETLESS_TOKEN",
        "FLEETLESS_CLOUD_URL",
        "TURTLE_MAP",
        "TURTLE_BATTERY_DRAIN",
        "TURTLE_VIEW_PORT",
    }


def test_the_file_is_one_service_and_nothing_else():
    """No build section, no second service, no host networking, no restart policy."""
    document = yaml.safe_load(COMPOSE.read_text())
    assert set(document) == {"services", "volumes"}
    assert set(document["services"]) == {"turtle"}
    assert set(service()) == {"image", "container_name", "environment", "ports", "volumes"}
    assert document["volumes"] == {"turtle-state": None}


def test_it_is_the_container_of_the_readmes_one_line():
    line = next(line for line in README.read_text().splitlines() if line.startswith("docker run "))
    words = shlex.split(line)
    option = {flag: words[words.index(flag) + 1] for flag in ("--name", "-p", "-v")}
    defaults = defaults_in_the_compose_file()

    assert service()["container_name"] == option["--name"] == "turtle"
    assert service()["image"] == words[-1] + ":latest" == "ghcr.io/fleetless/turtle-sim:latest"
    assert service()["volumes"] == [option["-v"]] == ["turtle-state:/state"]
    # The view on this machine only, and the port follows the variable on
    # both sides.
    (ports,) = service()["ports"]
    assert ports == "127.0.0.1:${TURTLE_VIEW_PORT:-8765}:${TURTLE_VIEW_PORT:-8765}"
    assert WITH_DEFAULT.sub(lambda written: defaults[written[1]], ports) == option["-p"]


def test_every_variable_stands_in_the_compose_file_with_its_default():
    defaults = defaults_in_the_compose_file()
    assert set(defaults) == read_by_the_container()
    # The token and the cloud have no default to write down: passed empty,
    # which the container reads as unset.
    assert defaults["FLEETLESS_TOKEN"] == "" and defaults["FLEETLESS_CLOUD_URL"] == ""
    # Every other default is the one the launch file would have taken
    # without the variable, and the launch file has no default beside them.
    others = {name: default for name, default in defaults.items() if default}
    assert others == defaults_in_the_launch_file()
    assert len(others) == len(defaults) - 2


def test_every_variable_has_its_one_line_comment():
    lines = COMPOSE.read_text().splitlines()
    for name in read_by_the_container():
        (at,) = (index for index, line in enumerate(lines) if line.startswith(f"      {name}: "))
        assert lines[at - 1].startswith("      # "), name
        assert not lines[at - 2].lstrip().startswith("#"), f"{name}: more than one line"


def test_the_commented_line_mounts_a_map_where_turtle_map_names_it():
    text = COMPOSE.read_text()
    (mount,) = re.findall(r"^      # - (\S+\.png:(\S+\.png):ro)$", text, re.MULTILINE)
    assert f"TURTLE_MAP={mount[1]}" in text
    # As in the README's own line for a painted map.
    assert f"-v {mount[0]} -e TURTLE_MAP={mount[1]} " in README.read_text()
    # Uncommented, it is a second volume and the file still reads.
    with_the_map = yaml.safe_load(text.replace(f"# - {mount[0]}", f"- {mount[0]}"))
    assert with_the_map["services"]["turtle"]["volumes"] == ["turtle-state:/state", mount[0]]


def test_env_example_names_the_same_variables_with_their_defaults():
    lines = EXAMPLE.read_text().splitlines()
    live = dict(re.findall(r"^(" + VARIABLE + r")=(.*)$", "\n".join(lines), re.MULTILINE))
    commented = dict(re.findall(r"^# (" + VARIABLE + r")=(.*)$", "\n".join(lines), re.MULTILINE))
    # Ready to take the token, and everything else at its default until a
    # line is uncommented.
    assert live == {"FLEETLESS_TOKEN": ""}
    assert {**live, **commented} == defaults_in_the_compose_file()


def test_the_readmes_table_names_the_same_variables_with_their_defaults():
    rows = dict(
        re.findall(
            r"^\| `(" + VARIABLE + r")` \| (.+?) \| .+ \|$", README.read_text(), re.MULTILINE
        )
    )
    assert set(rows) == read_by_the_container()
    for name, default in defaults_in_the_compose_file().items():
        if default:
            assert rows[name] == f"`{default}`", name


def test_a_token_in_env_reaches_neither_the_repository_nor_a_build():
    for name in (".gitignore", ".dockerignore"):
        lines = (ROOT / name).read_text().splitlines()
        assert ".env" in lines, name
        # And nothing takes it back, or hides the template.
        assert not [line for line in lines if line.startswith("!")], name
        assert ".env.example" not in lines and ".env*" not in lines, name
