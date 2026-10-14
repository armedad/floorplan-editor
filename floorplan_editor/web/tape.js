// Tape measurements. Edges are wall faces (openings cut out), counters, and fixtures.
// Rays go up, down, left, and right in plan inches, where y grows downward.

import { fixtureCorners, round2, wallFrame } from "./geom.js";

const MIN_HIT = 0.05;

export function tapeEdges(plan) {
  const edges = [];
  const openings = new Map();
  for (const opening of plan.openings || []) {
    const list = openings.get(opening.wall) || [];
    list.push(opening);
    openings.set(opening.wall, list);
  }
  for (const wall of plan.walls || []) {
    const frame = wallFrame(wall);
    if (!frame.len) continue;
    const half = (wall.thickness || 0) / 2;
    const cuts = (openings.get(wall.id) || [])
      .map((o) => [Math.max(0, o.offset), Math.min(frame.len, o.offset + o.width)])
      .filter(([a, b]) => b - a > 0.01)
      .sort((a, b) => a[0] - b[0]);
    const spans = [];
    let t = 0;
    for (const [a, b] of cuts) {
      if (a > t + 0.01) spans.push([t, a]);
      t = Math.max(t, b);
    }
    if (t < frame.len - 0.01) spans.push([t, frame.len]);
    const at = (s, side) => [
      wall.x1 + frame.ux * s + frame.nx * half * side,
      wall.y1 + frame.uy * s + frame.ny * half * side,
    ];
    for (const [a, b] of spans) {
      edges.push([at(a, 1), at(b, 1)]);
      edges.push([at(a, -1), at(b, -1)]);
    }
    edges.push([at(0, -1), at(0, 1)]);
    edges.push([at(frame.len, -1), at(frame.len, 1)]);
    for (const [a, b] of cuts) {
      if (a > 0.01) edges.push([at(a, -1), at(a, 1)]);
      if (b < frame.len - 0.01) edges.push([at(b, -1), at(b, 1)]);
    }
  }
  for (const counter of plan.counters || []) {
    const pts = counter.points
      ? counter.points
      : [
          [counter.x, counter.y],
          [counter.x + counter.w, counter.y],
          [counter.x + counter.w, counter.y + counter.h],
          [counter.x, counter.y + counter.h],
        ];
    for (let i = 0; i < pts.length; i++) edges.push([pts[i], pts[(i + 1) % pts.length]]);
  }
  for (const fixture of plan.fixtures || []) {
    const pts = fixtureCorners(fixture);
    for (let i = 0; i < pts.length; i++) edges.push([pts[i], pts[(i + 1) % pts.length]]);
  }
  return edges;
}

function rayHit(ox, oy, dx, dy, ax, ay, bx, by) {
  const ex = bx - ax;
  const ey = by - ay;
  const det = dx * ey - dy * ex;
  if (Math.abs(det) < 1e-9) return null;
  const t = ((ax - ox) * ey - (ay - oy) * ex) / det;
  const u = ((ax - ox) * dy - (ay - oy) * dx) / det;
  if (t <= MIN_HIT || u < -1e-6 || u > 1 + 1e-6) return null;
  return t;
}

const DIRS = { left: [-1, 0], right: [1, 0], up: [0, -1], down: [0, 1] };

/** First edge each axis ray hits, or null when that direction is open. */
export function rayHits(edges, x, y) {
  const hits = { left: null, right: null, up: null, down: null };
  for (const [name, [dx, dy]] of Object.entries(DIRS)) {
    let best = Infinity;
    for (const [[ax, ay], [bx, by]] of edges) {
      const t = rayHit(x, y, dx, dy, ax, ay, bx, by);
      if (t != null && t < best) {
        best = t;
        hits[name] = { x: x + dx * t, y: y + dy * t, t };
      }
    }
  }
  return hits;
}

function span(hitA, hitB, x, y, horizontal) {
  if (!hitA && !hitB) return null;
  const a = hitA || { x, y };
  const b = hitB || { x, y };
  const measure = horizontal
    ? { x1: a.x, y1: y, x2: b.x, y2: y }
    : { x1: x, y1: a.y, x2: x, y2: b.y };
  if (Math.hypot(measure.x2 - measure.x1, measure.y2 - measure.y1) < 0.5) return null;
  return {
    x1: round2(measure.x1),
    y1: round2(measure.y1),
    x2: round2(measure.x2),
    y2: round2(measure.y2),
  };
}

