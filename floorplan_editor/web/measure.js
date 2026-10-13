// Driving dimensions: the measurements shown on a selection, and the edit that makes one read a typed value.
// Everything here is in plan inches and has no DOM, so it runs under node for tests.

import { formatLength, fixtureCorners, round2, wallFrame, wallLength } from "./geom.js";

const PARALLEL = 0.02;
const AXIS = 0.02;
const REACH = 360;

const cross = (ax, ay, bx, by) => ax * by - ay * bx;

export function findObject(plan, kind, id) {
  const list = plan[kind] || [];
  if (kind === "labels") return list[Number(id)] || null;
  return list.find((o) => o.id === id) || null;
}

/** Where other walls cross this wall's centerline, as distances along it with their two faces. */
export function wallFaces(plan, wall) {
  const f = wallFrame(wall);
  const faces = [];
  for (const other of plan.walls || []) {
    if (other.id === wall.id) continue;
    const of = wallFrame(other);
    const denom = cross(f.ux, f.uy, of.ux, of.uy);
    if (Math.abs(denom) < 0.05) continue;
    const qx = other.x1 - wall.x1;
    const qy = other.y1 - wall.y1;
    const t = cross(qx, qy, of.ux, of.uy) / denom;
    const s = cross(qx, qy, f.ux, f.uy) / denom;
    const reach = wall.thickness / 2 + 1;
    if (s < -reach || s > of.len + reach || t < -1 || t > f.len + 1) continue;
    const half = other.thickness / 2 / Math.abs(denom);
    faces.push({ t, before: t - half, after: t + half, id: other.id });
  }
  return faces.sort((a, b) => a.t - b.t);
}

/** Clear distances from a door or window to the wall faces (or wall ends) on either side of it. */
export function openingGaps(plan, opening) {
  const wall = findObject(plan, "walls", opening.wall);
  if (!wall) return null;
  const len = wallLength(wall);
  let faceBefore = 0;
  let faceAfter = len;
  for (const face of wallFaces(plan, wall)) {
    if (face.t <= opening.offset && face.after > faceBefore) faceBefore = Math.min(face.after, opening.offset);
    if (face.t >= opening.offset + opening.width && face.before < faceAfter) faceAfter = Math.max(face.before, opening.offset + opening.width);
  }
  return { wall, len, faceBefore, faceAfter, gapStart: opening.offset - faceBefore, gapEnd: faceAfter - opening.offset - opening.width };
}

/** The nearest parallel wall on each side, with the clear distance between their faces. */
export function parallelGaps(plan, wall) {
  const f = wallFrame(wall);
  const best = {};
  for (const other of plan.walls || []) {
    if (other.id === wall.id) continue;
    const of = wallFrame(other);
    if (Math.abs(cross(f.ux, f.uy, of.ux, of.uy)) > PARALLEL) continue;
    const d = (other.x1 - wall.x1) * f.nx + (other.y1 - wall.y1) * f.ny;
    const ta = (other.x1 - wall.x1) * f.ux + (other.y1 - wall.y1) * f.uy;
    const tb = (other.x2 - wall.x1) * f.ux + (other.y2 - wall.y1) * f.uy;
    const lo = Math.max(0, Math.min(ta, tb));
    const hi = Math.min(f.len, Math.max(ta, tb));
    if (hi - lo < 1) continue;
    const clear = Math.abs(d) - (wall.thickness + other.thickness) / 2;
    if (clear <= 0.01 || Math.abs(d) > REACH) continue;
    const side = d > 0 ? 1 : -1;
    if (!best[side] || Math.abs(d) < Math.abs(best[side].d)) best[side] = { side, d, clear, other, mid: (lo + hi) / 2 };
  }
  return Object.values(best);
}

/** The axis-aligned box an object covers, in inches. */
export function boxOf(kind, obj) {
  if (kind === "fixtures") {
    const pts = fixtureCorners(obj);
    const xs = pts.map((p) => p[0]);
    const ys = pts.map((p) => p[1]);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  }
  if (kind === "counters" && obj.points) {
    const xs = obj.points.map((p) => p[0]);
    const ys = obj.points.map((p) => p[1]);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  }
  if (kind === "labels") return [obj.x, obj.y, obj.x, obj.y];
  return [obj.x, obj.y, obj.x + obj.w, obj.y + obj.h];
}

function wallBox(w) {
  const h = w.thickness / 2;
  return [Math.min(w.x1, w.x2) - h, Math.min(w.y1, w.y2) - h, Math.max(w.x1, w.x2) + h, Math.max(w.y1, w.y2) + h];
}

