// SPDX-License-Identifier: Apache-2.0

// The 3D scene: renderer, camera, light, and the layers that draw the world,
// the laser and the turtle.
//
// Axes. ROS has x east, y north, z up. three.js has y up and a default camera
// that looks down -z. One group, `ros`, is turned a quarter around x and takes
// everything below it from the one to the other: ROS (x, y, z) lands on
// three.js (x, z, -y). East stays to the right, north is away from a camera
// that stands in the south, up is up. The layers are children of that group
// and think in ROS coordinates only; this file is the one place that knows
// both.

import * as THREE from "three";

import { ScanLayer } from "./scan.js";
import { TurtleLayer } from "./turtle.js";
import { OrbitControls } from "./vendor/OrbitControls.js";
import { WorldLayer } from "./world.js";

const FIELD_OF_VIEW_DEG = 35;
// The first look, and "Show the whole world": from the south, this far above
// the horizon.
const OVERVIEW_ELEVATION_DEG = 52;
// Where the camera settles when it starts to follow from far away, metres.
const FOLLOW_DISTANCE_M = 4;
const FLIGHT_MS = 700;
// The pose arrives 20 times a second and is drawn 60 times. It is eased
// over this many seconds so the turtle glides instead of ticking.
const POSE_EASE_S = 0.05;
// A step longer than this between two poses is a teleport or a reset. The
// turtle is put there, not slid there.
const JUMP_M = 0.3;
// Where style.css moves the panel from the side of the scene to below it.
const PANEL_BELOW = "(max-width: 640px)";

export class Scene {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {HTMLElement} panel  the scene is centred in the room beside it
   */
  constructor(canvas, panel) {
    this.canvas = canvas;
    this.panel = panel;

    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(FIELD_OF_VIEW_DEG, 1, 0.1, 400);

    this.skyLight = new THREE.HemisphereLight(0xffffff, 0x9aa893, 1);
    this.sun = new THREE.DirectionalLight(0xfff3dc, 1);
    this.sun.position.set(-0.5, 1, 0.6); // from the south-west, well up
    this.scene.add(this.skyLight, this.sun);

    this.ros = new THREE.Group();
    this.ros.rotation.x = -Math.PI / 2;
    this.scene.add(this.ros);

    // The layers, bottom to top. Each owns a group and knows nothing of the
    // others. A new layer (the path, the target, the trail) is one more
    // object here and one more set...() method below.
    this.world = new WorldLayer();
    this.scan = new ScanLayer();
    this.turtle = new TurtleLayer();
    this.ros.add(this.world.group, this.scan.group, this.turtle.group);

    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.12;
    this.controls.maxPolarAngle = 0.49 * Math.PI; // never below the floor
    this.controls.minDistance = 0.6;
    this.controls.maxDistance = 150;

    this.latest = null; // the pose of the last `state` frame
    this.shown = null; // the pose on screen, easing towards it
    this.follow = false;
    this.flight = null;
    this.lastFrameAt = 0;

    const scheme = window.matchMedia("(prefers-color-scheme: dark)");
    scheme.addEventListener("change", () => this.applyScheme(scheme.matches));
    this.applyScheme(scheme.matches);

    window.addEventListener("resize", () => this.resize());
    // The panel grows and shrinks with what it says.
    new ResizeObserver(() => this.resize()).observe(panel);
    this.resize();
    this.placeCamera(this.overview());

    this.renderer.setAnimationLoop((now) => this.frame(now));
  }

  // ---- what the frames bring ------------------------------------------------

  /** @param {{width: number, height: number, resolution: number, kinds: Uint8Array}} world */
  setWorld(world) {
    const first = this.world.size.x === 0;
    this.world.set(world);
    if (first) this.placeCamera(this.overview());
  }

  /** @param {{x: number, y: number, heading_deg: number}} state  a `state` frame */
  setState(state) {
    const pose = {
      x: state.x,
      y: state.y,
      heading: THREE.MathUtils.degToRad(state.heading_deg),
    };
    const jumped =
      this.shown === null || Math.hypot(pose.x - this.shown.x, pose.y - this.shown.y) > JUMP_M;
    this.latest = pose;
    if (jumped) this.shown = { ...pose };
  }

  /** @param {object} scan  a `scan` frame */
  setScan(scan) {
    if (this.latest !== null) this.scan.set(scan, this.latest);
  }

  // ---- the camera -----------------------------------------------------------

  /** Keeps the turtle in the middle of the picture, or stops doing so. */
  setFollow(on) {
    this.follow = on;
    if (!on || this.shown === null) return;
    // Fly to the turtle, keeping the direction of view. From far away also
    // come closer: following a dot is no use.
    this.fly(() => {
      const target = toThree(this.shown.x, this.shown.y);
      const offset = this.camera.position.clone().sub(this.controls.target);
      offset.setLength(Math.min(offset.length(), FOLLOW_DISTANCE_M));
      return { target, position: target.clone().add(offset) };
    });
  }

  showOverview() {
    this.follow = false;
    this.fly(() => this.overview());
  }

