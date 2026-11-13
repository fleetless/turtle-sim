# SPDX-License-Identifier: Apache-2.0
"""The workflow that moves the bridge's pin, and the two scripts it asks.

`bridge-bump` follows Fleetless's apt repository: a bridge is in the image
once it is promoted, and apt is where a promoted bridge is. For a newer
version it takes the SHA-256 of the `.deb` from the bridge's GitHub release
and holds it against the one apt's index gives. Only when the two agree does
it open the pull request that moves both lines of the pin.

That path runs on GitHub once per promoted bridge, so it is run here: the
scripts with files, and the workflow's own `run:` bodies, cut out of the
YAML and given to bash in the order of the job, with stand-ins for `curl`,
`git` and `gh`. Nothing here asks the network.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "bridge-bump.yml"
APT_VERSION = ROOT / "scripts" / "bridge-apt-version"
DEB_SHA256 = ROOT / "scripts" / "bridge-deb-sha256"

PKG = "ros-jazzy-fleetless-bridge"
RELEASES = "https://github.com/fleetless/bridge/releases/download"
A = "0573a9545b432b3680266c07adc0aa6529f56644d49cf1bd3d41dc98b72734a6"
B = "a452efe99911c75edc7a30991b7c0ca20c7f785e95be0d3b17faf489ee5bd9b9"
C = "c723d14527c709f5742e8f79c203374eaaa375f1a23e5ee8e4d0762c54a50a60"
# What the App's token is in these tests. It must reach no command line.
TOKEN = "ghs_a-token-nobody-may-see"


def deb(version: str, distro: str = "jazzy") -> str:
    return f"ros-{distro}-fleetless-bridge_{version}_all.deb"


def sums(*lines: tuple[str, str]) -> str:
    """A SHA256SUMS file as `sha256sum` writes it: the sum, two blanks, the name."""
    return "".join(f"{sha256}  {name}\n" for sha256, name in lines)


def release_sums(version: str, sha256: str) -> str:
    """What a release of the bridge carries: one line for each of its three files."""
    upstream = version.rsplit("-", 1)[0]
    return sums(
        ("f" * 64, f"ros-humble-fleetless-bridge_{upstream}-0jammy_all.deb"),
        (sha256, deb(version)),
        ("e" * 64, f"ros-lyrical-fleetless-bridge_{upstream}-0resolute_all.deb"),
    )


def stanza(version: str, sha256: str | None, package: str = PKG) -> str:
    """One package of a Packages index, with the fields apt's repository writes."""
    fields = [
        f"Package: {package}",
        f"Version: {version}",
        "Architecture: all",
        "Depends: python3, ros-jazzy-rclpy",
        f"Filename: pool/main/r/{package}/{package}_{version}_all.deb",
        "Size: 269554",
        *([f"SHA256: {sha256}"] if sha256 is not None else []),
        "SHA1: ba41d5788cc246d54831c8c7f37c8fd4250f8daa",
        "MD5sum: 97f9bad2366e5698d50257c7f3d5405e",
        "Description: Fleetless Bridge: connects a ROS 2 robot to the Fleetless cloud.",
        " A second line of it.",
        " .",
        " And one after an empty one, which is a dot and no end of the stanza.",
    ]
    return "\n".join(fields) + "\n"


def index(*stanzas: str) -> str:
    return "\n".join(stanzas)


# --- bridge-deb-sha256, with files -----------------------------------------------


def sha256_of(directory: Path, version: str, sums_file: str, packages_file: str):
    (directory / "SHA256SUMS").write_text(sums_file)
    (directory / "Packages").write_text(packages_file)
    return subprocess.run(
        [str(DEB_SHA256), version, str(directory / "SHA256SUMS"), str(directory / "Packages")],
        capture_output=True,
        text=True,
        timeout=30,
    )


def refused(ran: subprocess.CompletedProcess, directory: Path) -> str:
    """The one sentence of a refusal. It names both files compared, and no sum is printed."""
    assert (ran.returncode, ran.stdout) == (1, ""), ran
    assert ran.stderr.count("\n") == 1, ran.stderr
    assert ran.stderr.startswith("bridge-deb-sha256: "), ran.stderr
    assert str(directory / "SHA256SUMS") in ran.stderr
    assert str(directory / "Packages") in ran.stderr
    return ran.stderr


