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
LAUNCH_FILES = sorted((ROOT / "ros").glob("*/launch/*.py"))

# What a user may set is named like this, and nothing else of ours is.
VARIABLE = r"(?:FLEETLESS|TURTLE)_[A-Z0-9_]+"
# How Python asks the environment for a variable: by one of these names, or
# by one of `os.environ`'s own. And what the name's argument is called.
ASKS = {"getenv", "EnvironmentVariable"}
ASKS_OF_ENVIRON = {"get", "pop", "setdefault"}
NAMED = {"name", "key"}
# What may stand before `NAME=` where a shell file or a Dockerfile sets it.
SETS = "(?:export|local|readonly|declare|ENV|ARG)"
# Written with a default, so nobody has to edit the file: ${NAME:-default}.
WITH_DEFAULT = re.compile(r"\$\{(" + VARIABLE + r"):-([^}]*)\}")


def service() -> dict:
    return yaml.safe_load(COMPOSE.read_text())["services"]["turtle"]


def copied_into_the_image(root: Path) -> set[Path]:
    """Every file the Dockerfile copies out of the checkout, wherever it lies.

    A directory stands for the files below it. `COPY --from` takes from
    another stage and not from the checkout.
    """
    files = set()
    for line in re.findall(r"^COPY (.+)$", (root / "Dockerfile").read_text(), re.MULTILINE):
        words = line.split()
        if any(word.startswith("--from") for word in words):
            continue
        for source in (root / word for word in words[:-1] if not word.startswith("--")):
            files |= {path for path in source.rglob("*") if path.is_file()} or {source}
    return files


def is_a_test(path: Path) -> bool:
    """Below a test directory, or named as pytest names its own files."""
    return "test" in path.parts or path.name.startswith("test_") or path.name == "conftest.py"


def language(path: Path) -> str | None:
    """`python` or `shell` for a script, by its name or its first line. Else nothing."""
    if path.suffix in {".py", ".sh", ".bash"}:
        return "python" if path.suffix == ".py" else "shell"
    with path.open("rb") as file:
        first = file.readline()
    if not first.startswith(b"#!"):
        return None
    return "python" if b"python" in first else "shell" if re.search(rb"\b\w*sh\b", first) else None


def scripts_of_the_container(root: Path = ROOT) -> dict[Path, str]:
    """Every script that runs in the container, with its language.

    What the Dockerfile copies into the image, from wherever it comes, and
    the Python of every package: the nodes as much as the launch file. Not
    the tests, which run beside the image and not in it.
    """
    candidates = copied_into_the_image(root) | set((root / "ros").glob("*/**/*.py"))
    return {
        path: language(path)
        for path in sorted(candidates)
        if path.is_file() and not is_a_test(path.relative_to(root)) and language(path)
    }


def is_the_environment(node: ast.AST) -> bool:
    """`os.environ`, or `environ` by itself."""
    if isinstance(node, ast.Attribute):
        return node.attr == "environ"
    return isinstance(node, ast.Name) and node.id == "environ"


def asked_of_the_environment(tree: ast.AST) -> list[ast.expr]:
    """What a piece of Python hands to the environment as the name of a variable.

    `os.getenv(name)` and launch's `EnvironmentVariable(name)`, by whatever
    path they are called; `os.environ.get(name)`, `os.environ[name]` when it
    is read, and `name in os.environ`.
    """
    asked = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            by_name = isinstance(node.func, ast.Name) and node.func.id in ASKS
            by_path = isinstance(node.func, ast.Attribute) and (
                node.func.attr in ASKS
                or (node.func.attr in ASKS_OF_ENVIRON and is_the_environment(node.func.value))
            )
            if by_name or by_path:
                named = [keyword.value for keyword in node.keywords if keyword.arg in NAMED]
                asked += (node.args or named)[:1]
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
            asked += [node.slice] if is_the_environment(node.value) else []
        elif isinstance(node, ast.Compare) and any(map(is_the_environment, node.comparators)):
            asked.append(node.left)
    return asked


def handed_to(tree: ast.AST, function: str, argument: str, position: int) -> list[ast.expr]:
    """What the calls of a function hand it for one argument, by position or by name."""
    handed = []
    for call in ast.walk(tree):
        if isinstance(call, ast.Call) and function in (
            getattr(call.func, "id", None),
            getattr(call.func, "attr", None),
        ):
            by_name = [keyword.value for keyword in call.keywords if keyword.arg == argument]
            handed += call.args[position : position + 1] or by_name
    return handed


