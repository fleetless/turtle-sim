// SPDX-License-Identifier: Apache-2.0

// The world layer: the floor with its squares, and what stands on it.
//
// Everything here is in ROS coordinates (x east, y north, z up, metres);
// scene.js puts this group under the one rotation that turns them into
// three.js's.
//
// A `world` frame holds one byte per cell. The flat kinds (floor, water, the
// pad) become one texture with a texel per cell. The kinds that stand up
// (walls, rocks) become one mesh each. So the whole world is a handful of
// draw calls, however many cells block.

import * as THREE from "three";

/** The kinds of fleetless_turtle_msgs/msg/World. */
export const KIND = { FREE: 0, WALL: 1, ROCK: 2, WATER: 3, PAD: 4 };

// The squares A1 to P16 are 16 cells wide, whatever the size of the grid.
const SQUARE_CELLS = 16;

// How the kinds look. Heights are for the eye only: for the simulator a wall,
// a rock and a pond block alike.
const WALL_HEIGHT_M = 0.3;
const ROCK_HEIGHT_M = 0.09;
const FRAME_WIDTH_M = 1.3;
// What is painted on the floor lies this far above it.
const FLAT_M = 0.004;
const LABEL_SIZE_M = 0.8;

const COLOR = {
  lawn: [0xa6, 0xc4, 0x8d],
  lawnOtherSquare: [0x9d, 0xbd, 0x85],
  water: [0x4b, 0x9c, 0xd3],
  waterShore: [0x6c, 0xb4, 0xdf],
  pad: [0xf2, 0xdc, 0x6b],
  padEdge: [0xd6, 0xb4, 0x3a],
  underBlocks: [0x7d, 0x8a, 0x72],
  wall: 0xe9e2d4,
  rock: 0x8f918c,
  frame: 0x3f5b45,
  squareLines: 0x4c6a45,
  label: "#eef0e2",
  padMark: "#4a4326",
};

export class WorldLayer {
  constructor() {
    this.group = new THREE.Group();
    /** Width and height in metres; scene.js frames the camera with them. */
    this.size = { x: 0, y: 0 };

    // Rebuilt when the grid's dimensions change, which for a running
    // simulator is once.
    this.board = new THREE.Group();
    // Rebuilt with every `world` frame.
    this.blocks = new THREE.Group();
    this.group.add(this.board, this.blocks);

    this.dimensions = "";
    this.floorTexture = null;
    this.padMark = null;
  }

  /**
   * @param {object} world
   * @param {number} world.width       cells along x
   * @param {number} world.height      cells along y
   * @param {number} world.resolution  metres per cell
   * @param {Uint8Array} world.kinds   row-major from cell (0, 0)
   */
  set({ width, height, resolution, kinds }) {
    const dimensions = `${width}x${height}@${resolution}`;
    if (dimensions !== this.dimensions) {
      this.dimensions = dimensions;
      this.size = { x: width * resolution, y: height * resolution };
      this.buildBoard(width, height, resolution);
    }
    paintFloor(this.floorTexture, width, height, kinds);
    this.buildBlocks(width, height, resolution, kinds);
    this.placePadMark(width, height, resolution, kinds);
  }

  // ---- the board: floor, frame, square lines, labels ----------------------

  buildBoard(width, height, resolution) {
    dispose(this.board);
    const sizeX = width * resolution;
    const sizeY = height * resolution;

    // The floor. Row 0 of the texture is the south edge, as in the frame.
    this.floorTexture = new THREE.DataTexture(
      new Uint8Array(width * height * 4),
      width,
      height,
      THREE.RGBAFormat,
    );
    this.floorTexture.colorSpace = THREE.SRGBColorSpace;
    this.floorTexture.magFilter = THREE.NearestFilter; // a cell is a square, not a blur
    this.floorTexture.minFilter = THREE.LinearMipmapLinearFilter;
    this.floorTexture.generateMipmaps = true;
    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(sizeX, sizeY),
      // Pushed back a little in depth, so the lines and the pad's mark that
      // lie just above the floor are drawn on it and not in a fight with it.
      new THREE.MeshLambertMaterial({
        map: this.floorTexture,
        polygonOffset: true,
        polygonOffsetFactor: 1,
        polygonOffsetUnits: 1,
      }),
    );
    floor.position.set(sizeX / 2, sizeY / 2, 0);
    this.board.add(floor);

