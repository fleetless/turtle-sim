// SPDX-License-Identifier: Apache-2.0

// The path layer: where the turtle is going, and where it has been.
//
// A `plan` frame is the path a goal follows, from where the turtle stood
// when it was planned to the target. The part that still lies ahead of the
// turtle is drawn as a band on the floor, and the target gets a pin. What
// lies behind the turtle is the trail's business: a `trail` frame is the
// recorded trail, a dot for every tenth of a metre the turtle drove.
//
// Both are kept readable at every zoom. The band is never thinner than a few
// pixels and the pin never shorter than a fingertip, so they show from above
// the whole world; up close they are their real size.
//
// ROS coordinates, as in world.js.

import * as THREE from "three";

// Heights above the floor, so that nothing fights with it or with each other.
const TRAIL_HEIGHT_M = 0.01;
const PLAN_HEIGHT_M = 0.018;

// The band: at least this wide on the floor, and at least this wide on screen.
const PLAN_WIDTH_M = 0.03;
const PLAN_WIDTH_PX = 4;
// It is rebuilt when the zoom has changed its width by more than this share.
const REBUILD_AT = 0.08;
// How round the band's joints and ends are.
const JOINT_SEGMENTS = 14;

// The pin: its real height, and the least it measures on screen.
const PIN_HEIGHT_M = 0.34;
const PIN_HEIGHT_PX = 34;

// A trail dot: its real width, and the least it measures on screen.
const TRAIL_DOT_M = 0.035;
const TRAIL_DOT_PX = 3.5;

const COLOR = {
  plan: 0x6246ea,
  pinPole: 0x2b2350,
  pinHead: 0x7a5cff,
  pinRing: 0x6246ea,
  trail: 0x6f5632,
};

export class PathLayer {
  constructor() {
    this.group = new THREE.Group();

    this.plan = [];
    // The band as last built: its width in metres and where it began. A
    // width of 0 asks for a rebuild.
    this.bandWidth = 0;
    this.bandStart = [0, 0];
    this.band = new THREE.Mesh(
      new THREE.BufferGeometry(),
      new THREE.MeshBasicMaterial({ color: COLOR.plan, side: THREE.DoubleSide }),
    );
    this.band.frustumCulled = false; // rebuilt in place; a bounding sphere would go stale
    this.band.visible = false;

    this.pin = buildPin();
    this.pin.visible = false;

    this.trailCapacity = 0;
    this.trail = new THREE.Points(
      new THREE.BufferGeometry(),
      new THREE.PointsMaterial({
        color: COLOR.trail,
        map: dotTexture(), // a point is drawn as a square; the texture makes it round
        size: TRAIL_DOT_PX,
        sizeAttenuation: false,
        // Drawn after the floor, like everything that lies on it.
        transparent: true,
        opacity: 0.9,
        depthWrite: false,
      }),
    );
    this.trail.frustumCulled = false;
    this.growTrail(256);

    this.group.add(this.trail, this.band, this.pin);
  }

  /** @param {number[][]} points  a `plan` frame's points; empty without a goal */
  setPlan(points) {
    this.plan = places(points);
    this.bandWidth = 0;
    const going = this.plan.length > 0;
    this.band.visible = going;
    this.pin.visible = going;
    if (going) {
      const [x, y] = this.plan[this.plan.length - 1];
      this.pin.position.set(x, y, 0);
    }
  }

  /** The target in metres, or null without a goal. */
  target() {
    return this.plan.length > 0 ? this.plan[this.plan.length - 1] : null;
  }

  /** @param {number[][]} points  a `trail` frame's points, oldest first */
  setTrail(points) {
    const trail = places(points);
    if (trail.length > this.trailCapacity) this.growTrail(trail.length);
    for (let i = 0; i < trail.length; i++) {
      this.trailPositions.setXYZ(i, trail[i][0], trail[i][1], TRAIL_HEIGHT_M);
    }
    this.trailPositions.needsUpdate = true;
    this.trail.geometry.setDrawRange(0, trail.length);
  }

  growTrail(capacity) {
    this.trail.geometry.dispose();
    this.trailCapacity = capacity;
    this.trailPositions = new THREE.BufferAttribute(new Float32Array(capacity * 3), 3);
    this.trailPositions.setUsage(THREE.DynamicDrawUsage);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", this.trailPositions);
    geometry.setDrawRange(0, 0);
    this.trail.geometry = geometry;
  }

