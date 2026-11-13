# 👀 fleetless_turtle_viewer

One ROS node that also serves HTTP. It subscribes to the simulator's topics
like any other consumer and pushes what it hears to a browser page. It only
shows: nothing a browser sends changes anything.

## Routes

| Route | What |
|---|---|
| `/` | The page, and its files beside it (`web/`). |
| `/ws` | The WebSocket the page listens on. |
| `/healthz` | `200` while a `/turtle/status` message arrived within the last 2 s, else `503`. |
| `/fleetless.yaml` | The shipped configuration, as a download. |

## Parameters

| Parameter | Default | Meaning |
|---|---|---|
| `port` | `8765` | The port to listen on. In use, or not a port: one sentence, exit 2. |
| `config` | `/opt/turtle/fleetless/turtle.fleetless.yaml` | The file behind `/fleetless.yaml`. |

## Who may connect

`/ws` answers the view's own page, and whoever is no browser.

A browser names the page that opens a WebSocket in the `Origin` header, and
a page cannot change it. The view compares that header with `Host`, the
place the request was sent to:

- **The same host and port: served.** That is the view's own page, on
  whatever port Docker maps the view to.
- **No `Origin`: served.** A script, `curl`, a test.
- **Anything else: `403` and one sentence**, as plain text. Another host,
  another port, `null`, an empty header, two of them, or an `Origin` without
  a `Host` to compare it with. The log names each such origin once, the
  first hundred of them.

The scheme is not compared, so a proxy may end TLS in front of the view. A
port left out is the default one of the origin's scheme, 80 or 443. The
proxy has to pass `Host` on as the browser sent it: one that puts its own
target there gets the view's own page refused.

The other routes answer everybody. A browser keeps another site from
reading them, and none of them holds the turtle's state.

## The WebSocket protocol

JSON text frames, server to page only. Whatever the page sends is read and
dropped. Every frame has a `type`; a page ignores the types it does not know.

### `world`

Sent on connect and whenever `/turtle/world` changes.

```json
{"type": "world", "width": 256, "height": 256, "resolution": 0.1, "kinds": "AAEC…"}
```

`kinds` is base64 of `width × height` bytes, row-major from cell `(0, 0)`,
the south-west corner: byte `cy × width + cx` is the kind of cell
`(cx, cy)`. `0` free, `1` wall, `2` rock, `3` water, `4` charging pad.
`resolution` is the cell size in metres.

### `state`

Sent at 20 Hz, with every `/odom` message, once `/odom`, `/turtle/status`
and `/battery_state` have each been heard.

```json
{
  "type": "state",
  "x": 0.85, "y": 0.85, "heading_deg": 0.0,
  "cell_x": 8, "cell_y": 8, "square": "A1",
  "speed": 0.0, "turn_rate": 0.0,
  "mode": "idle",
  "crawling": false, "on_charger": true, "bumper": false,
  "collisions": 0, "odometer_m": 0.0, "trail": false,
  "battery_percent": 100.0, "charging": false,
  "bridge": false
}
```

- `x`, `y` are metres in the `map` frame, `heading_deg` is 0 at east and
  grows counter-clockwise, in `[0, 360)`. All three come from `/odom`.
- `cell_x` to `trail` are the fields of `TurtleStatus`, unchanged.
- `battery_percent` is 0 to 100; `charging` is true while the battery
  reports that it charges.
- `bridge` is true while a node named `fleetless_bridge` is on the ROS
  graph. That is all the view can know: the bridge publishes no connection
  state into ROS.

### `scan`

Sent with every `/scan` message, 5 Hz.

```json
{"type": "scan", "angle_min": -3.1416, "angle_increment": 0.0873, "ranges": [1.25, null, 0.4]}
```

Ray `i` points along `angle_min + i × angle_increment` radians from the
turtle's heading, counter-clockwise. A range is metres, or `null` where the
ray hit nothing.

### `plan`

Sent on connect and whenever `/turtle/plan` changes: when a goal has planned
its path, when it plans again, and when it ends.

```json
{"type": "plan", "points": [[0.85, 0.85], [4.85, 6.95], [10.05, 18.05]]}
```

`points` are `[x, y]` in metres in the `map` frame: where the turtle stood
when the path was planned, the waypoints, and last the target. Without a
goal the list is empty. The page draws the part that still lies ahead of
the turtle, and a pin on the target.

### `trail`

Sent on connect and whenever `/turtle/trail` changes: at most once a second
as the trail grows, at once when `reset` or `set_trail` emptied or switched it.

```json
{"type": "trail", "points": [[0.85, 0.85], [0.95, 0.85], [1.05, 0.86]]}
```

`points` are `[x, y]` in metres, oldest first: where the turtle has been,
a point every 0.1 m, the newest 2000. Empty while the trail is off.