/** Clear distance from a box to the nearest square-on wall in each direction (left, right, up, down). */
export function clearances(plan, box) {
  const [x0, y0, x1, y1] = box;
  const out = {};
  const consider = (dir, gap, lo, hi, wall) => {
    if (gap < -0.01 || gap > REACH || hi - lo < 0.5) return;
    if (!out[dir] || gap < out[dir].gap) out[dir] = { dir, gap: Math.max(0, gap), mid: (lo + hi) / 2, wall: wall.id };
  };
  for (const w of plan.walls || []) {
    const f = wallFrame(w);
    if (Math.abs(f.ux) > AXIS && Math.abs(f.uy) > AXIS) continue;
    const [wx0, wy0, wx1, wy1] = wallBox(w);
    const ylo = Math.max(y0, wy0);
    const yhi = Math.min(y1, wy1);
    const xlo = Math.max(x0, wx0);
    const xhi = Math.min(x1, wx1);
    if (wx0 >= x1 - 0.01) consider("right", wx0 - x1, ylo, yhi, w);
    if (wx1 <= x0 + 0.01) consider("left", x0 - wx1, ylo, yhi, w);
    if (wy0 >= y1 - 0.01) consider("down", wy0 - y1, xlo, xhi, w);
    if (wy1 <= y0 + 0.01) consider("up", y0 - wy1, xlo, xhi, w);
  }
  return out;
}

function endNames(ux, uy) {
  if (Math.abs(ux) >= Math.abs(uy)) return ux >= 0 ? { start: "left", end: "right" } : { start: "right", end: "left" };
  return uy >= 0 ? { start: "top", end: "bottom" } : { start: "bottom", end: "top" };
}

const GAP_NAMES = { left: "to the wall on the left", right: "to the wall on the right", up: "to the wall above", down: "to the wall below" };
const DIR_VEC = { left: [-1, 0], right: [1, 0], up: [0, -1], down: [0, 1] };

function gapDims(plan, box) {
  const [x0, y0, x1, y1] = box;
  return Object.values(clearances(plan, box)).map((c) => {
    const [vx, vy] = DIR_VEC[c.dir];
    const a = vx ? [vx > 0 ? x1 : x0, c.mid] : [c.mid, vy > 0 ? y1 : y0];
    const b = [a[0] + vx * c.gap, a[1] + vy * c.gap];
    return { key: `gap-${c.dir}`, a, b, normal: [0, 0], value: c.gap, gap: true, title: `Clear distance ${GAP_NAMES[c.dir]} (${c.wall})` };
  });
}

/** Every measurement shown for one selected object. `normal` points to the side the dimension line sits on. */
export function dimsFor(plan, kind, id) {
  const obj = findObject(plan, kind, id);
  if (!obj) return [];
  const dims = [];
  if (kind === "walls") {
    const f = wallFrame(obj);
    const names = endNames(f.ux, f.uy);
    dims.push({
      key: "length",
      a: [obj.x1, obj.y1],
      b: [obj.x2, obj.y2],
      normal: [f.nx, f.ny],
      pad: obj.thickness / 2,
      value: f.len,
      anchors: { start: `Keep the ${names.start} end`, center: "Keep the middle", end: `Keep the ${names.end} end` },
      title: "Wall length",
    });
    for (const g of parallelGaps(plan, obj)) {
      const px = obj.x1 + f.ux * g.mid;
      const py = obj.y1 + f.uy * g.mid;
      const n = [f.nx * g.side, f.ny * g.side];
      const a = [px + (n[0] * obj.thickness) / 2, py + (n[1] * obj.thickness) / 2];
      dims.push({
        key: `gap-${g.side > 0 ? "a" : "b"}`,
        a,
        b: [a[0] + n[0] * g.clear, a[1] + n[1] * g.clear],
        normal: [0, 0],
        value: g.clear,
        gap: true,
        side: g.side,
        title: `Clear distance to ${g.other.id}; typing a value moves this wall`,
      });
    }
  } else if (kind === "openings") {
    const g = openingGaps(plan, obj);
    if (!g) return [];
    const f = wallFrame(g.wall);
    const names = endNames(f.ux, f.uy);
    const at = (t) => [g.wall.x1 + f.ux * t, g.wall.y1 + f.uy * t];
    const normal = [f.nx, f.ny];
    const pad = g.wall.thickness / 2;
    dims.push({ key: "gap-start", a: at(g.faceBefore), b: at(obj.offset), normal, pad, value: g.gapStart, gap: true, title: `Distance from the ${names.start} corner` });
    dims.push({
      key: "width",
      a: at(obj.offset),
      b: at(obj.offset + obj.width),
      normal,
      pad,
      value: obj.width,
      anchors: { start: `Keep the ${names.start} edge`, center: "Keep the middle", end: `Keep the ${names.end} edge` },
      title: `${obj.type === "door" ? "Door" : "Window"} width`,
    });
    dims.push({ key: "gap-end", a: at(obj.offset + obj.width), b: at(g.faceAfter), normal, pad, value: g.gapEnd, gap: true, title: `Distance from the ${names.end} corner` });
  } else if (kind === "counters" && obj.points) {
    const pts = obj.points;
    let area = 0;
    pts.forEach((p, i) => {
      const q = pts[(i + 1) % pts.length];
      area += cross(p[0], p[1], q[0], q[1]);
    });
    const outward = area > 0 ? -1 : 1;
    pts.forEach((p, i) => {
      const q = pts[(i + 1) % pts.length];
      const len = Math.hypot(q[0] - p[0], q[1] - p[1]);
      if (len < 0.5) return;
      const n = [(-(q[1] - p[1]) / len) * outward, ((q[0] - p[0]) / len) * outward];
      dims.push({ key: `edge-${i}`, a: p, b: q, normal: n, value: len, title: `Edge ${i + 1}; typing a value moves corner ${((i + 1) % pts.length) + 1}` });
    });
    dims.push(...gapDims(plan, boxOf(kind, obj)));
  } else if (kind === "counters" || kind === "erase" || kind === "fixtures") {
    let tl;
    let tr;
    let br;
    let up;
    let right;
    if (kind === "fixtures") {
      [tl, tr, br] = fixtureCorners(obj);
      const ex = [tr[0] - tl[0], tr[1] - tl[1]];
      const ey = [br[0] - tr[0], br[1] - tr[1]];
      const lx = Math.hypot(...ex) || 1;
      const ly = Math.hypot(...ey) || 1;
      up = [-ey[0] / ly, -ey[1] / ly];
      right = [ex[0] / lx, ex[1] / lx];
    } else {
      tl = [obj.x, obj.y];
      tr = [obj.x + obj.w, obj.y];
      br = [obj.x + obj.w, obj.y + obj.h];
      up = [0, -1];
      right = [1, 0];
    }
    const across = endNames(right[0], right[1]);
    const down = endNames(-up[0], -up[1]);
    dims.push({ key: "w", a: tl, b: tr, normal: up, value: obj.w, anchors: { start: `Keep the ${across.start} side`, center: "Keep the middle", end: `Keep the ${across.end} side` }, title: "Width" });
    dims.push({ key: "h", a: tr, b: br, normal: right, value: obj.h, anchors: { start: `Keep the ${down.start} side`, center: "Keep the middle", end: `Keep the ${down.end} side` }, title: "Depth" });
    dims.push(...gapDims(plan, boxOf(kind, obj)));
  }
  for (const d of dims) d.label = formatLength(d.value);
  return dims;
}

