// SPDX-License-Identifier: Apache-2.0

// The turtle layer: a turtle built from a few spheres, and a marker that
// keeps it findable when the camera shows the whole world.
//
// ROS coordinates, as in world.js: the turtle looks along its own +x and
// z is up. This shape is only for the view.

import * as THREE from "three";

// The simulator's turtle is a circle of 0.1 m radius, and the protocol does
// not carry that number, so it stands here once more. The rim of the shell is
// drawn at exactly that radius: what touches a wall on screen is what touches
// it in the simulation.
const FOOTPRINT_RADIUS_M = 0.1;

// The marker is never smaller than this on screen, in CSS pixels.
const MARKER_RADIUS_PX = 17;
const MARKER_RADIUS_M = 0.2;

const COLOR = {
  shell: 0x2f6f4f,
  shellTop: 0x3f8a61,
  rim: 0x86b86b,
  skin: 0xa3c97d,
  eye: 0x1c2420,
  marker: 0xfff6d6,
};

export class TurtleLayer {
  constructor() {
    this.group = new THREE.Group();
    this.group.visible = false; // until the first `state` frame
    this.group.add(buildBody());

    this.markerMaterial = new THREE.MeshBasicMaterial({
      color: COLOR.marker,
      transparent: true,
      opacity: 0,
      depthTest: false, // seen through walls: it is there to be found
    });
    this.marker = buildMarker(this.markerMaterial);
    this.group.add(this.marker);
  }

  /** @param {number} heading  radians, 0 = east, counter-clockwise */
  setPose(x, y, heading) {
    this.group.visible = true;
    this.group.position.set(x, y, 0);
    this.group.rotation.z = heading;
  }

  /**
   * Grows the marker so that it keeps its size on screen, and lets it fade
   * once the camera is close enough for the turtle to speak for itself.
   *
   * @param {number} metresPerPixel  at the turtle's distance from the camera
   */
  updateMarker(metresPerPixel) {
    const scale = (MARKER_RADIUS_PX * metresPerPixel) / MARKER_RADIUS_M;
    this.marker.scale.setScalar(Math.max(1, scale));
    this.markerMaterial.opacity = 0.9 * THREE.MathUtils.clamp(scale - 0.6, 0, 1);
    this.marker.visible = this.markerMaterial.opacity > 0.01;
  }
}

function buildBody() {
  const body = new THREE.Group();
  const lambert = (color) => new THREE.MeshLambertMaterial({ color });
  const r = FOOTPRINT_RADIUS_M;

  // The rim: a flat disc, the footprint itself.
  const rim = new THREE.Mesh(zUp(new THREE.CylinderGeometry(r, r, 0.016, 40)), lambert(COLOR.rim));
  rim.position.z = 0.022;
  body.add(rim);

  // The shell: half a sphere, pressed flat.
  const shell = new THREE.Mesh(dome(0.9 * r, 0.62), lambert(COLOR.shell));
  shell.position.z = 0.026;
  body.add(shell);
  const top = new THREE.Mesh(dome(0.5 * r, 0.5), lambert(COLOR.shellTop));
  top.position.z = 0.054;
  body.add(top);

  // The head, in front, where +x is. It says which way the turtle looks.
  const skin = lambert(COLOR.skin);
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.036, 20, 14), skin);
  head.position.set(r + 0.012, 0, 0.034);
  body.add(head);
  for (const side of [-1, 1]) {
    const eye = new THREE.Mesh(new THREE.SphereGeometry(0.008, 10, 8), lambert(COLOR.eye));
    eye.position.set(r + 0.034, side * 0.018, 0.048);
    body.add(eye);
  }

  // Four legs and a tail. They do not move here.
  for (const [x, y] of [
    [0.062, 0.078],
    [0.062, -0.078],
    [-0.062, 0.078],
    [-0.062, -0.078],
  ]) {
    const leg = new THREE.Mesh(new THREE.SphereGeometry(0.028, 14, 10), skin);
    leg.scale.set(1.15, 0.8, 0.55);
    leg.position.set(x, y, 0.014);
    body.add(leg);
  }
  const tail = new THREE.Mesh(new THREE.SphereGeometry(0.014, 10, 8), skin);
  tail.scale.set(1.8, 0.8, 0.7);
  tail.position.set(-r - 0.006, 0, 0.016);
  body.add(tail);

  return body;
}

/** A ring on the ground around the turtle and an arrowhead where it looks. */
function buildMarker(material) {
  const marker = new THREE.Group();
  marker.position.z = 0.012;
  marker.renderOrder = 10;

  const ring = new THREE.Mesh(
    new THREE.RingGeometry(0.82 * MARKER_RADIUS_M, MARKER_RADIUS_M, 48),
    material,
  );
  ring.renderOrder = 10;
  marker.add(ring);

  const tip = new THREE.Shape();
  tip.moveTo(1.75 * MARKER_RADIUS_M, 0);
  tip.lineTo(1.12 * MARKER_RADIUS_M, 0.42 * MARKER_RADIUS_M);
  tip.lineTo(1.12 * MARKER_RADIUS_M, -0.42 * MARKER_RADIUS_M);
  tip.closePath();
  const arrow = new THREE.Mesh(new THREE.ShapeGeometry(tip), material);
  arrow.renderOrder = 10;
  marker.add(arrow);

  return marker;
}

/** Half a sphere with its flat side down, `flatness` times as high as wide. */
function dome(radius, flatness) {
  const geometry = zUp(new THREE.SphereGeometry(radius, 32, 14, 0, 2 * Math.PI, 0, Math.PI / 2));
  geometry.scale(1, 1, flatness);
  return geometry;
}

/** three.js builds its shapes with y up. Here z is up. */
function zUp(geometry) {
  return geometry.rotateX(Math.PI / 2);
}