    // A frame around the floor that carries the squares' names. It has a
    // hole where the floor is, so the two never share a pixel.
    const outline = new THREE.Shape();
    outline.moveTo(-FRAME_WIDTH_M, -FRAME_WIDTH_M);
    outline.lineTo(sizeX + FRAME_WIDTH_M, -FRAME_WIDTH_M);
    outline.lineTo(sizeX + FRAME_WIDTH_M, sizeY + FRAME_WIDTH_M);
    outline.lineTo(-FRAME_WIDTH_M, sizeY + FRAME_WIDTH_M);
    outline.closePath();
    const hole = new THREE.Path();
    hole.moveTo(0, 0);
    hole.lineTo(0, sizeY);
    hole.lineTo(sizeX, sizeY);
    hole.lineTo(sizeX, 0);
    hole.closePath();
    outline.holes.push(hole);
    this.board.add(
      new THREE.Mesh(
        new THREE.ShapeGeometry(outline),
        new THREE.MeshLambertMaterial({ color: COLOR.frame }),
      ),
    );

    // The lines between the squares.
    const points = [];
    for (let cx = 0; cx <= width; cx += SQUARE_CELLS) {
      points.push(cx * resolution, 0, FLAT_M, cx * resolution, sizeY, FLAT_M);
    }
    for (let cy = 0; cy <= height; cy += SQUARE_CELLS) {
      points.push(0, cy * resolution, FLAT_M, sizeX, cy * resolution, FLAT_M);
    }
    const lines = new THREE.BufferGeometry();
    lines.setAttribute("position", new THREE.Float32BufferAttribute(points, 3));
    this.board.add(
      new THREE.LineSegments(
        lines,
        new THREE.LineBasicMaterial({
          color: COLOR.squareLines,
          transparent: true,
          opacity: 0.55,
        }),
      ),
    );

    // Letters along x, numbers along y, on all four sides. Sprites, so they
    // face the camera from wherever it looks.
    const square = SQUARE_CELLS * resolution;
    const offset = FRAME_WIDTH_M / 2;
    for (let column = 0; column * SQUARE_CELLS < width; column++) {
      const material = labelMaterial(columnLetter(column));
      const x = (column + 0.5) * square;
      this.board.add(label(material, x, -offset), label(material, x, sizeY + offset));
    }
    for (let row = 0; row * SQUARE_CELLS < height; row++) {
      const material = labelMaterial(String(row + 1));
      const y = (row + 0.5) * square;
      this.board.add(label(material, -offset, y), label(material, sizeX + offset, y));
    }

    // The mark on the charging pad; placePadMark() puts it where the pad is.
    this.padMark = new THREE.Mesh(
      new THREE.PlaneGeometry(1, 1),
      new THREE.MeshBasicMaterial({ map: padMarkTexture(), transparent: true, depthWrite: false }),
    );
    this.padMark.visible = false;
    this.board.add(this.padMark);
  }

  // ---- walls and rocks -----------------------------------------------------

  buildBlocks(width, height, resolution, kinds) {
    dispose(this.blocks);
    const grid = { width, height, resolution, kinds };
    const add = (geometry) => {
      if (geometry === null) return;
      this.blocks.add(
        new THREE.Mesh(geometry, new THREE.MeshLambertMaterial({ vertexColors: true })),
      );
    };
    add(
      blockGeometry(grid, KIND.WALL, COLOR.wall, {
        heightOf: () => WALL_HEIGHT_M,
        shadeOf: () => 1,
      }),
    );
    // No two rocks are quite alike: a little noise in height and shade, the
    // same for the same cell every time.
    add(
      blockGeometry(grid, KIND.ROCK, COLOR.rock, {
        heightOf: (cx, cy) => ROCK_HEIGHT_M * (0.85 + 0.3 * noise(cx, cy)),
        shadeOf: (cx, cy) => 0.93 + 0.14 * noise(cy, cx),
      }),
    );
  }

  // ---- the pad's mark ------------------------------------------------------

  placePadMark(width, height, resolution, kinds) {
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    for (let cy = 0; cy < height; cy++) {
      for (let cx = 0; cx < width; cx++) {
        if (kinds[cy * width + cx] !== KIND.PAD) continue;
        minX = Math.min(minX, cx);
        maxX = Math.max(maxX, cx);
        minY = Math.min(minY, cy);
        maxY = Math.max(maxY, cy);
      }
    }
    this.padMark.visible = Number.isFinite(minX);
    if (!this.padMark.visible) return;
    const spanX = (maxX - minX + 1) * resolution;
    const spanY = (maxY - minY + 1) * resolution;
    const size = 0.62 * Math.min(spanX, spanY);
    this.padMark.scale.set(size, size, 1);
    this.padMark.position.set(
      minX * resolution + spanX / 2,
      minY * resolution + spanY / 2,
      FLAT_M,
    );
  }
}