def names_in_python(path: Path) -> set[str]:
    """Every variable a Python file reads from the environment.

    By what the code does and not by what it spells: a text that only looks
    like a variable's name is none. The name may be the text itself, a
    constant of the module, or what a call hands to a function of this file
    that asks the environment for its own argument, as the launch file's
    `from_environment("NAME", default)` does.
    """
    tree = ast.parse(path.read_text())
    constants = {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    def text(node: ast.expr) -> object:
        if isinstance(node, ast.Name):
            return constants.get(node.id)
        return node.value if isinstance(node, ast.Constant) else None

    names = {text(node) for node in asked_of_the_environment(tree)}
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        arguments = [each.arg for each in function.args.args if each.arg not in ("self", "cls")]
        for asked in asked_of_the_environment(function):
            if isinstance(asked, ast.Name) and asked.id in arguments:
                position = arguments.index(asked.id)
                names |= {text(node) for node in handed_to(tree, function.name, asked.id, position)}
    return {name for name in names if isinstance(name, str) and re.fullmatch(VARIABLE, name)}


def names_in_shell(path: Path) -> set[str]:
    """Every variable a shell file takes from whoever started it.

    That is a name it expands before a line of its own has set it.
    `NAME=${NAME:-default}` asks first and sets after, so it counts; a name
    the file sets and only then expands is its own. A comment expands
    nothing. A Dockerfile reads the same way.
    """
    lines = path.read_text().splitlines()
    text = "\n".join("" if line.lstrip().startswith("#") else line for line in lines)
    names = set()
    for expansion in re.finditer(r"\$\{?(" + VARIABLE + r")", text):
        above = text[: text.rfind("\n", 0, expansion.start()) + 1]
        if not re.search(rf"^\s*(?:{SETS}\s+)?{expansion[1]}=", above, re.MULTILINE):
            names.add(expansion[1])
    return names


def read_by_the_container(root: Path = ROOT) -> set[str]:
    """The variables the container reads from whoever starts it.

    From the code, not from a list: the Dockerfile's own lines and every
    script of the container. FLEETLESS_CLOUD_URL is the bridge's to read; it
    is here because the entrypoint looks at it first.
    """
    names = names_in_shell(root / "Dockerfile")
    for path, written_in in scripts_of_the_container(root).items():
        names |= names_in_python(path) if written_in == "python" else names_in_shell(path)
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
    read = {
        str(path.relative_to(ROOT)): language
        for path, language in scripts_of_the_container().items()
    }
    # The image's scripts, with and without a suffix, the launch file, a node.
    assert {
        "docker/entrypoint.sh": "shell",
        "docker/environment.sh": "shell",
        "docker/start.sh": "shell",
        "docker/ros2": "shell",
        "docker/launcher.py": "python",
        "ros/fleetless_turtle_bringup/launch/turtle.launch.py": "python",
        "ros/fleetless_turtle_sim/fleetless_turtle_sim/node.py": "python",
    }.items() <= read.items()
    # No test, and nothing that only lies beside the image.
    assert not [name for name in read if "/test/" in name or name.endswith("-test.sh")]
    assert len(LAUNCH_FILES) == 1
    # The entrypoint's one and the launch file's four.
    assert read_by_the_container() == {
        "FLEETLESS_TOKEN",
        "FLEETLESS_CLOUD_URL",
        "TURTLE_MAP",
        "TURTLE_BATTERY_DRAIN",
        "TURTLE_VIEW_PORT",
    }


def checkout(root: Path, files: dict[str, str]) -> Path:
    """A small checkout for one case: the files it names, and nothing else."""
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    return root


def test_a_node_is_read_as_the_launch_file_is(tmp_path):
    root = checkout(
        tmp_path,
        {
            "Dockerfile": "FROM ros:jazzy-ros-base\n",
            "ros/a_package/a_package/node.py": 'import os\nos.environ.get("TURTLE_IN_A_NODE")\n',
            "ros/a_package/launch/a.launch.py": 'import os\nos.getenv("TURTLE_IN_A_LAUNCH_FILE")\n',
            "ros/a_package/test/test_node.py": 'import os\nos.environ["TURTLE_IN_A_TEST"]\n',
            "ros/a_package/test/conftest.py": 'import os\nos.getenv("TURTLE_IN_A_CONFTEST")\n',
        },
    )
    assert read_by_the_container(root) == {"TURTLE_IN_A_NODE", "TURTLE_IN_A_LAUNCH_FILE"}


def test_a_shell_variable_that_is_read_before_it_is_set_is_read(tmp_path):
    root = checkout(
        tmp_path,
        {
            "Dockerfile": (
                "COPY docker/entrypoint.sh /opt/turtle/\n"
                "ENV FLEETLESS_SET_BY_THE_IMAGE=/state\n"
                'CMD echo "$FLEETLESS_SET_BY_THE_IMAGE ${TURTLE_IN_THE_DOCKERFILE:-1}"\n'
            ),
            "docker/entrypoint.sh": (
                "TURTLE_WITH_A_DEFAULT=${TURTLE_WITH_A_DEFAULT:-8765}\n"
                "export TURTLE_ITS_OWN=/tmp/own\n"
                'echo "$TURTLE_WITH_A_DEFAULT $TURTLE_ITS_OWN $TURTLE_JUST_READ"\n'
                "# A comment may name $TURTLE_IN_A_COMMENT.\n"
            ),
        },
    )
    assert read_by_the_container(root) == {
        "TURTLE_WITH_A_DEFAULT",
        "TURTLE_JUST_READ",
        "TURTLE_IN_THE_DOCKERFILE",
    }


def test_a_script_is_read_wherever_the_dockerfile_takes_it_from(tmp_path):
    root = checkout(
        tmp_path,
        {
            "Dockerfile": (
                "COPY --chmod=755 tools/hook.sh bin/turtle /usr/local/bin/\n"
                "COPY share /opt/turtle/share\n"
                "COPY --from=build /opt/turtle/install /opt/turtle/install\n"
            ),
            "tools/hook.sh": 'echo "$TURTLE_IN_A_HOOK"\n',
            "tools/beside.sh": 'echo "$TURTLE_NOT_COPIED"\n',
            "bin/turtle": (
                '#!/usr/bin/env python3\nimport os\nos.environ["TURTLE_WITHOUT_A_SUFFIX"]\n'
            ),
            "share/deep/down.sh": 'echo "${TURTLE_IN_A_DIRECTORY}"\n',
            "share/notes.txt": "TURTLE_IN_A_TEXT is $TURTLE_IN_A_TEXT\n",
        },
    )
    assert read_by_the_container(root) == {
        "TURTLE_IN_A_HOOK",
        "TURTLE_WITHOUT_A_SUFFIX",
        "TURTLE_IN_A_DIRECTORY",
    }


def test_python_is_read_by_what_it_asks_the_environment(tmp_path):
    root = checkout(
        tmp_path,
        {
            "Dockerfile": "FROM ros:jazzy-ros-base\n",
            "ros/a_package/a_package/node.py": (
                "import os\n"
                "from os import environ, getenv\n"
                "from launch.substitutions import EnvironmentVariable\n"
                'CONSTANT = "TURTLE_BY_A_CONSTANT"\n'
                "def from_environment(name, default):\n"
                "    return EnvironmentVariable(name, default_value=default)\n"
                "class Node:\n"
                "    def read(self, default, name):\n"
                "        return os.environ.get(name, default)\n"
                "    def all(self):\n"
                '        self.read("1", "TURTLE_BY_A_METHOD")\n'
                '        self.read("1", name="TURTLE_BY_A_KEYWORD")\n'
                'os.environ.get("TURTLE_BY_GET", "1")\n'
                'os.environ["TURTLE_BY_INDEX"]\n'
                'os.getenv("TURTLE_BY_GETENV")\n'
                'getenv(key="TURTLE_BY_KEY")\n'
                '"TURTLE_BY_IN" in environ\n'
                'EnvironmentVariable(name="TURTLE_BY_LAUNCH")\n'
                "os.environ.get(CONSTANT)\n"
                'from_environment("TURTLE_BY_A_FUNCTION", "1")\n'
            ),
        },
    )
    assert read_by_the_container(root) == {
        "TURTLE_BY_GET",
        "TURTLE_BY_INDEX",
        "TURTLE_BY_GETENV",
        "TURTLE_BY_KEY",
        "TURTLE_BY_IN",
        "TURTLE_BY_LAUNCH",
        "TURTLE_BY_A_CONSTANT",
        "TURTLE_BY_A_FUNCTION",
        "TURTLE_BY_A_METHOD",
        "TURTLE_BY_A_KEYWORD",
    }


def test_a_name_that_python_only_spells_is_not_read(tmp_path):
    """What a node or a launch file may hold without this test minding it."""
    spelt = (
        "import os\n"
        'COLOUR = "TURTLE_GREEN"\n'
        'SENTENCES = {"TURTLE_MAP": "the map"}\n'
        'paint("TURTLE_SHELL", colour="TURTLE_GREEN")\n'
        'frames.get("TURTLE_FRAME")\n'
        'os.environ["TURTLE_SET_FOR_A_CHILD"] = "1"\n'
        'os.environ.get("ROS_DOMAIN_ID")\n'
        "TURTLE_RADIUS_M = 0.1\n"
    )
    root = checkout(
        tmp_path,
        {
            "Dockerfile": "FROM ros:jazzy-ros-base\n",
            "ros/a_package/a_package/node.py": spelt,
            "ros/a_package/launch/a.launch.py": spelt,
        },
    )
    assert read_by_the_container(root) == set()


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


def test_the_header_says_where_the_view_is_as_the_variable_has_it():
    header = COMPOSE.read_text().split("services:")[0]
    port = defaults_in_the_compose_file()["TURTLE_VIEW_PORT"]
    # The default's address, and the variable that moves it.
    assert f"http://localhost:{port}," in header and "TURTLE_VIEW_PORT" in header


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
