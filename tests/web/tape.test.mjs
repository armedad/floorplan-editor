import assert from "node:assert/strict";
import { test } from "node:test";

import { alignPoint, clickMeasures, placePoint, rayHits, tapeEdges } from "../../floorplan_editor/web/tape.js";

function room() {
  return {
    walls: [
      { id: "n", x1: 0, y1: 0, x2: 200, y2: 0, thickness: 4 },
      { id: "s", x1: 0, y1: 100, x2: 200, y2: 100, thickness: 4 },
      { id: "w", x1: 0, y1: 0, x2: 0, y2: 100, thickness: 4 },
      { id: "e", x1: 200, y1: 0, x2: 200, y2: 100, thickness: 4 },
      { id: "far", x1: 0, y1: -40, x2: 200, y2: -40, thickness: 4 },
    ],
    openings: [{ id: "door", wall: "n", type: "door", offset: 80, width: 40 }],
  };
}

test("rays stop at the first face and pass through a doorway", () => {
  const edges = tapeEdges(room());
  const open = rayHits(edges, 100, 50);
  assert.ok(Math.abs(open.up.y - -38) < 1e-6);
  assert.ok(Math.abs(open.down.y - 98) < 1e-6);
  assert.ok(Math.abs(open.left.x - 2) < 1e-6);
  assert.ok(Math.abs(open.right.x - 198) < 1e-6);
  const blocked = rayHits(edges, 40, 50);
  assert.ok(Math.abs(blocked.up.y - 2) < 1e-6);
});

test("a click keeps the horizontal and vertical spans", () => {
  const measures = clickMeasures(tapeEdges(room()), 100, 50);
  assert.equal(measures.length, 2);
  assert.deepEqual(measures[0], { x1: 2, y1: 50, x2: 198, y2: 50 });
  assert.deepEqual(measures[1], { x1: 100, y1: -38, x2: 100, y2: 98 });
});

test("a click with one side open ends at the pointer", () => {
  const edges = tapeEdges({ walls: [{ id: "w", x1: 0, y1: 0, x2: 0, y2: 40, thickness: 0 }] });
  const measures = clickMeasures(edges, 30, 20);
  assert.deepEqual(measures, [{ x1: 0, y1: 20, x2: 30, y2: 20 }]);
});

test("drag snaps to an edge and straightens when nearly level", () => {
  const edges = tapeEdges({ walls: [{ id: "v", x1: 48, y1: -20, x2: 48, y2: 20, thickness: 0 }] });
  const straight = placePoint([], 80, 3, 8, [0, 0]);
  assert.deepEqual(straight, { x: 80, y: 0, kind: "axis" });
  const both = placePoint(edges, 50, 4, 8, [0, 0]);
  assert.deepEqual(both, { x: 48, y: 0, kind: "edge" });
  const face = placePoint(tapeEdges({ walls: [{ id: "h", x1: 0, y1: 0, x2: 100, y2: 0, thickness: 0 }] }), 50, 3, 8);
  assert.deepEqual(face, { x: 50, y: 0, kind: "edge" });
  const far = placePoint(edges, 50, 100, 8);
  assert.deepEqual(far, { x: 50, y: 100, kind: "free" });
});

test("a new corner lines up with vertical and horizontal edges on their own", () => {
  const edges = tapeEdges({
    walls: [
      { id: "v", x1: 40, y1: 0, x2: 40, y2: 100, thickness: 0 },
      { id: "h", x1: 0, y1: 70, x2: 100, y2: 70, thickness: 0 },
    ],
  });
  assert.deepEqual(alignPoint(edges, 44, 66, 8), { x: 40, y: 70, kind: "edge" });
  assert.deepEqual(alignPoint(edges, 10, 66, 8), { x: 10, y: 70, kind: "edge" });
  assert.deepEqual(alignPoint(edges, 10, 10, 8), { x: 10, y: 10, kind: "free" });
});