// ---- blocks: one mesh for all cells of a kind ------------------------------

const UP = [0, 0, 1];
const WEST = [-1, 0, 0];
const EAST = [1, 0, 0];
const SOUTH = [0, -1, 0];
const NORTH = [0, 1, 0];

/**
 * A block's side: the upright rectangle of height `z` over the line on the
 * floor from a to b. Walking from a to b, the outside is on the right.
 */
function side(ax, ay, bx, by, z) {
  return [
    [ax, ay, 0],
    [bx, by, 0],
    [bx, by, z],
    [ax, ay, z],
  ];
}

/**
 * The cells of one kind as boxes, in one geometry: a top for every cell, and
 * a side only where the neighbour is not a block of that kind at least as
 * high. Faces between two blocks are never seen, so they are never built.
 * Null if the world has no cell of the kind.
 */
function blockGeometry({ width, height, resolution, kinds }, kind, color, { heightOf, shadeOf }) {
  const positions = [];
  const normals = [];
  const colors = [];
  const indices = [];
  const tint = new THREE.Color();

  // Four corners, counter-clockwise as seen from outside the block.
  const face = (corners, normal) => {
    const first = positions.length / 3;
    for (const corner of corners) {
      positions.push(...corner);
      normals.push(...normal);
      colors.push(tint.r, tint.g, tint.b);
    }
    indices.push(first, first + 1, first + 2, first, first + 2, first + 3);
  };
  // How high the block on a cell stands; 0 where there is none of this kind.
  const blockAt = (cx, cy) =>
    cx >= 0 && cy >= 0 && cx < width && cy < height && kinds[cy * width + cx] === kind
      ? heightOf(cx, cy)
      : 0;

  for (let cy = 0; cy < height; cy++) {
    for (let cx = 0; cx < width; cx++) {
      const z = blockAt(cx, cy);
      if (z === 0) continue;
      tint.set(color).multiplyScalar(shadeOf(cx, cy));
      // Neighbours share these numbers exactly, so no light gets between them.
      const x0 = cx * resolution;
      const x1 = (cx + 1) * resolution;
      const y0 = cy * resolution;
      const y1 = (cy + 1) * resolution;

      face(
        [
          [x0, y0, z],
          [x1, y0, z],
          [x1, y1, z],
          [x0, y1, z],
        ],
        UP,
      );
      if (blockAt(cx - 1, cy) < z) face(side(x0, y1, x0, y0, z), WEST);
      if (blockAt(cx + 1, cy) < z) face(side(x1, y0, x1, y1, z), EAST);
      if (blockAt(cx, cy - 1) < z) face(side(x0, y0, x1, y0, z), SOUTH);
      if (blockAt(cx, cy + 1) < z) face(side(x1, y1, x0, y1, z), NORTH);
    }
  }
  if (indices.length === 0) return null;

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute("normal", new THREE.Float32BufferAttribute(normals, 3));
  geometry.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
  geometry.setIndex(indices);
  return geometry;
}

// ---- the floor's texture: one texel per cell -------------------------------