def test_the_sum_both_sources_agree_on_is_printed(tmp_path):
    ran = sha256_of(
        tmp_path, "6.5.1-0noble", release_sums("6.5.1-0noble", A), stanza("6.5.1-0noble", A)
    )
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, A + "\n", "")


def test_a_line_written_for_a_binary_file_is_the_same_line(tmp_path):
    """`sha256sum -b` puts a star before the name."""
    written = f"{A} *{deb('6.5.1-0noble')}\n"
    ran = sha256_of(tmp_path, "6.5.1-0noble", written, stanza("6.5.1-0noble", A))
    assert (ran.returncode, ran.stdout) == (0, A + "\n"), ran


def test_the_stanza_is_found_among_others(tmp_path):
    packages = index(
        stanza("6.5.1-0noble", B, package="ros-jazzy-something-else"),
        stanza("6.5.0-0noble", C),
        stanza("6.5.1-0noble", A),
    )
    ran = sha256_of(tmp_path, "6.5.1-0noble", release_sums("6.5.1-0noble", A), packages)
    assert (ran.returncode, ran.stdout) == (0, A + "\n"), ran


def test_two_sums_that_differ_are_refused_with_both(tmp_path):
    """That is a finding about how a bridge gets to apt, and worth a red run."""
    ran = sha256_of(
        tmp_path, "6.5.1-0noble", release_sums("6.5.1-0noble", A), stanza("6.5.1-0noble", B)
    )
    sentence = refused(ran, tmp_path)
    assert "disagree" in sentence
    assert A in sentence
    assert B in sentence
    assert deb("6.5.1-0noble") in sentence


def test_a_release_without_the_file_s_line_is_refused(tmp_path):
    only_the_others = sums(("f" * 64, deb("6.5.1-0jammy", "humble")), ("e" * 64, "SOMETHING_ELSE"))
    ran = sha256_of(tmp_path, "6.5.1-0noble", only_the_others, stanza("6.5.1-0noble", A))
    assert f"no line for {deb('6.5.1-0noble')}" in refused(ran, tmp_path)


def test_the_line_of_another_version_is_not_this_one_s(tmp_path):
    ran = sha256_of(
        tmp_path, "6.5.1-0noble", release_sums("6.5.0-0noble", A), stanza("6.5.1-0noble", A)
    )
    assert f"no line for {deb('6.5.1-0noble')}" in refused(ran, tmp_path)


def test_an_empty_sums_file_is_refused(tmp_path):
    ran = sha256_of(tmp_path, "6.5.1-0noble", "", stanza("6.5.1-0noble", A))
    assert f"no line for {deb('6.5.1-0noble')}" in refused(ran, tmp_path)


def test_two_lines_for_the_file_are_refused(tmp_path):
    twice = sums((A, deb("6.5.1-0noble")), (B, deb("6.5.1-0noble")))
    ran = sha256_of(tmp_path, "6.5.1-0noble", twice, stanza("6.5.1-0noble", A))
    assert "2 lines for" in refused(ran, tmp_path)


@pytest.mark.parametrize(
    "malformed",
    [A[:63], A + "0", A.upper(), "z" * 64, "sha256:" + A[:57]],
    ids=["63 digits", "65 digits", "capitals", "no hex", "a prefix"],
)
def test_a_malformed_sum_in_the_release_is_refused(tmp_path, malformed):
    ran = sha256_of(
        tmp_path, "6.5.1-0noble", sums((malformed, deb("6.5.1-0noble"))), stanza("6.5.1-0noble", A)
    )
    sentence = refused(ran, tmp_path)
    assert "no SHA-256" in sentence
    assert f"'{malformed}'" in sentence


def test_a_version_apt_does_not_list_is_refused(tmp_path):
    """Released and not promoted: on GitHub, and not in apt."""
    ran = sha256_of(
        tmp_path, "6.5.1-0noble", release_sums("6.5.1-0noble", B), stanza("6.5.0-0noble", A)
    )
    assert f"lists no {PKG} 6.5.1-0noble" in refused(ran, tmp_path)


def test_another_package_of_that_version_is_not_the_bridge(tmp_path):
    other = stanza("6.5.1-0noble", A, package="ros-jazzy-fleetless-bridge-extras")
    ran = sha256_of(tmp_path, "6.5.1-0noble", release_sums("6.5.1-0noble", A), other)
    assert f"lists no {PKG} 6.5.1-0noble" in refused(ran, tmp_path)


