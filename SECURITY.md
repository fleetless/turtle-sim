# Security

## Reporting a vulnerability

Email **security@fleetless.dev** with what you did, what happened, what you
expected, and a version — the image's, which
`docker image inspect ghcr.io/fleetless/turtle-sim` shows as the label
`org.opencontainers.image.version`, or the git commit. We acknowledge within
three working days and tell you what we intend to do.

Please do not open a public issue for a vulnerability until we have had a
chance to respond.

## What this image is, for the purpose of a threat model

turtle-sim is a simulator: a turtle in a grid world, with ROS 2 and the
Fleetless Bridge, in one Docker image. It stands in for a robot on a
developer's machine and moves no hardware. It is not built to face a
network, so it is worth stating precisely where its surface ends.

One container. Inside run the simulator, the cameras, ROS's
`robot_state_publisher`, the view, a `ros2` daemon and, when a token is set,
the bridge — all of them as the user `turtle`, uid 1000, none as root.

### One port listens: the view

The view is an HTTP server on `TURTLE_VIEW_PORT`, 8765 unless you change it.
It is the only port the image exposes, and the only TCP port in the
container that listens beyond loopback.

| Route | What it gives |
|---|---|
| `/`, and the page's files | the page |
| `/ws` | the turtle's state over a WebSocket: pose, battery, map, laser, path |
| `/healthz` | whether the simulator has spoken within the last two seconds |
| `/fleetless.yaml` | the shipped configuration, a file of this repository |

**It only shows.** Every route answers `GET` and `HEAD`; any other method
gets a `405`. What a browser sends on the WebSocket is read and dropped, and
the view's node publishes nothing into ROS. No path leads from a request to
the turtle.

**There is no sign-in.** Whoever reaches the port sees the turtle. That is
why the README's `docker run` line and `docker-compose.yaml` publish the
port on `127.0.0.1` only; `-p 8765:8765` hands it to your network with
nothing in front of it. Whatever `-p` says, the Docker host reaches the view
at the container's own address, and so does every container on the same
Docker network.

**A page of another website does not get the state.** A browser names the
page that opens a WebSocket in the `Origin` header, and the page cannot
change it. `/ws` serves a request whose `Origin` names the host and port the
request was sent to, and a request without an `Origin`, which is no
browser's. Anything else is answered `403`. The other routes answer any
request; a browser keeps another site's page from reading what comes back,
and none of them holds the turtle's state.

**The residual, stated plainly:** that comparison trusts names. A page
served under a host name and port that its owner then points at your machine
(DNS rebinding) names the same host and port in both headers, and is served.
And where `Host` names no port, the view takes the default port of the page's
scheme: a page under the same host name on the other of 80 and 443 is served
too. What either gets is the state of a simulated turtle.

The page itself loads nothing from outside the container: three.js is
vendored, and `test/test_page.py` holds the page to that.

### The token

`FLEETLESS_TOKEN` comes from the environment and binds the bridge to exactly
one robot. The turtle's own code asks one thing of it: whether there is one.
A token that is empty or only blanks counts as none. With a token the launch
file starts the bridge; without one it says so and starts none. The bridge
reads the token and presents it to the cloud; its own
`SECURITY.md` says how it treats it. Nothing of the turtle's logs the token,
writes it to a file or serves it.

With a token the container is a robot in Fleetless. What the cloud can ask of
it is bounded by the configuration you publish, as the bridge's `SECURITY.md`
describes, and what it moves is a simulated turtle.

It is a variable of the container all the same. Every process inside can
read it, and `docker inspect` shows it to whoever may talk to the Docker
daemon. `--env-file`, as in the README, keeps it out of your shell's history.

### `/state`

The volume at `/state` outlives the container. The bridge keeps there which
ROS goal belongs to which Fleetless job: its record of jobs in flight. The
turtle's own code writes nothing there, and it holds no token.

### The ROS graph

The image sets `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`: the nodes look for
each other inside the container and nowhere else. Docker publishes none of
DDS's ports: they are not on your machine's own address.

**The residual, stated plainly:** that setting limits whom the nodes look
for, not whom they answer. DDS's sockets are bound on the container's own
network address too. A process that can send to that address and names the
container as a static peer (`ROS_STATIC_PEERS`) joins the graph, and whoever
is on the graph can drive the turtle. That is a process on the Docker host,
or another container on the same Docker network: `docker run` puts the
turtle on Docker's default bridge, beside every other container started that
way. On a network of its own, which is what Docker Compose gives it, other
containers reach neither the graph nor the view. The host still does.

### What you mount

A directory mounted at `/bridge-src` is built and run in place of the
installed bridge. Code you mount is code you run.

### What is out of scope

- The Fleetless Bridge inside the image, and the Fleetless cloud. The bridge
  has a `SECURITY.md` of its own, in its repository. Report a vulnerability
  in either to the same address, security@fleetless.dev.
- Docker and the machine it runs on. Whoever may talk to the Docker daemon
  can `docker exec` into the container, read its environment and drive the
  turtle.
- The packages in the image that are not ours: Ubuntu's and ROS's. They are
  no newer than the day their release was built.
- What the turtle does. It is a simulation and no test of a robot's safety;
  the README says where it is kinder than the world.

## Supported versions

The most recent release gets security fixes: `ghcr.io/fleetless/turtle-sim:latest`,
which is also its `X.Y.Z` tag. Older tags don't, and no released image is
built again: a fix is a new release. The bridge inside is pinned to one exact
version, which the release's notes name. A workflow asks Fleetless's apt
repository once a day and opens a pull request when it serves a newer one.