  /** The pose from which the whole world, frame included, fits the picture. */
  overview() {
    // Before the first `world` frame, assume the size the simulator ships.
    const sizeX = this.world.size.x || 25.6;
    const sizeY = this.world.size.y || 25.6;
    const margin = 2.2; // the frame, and some air
    const halfX = sizeX / 2 + margin;
    const halfY = sizeY / 2 + margin;

    const elevation = THREE.MathUtils.degToRad(OVERVIEW_ELEVATION_DEG);
    const sin = Math.sin(elevation);
    const cos = Math.cos(elevation);
    const tanVertical = Math.tan(THREE.MathUtils.degToRad(FIELD_OF_VIEW_DEG) / 2);
    // What the panel leaves free of the picture, as tangents from its middle.
    const tanUp = tanVertical * this.free.y;
    const tanSide = tanVertical * this.camera.aspect * this.free.x;

    // Seen at an angle, the near half of the world is taller on screen than
    // the far half. So the camera aims a little south of the middle, by
    // `south`, which puts the near and the far edge equally far from the
    // picture's edges. The near edge is also the widest thing in the picture
    // and has to fit sideways. Distance and aim depend on each other; a few
    // rounds settle them.
    let south = 0;
    let distance = 0;
    for (let round = 0; round < 6; round++) {
      const near = (halfY - south) * cos; // how much closer the near edge is
      distance = Math.max(halfX / tanSide + near, ((halfY - south) * sin) / tanUp + near);
      south = ((halfY * halfY - south * south) * cos) / distance;
    }

    const target = toThree(sizeX / 2, sizeY / 2 - south);
    const position = target.clone().add(new THREE.Vector3(0, sin, cos).multiplyScalar(distance));
    return { target, position };
  }

  placeCamera({ target, position }) {
    this.controls.target.copy(target);
    this.camera.position.copy(position);
    this.controls.update();
  }

  /**
   * Moves the camera to a pose over FLIGHT_MS.
   *
   * @param {() => {target: THREE.Vector3, position: THREE.Vector3}} destination
   *   asked again every frame, because the turtle does not wait
   */
  fly(destination) {
    this.flight = {
      destination,
      fromTarget: this.controls.target.clone(),
      fromPosition: this.camera.position.clone(),
      startedAt: performance.now(),
    };
  }

  // ---- one frame ------------------------------------------------------------

  frame(now) {
    const dt = Math.min(0.1, (now - this.lastFrameAt) / 1000);
    this.lastFrameAt = now;

    if (this.shown !== null) {
      const ease = 1 - Math.exp(-dt / POSE_EASE_S);
      this.shown.x += (this.latest.x - this.shown.x) * ease;
      this.shown.y += (this.latest.y - this.shown.y) * ease;
      this.shown.heading += shortestTurn(this.shown.heading, this.latest.heading) * ease;
      this.turtle.setPose(this.shown.x, this.shown.y, this.shown.heading);
      this.scan.setOrigin(this.shown.x, this.shown.y);
    }

    if (this.flight !== null) {
      const t = Math.min(1, (now - this.flight.startedAt) / FLIGHT_MS);
      const eased = t * t * (3 - 2 * t);
      const to = this.flight.destination();
      this.controls.target.lerpVectors(this.flight.fromTarget, to.target, eased);
      this.camera.position.lerpVectors(this.flight.fromPosition, to.position, eased);
      if (t === 1) this.flight = null;
    } else if (this.follow && this.shown !== null) {
      // Camera and target move by what the turtle moved: the view the user
      // chose stays as it is, with the turtle in its middle.
      const moved = toThree(this.shown.x, this.shown.y).sub(this.controls.target);
      this.controls.target.add(moved);
      this.camera.position.add(moved);
    }
    this.controls.update();

    if (this.shown !== null) {
      const distance = this.camera.position.distanceTo(toThree(this.shown.x, this.shown.y));
      const viewHeight =
        2 * distance * Math.tan(THREE.MathUtils.degToRad(FIELD_OF_VIEW_DEG) / 2);
      this.turtle.updateMarker(viewHeight / this.canvas.clientHeight);
    }

    this.renderer.render(this.scene, this.camera);
  }

  // ---- the page around the scene ---------------------------------------------

  resize() {
    const width = window.innerWidth;
    const height = window.innerHeight;
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;

    // The panel covers part of the picture. The middle of the scene belongs
    // in the middle of the room that is left: beside the panel, or above it
    // on a narrow screen. The camera renders a window that is shifted the
    // other way, which moves what it shows to where the room is.
    const panel = this.panel.getBoundingClientRect();
    const below = window.matchMedia(PANEL_BELOW).matches;
    const taken = below ? { x: 0, y: height - panel.top } : { x: panel.right, y: 0 };
    this.free = { x: (width - taken.x) / width, y: (height - taken.y) / height };
    this.camera.setViewOffset(width, height, -taken.x / 2, taken.y / 2, width, height);
    this.camera.updateProjectionMatrix();
  }

  /** The sky is the page's background, and the light dims with it. */
  applyScheme(dark) {
    const sky = getComputedStyle(document.documentElement).getPropertyValue("--sky").trim();
    this.scene.background = new THREE.Color(sky || "#e9efe6");
    this.skyLight.intensity = dark ? 1.25 : 1.5;
    this.sun.intensity = dark ? 1.9 : 2.2;
  }
}

/** A point on the floor, or `height` above it, from ROS to three.js. */
function toThree(x, y, height = 0) {
  return new THREE.Vector3(x, height, -y);
}

/** The signed angle from `from` to `to`, the short way round. */
function shortestTurn(from, to) {
  const turn = (to - from) % (2 * Math.PI);
  if (turn > Math.PI) return turn - 2 * Math.PI;
  if (turn < -Math.PI) return turn + 2 * Math.PI;
  return turn;
}
