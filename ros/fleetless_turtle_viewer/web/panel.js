// SPDX-License-Identifier: Apache-2.0

// The panel: the numbers of a `state` frame as text, and the sentence at the
// top of the page that says what the view is waiting for.

// Below this the battery bar changes colour. It is the threshold of the
// `battery_low` alert in the shipped fleetless.yaml.
const BATTERY_LOW_PERCENT = 20;

const NOTICES = {
  connecting: "Connecting.",
  waiting: "Waiting for the turtle.",
  live: "",
  quiet: "The turtle went quiet. Still listening.",
  lost: "The connection to the container dropped. Retrying.",
};

const BRIDGE = {
  true: {
    value: "running",
    hint:
      "The Fleetless console shows whether the robot is connected. " +
      "The bridge publishes no connection state into ROS, so this page cannot know.",
  },
  false: {
    value: "not running",
    hint: "No bridge node is on the ROS graph. A container without FLEETLESS_TOKEN starts none.",
  },
};

// Yaw as ROS counts it: 0° is east and it grows counter-clockwise.
const COMPASS = [
  "east",
  "north-east",
  "north",
  "north-west",
  "west",
  "south-west",
  "south",
  "south-east",
];

export class Panel {
  constructor(root = document) {
    this.body = root.body;
    this.notice = root.getElementById("notice");
    this.el = {};
    for (const id of [
      "battery",
      "battery-level",
      "chip-charging",
      "chip-on-charger",
      "chip-crawling",
      "chip-bumper",
      "cell",
      "square",
      "heading",
      "speed",
      "mode",
      "odometer",
      "collisions",
      "bridge",
      "bridge-hint",
    ]) {
      this.el[id] = root.getElementById(id);
    }
  }

  /** One of the keys of NOTICES. CSS reads it from <body data-view>. */
  setView(view) {
    if (this.body.dataset.view === view) return;
    this.body.dataset.view = view;
    this.notice.textContent = NOTICES[view];
  }

  /** @param {object} state  a `state` frame */
  setState(state) {
    const percent = clamp(state.battery_percent, 0, 100);
    this.text("battery", `${percent.toFixed(0)} %`);
    const level = this.el["battery-level"];
    level.style.width = `${percent}%`;
    level.classList.toggle("low", percent < BATTERY_LOW_PERCENT);

    this.show("chip-charging", state.charging);
    this.show("chip-on-charger", state.on_charger && !state.charging);
    this.show("chip-crawling", state.crawling);
    this.show("chip-bumper", state.bumper);

    this.text("cell", `(${state.cell_x}, ${state.cell_y})`);
    this.text("square", state.square);
    this.text("heading", `${state.heading_deg.toFixed(0)}° ${compass(state.heading_deg)}`);
    this.text("speed", `${state.speed.toFixed(2)} m/s`);
    this.text("mode", state.mode);
    this.text("odometer", `${state.odometer_m.toFixed(1)} m`);
    this.text("collisions", String(state.collisions));

    const bridge = BRIDGE[Boolean(state.bridge)];
    this.text("bridge", bridge.value);
    this.text("bridge-hint", bridge.hint);
  }

  // The frames come 20 times a second and mostly say what they said before.
  // The DOM is only touched when a text changes.
  text(id, value) {
    const element = this.el[id];
    if (element.textContent !== value) element.textContent = value;
  }

  show(id, on) {
    const element = this.el[id];
    if (element.hidden === on) element.hidden = !on;
  }
}

function compass(headingDeg) {
  const sector = Math.round(headingDeg / 45) % COMPASS.length;
  return COMPASS[(sector + COMPASS.length) % COMPASS.length];
}

function clamp(value, low, high) {
  return Math.min(high, Math.max(low, value));
}
