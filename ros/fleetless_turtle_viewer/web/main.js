// SPDX-License-Identifier: Apache-2.0

// The page, wired together: frames come in over the WebSocket and go to the
// scene and the panel. Nothing goes the other way. This view is a ROS
// consumer with a browser attached, and it only shows.
//
// Read on in: connection.js (the socket), scene.js (the 3D scene and its
// layers), panel.js (the numbers).

import { connect } from "./connection.js";
import { Panel } from "./panel.js";
import { Scene } from "./scene.js";

// A turtle that has said nothing for this long has gone quiet. The same two
// seconds after which the node's /healthz stops answering 200.
const QUIET_AFTER_MS = 2000;

const panel = new Panel(document);
const scene = createScene();

/** The scene, or null where the browser has no WebGL to draw it with. */
function createScene() {
  try {
    return new Scene(document.getElementById("scene"), document.getElementById("panel"));
  } catch (error) {
    // The numbers need no WebGL. They go on.
    console.error(error);
    document.body.classList.add("no-scene");
    document.getElementById("no-scene").hidden = false;
    return null;
  }
}

// ---- frames -------------------------------------------------------------------

let socketOpen = false;
let droppedOnce = false;
let lastStateAt = null; // on this connection

const handlers = {
  world(frame) {
    const kinds = decodeKinds(frame);
    if (kinds !== null) {
      scene?.setWorld({
        width: frame.width,
        height: frame.height,
        resolution: frame.resolution,
        kinds,
      });
    }
  },
  state(frame) {
    lastStateAt = performance.now();
    scene?.setState(frame);
    panel.setState(frame);
    refreshView();
  },
  scan(frame) {
    scene?.setScan(frame);
  },
};

connect({
  onFrame(frame) {
    // A newer node may send types this page has not heard of. They pass.
    if (Object.hasOwn(handlers, frame.type)) handlers[frame.type](frame);
  },
  onOpen() {
    socketOpen = true;
    lastStateAt = null;
    refreshView();
  },
  onLost() {
    socketOpen = false;
    droppedOnce = true;
    refreshView();
  },
});

/** `kinds` is base64 of one byte per cell. Null if it is not that. */
function decodeKinds(frame) {
  let bytes;
  try {
    bytes = Uint8Array.from(atob(frame.kinds), (character) => character.charCodeAt(0));
  } catch {
    return null;
  }
  return bytes.length === frame.width * frame.height ? bytes : null;
}

// ---- what the page says about itself --------------------------------------------

function refreshView() {
  let view;
  if (!socketOpen) view = droppedOnce ? "lost" : "connecting";
  else if (lastStateAt === null) view = "waiting";
  else if (performance.now() - lastStateAt > QUIET_AFTER_MS) view = "quiet";
  else view = "live";
  panel.setView(view);
}

// Silence is not an event, so somebody has to look at the clock.
setInterval(refreshView, 500);

// ---- the two camera controls -----------------------------------------------------

const follow = document.getElementById("follow");
follow.addEventListener("change", () => scene?.setFollow(follow.checked));

document.getElementById("overview").addEventListener("click", () => {
  follow.checked = false;
  scene?.showOverview();
});