def test_an_empty_index_is_refused(tmp_path):
    ran = sha256_of(tmp_path, "6.5.1-0noble", release_sums("6.5.1-0noble", A), "")
    assert f"lists no {PKG} 6.5.1-0noble" in refused(ran, tmp_path)


def test_a_stanza_listed_twice_is_refused(tmp_path):
    twice = index(stanza("6.5.1-0noble", A), stanza("6.5.1-0noble", A))
    ran = sha256_of(tmp_path, "6.5.1-0noble", release_sums("6.5.1-0noble", A), twice)
    assert "2 times" in refused(ran, tmp_path)


def test_a_stanza_without_the_field_is_refused(tmp_path):
    ran = sha256_of(
        tmp_path, "6.5.1-0noble", release_sums("6.5.1-0noble", A), stanza("6.5.1-0noble", None)
    )
    assert "no SHA256" in refused(ran, tmp_path)


def test_the_field_of_the_next_stanza_is_not_this_one_s(tmp_path):
    """A stanza ends at its empty line: what follows belongs to the next package."""
    packages = index(stanza("6.5.1-0noble", None), stanza("6.5.1-0noble", A, package="another"))
    ran = sha256_of(tmp_path, "6.5.1-0noble", release_sums("6.5.1-0noble", A), packages)
    assert "no SHA256" in refused(ran, tmp_path)


@pytest.mark.parametrize(
    "malformed", [A[:63], A.upper(), "z" * 64], ids=["short", "capitals", "no hex"]
)
def test_a_malformed_sum_in_the_index_is_refused(tmp_path, malformed):
    ran = sha256_of(
        tmp_path, "6.5.1-0noble", release_sums("6.5.1-0noble", A), stanza("6.5.1-0noble", malformed)
    )
    sentence = refused(ran, tmp_path)
    assert "no SHA-256" in sentence
    assert f"'{malformed}'" in sentence


