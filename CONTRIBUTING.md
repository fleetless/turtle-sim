# 🤝 Contributing to turtle-sim

Thanks for showing up. This is how to test it, what a change needs, and how
it gets released.

## Where the work happens

The canonical repository is
[github.com/fleetless/turtle-sim](https://github.com/fleetless/turtle-sim) —
public. **Pull requests are welcome** — we read them, review them, land them.
A bug or a question goes into an issue there. A vulnerability goes by mail:
[`SECURITY.md`](SECURITY.md).

**CI runs every check below on every pull request and every push to `main`**
(`.github/workflows/verify.yml`). That doesn't make running them yourself
optional — a red run ten minutes after you pushed is a worse loop than a red
test on your own machine, and CI runs the same commands this file does. The
checks:

| What | Command | Your machine needs |
|---|---|---|
| the five modes below, one after the other | `./run-tests.sh` | what `image` needs |
| ruff, shellcheck and actionlint | `./run-tests.sh lint` | Docker |
| the simulation core, the cameras' renderers, the map, the model, the page, the yaml against the simulator | `./run-tests.sh unit` | Docker |
| the yaml against the schema of `@fleetless/contracts`, and the release script | `./run-tests.sh yaml` | Docker |
| `colcon build` and `colcon test` in ROS 2 Jazzy: the nodes on a real graph | `./run-tests.sh ros` | Docker |
| the image: built, smoke-tested, and brought up by `docker-compose.yaml` | `./run-tests.sh image` | Docker with Compose v2, curl |
| the patterns behind the next line | `node --test "scripts/*.test.mjs"` | git, Node 22 |
| that no commit message cites something a public reader cannot open | `node scripts/verify-commit-messages.mjs` | git, Node 22 |

## Contributor Licence Agreement

By submitting a contribution you agree that it is licensed under the Apache
License 2.0 (see `LICENSE`), that you wrote it or otherwise have the right to
submit it, and that you grant Dehne Robotik GmbH the right to distribute it
under that licence and to relicense the project's own distribution of it.

## Running the tests

The modes run in Docker. You need no ROS, no Python packages and no Node on
your machine for them:

```sh
./run-tests.sh          # all five
./run-tests.sh unit     # one of them
```

The first run pays for the downloads; Docker caches the rest. A mode takes no
further arguments: it runs whole.

**`unit` and `ros` overlap on purpose.** The simulation core and the cameras'
renderers are plain Python. `unit` tests them where no ROS is installed, with
pinned versions of numpy and OpenCV; `ros` tests them again with Ubuntu's,
beside the nodes, which only exist on a ROS graph.

**`ros` builds its workspace from nothing, every time.** A Docker volume keeps
it between runs: `TURTLE_TEST_WS=turtle-ws ./run-tests.sh ros`.

**`image` takes two ports on your machine**, 8765 for the smoke test and the
one above it for the Compose file's test. On a machine that is not yours
alone, give the run names and ports of its own:

| Variable | Default | What |
|---|---|---|
| `TURTLE_TEST_IMAGE` | `turtle-sim:test` | the tag `image` builds; `lint`, `unit` and `ros` build theirs under the same name with `-unit` and `-ros` appended |
| `TURTLE_TEST_NAME` | `turtle-sim-test` | the prefix of every container's name |
| `TURTLE_TEST_PORT` | `8765` | the smoke test's port; the Compose file's test takes the next one |
| `TURTLE_TEST_WS` | none | a Docker volume for the colcon workspace of `ros` |

The commit check is the one that does not run in Docker. It reads your git
history rather than your files, with git and Node 22; see
[Commit messages](#commit-messages).

## Releasing

**Release**, in the Actions tab, releases `main`; a maintainer presses it.
The version comes from the Conventional Commits since the last tag. No file
in the repository carries it: it is the git tag `vX.Y.Z` and the image's tag.

The workflow refuses a commit that `verify` has not passed. It builds the
image for amd64 and for arm64, each on a runner of its own kind, and runs the
smoke test on both. Only then does anything get a name: the tag, the image as
`ghcr.io/fleetless/turtle-sim:X.Y.Z` and `latest`, and a GitHub release whose
notes name the bridge inside. Nothing deploys the image. Nobody runs this by
hand outside the button — see `.github/workflows/release.yml` for what it
does and in what order.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/), in English:

```
fix(sim): only a new trail is published at once, its growth keeps to once a second
```

**The type decides the next version.** `feat` raises the minor version.
Anything else raises the patch. A `!` after the type, or a `BREAKING CHANGE:`
footer, marks a breaking change; below 1.0 that is a minor version too. So
`feat` is for something whoever runs the image gets. A change to tests, CI,
tooling or documentation alone is `test`, `ci`, `build` or `docs`.

**The release notes are your commit subjects, verbatim, one bullet each** —
every one of them, whatever its type. A subject that would read strangely as
a release note — "wip", "fix typo", "address review" — reads exactly as
strangely to whoever looks up what changed in the image they pulled.

**A commit message is public the moment it is pushed, and stays.** It cites
nothing that only somebody inside Fleetless can open: no host name of ours,
no path on a developer's machine, no file of another repository, no internal
id, and no language but English. This rule is mostly ours to trip over, not
yours. `scripts/verify-commit-messages.mjs` checks the messages you are about
to push, `origin/main..HEAD`, and CI checks every message of your pull
request on every push to it. Run it before you push:

```sh
node scripts/verify-commit-messages.mjs
```

It says which message, which line and which kind of thing. Reword the message
and push again.

## What a change needs

- **English** for code, comments, commit messages and documentation.
- **A test that fails without the change.** Write it, watch it go red, then
  fix the code. A test added afterward usually proves the code it was written
  against, not the behaviour that was asked for.
- **The SPDX header** `SPDX-License-Identifier: Apache-2.0` in the first lines
  of every new file that can hold a comment, the way its neighbours carry it.
- **The view only shows.** No route changes anything, and the page never
  sends: driving goes through Fleetless. `test/test_page.py` and the view's
  own tests hold both, and [`SECURITY.md`](SECURITY.md) counts on it.
- **Nothing is loaded from outside the container.** The page's three.js is
  vendored, and a test pins its files by their hashes. Anything else the page
  needs is vendored the same way and named in `NOTICE`.
- **The core and the renderers stay free of ROS.** `core/` and `render/` are
  functions and numbers; the nodes beside them are thin adapters. `unit`
  fails on an `import rclpy` in there, because it has none.
- **A number has one home.** The simulator's are in `core/constants.py`. The
  yaml and the page repeat some of them, and tests compare. Change the number
  at home and let the tests say where else it stands.
- **The map and the meshes are drawn by scripts**, `tools/make_garden.py` and
  `tools/make_meshes.py`. Change the script and run it; a test compares the
  committed files with what the script makes.
- **The bridge inside is the released package, unchanged.** A change to the
  bridge belongs in [its own repository](https://github.com/fleetless/bridge).
  The pin in the `Dockerfile` moves by a pull request that a workflow opens.
- **No file sends its reader somewhere only we can open.**
  `test/test_repository.py` checks for the one such place it knows.

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).