function paintFloor(texture, width, height, kinds) {
  const data = texture.image.data;
  const kindAt = (cx, cy) =>
    cx < 0 || cy < 0 || cx >= width || cy >= height ? -1 : kinds[cy * width + cx];
  // True where one of the four neighbours is of another kind.
  const atEdge = (cx, cy, kind) =>
    kindAt(cx - 1, cy) !== kind ||
    kindAt(cx + 1, cy) !== kind ||
    kindAt(cx, cy - 1) !== kind ||
    kindAt(cx, cy + 1) !== kind;

  for (let cy = 0; cy < height; cy++) {
    for (let cx = 0; cx < width; cx++) {
      const kind = kinds[cy * width + cx];
      let color;
      if (kind === KIND.WATER) {
        color = atEdge(cx, cy, kind) ? COLOR.waterShore : COLOR.water;
      } else if (kind === KIND.PAD) {
        color = atEdge(cx, cy, kind) ? COLOR.padEdge : COLOR.pad;
      } else if (kind === KIND.WALL || kind === KIND.ROCK) {
        color = COLOR.underBlocks;
      } else {
        // The squares as on a chess board, only quieter.
        const other = (Math.floor(cx / SQUARE_CELLS) + Math.floor(cy / SQUARE_CELLS)) % 2 === 1;
        color = other ? COLOR.lawnOtherSquare : COLOR.lawn;
      }
      // Every other cell a shade lighter, like the squares in small. Up
      // close the cells can be counted; from afar it is one colour.
      const shade = (cx + cy) % 2 === 0 ? 1.012 : 0.988;
      const i = 4 * (cy * width + cx);
      data[i] = Math.min(255, color[0] * shade);
      data[i + 1] = Math.min(255, color[1] * shade);
      data[i + 2] = Math.min(255, color[2] * shade);
      data[i + 3] = 255;
    }
  }
  texture.needsUpdate = true;
}

/** 0 to 1, the same for the same cell every time. No randomness in a view. */
function noise(a, b) {
  let h = Math.imul(a + 1, 0x9e3779b1) ^ Math.imul(b + 1, 0x85ebca6b);
  h = Math.imul(h ^ (h >>> 15), 0xc2b2ae35);
  return ((h ^ (h >>> 13)) >>> 0) / 0xffffffff;
}

// ---- labels and marks, drawn on a canvas -----------------------------------

function columnLetter(column) {
  return String.fromCharCode("A".charCodeAt(0) + column);
}

function labelMaterial(text) {
  const size = 128;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const context = canvas.getContext("2d");
  context.fillStyle = COLOR.label;
  context.font = `600 ${size * 0.62}px system-ui, -apple-system, "Segoe UI", sans-serif`;
  context.textAlign = "center";
  context.textBaseline = "middle";
  context.fillText(text, size / 2, size / 2 + size * 0.04);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  // A sprite stands upright towards the camera, so half of it would sink into
  // the floor it is anchored on. Without the depth test it stays whole.
  return new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false });
}

function label(material, x, y) {
  const sprite = new THREE.Sprite(material);
  sprite.position.set(x, y, 0.05);
  sprite.scale.set(LABEL_SIZE_M, LABEL_SIZE_M, 1);
  return sprite;
}

/** A lightning bolt, the usual sign for "charge here". */
function padMarkTexture() {
  const size = 256;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const context = canvas.getContext("2d");
  context.fillStyle = COLOR.padMark;
  context.beginPath();
  for (const [index, [x, y]] of [
    [0.58, 0.06],
    [0.24, 0.56],
    [0.47, 0.56],
    [0.4, 0.94],
    [0.76, 0.42],
    [0.52, 0.42],
  ].entries()) {
    if (index === 0) context.moveTo(x * size, y * size);
    else context.lineTo(x * size, y * size);
  }
  context.closePath();
  context.fill();
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

// ---- tidying up -------------------------------------------------------------

/** Empties a group and frees what its children held on the GPU. */
function dispose(group) {
  for (const child of [...group.children]) {
    group.remove(child);
    // Every sprite shares three.js's one sprite geometry: not ours to free.
    if (!child.isSprite) child.geometry?.dispose();
    child.material?.map?.dispose();
    child.material?.dispose();
  }
}
