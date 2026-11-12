// SPDX-License-Identifier: Apache-2.0

// The laser layer: one thin line per ray that hit something, and a dot where
// it hit.
//
// A `scan` frame gives ranges along angles in the turtle's frame. They are
// turned into points of the world the moment the frame arrives. The scan
// comes 5 times a second, the pose 20 times: between two scans the lines
// keep their far ends where the laser saw something and follow the turtle
// with their near ends. A ray that hit nothing (`null`) is not drawn.
//
// ROS coordinates, as in world.js.

import * as THREE from "three";

// A little above the floor, so the rays are not swallowed by it.
const RAY_HEIGHT_M = 0.05;
const COLOR = 0xff6a3d;

export class ScanLayer {
  constructor() {
    this.group = new THREE.Group();
    this.capacity = 0;
    this.hits = 0;
    this.grow(72);
  }

  /** Makes room for `rays` rays. A laser with more rays just gets more. */
  grow(rays) {
    this.group.clear();
    this.lines?.geometry.dispose();
    this.dots?.geometry.dispose();
    this.capacity = rays;

    // Two points per ray: the turtle, the hit.
    this.linePositions = new THREE.BufferAttribute(new Float32Array(rays * 6), 3);
    this.linePositions.setUsage(THREE.DynamicDrawUsage);
    const lineGeometry = new THREE.BufferGeometry();
    lineGeometry.setAttribute("position", this.linePositions);
    this.lines = new THREE.LineSegments(
      lineGeometry,
      new THREE.LineBasicMaterial({
        color: COLOR,
        transparent: true,
        opacity: 0.22,
        depthWrite: false,
      }),
    );

    this.dotPositions = new THREE.BufferAttribute(new Float32Array(rays * 3), 3);
    this.dotPositions.setUsage(THREE.DynamicDrawUsage);
    const dotGeometry = new THREE.BufferGeometry();
    dotGeometry.setAttribute("position", this.dotPositions);
    this.dots = new THREE.Points(
      dotGeometry,
      new THREE.PointsMaterial({
        color: COLOR,
        size: 3.5,
        sizeAttenuation: false,
        transparent: true,
        opacity: 0.85,
        depthWrite: false,
      }),
    );

    // The points move every frame; a bounding sphere would always be stale.
    this.lines.frustumCulled = false;
    this.dots.frustumCulled = false;
    this.group.add(this.lines, this.dots);
    this.setHits(0);
  }

  /**
   * @param {object} scan  a `scan` frame
   * @param {{x: number, y: number, heading: number}} pose  the turtle when it arrived
   */
  set(scan, pose) {
    const ranges = scan.ranges;
    if (ranges.length > this.capacity) this.grow(ranges.length);

    let hits = 0;
    for (let i = 0; i < ranges.length; i++) {
      const range = ranges[i];
      if (range === null) continue;
      const angle = pose.heading + scan.angle_min + i * scan.angle_increment;
      const x = pose.x + range * Math.cos(angle);
      const y = pose.y + range * Math.sin(angle);
      this.linePositions.setXYZ(2 * hits, pose.x, pose.y, RAY_HEIGHT_M);
      this.linePositions.setXYZ(2 * hits + 1, x, y, RAY_HEIGHT_M);
      this.dotPositions.setXYZ(hits, x, y, RAY_HEIGHT_M);
      hits += 1;
    }
    this.linePositions.needsUpdate = true;
    this.dotPositions.needsUpdate = true;
    this.setHits(hits);
  }

  /** Moves the near end of every ray to where the turtle is drawn now. */
  setOrigin(x, y) {
    for (let i = 0; i < this.hits; i++) {
      this.linePositions.setXYZ(2 * i, x, y, RAY_HEIGHT_M);
    }
    this.linePositions.needsUpdate = true;
  }

  setHits(hits) {
    this.hits = hits;
    this.lines.geometry.setDrawRange(0, 2 * hits);
    this.dots.geometry.setDrawRange(0, hits);
  }
}
