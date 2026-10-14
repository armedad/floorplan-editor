// Browser geometry the drawing tools rely on. A change here moves walls, snaps, and clicks.

import assert from "node:assert/strict";
import { test } from "node:test";

import { formatLength, indexScene, lockAxis, parseLength, pick, pointInPoly, rotate, snapPoint } from "../../floorplan_editor/web/geom.js";

const plan = { walls: [{ id: "w", x1: 0, y1: 0, x2: 100, y2: 0, thickness: 4 }] };

test("snap prefers a corner, then the wall, then the grid", () => {
  assert.deepEqual(snapPoint(plan, 3, 2, 8), { x: 0, y: 0, kind: "endpoint" });
  const onWall = snapPoint(plan, 40, 3, 8);
  assert.equal(onWall.kind, "wall");
  assert.equal(onWall.y, 0);
  assert.ok(Math.abs(onWall.x - 40) < 1e-9);
  const skipped = snapPoint(plan, 40, 3, 8, { skip: new Set(["w"]), grid: 12 });
  assert.deepEqual(skipped, { x: 36, y: 0, kind: "grid" });
  assert.deepEqual(snapPoint(plan, 40, 30, 4), { x: 40, y: 30, kind: "none" });
});

test("holding shift locks to the stronger axis", () => {
  assert.deepEqual(lockAxis([10, 20], 18, 22), [18, 20]);
  assert.deepEqual(lockAxis([10, 20], 12, 40), [10, 40]);
});

test("formatted lengths parse back to the same inches", () => {
  for (const inches of [0, 6, 12, 30, 126.5, -3]) {
    const text = formatLength(inches);
    assert.ok(Math.abs(parseLength(text) - inches) < 0.05, `${inches} -> ${text}`);
  }
});

test("a click hits the smaller object when two overlap", () => {
  const big = { op: "polygon", points: [[0, 0], [100, 0], [100, 100], [0, 100]], obj: ["counters", "big"] };
  const small = { op: "polygon", points: [[10, 10], [20, 10], [20, 20], [10, 20]], obj: ["fixtures", "small"] };
  const objects = indexScene([big, small]);
  assert.equal(pick(objects, 15, 15, 4).key, "fixtures:small");
  assert.equal(pick(objects, 80, 80, 4).key, "counters:big");
  assert.equal(pick(objects, 200, 200, 4), null);
  assert.equal(pointInPoly(15, 15, small.points), true);
  assert.equal(pointInPoly(0, 15, small.points), false);
});

test("rotation is clockwise on the sheet, where y grows downward", () => {
  const [x, y] = rotate(10, 0, 90);
  assert.ok(Math.abs(x) < 1e-9 && Math.abs(y - 10) < 1e-9);
});
