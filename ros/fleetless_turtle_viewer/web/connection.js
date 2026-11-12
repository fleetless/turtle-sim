// SPDX-License-Identifier: Apache-2.0

// The WebSocket to the viewer node, and nothing but listening on it.
//
// The node sends JSON text frames (the protocol is in this package's
// README). This side reads them and hands them on. It never calls send():
// the view only shows, and driving goes through Fleetless.

// How long to wait before each new attempt, in milliseconds. The last one
// repeats for as long as it takes.
const RETRY_DELAYS_MS = [500, 1000, 2000, 4000];

/**
 * Connects and keeps reconnecting.
 *
 * @param {object} handlers
 * @param {(frame: object) => void} handlers.onFrame  every frame that parses
 * @param {() => void} handlers.onOpen  the socket is open
 * @param {() => void} handlers.onLost  the socket closed, a retry is on its way
 */
export function connect({ onFrame, onOpen, onLost }) {
  // Relative to the page, so it still fits behind a proxy that mounts the
  // view under a path. new URL() cannot rewrite http: into ws:, hence the
  // replace.
  const url = new URL("ws", document.baseURI).href.replace(/^http/, "ws");
  let attempt = 0;

  function open() {
    const socket = new WebSocket(url);

    socket.addEventListener("open", () => {
      attempt = 0;
      onOpen();
    });

    socket.addEventListener("message", (event) => {
      let frame;
      try {
        frame = JSON.parse(event.data);
      } catch {
        return; // not a frame of ours
      }
      if (frame !== null && typeof frame === "object" && typeof frame.type === "string") {
        onFrame(frame);
      }
    });

    // A refused connection fires "error" and then "close", a dropped one only
    // "close". One handler covers both.
    socket.addEventListener("close", () => {
      onLost();
      const delay = RETRY_DELAYS_MS[Math.min(attempt, RETRY_DELAYS_MS.length - 1)];
      attempt += 1;
      setTimeout(open, delay);
    });
  }

  open();
}