/** The edit that makes dimension `dim` of an object read `value` inches. */
export function opForDim(plan, kind, id, dim, value, anchor = "start") {
  const obj = findObject(plan, kind, id);
  if (!obj || !Number.isFinite(value)) return null;
  if (dim.gap && value < 0) return null;
  if (!dim.gap && value <= 0) return null;
  const v = round2(value);
  if (kind === "walls") {
    if (dim.key === "length") return { op: "resize_wall", id, length: v, anchor, join: true };
    const f = wallFrame(obj);
    const shift = (dim.value - v) * dim.side;
    return { op: "move", kind, id, dx: round2(f.nx * shift), dy: round2(f.ny * shift), join: true };
  }
  if (kind === "openings") {
    const g = openingGaps(plan, obj);
    if (dim.key === "gap-start") return { op: "update", kind, id, fields: { offset: round2(g.faceBefore + v) } };
    if (dim.key === "gap-end") return { op: "update", kind, id, fields: { offset: round2(g.faceAfter - v - obj.width) } };
    const keep = { start: obj.offset, center: obj.offset + (obj.width - v) / 2, end: obj.offset + obj.width - v }[anchor];
    return { op: "update", kind, id, fields: { offset: round2(Math.max(0, keep)), width: v } };
  }
  if (dim.key.startsWith("gap-")) {
    const [vx, vy] = DIR_VEC[dim.key.slice(4)];
    const step = dim.value - v;
    return { op: "move", kind, id, dx: round2(vx * step), dy: round2(vy * step), join: false };
  }
  if (dim.key.startsWith("edge-")) {
    const i = Number(dim.key.slice(5));
    const n = obj.points.length;
    const [ax, ay] = obj.points[i];
    const [bx, by] = obj.points[(i + 1) % n];
    const len = Math.hypot(bx - ax, by - ay) || 1;
    return { op: "move_vertex", id, index: (i + 1) % n, x: round2(ax + ((bx - ax) / len) * v), y: round2(ay + ((by - ay) / len) * v) };
  }
  const axis = dim.key;
  const old = obj[axis];
  const shift = { start: (v - old) / 2, center: 0, end: -(v - old) / 2 }[anchor];
  if (kind === "fixtures") {
    const a = ((obj.rot || 0) * Math.PI) / 180;
    const [lx, ly] = axis === "w" ? [shift, 0] : [0, shift];
    const dx = lx * Math.cos(a) - ly * Math.sin(a);
    const dy = lx * Math.sin(a) + ly * Math.cos(a);
    return { op: "update", kind, id, fields: { [axis]: v, x: round2(obj.x + dx), y: round2(obj.y + dy) } };
  }
  const pos = axis === "w" ? "x" : "y";
  const start = { start: obj[pos], center: obj[pos] + (old - v) / 2, end: obj[pos] + old - v }[anchor];
  return { op: "update", kind, id, fields: { [axis]: v, [pos]: round2(start) } };
}