/** The horizontal span and the vertical span through a click. Either may be missing. */
export function clickMeasures(edges, x, y) {
  const hits = rayHits(edges, x, y);
  return [span(hits.left, hits.right, x, y, true), span(hits.up, hits.down, x, y, false)].filter(Boolean);
}

function closest(px, py, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;
  const l2 = dx * dx + dy * dy;
  let t = l2 ? ((px - ax) * dx + (py - ay) * dy) / l2 : 0;
  t = Math.max(0, Math.min(1, t));
  const x = ax + dx * t;
  const y = ay + dy * t;
  return { x, y, d: Math.hypot(px - x, py - y) };
}

function nearestEdge(edges, x, y, tol) {
  let best = null;
  for (const [[ax, ay], [bx, by]] of edges) {
    const hit = closest(x, y, ax, ay, bx, by);
    if (hit.d <= tol && (!best || hit.d < best.d)) best = hit;
  }
  return best;
}

/** Where an edge crosses the locked horizontal or vertical line, nearest the cursor. */
function axisCrossing(edges, x, y, axis, tol) {
  let best = null;
  for (const [[ax, ay], [bx, by]] of edges) {
    let hit = null;
    if (axis === "h") {
      if (Math.abs(ay - y) < 1e-6 && Math.abs(by - y) < 1e-6) hit = closest(x, y, ax, ay, bx, by);
      else if ((ay - y) * (by - y) <= 0 && ay !== by) {
        const u = (y - ay) / (by - ay);
        if (u >= -1e-6 && u <= 1 + 1e-6) {
          const hx = ax + u * (bx - ax);
          hit = { x: hx, y, d: Math.abs(hx - x) };
        }
      }
    } else if (Math.abs(ax - x) < 1e-6 && Math.abs(bx - x) < 1e-6) hit = closest(x, y, ax, ay, bx, by);
    else if ((ax - x) * (bx - x) <= 0 && ax !== bx) {
      const u = (x - ax) / (bx - ax);
      if (u >= -1e-6 && u <= 1 + 1e-6) {
        const hy = ay + u * (by - ay);
        hit = { x, y: hy, d: Math.abs(hy - y) };
      }
    }
    if (hit && hit.d <= tol && (!best || hit.d < best.d)) best = hit;
  }
  return best;
}

/**
 * Line a point up with nearby edges: x with a vertical edge, y with a horizontal one,
 * independently, so a corner can meet both. Otherwise the nearest point on any edge.
 */
export function alignPoint(edges, x, y, tol) {
  let bestX = null;
  let bestY = null;
  for (const [[ax, ay], [bx, by]] of edges) {
    const hit = closest(x, y, ax, ay, bx, by);
    if (hit.d > tol) continue;
    const dx = Math.abs(bx - ax);
    const dy = Math.abs(by - ay);
    if (dy >= dx && (!bestX || Math.abs(hit.x - x) < bestX.d)) bestX = { d: Math.abs(hit.x - x), x: hit.x };
    if (dx >= dy && (!bestY || Math.abs(hit.y - y) < bestY.d)) bestY = { d: Math.abs(hit.y - y), y: hit.y };
  }
  if (bestX || bestY) return { x: round2(bestX ? bestX.x : x), y: round2(bestY ? bestY.y : y), kind: "edge" };
  return placePoint(edges, x, y, tol);
}

/**
 * Snap a point onto a nearby edge. With an anchor, a drag close to horizontal or vertical
 * locks to that axis and then snaps along it.
 */
export function placePoint(edges, x, y, tol, anchor = null) {
  let px = x;
  let py = y;
  let axis = null;
  if (anchor) {
    const [ax, ay] = anchor;
    const adx = Math.abs(x - ax);
    const ady = Math.abs(y - ay);
    if (ady <= tol && ady <= adx) {
      py = ay;
      axis = "h";
    } else if (adx <= tol && adx < ady) {
      px = ax;
      axis = "v";
    }
  }
  if (axis) {
    const cross = axisCrossing(edges, px, py, axis, tol);
    if (cross) return { x: round2(cross.x), y: round2(cross.y), kind: "edge" };
  }
  const near = nearestEdge(edges, px, py, tol);
  if (near) {
    if (axis === "h" && Math.abs(near.y - anchor[1]) <= tol) return { x: round2(near.x), y: round2(anchor[1]), kind: "edge" };
    if (axis === "v" && Math.abs(near.x - anchor[0]) <= tol) return { x: round2(anchor[0]), y: round2(near.y), kind: "edge" };
    if (!axis) return { x: round2(near.x), y: round2(near.y), kind: "edge" };
  }
  return { x: round2(px), y: round2(py), kind: axis ? "axis" : "free" };
}