  /**
   * Once a frame: the band begins where the turtle is, and band, pin and
   * dots stay readable at the zoom of the moment.
   *
   * @param {{x: number, y: number} | null} turtle  where the turtle is drawn
   * @param {number} metresPerPixel          where the camera looks
   * @param {number} metresPerPixelAtTarget  at the target, if there is one
   * @param {number} pixelRatio              device pixels per CSS pixel
   */
  update(turtle, metresPerPixel, metresPerPixelAtTarget, pixelRatio) {
    this.trail.material.size =
      Math.max(TRAIL_DOT_PX, TRAIL_DOT_M / metresPerPixel) * pixelRatio;
    if (this.plan.length === 0) return;

    const ahead = turtle === null ? this.plan : aheadOf(this.plan, turtle.x, turtle.y);
    const width = Math.max(PLAN_WIDTH_M, PLAN_WIDTH_PX * metresPerPixel);
    const moved = Math.hypot(ahead[0][0] - this.bandStart[0], ahead[0][1] - this.bandStart[1]);
    // Rebuilt when it would look different: a pixel's worth of zoom or of way.
    if (
      Math.abs(width - this.bandWidth) > REBUILD_AT * this.bandWidth ||
      moved > metresPerPixel
    ) {
      this.bandWidth = width;
      this.bandStart = ahead[0];
      this.band.geometry.dispose();
      this.band.geometry = bandGeometry(ahead, width / 2);
    }
    const scale = (PIN_HEIGHT_PX * metresPerPixelAtTarget) / PIN_HEIGHT_M;
    this.pin.scale.setScalar(Math.max(1, scale));
  }
}

/**
 * What is left of a path for a turtle at (x, y): from the point of the path
 * nearest to it, through the waypoints after that point, to the target.
 */
function aheadOf(points, x, y) {
  let nearest = { distance: Infinity, leg: 0, point: points[0] };
  for (let leg = 0; leg + 1 < points.length; leg++) {
    const [ax, ay] = points[leg];
    const [bx, by] = points[leg + 1];
    const length2 = (bx - ax) ** 2 + (by - ay) ** 2;
    const along =
      length2 === 0
        ? 0
        : Math.min(1, Math.max(0, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / length2));
    const point = [ax + along * (bx - ax), ay + along * (by - ay)];
    const distance = Math.hypot(x - point[0], y - point[1]);
    if (distance < nearest.distance) nearest = { distance, leg, point };
  }
  return [nearest.point, ...points.slice(nearest.leg + 1)];
}

/** The points of a frame that are places: pairs of finite numbers. */
function places(points) {
  if (!Array.isArray(points)) return [];
  return points.filter(
    (point) =>
      Array.isArray(point) &&
      point.length === 2 &&
      Number.isFinite(point[0]) &&
      Number.isFinite(point[1]),
  );
}

/**
 * A band of the given half width along the points: a rectangle for every
 * leg and a disc on every point, which rounds the ends and closes the gap a
 * turn would leave on its outside.
 */
function bandGeometry(points, half) {
  const positions = [];
  const z = PLAN_HEIGHT_M;

  for (let i = 0; i + 1 < points.length; i++) {
    const [ax, ay] = points[i];
    const [bx, by] = points[i + 1];
    const length = Math.hypot(bx - ax, by - ay);
    if (length === 0) continue;
    // To the left of the leg, half a width long.
    const nx = (-(by - ay) / length) * half;
    const ny = ((bx - ax) / length) * half;
    positions.push(
      ax + nx, ay + ny, z, ax - nx, ay - ny, z, bx - nx, by - ny, z,
      ax + nx, ay + ny, z, bx - nx, by - ny, z, bx + nx, by + ny, z,
    );
  }
  for (const [x, y] of points) {
    for (let k = 0; k < JOINT_SEGMENTS; k++) {
      const a = (2 * Math.PI * k) / JOINT_SEGMENTS;
      const b = (2 * Math.PI * (k + 1)) / JOINT_SEGMENTS;
      positions.push(
        x, y, z,
        x + half * Math.cos(a), y + half * Math.sin(a), z,
        x + half * Math.cos(b), y + half * Math.sin(b), z,
      );
    }
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  return geometry;
}

/** A filled circle, white on nothing. The material gives it its colour. */
function dotTexture() {
  const size = 32;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const context = canvas.getContext("2d");
  context.fillStyle = "#ffffff";
  context.beginPath();
  context.arc(size / 2, size / 2, size / 2 - 1, 0, 2 * Math.PI);
  context.fill();
  return new THREE.CanvasTexture(canvas);
}

/** A pin for the target: a ring on the floor, a pole, and a head on top. */
function buildPin() {
  const pin = new THREE.Group();

  // The ring marks the cell. Seen through walls, like the turtle's marker:
  // it is there to be found.
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(0.075, 0.1, 40),
    new THREE.MeshBasicMaterial({
      color: COLOR.pinRing,
      transparent: true,
      opacity: 0.9,
      depthTest: false,
    }),
  );
  ring.position.z = PLAN_HEIGHT_M + 0.004;
  ring.renderOrder = 9;
  pin.add(ring);

  const pole = new THREE.Mesh(
    new THREE.CylinderGeometry(0.008, 0.008, PIN_HEIGHT_M, 10).rotateX(Math.PI / 2),
    new THREE.MeshLambertMaterial({ color: COLOR.pinPole }),
  );
  pole.position.z = PIN_HEIGHT_M / 2;
  pin.add(pole);

  const head = new THREE.Mesh(
    new THREE.SphereGeometry(0.045, 20, 14),
    new THREE.MeshLambertMaterial({ color: COLOR.pinHead }),
  );
  head.position.z = PIN_HEIGHT_M;
  pin.add(head);

  return pin;
}
