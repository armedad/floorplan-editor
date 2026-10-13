import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { clearances, dimsFor, openingGaps, opForDim, parallelGaps } from "../../floorplan_editor/web/measure.js";

const plan = JSON.parse(readFileSync(new URL("../../examples/sample/plan.json", import.meta.url), "utf8"));
const dim = (kind, id, key) => dimsFor(plan, kind, id).find((d) => d.key === key);

test("door gaps run to the faces of the walls at each side", () => {
  const door = plan.openings.find((o) => o.id === "bath-door");
  const g = openingGaps(plan, door);
  assert.equal(g.gapStart, 18);
  assert.equal(g.gapEnd, 49);
  assert.deepEqual(opForDim(plan, "openings", "bath-door", dim("openings", "bath-door", "gap-end"), 40), { op: "update", kind: "openings", id: "bath-door", fields: { offset: 29 } });
  assert.deepEqual(opForDim(plan, "openings", "bath-door", dim("openings", "bath-door", "width"), 32, "end").fields, { offset: 16, width: 32 });
});

test("a wall measures clear space to the parallel walls on each side", () => {
  const wall = plan.walls.find((w) => w.id === "bath-w");
  const gaps = Object.fromEntries(parallelGaps(plan, wall).map((g) => [g.other.id, g.clear]));
  assert.deepEqual(gaps, { "ext-e": 95, "ext-w": 295 });
  const toEast = dimsFor(plan, "walls", "bath-w").find((d) => d.gap && d.value === 95);
  assert.deepEqual(opForDim(plan, "walls", "bath-w", toEast, 90), { op: "move", kind: "walls", id: "bath-w", dx: 5, dy: 0, join: true });
  assert.deepEqual(opForDim(plan, "walls", "bath-w", dim("walls", "bath-w", "length"), 100, "end"), { op: "resize_wall", id: "bath-w", length: 100, anchor: "end", join: true });
  assert.match(dim("walls", "bath-w", "length").anchors.start, /top/);
});

test("boxes measure clearance to square-on walls and resize from an anchor", () => {
  const toilet = plan.fixtures.find((f) => f.id === "toilet");
  const gaps = clearances(plan, [toilet.x - 10, toilet.y - 14, toilet.x + 10, toilet.y + 14]);
  assert.deepEqual(Object.fromEntries(Object.values(gaps).map((g) => [g.dir, g.gap])), { left: 8, up: 3, right: 67, down: 74 });
  assert.deepEqual(opForDim(plan, "fixtures", "toilet", dim("fixtures", "toilet", "gap-left"), 15), { op: "move", kind: "fixtures", id: "toilet", dx: 7, dy: 0, join: false });
  assert.deepEqual(opForDim(plan, "fixtures", "toilet", dim("fixtures", "toilet", "w"), 24, "start").fields, { w: 24, x: 322, y: 20 });
  const box = opForDim(plan, "counters", "peninsula", dim("counters", "peninsula", "h"), 50, "end");
  assert.deepEqual(box.fields, { h: 50, y: 70 });
});

test("outline counters measure each edge", () => {
  const edges = dimsFor(plan, "counters", "kitchen-l").filter((d) => d.key.startsWith("edge-"));
  assert.equal(edges.length, 6);
  assert.equal(edges[0].value, 137);
  assert.deepEqual(opForDim(plan, "counters", "kitchen-l", edges[0], 120), { op: "move_vertex", id: "kitchen-l", index: 1, x: 123, y: 3 });
});
