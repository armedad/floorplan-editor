import assert from "node:assert/strict";
import { test } from "node:test";

import { parseLength, parseTyped, wallAngle } from "../../floorplan_editor/web/geom.js";

test("lengths and expressions", () => {
  const cases = {
    "10'6\"": 126,
    "10' 6 1/2\"": 126.5,
    "5'6-1/2\"": 66.5,
    "126": 126,
    "3ft + 4in": 40,
    "48/2": 24,
    "(10'-3\")*2": 246,
    "10' - 3\"": 117,
    "6 1/2": 6.5,
    "5'": 60,
    "2'6": 30,
    "-3": -3,
    "30\" + 1/2": 30.5,
    "254mm": 10,
    "1m": 1000 / 25.4,
    "12 ft": 144,
  };
  for (const [text, inches] of Object.entries(cases)) assert.ok(Math.abs(parseLength(text) - inches) < 1e-9, `${text} -> ${parseLength(text)}`);
  for (const bad of ["abc", "12 +", "", "(3", "3)"]) assert.ok(Number.isNaN(parseLength(bad)), bad);
});

test("typed input while drawing", () => {
  const [x, y] = parseTyped("10'<90", [0, 0], [1, 0]);
  assert.ok(Math.abs(x) < 1e-9 && Math.abs(y + 120) < 1e-9);
  assert.deepEqual(parseTyped("24, -12", [5, 5], [1, 0]), [29, -7]);
  assert.deepEqual(parseTyped("36", [0, 0], [0, 2]), [0, 36]);
  assert.deepEqual(parseTyped("3' + 6", [0, 0], [-1, 0]), [-42, 0]);
  assert.equal(parseTyped("nope", [0, 0], [1, 0]), null);
});

test("wall angles read counterclockwise from right", () => {
  assert.equal(wallAngle({ x1: 0, y1: 0, x2: 10, y2: 0 }), 0);
  assert.equal(wallAngle({ x1: 0, y1: 0, x2: 0, y2: -10 }), 90);
  assert.equal(wallAngle({ x1: 0, y1: 0, x2: 0, y2: 10 }), 270);
});
