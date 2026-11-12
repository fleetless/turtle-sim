# 🐢 turtle-sim

A turtle on two wheels in a garden of 256 × 256 cells, with ROS 2 Jazzy and
the Fleetless Bridge, in one Docker image. One line starts it, and it
connects to [Fleetless](https://fleetless.dev) exactly as a real robot does:
live values, alerts, and a publisher to drive it with. It needs no GPU, no
X11 and no robot.

![The view: the garden from above, the turtle and its laser.](docs/view.png)

## 🚀 Three steps

> The image is private for now. Run `docker login ghcr.io` first, with a
> token that may read the packages of the `fleetless` organisation.

**1. Create the robot.** In the [console](https://console.fleetless.dev), open
**Robots** and create one. The token (`frt_…`) is shown once. Copy it.

**2. Start the turtle.** One line, the same in bash, PowerShell and cmd:

```sh
docker run --rm -it --name turtle -p 127.0.0.1:8765:8765 -v turtle-state:/state -e FLEETLESS_TOKEN=frt_... ghcr.io/fleetless/turtle-sim
```

The view is at <http://localhost:8765>, and the robot turns connected in the
console within a few seconds. To keep the token out of the shell history,
put `FLEETLESS_TOKEN=frt_...` into a file and pass that:

```sh
docker run --rm -it --name turtle -p 127.0.0.1:8765:8765 -v turtle-state:/state --env-file turtle.env ghcr.io/fleetless/turtle-sim
```

Or the same container by Docker Compose. Take
[`docker-compose.yaml`](docker-compose.yaml) and [`.env.example`](.env.example)
from this repository, copy `.env.example` to `.env`, write the token into
it, and start it beside the two files:

```sh
docker compose up
```

Ctrl-C stops the turtle and `docker compose down` removes it. `.env` is in
`.gitignore`, so the token stays out of every commit. Any Docker Compose v2
will do; Docker Desktop brings one.

Without a token the simulator and the view run all the same; no bridge is
started, and the log says so.

**3. Publish the configuration.** The robot exposes nothing until it is told
what. Take [`fleetless/turtle.fleetless.yaml`](fleetless/turtle.fleetless.yaml),
which the view also hands out at <http://localhost:8765/fleetless.yaml>, put
it into the robot's configuration in the console and publish it.

Publishing lists one `unknown_type` warning per entry, for standard ROS types
as much as for the turtle's own: the cloud has not looked at this robot's
message types yet. They are warnings. The file publishes, the bridge applies
it, the values flow. To be rid of them, have the cloud fetch the types once
while the turtle is connected (over MCP: `console_introspection_write`,
action `fetch_types`) and publish again.

## 🕹️ What to try

- **Drive it.** The console's Controls send `drive`: a speed and a turn
  rate. Watch `square` change. When the messages stop, the turtle stops by
  itself, half a second later.
- **Ask a model.** Connect an MCP client to Fleetless and ask what the robot
  exposes (`robots.describe`), then to drive to the pond and say when
  `range_front` drops below half a metre. The descriptions in the yaml are
  all the model has to go on, which is why they are long.
- **Build on it.** The [app starter](https://docs.fleetless.dev/recipes/app-starter/)
  is a dashboard on `@fleetless/sdk`. Point it at this robot.
- **Empty the battery.** Full speed does it in five minutes. Then the turtle
  crawls, `battery_empty` fires, and the way out is the charging pad in
  square `A1`, one minute for a full charge. `-e TURTLE_BATTERY_DRAIN=10`
  gets you there sooner.
- **Hit something.** `obstacle_ahead` fires when a wall is 0.3 m ahead,
  `bumper` goes true at it, `collisions` counts. The turtle does not slide
  along walls. It was not asked to.

![Close to the turtle: its laser rays end at a hedge, with the rock alley behind it.](docs/view-close.png)

## 📋 What the turtle exposes

Everything in [`fleetless/turtle.fleetless.yaml`](fleetless/turtle.fleetless.yaml).
Actions, services, cameras and a model are on their way; this table grows
with them.

| Slug | Kind | What |
|---|---|---|
| `battery_soc` | datapoint | Battery in percent, with history. Alerts: `battery_low` below 20 %, `battery_empty` below 1 %. |
| `on_charger` | datapoint | True on the charging pad. |
| `crawling` | datapoint | True while the battery is empty and the speed limits are reduced. Alert while true. |
| `cell_x`, `cell_y` | datapoints | The turtle's cell, 0 to 255 each, from the south-west corner. |
| `square` | datapoint | The square it is in, `A1` to `P16`. |
| `heading` | datapoint | Degrees, 0 is east, counter-clockwise. |
| `speed` | datapoint | m/s along the heading, negative backwards. |
| `mode` | datapoint | `idle`, `teleop` or `charging`. |
| `odometer` | datapoint | Metres driven, with history. |
| `bumper`, `collisions` | datapoints | Pushing against something right now, and how often so far. |
| `range_front`, `range_left`, `range_right` | datapoints | Metres to the nearest obstacle, 0.1 to 4.0. Alert `obstacle_ahead` on the front one, below 0.3 m. |
| `scan` | datapoint | The whole laser scan: 72 ranges, 5° apart. |
| `drive` | publisher | `linear_speed` ±0.5 m/s, `angular_speed` ±2.0 rad/s. Without a new message for 500 ms, the bridge sends a zero Twist. |

`bridge_state` is built in on every robot and says whether the bridge is
connected. The status values are throttled to 2 Hz, the booleans and the
ranges are not.

## 🗺️ Variables, and a map of your own

Both ways of starting take the same five. `docker run` gets one as
`-e NAME=value` or from `--env-file`. Compose reads it from the shell or from
`.env`, and the shell wins; `docker-compose.yaml` lists them all and needs no
editing.

| Variable | Default | Meaning |
|---|---|---|
| `FLEETLESS_TOKEN` | — | The robot's token. Unset or empty: no bridge is started. |
| `FLEETLESS_CLOUD_URL` | the bridge's own default | Passed through to the bridge. |
| `TURTLE_MAP` | `/opt/turtle/maps/garden.png` | The map file. |
| `TURTLE_BATTERY_DRAIN` | `1.0` | Factor on the battery's discharge. `0` turns it off. Charging is not scaled. |
| `TURTLE_VIEW_PORT` | `8765` | The port the view listens on inside the container. For another port on your machine, change the left number of `-p`. With Compose the variable moves both sides, and the view is at that port on your machine too. |

A value the turtle cannot use ends the container with one sentence that
names the variable and the value, and a non-zero exit code:

```
TURTLE_BATTERY_DRAIN (the parameter battery_drain) is "abc": the battery drain factor is a number of 0 or more.
```

The battery is not kept across a restart: a new container is full.

**The map** is an 8-bit RGB PNG of exactly 256 × 256 pixels, one pixel per
cell of 0.1 m, north up. Five colours and no other:

| Colour | Kind | Blocks |
|---|---|---|
| `#ffffff` | free | no |
| `#000000` | wall | yes |
| `#808080` | rock | yes |
| `#0000ff` | water | yes |
| `#00ff00` | charging pad | no |

Paint with a pencil, not a brush: anti-aliasing invents a sixth colour, and
the refusal names the pixel. The map needs a pad with room for the turtle,
which is two cells wide and starts in the pad's middle, heading east.
Outside the picture everything blocks. Then mount it and name it:

```sh
docker run --rm -it --name turtle -p 127.0.0.1:8765:8765 -v turtle-state:/state -v /path/to/my-map.png:/maps/my-map.png:ro -e TURTLE_MAP=/maps/my-map.png -e FLEETLESS_TOKEN=frt_... ghcr.io/fleetless/turtle-sim
```

With Compose, `docker-compose.yaml` holds that mount as a commented line:
uncomment it, give it your file's path, and set `TURTLE_MAP=/maps/my-map.png`
in `.env`.

This is the shipped one, `maps/garden.png`, drawn by
[`tools/make_garden.py`](tools/make_garden.py), with the names of the squares:

![The garden: a house, a shed, two ponds, rock beds, an alley of rocks, an orchard, a dead end and a courtyard without a door.](docs/garden.png)

## ⚙️ How it works

One container, one launch file, three processes:

| Process | Package | Does |
|---|---|---|
| the simulator | `fleetless_turtle_sim` | Steps the world 50 times a second, takes `/cmd_vel`, publishes what a robot publishes. |
| the view | `fleetless_turtle_viewer` | Subscribes like anybody else and serves the page. It only shows; nothing a browser sends changes anything. |
| the bridge | `ros-jazzy-fleetless-bridge` | Fleetless's own package from its apt repository, started by its own launch file, unchanged. Only with a token. |

Nothing is special about any of them. The bridge sees the turtle through
ROS topics, as it would see any robot, and so does the view. DDS stays inside
the container, so the graph is yours to look at:

```sh
docker exec -it turtle ros2 topic list
```

| Topic | Type | Rate |
|---|---|---|
| `/cmd_vel` (in) | `geometry_msgs/msg/Twist` | — |
| `/odom` | `nav_msgs/msg/Odometry` | 20 Hz |
| `/tf`, `/tf_static` | | 20 Hz, once |
| `/scan` | `sensor_msgs/msg/LaserScan` | 5 Hz |
| `/range/front`, `/range/left`, `/range/right` | `sensor_msgs/msg/Range` | 10 Hz |
| `/battery_state` | `sensor_msgs/msg/BatteryState` | 1 Hz |
| `/turtle/status` | `fleetless_turtle_msgs/msg/TurtleStatus` | 10 Hz |
| `/turtle/world` | `fleetless_turtle_msgs/msg/World` | latched |
| `/map` | `nav_msgs/msg/OccupancyGrid` | latched |

**Which file to read first**, if you are here to see how a ROS 2 robot is
wired to Fleetless:

1. [`fleetless/turtle.fleetless.yaml`](fleetless/turtle.fleetless.yaml): what
   Fleetless is told. Every slug is one line of ROS and a description.
2. [`ros/fleetless_turtle_bringup/launch/turtle.launch.py`](ros/fleetless_turtle_bringup/launch/turtle.launch.py):
   how the bridge is started beside a robot's own nodes.
3. [`ros/fleetless_turtle_sim/fleetless_turtle_sim/node.py`](ros/fleetless_turtle_sim/fleetless_turtle_sim/node.py):
   the robot's side, a thin ROS adapter.
4. [`core/simulation.py`](ros/fleetless_turtle_sim/fleetless_turtle_sim/core/simulation.py)
   and [`core/constants.py`](ros/fleetless_turtle_sim/fleetless_turtle_sim/core/constants.py):
   the simulation itself, without a line of ROS, and every number it uses.

The other packages: `fleetless_turtle_msgs` holds the turtle's own messages,
services and actions, and `fleetless_turtle_viewer` documents the view's
WebSocket protocol in [its README](ros/fleetless_turtle_viewer/README.md).

## 🛑 What a simulator owes you in honesty

**Two stops, and which is which.** The turtle itself stops driving 0.5 s
after the last `/cmd_vel`: that is the robot-side stop, it lives in the
simulator, and it holds when the bridge is gone. The `failsafe` of `drive` in
the yaml is the other layer: the bridge sends a zero Twist when no client has
published for 500 ms, which covers an app or a link that fell silent while
the bridge still runs. A real robot needs the first kind in its own
controller, and neither is a safety function. Read the
[safety notice](https://docs.fleetless.dev/#safety-notice) before the robot
is real.

**The odometry is exact.** The pose published is the pose simulated, and
`map → odom` is the identity. No noise, no drift, no slip. A real robot is
not this lucky.

**An empty range is reported in two ways.** A laser ray that hits nothing
within 4 m is `inf` in `/scan`, as REP 117 asks, and the bridge sends that as
`null`. A range sensor that sees nothing reports its `max_range`, 4.0, a
number, so a chart of `range_front` has no holes.

## 🔧 Testing an unreleased bridge

Mount a checkout of the bridge at `/bridge-src`. The container copies it,
builds it and runs it in place of the installed package:

```sh
docker run --rm -it --name turtle -p 127.0.0.1:8765:8765 -v turtle-state:/state -v /path/to/bridge:/bridge-src:ro -e FLEETLESS_TOKEN=frt_... ghcr.io/fleetless/turtle-sim
```

The log names both, `building the bridge from /bridge-src. It replaces the
installed ros-jazzy-fleetless-bridge 6.2.3-0noble.` A build that fails ends
the container; the installed bridge is not started in its place. For a cloud
other than the default, add `-e FLEETLESS_CLOUD_URL=wss://…/bridge`.

The bridge in the image is pinned to one exact version in the `Dockerfile`.
A workflow asks Fleetless's apt repository once a day and opens a pull
request when there is a newer one.

## ✅ Tests

Everything CI runs, in Docker, so the host needs Docker and nothing else:

```sh
./run-tests.sh          # all of it
./run-tests.sh unit     # the simulation core, plain pytest, no ROS
```

| Mode | What |
|---|---|
| `lint` | ruff, shellcheck, actionlint |
| `unit` | the core, the shipped map against its script, the yaml against the simulator's numbers |
| `ros` | `colcon build` and `colcon test` in ROS 2 Jazzy: the nodes on a real graph |
| `yaml` | the yaml against the schema of `@fleetless/contracts`, and the release script |
| `image` | builds the image, runs its smoke test, and brings `docker-compose.yaml` up against it |

## 📄 License

Apache-2.0, see [`LICENSE`](LICENSE). [`NOTICE`](NOTICE) names what is
vendored: three.js, under the MIT license.