@pytest.mark.parametrize("version", ["", "6.5.1 0noble", "6.5.1;id", "../6.5.1", "$(id)"])
def test_what_is_no_debian_version_is_refused_before_anything_is_read(tmp_path, version):
    """The version becomes part of a URL and of a file's name."""
    ran = subprocess.run(
        [str(DEB_SHA256), version, str(tmp_path / "none"), str(tmp_path / "none")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert (ran.returncode, ran.stdout) == (1, ""), ran
    assert ran.stderr == f"bridge-deb-sha256: '{version}' is not a Debian version.\n"


@pytest.mark.parametrize("arguments", [[], ["6.5.1-0noble", "one-file"], ["a", "b", "c", "d"]])
def test_it_takes_a_version_or_a_version_and_two_files(arguments):
    ran = subprocess.run([str(DEB_SHA256), *arguments], capture_output=True, text=True, timeout=30)
    assert (ran.returncode, ran.stdout) == (2, ""), ran
    assert ran.stderr.startswith("usage: bridge-deb-sha256 <version>")


def test_a_file_that_is_not_there_is_refused(tmp_path):
    (tmp_path / "Packages").write_text(stanza("6.5.1-0noble", A))
    ran = subprocess.run(
        [str(DEB_SHA256), "6.5.1-0noble", str(tmp_path / "SHA256SUMS"), str(tmp_path / "Packages")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert f"could not read {tmp_path / 'SHA256SUMS'}" in refused(ran, tmp_path)


# --- The stand-ins -----------------------------------------------------------------

# Writes down what it was asked. `curl` then answers a URL with the file the
# test put behind it, and fails for any other as curl fails for a 404. `gh`
# answers the one question the workflow asks it. `git` does nothing.
STAND_IN = """#!{python}
import json
import os
import sys
from pathlib import Path

name, arguments = Path(sys.argv[0]).name, sys.argv[1:]
with open(os.environ["STAND_IN_LOG"], "a") as log:
    log.write(json.dumps([name, *arguments]) + "\\n")
if name == "curl":
    served = json.loads(Path(os.environ["CURL_SERVES"]).read_text())
    if arguments[-1] not in served:
        sys.stderr.write("curl: (22) The requested URL returned error: 404\\n")
        sys.exit(22)
    sys.stdout.write(served[arguments[-1]])
if name == "gh" and arguments[:2] == ["pr", "list"]:
    sys.stdout.write(os.environ.get("GH_OPEN_PULL_REQUEST", ""))
"""


def stand_ins(directory: Path, served: dict[str, str]) -> dict[str, str]:
    """A `PATH` with the stand-ins before everything else, and where they write to."""
    commands = directory / "commands"
    commands.mkdir()
    for name in ["curl", "git", "gh"]:
        (commands / name).write_text(STAND_IN.format(python=sys.executable))
        (commands / name).chmod(0o755)
    (directory / "served.json").write_text(json.dumps(served))
    (directory / "asked").touch()
    return {
        "PATH": f"{commands}{os.pathsep}{os.environ['PATH']}",
        "STAND_IN_LOG": str(directory / "asked"),
        "CURL_SERVES": str(directory / "served.json"),
    }


def asked(directory: Path, command: str | None = None) -> list[list[str]]:
    """Every command line a stand-in was called with; of one command, without its name."""
    lines = [json.loads(line) for line in (directory / "asked").read_text().splitlines()]
    return lines if command is None else [line[1:] for line in lines if line[0] == command]


def architecture() -> str:
    return subprocess.run(
        ["dpkg", "--print-architecture"], capture_output=True, text=True, check=True
    ).stdout.strip()


def apt_index_url() -> str:
    return f"https://registry.fleetless.dev/apt/dists/jazzy/main/binary-{architecture()}/Packages"


def sums_url(upstream: str) -> str:
    return f"{RELEASES}/v{upstream}/SHA256SUMS"


# --- The two scripts, fetching ----------------------------------------------------


def fetching(directory: Path, script: Path, arguments: list[str], served: dict[str, str]):
    return subprocess.run(
        [str(script), *arguments],
        env={**os.environ, **stand_ins(directory, served)},
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_the_sum_is_asked_of_the_release_s_sums_and_of_apt_s_index(tmp_path):
    served = {
        sums_url("6.5.1"): release_sums("6.5.1-0noble", A),
        apt_index_url(): stanza("6.5.1-0noble", A),
    }
    ran = fetching(tmp_path, DEB_SHA256, ["6.5.1-0noble"], served)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, A + "\n", "")
    # Two questions, each of them an error when it is not answered, and neither waits for ever.
    urls = [line[-1] for line in asked(tmp_path, "curl")]
    assert urls == [sums_url("6.5.1"), apt_index_url()]
    for line in asked(tmp_path, "curl"):
        assert "-fsSL" in line
        assert "--max-time" in line


def test_the_release_s_tag_is_the_version_up_to_its_last_hyphen(tmp_path):
    served = {
        sums_url("7.0.0~rc1-2"): sums((A, deb("7.0.0~rc1-2-0noble"))),
        apt_index_url(): stanza("7.0.0~rc1-2-0noble", A),
    }
    ran = fetching(tmp_path, DEB_SHA256, ["7.0.0~rc1-2-0noble"], served)
    assert (ran.returncode, ran.stdout) == (0, A + "\n"), ran


def test_both_scripts_read_the_same_index(tmp_path):
    """The version and its sum are two answers of one file."""
    served = {
        sums_url("6.5.1"): release_sums("6.5.1-0noble", A),
        apt_index_url(): stanza("6.5.1-0noble", A),
    }
    (tmp_path / "version").mkdir()
    (tmp_path / "sum").mkdir()
    assert fetching(tmp_path / "version", APT_VERSION, [], served).returncode == 0
    assert fetching(tmp_path / "sum", DEB_SHA256, ["6.5.1-0noble"], served).returncode == 0
    [version_asks] = asked(tmp_path / "version", "curl")
    assert version_asks[-1] == apt_index_url()
    assert version_asks[-1] in [line[-1] for line in asked(tmp_path / "sum", "curl")]


def test_a_release_without_sums_is_refused(tmp_path):
    ran = fetching(
        tmp_path, DEB_SHA256, ["6.5.1-0noble"], {apt_index_url(): stanza("6.5.1-0noble", A)}
    )
    assert (ran.returncode, ran.stdout) == (1, ""), ran
    [sentence] = [line for line in ran.stderr.splitlines() if line.startswith("bridge-deb-sha256:")]
    assert f"could not read {sums_url('6.5.1')}" in sentence
    assert apt_index_url() in sentence


def test_an_index_that_does_not_answer_is_refused(tmp_path):
    ran = fetching(
        tmp_path, DEB_SHA256, ["6.5.1-0noble"], {sums_url("6.5.1"): release_sums("6.5.1-0noble", A)}
    )
    assert (ran.returncode, ran.stdout) == (1, ""), ran
    [sentence] = [line for line in ran.stderr.splitlines() if line.startswith("bridge-deb-sha256:")]
    assert f"could not read {apt_index_url()}" in sentence
    assert sums_url("6.5.1") in sentence


def test_apt_s_highest_version_of_the_bridge_is_the_one_to_follow(tmp_path):
    packages = index(
        stanza("6.4.0-0noble", C),
        stanza("6.10.0-0noble", A),
        stanza("6.9.9-0noble", B),
        stanza("99.0.0-0noble", B, package="ros-jazzy-something-else"),
    )
    ran = fetching(tmp_path, APT_VERSION, [], {apt_index_url(): packages})
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "6.10.0-0noble\n", "")


def test_what_apt_serves_is_followed_whatever_is_released(tmp_path):
    """A bridge that is released and not promoted is on GitHub. Nobody asks GitHub."""
    served = {
        apt_index_url(): stanza("6.5.0-0noble", A),
        sums_url("6.5.1"): release_sums("6.5.1-0noble", B),
    }
    ran = fetching(tmp_path, APT_VERSION, [], served)
    assert (ran.returncode, ran.stdout) == (0, "6.5.0-0noble\n"), ran
    assert [line[-1] for line in asked(tmp_path, "curl")] == [apt_index_url()]


@pytest.mark.parametrize(
    ("packages", "sentence"),
    [
        (stanza("6.5.0-0noble", A, package="ros-jazzy-something-else"), f"lists no {PKG}"),
        ("", f"lists no {PKG}"),
        (stanza("6.5.0;id", A), "malformed version"),
    ],
    ids=["another package", "an empty index", "a malformed version"],
)
def test_an_index_that_names_no_version_to_follow_is_refused(tmp_path, packages, sentence):
    ran = fetching(tmp_path, APT_VERSION, [], {apt_index_url(): packages})
    assert (ran.returncode, ran.stdout) == (1, ""), ran
    assert sentence in ran.stderr


# --- The workflow, read ------------------------------------------------------------


def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def steps() -> list[dict]:
    [job] = workflow()["jobs"].values()
    return job["steps"]


def test_it_starts_daily_by_hand_and_when_a_bridge_is_promoted():
    # YAML 1.1 reads the key `on` as a truth value, and so does PyYAML.
    triggers = workflow()[True]
    assert set(triggers) == {"schedule", "workflow_dispatch", "repository_dispatch"}
    assert triggers["repository_dispatch"] == {"types": ["bridge-promoted"]}
    assert triggers["schedule"] == [{"cron": "23 5 * * *"}]


def test_nothing_of_the_event_that_started_it_is_read():
    """Whoever may send a dispatch decides that the workflow runs, and nothing of what it does."""
    assert "github.event" not in WORKFLOW.read_text()
    assert "client_payload" not in WORKFLOW.read_text()


def test_no_expression_stands_in_a_line_of_shell():
    """A value reaches a script through its environment, where it is data."""
    bodies = [step["run"] for step in steps() if "run" in step]
    assert len(bodies) >= 4
    for body in bodies:
        assert "${{" not in body


def test_the_token_is_for_this_repository_and_two_permissions():
    [app] = [step for step in steps() if "create-github-app-token" in step.get("uses", "")]
    assert app["with"] == {
        "app-id": "${{ secrets.FLEETLESS_APP_ID }}",
        "private-key": "${{ secrets.FLEETLESS_APP_PRIVATE_KEY }}",
        "owner": "fleetless",
        "repositories": "turtle-sim",
        "permission-contents": "write",
        "permission-pull-requests": "write",
    }
    # And it is handed to the one step that opens the pull request.
    takers = [step for step in steps() if "steps.app.outputs.token" in json.dumps(step)]
    assert [step["name"] for step in takers] == ["the pull request"]


# --- The workflow, run -------------------------------------------------------------

EXPRESSION = re.compile(r"\$\{\{ (.+?) \}\}")
COMPARISON = re.compile(r"([\w.]+) (==|!=) '([^']*)'")


def value_of(expression: str, known: dict[str, str]) -> str:
    """What GitHub makes of the few expressions this workflow uses.

    A name, or a name compared with a text. A name nobody set is empty, as
    on GitHub. Anything else is a kind of expression this test does not
    know, and says so.
    """
    comparison = COMPARISON.fullmatch(expression)
    if comparison:
        name, how, text = comparison.groups()
        return str((known.get(name, "") == text) == (how == "==")).lower()
    assert re.fullmatch(r"[\w.]+", expression), f"an expression this test cannot read: {expression}"
    return known.get(expression, "")


def filled_in(text: str, known: dict[str, str]) -> str:
    return EXPRESSION.sub(lambda found: value_of(found[1], known), text)


def run_the_job(
    directory: Path,
    *,
    apt_serves: str,
    released: dict[str, str],
    dockerfile: str | None = None,
    open_pull_request: str = "",
    secrets: bool = True,
    without: tuple[str, ...] = (),
) -> dict:
    """Runs the job's steps in their order, as GitHub would, in a copy of the checkout.

    `apt_serves` is apt's index. `released` holds the SHA256SUMS of each
    release there is, by its upstream version. A step that fails ends the
    job. A step that is an action is not run: checking out is the copy, and
    the App's token is `TOKEN`. `without` names steps to leave out, as an
    edit of the workflow might.
    """
    checkout = directory / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    for script in [APT_VERSION, DEB_SHA256]:
        shutil.copy(script, checkout / "scripts" / script.name)
    before = dockerfile if dockerfile is not None else (ROOT / "Dockerfile").read_text()
    (checkout / "Dockerfile").write_text(before)
    served = {apt_index_url(): apt_serves}
    served |= {sums_url(upstream): text for upstream, text in released.items()}
    environment = {
        **os.environ,
        **stand_ins(directory, served),
        "GITHUB_REPOSITORY": "fleetless/turtle-sim",
        "GH_OPEN_PULL_REQUEST": open_pull_request,
    }
    known = {"steps.app.outcome": "success", "steps.app.outputs.token": TOKEN}
    if secrets:
        known |= {"secrets.FLEETLESS_APP_ID": "1", "secrets.FLEETLESS_APP_PRIVATE_KEY": "a key"}
    ran, failed = {}, None
    for number, step in enumerate(steps()):
        name = step.get("name") or step.get("id") or step["uses"]
        if "run" not in step:
            continue
        wanted = filled_in(step.get("if", "${{ always == 'true' }}"), known | {"always": "true"})
        if failed or name in without or wanted != "true":
            ran[name] = None
            continue
        output = directory / f"output-{number}"
        output.touch()
        script = directory / f"step-{number}.sh"
        script.write_text(step["run"])
        own = {key: filled_in(str(value), known) for key, value in step.get("env", {}).items()}
        # `bash -e`, as GitHub runs a `run:` that names no shell.
        ran[name] = subprocess.run(
            ["bash", "-e", str(script)],
            cwd=checkout,
            env={**environment, **own, "GITHUB_OUTPUT": str(output)},
            capture_output=True,
            text=True,
            timeout=60,
        )
        if ran[name].returncode != 0:
            failed = name
        for line in output.read_text().splitlines():
            key, _, value = line.partition("=")
            known[f"steps.{step.get('id')}.outputs.{key}"] = value
    return {
        "ran": ran,
        "failed": failed,
        "before": before,
        "dockerfile": (checkout / "Dockerfile").read_text(),
        "known": known,
        "asked": asked(directory),
        "git": asked(directory, "git"),
        "gh": asked(directory, "gh"),
        "urls": [line[-1] for line in asked(directory, "curl")],
    }


def pinned() -> tuple[str, str]:
    """The version and the sum this checkout's Dockerfile pins."""
    text = (ROOT / "Dockerfile").read_text()
    [version] = re.findall(r"^ARG BRIDGE_VERSION=(.+)$", text, re.MULTILINE)
    [sha256] = re.findall(r"^ARG BRIDGE_DEB_SHA256=(.+)$", text, re.MULTILINE)
    return version, sha256


NEWER = "99.1.0-0noble"
VERSIONS, SUM, PULL_REQUEST = (
    "what apt serves, and what the Dockerfile pins",
    "the sum of its .deb, from the release and from apt",
    "the pull request",
)


def test_the_job_has_the_steps_these_tests_run():
    names = [step.get("name") for step in steps() if "run" in step]
    assert names[0] == VERSIONS
    assert names[1] == SUM
    assert names[-1] == PULL_REQUEST


def test_apt_serving_the_pinned_bridge_opens_nothing(tmp_path):
    """Also with a newer bridge released: it is not promoted, and GitHub is not asked."""
    version, sha256 = pinned()
    job = run_the_job(
        tmp_path, apt_serves=stanza(version, sha256), released={"99.1.0": release_sums(NEWER, B)}
    )
    assert job["failed"] is None, job["ran"]
    assert job["known"]["steps.versions.outputs.newer"] == "false"
    assert f"apt serves {version}, the Dockerfile pins {version}" in job["ran"][VERSIONS].stdout
    assert job["ran"][SUM] is None
    assert job["ran"][PULL_REQUEST] is None
    assert job["urls"] == [apt_index_url()]
    assert (job["git"], job["gh"]) == ([], [])
    assert job["dockerfile"] == job["before"]


def test_apt_serving_an_older_bridge_than_the_pin_opens_nothing(tmp_path):
    job = run_the_job(tmp_path, apt_serves=stanza("0.0.1-0noble", A), released={})
    assert job["failed"] is None, job["ran"]
    assert job["known"]["steps.versions.outputs.newer"] == "false"
    assert (job["git"], job["gh"]) == ([], [])
    assert job["dockerfile"] == job["before"]


def test_a_newer_bridge_in_apt_moves_both_lines_in_one_commit(tmp_path):
    version, sha256 = pinned()
    job = run_the_job(
        tmp_path, apt_serves=stanza(NEWER, B), released={"99.1.0": release_sums(NEWER, B)}
    )
    assert job["failed"] is None, job["ran"]
    assert job["known"]["steps.sum.outputs.sha256"] == B
    assert job["urls"] == [apt_index_url(), sums_url("99.1.0"), apt_index_url()]

    # The two lines of the pin, and no other line of the Dockerfile.
    was, now = job["before"].splitlines(), job["dockerfile"].splitlines()
    assert len(was) == len(now)
    assert [(old, new) for old, new in zip(was, now, strict=True) if old != new] == [
        (f"ARG BRIDGE_VERSION={version}", f"ARG BRIDGE_VERSION={NEWER}"),
        (f"ARG BRIDGE_DEB_SHA256={sha256}", f"ARG BRIDGE_DEB_SHA256={B}"),
    ]

    # One branch, one commit of everything changed, one push, one pull request.
    branch = f"deps/bridge-{NEWER}"
    git = [[word for word in line if word != "-q"] for line in job["git"]]
    assert git[0] == ["switch", "-c", branch]
    [commit] = [line for line in git if "commit" in line]
    assert commit[commit.index("commit") :] == ["commit", "-am", f"fix(image): bridge {NEWER}"]
    [push] = [line for line in git if "push" in line]
    assert push[-2:] == ["origin", f"HEAD:refs/heads/{branch}"]
    [created] = [line for line in job["gh"] if line[:2] == ["pr", "create"]]
    assert created[created.index("--title") + 1] == f"fix(image): bridge {NEWER}"
    assert created[created.index("--head") + 1] == branch
    assert created[created.index("--base") + 1] == "main"
    body = created[created.index("--body") + 1]
    for said in [NEWER, version, B, "merged by hand"]:
        assert said in body


def test_the_token_reaches_no_command_line(tmp_path):
    """Whatever lists processes sees a command line. The token travels in the environment."""
    job = run_the_job(
        tmp_path, apt_serves=stanza(NEWER, B), released={"99.1.0": release_sums(NEWER, B)}
    )
    assert job["failed"] is None, job["ran"]
    assert [line for line in job["git"] if "push" in line]
    for line in job["asked"]:
        assert TOKEN not in " ".join(line)
    for step in job["ran"].values():
        assert step is None or TOKEN not in step.stdout + step.stderr


def test_a_sum_the_release_and_apt_disagree_on_opens_nothing(tmp_path):
    job = run_the_job(
        tmp_path, apt_serves=stanza(NEWER, A), released={"99.1.0": release_sums(NEWER, B)}
    )
    assert job["failed"] == SUM
    assert job["ran"][SUM].returncode == 1
    sentence = job["ran"][SUM].stderr
    assert "disagree" in sentence
    assert A in sentence
    assert B in sentence
    assert job["ran"][PULL_REQUEST] is None
    assert (job["git"], job["gh"]) == ([], [])
    assert job["dockerfile"] == job["before"]


def test_a_release_without_sums_opens_nothing(tmp_path):
    job = run_the_job(tmp_path, apt_serves=stanza(NEWER, B), released={})
    assert job["failed"] == SUM
    assert f"could not read {sums_url('99.1.0')}" in job["ran"][SUM].stderr
    assert (job["git"], job["gh"]) == ([], [])
    assert job["dockerfile"] == job["before"]


def test_a_release_without_the_file_s_line_opens_nothing(tmp_path):
    job = run_the_job(
        tmp_path,
        apt_serves=stanza(NEWER, B),
        released={"99.1.0": sums(("f" * 64, "ros-humble-fleetless-bridge_99.1.0-0jammy_all.deb"))},
    )
    assert job["failed"] == SUM
    assert f"no line for {deb(NEWER)}" in job["ran"][SUM].stderr
    assert (job["git"], job["gh"]) == ([], [])


def test_a_version_is_never_written_without_the_sum_of_its_file(tmp_path):
    """Should the step before ever be skipped: no pin of a version alone."""
    job = run_the_job(
        tmp_path,
        apt_serves=stanza(NEWER, B),
        released={"99.1.0": release_sums(NEWER, B)},
        without=(SUM,),
    )
    assert job["failed"] == PULL_REQUEST
    assert "No SHA-256" in job["ran"][PULL_REQUEST].stderr
    assert (job["git"], job["gh"]) == ([], [])
    assert job["dockerfile"] == job["before"]


def test_a_pull_request_that_is_already_open_is_left_alone(tmp_path):
    job = run_the_job(
        tmp_path,
        apt_serves=stanza(NEWER, B),
        released={"99.1.0": release_sums(NEWER, B)},
        open_pull_request="17\n",
    )
    assert job["failed"] is None, job["ran"]
    assert "already open" in job["ran"][PULL_REQUEST].stdout
    assert job["git"] == []
    assert [line[:2] for line in job["gh"]] == [["pr", "list"]]
    assert job["dockerfile"] == job["before"]


@pytest.mark.parametrize(
    ("change", "sentence"),
    [
        (
            lambda text: re.sub(r"(?m)^ARG BRIDGE_DEB_SHA256=.*\n", "", text),
            "ARG BRIDGE_DEB_SHA256=",
        ),
        (lambda text: text + f"ARG BRIDGE_DEB_SHA256={A}\n", "ARG BRIDGE_DEB_SHA256="),
        (lambda text: text + "ARG BRIDGE_VERSION=1.0.0-0noble\n", "ARG BRIDGE_VERSION="),
    ],
    ids=["no line for the sum", "two lines for the sum", "two lines for the version"],
)
def test_a_dockerfile_whose_pin_is_not_two_lines_is_refused(tmp_path, change, sentence):
    """The workflow rewrites lines by their beginnings. It has to know there is one of each."""
    job = run_the_job(
        tmp_path,
        apt_serves=stanza(NEWER, B),
        released={"99.1.0": release_sums(NEWER, B)},
        dockerfile=change((ROOT / "Dockerfile").read_text()),
    )
    assert job["failed"] == VERSIONS
    assert sentence in job["ran"][VERSIONS].stderr
    assert (job["git"], job["gh"]) == ([], [])


def test_a_version_apt_serves_that_is_no_debian_version_is_refused(tmp_path):
    job = run_the_job(tmp_path, apt_serves=stanza("99.1.0|rm", B), released={})
    assert job["failed"] == VERSIONS
    assert (job["git"], job["gh"]) == ([], [])
    assert job["dockerfile"] == job["before"]


def test_without_the_app_s_secrets_nothing_is_opened(tmp_path):
    job = run_the_job(
        tmp_path,
        apt_serves=stanza(NEWER, B),
        released={"99.1.0": release_sums(NEWER, B)},
        secrets=False,
    )
    assert job["failed"] == "the App's secrets reach this repository"
    assert "::error::" in job["ran"][job["failed"]].stdout
    assert (job["git"], job["gh"]) == ([], [])


def test_the_few_expressions_of_the_workflow_are_read_as_github_reads_them():
    known = {"steps.versions.outputs.newer": "true", "secrets.ID": "1"}
    assert value_of("steps.versions.outputs.newer == 'true'", known) == "true"
    assert value_of("steps.versions.outputs.newer != 'true'", known) == "false"
    assert value_of("secrets.ID != ''", known) == "true"
    assert value_of("secrets.KEY != ''", known) == "false"
    assert value_of("steps.app.outputs.token", known) == ""
    assert filled_in("${{ secrets.ID }} and ${{ secrets.ID == '1' }}", known) == "1 and true"
    with pytest.raises(AssertionError):
        value_of("contains(a, 'b')", known)
