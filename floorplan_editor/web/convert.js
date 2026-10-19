// Turning traced pieces into what they are: the Convert menu, merging walls into one, wall
// thickness with a face that stays put, and finding stacked walls and stray bits after a trace.

import { api } from "./api.js";
import * as E from "./app.js";
import { formatLength, parseLength, round2 } from "./geom.js";
import { popInput, showMenu } from "./ui.js";

const wallKeys = () => E.selectionInfo().planKeys.filter((k) => k.startsWith("walls:"));
const idsOf = (keys) => keys.map((k) => E.splitKey(k)[1]);
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function shortList(keys) {
  const names = keys.map((k) => E.splitKey(k)[1]);
  return names.length > 6 ? `${names.slice(0, 6).join(", ")}, …` : names.join(", ");
}

// ---------------------------------------------------------------- fixtures sized to the selection

function fixtureTypes() {
  const catalog = E.app.state && E.app.state.ffe_types;
  if (catalog && catalog.length) return catalog.map((item) => item.id);
  return (E.app.state && E.app.state.fixture_types) || Object.keys(E.FIXTURE_SIZES);
}

function fixtureLabel(type) {
  const catalog = E.app.state && E.app.state.ffe_types;
  const hit = catalog && catalog.find((item) => item.id === type);
  return hit ? hit.name : type.replace(/_/g, " ");
}

function sizeMismatch(type, w, h) {
  const [fw, fh] = E.FIXTURE_SIZES[type] || [24, 24];
  const off = (a, b) => Math.abs(Math.log(Math.max(a, 1) / b));
  return Math.min(off(w, fw) + off(h, fh), off(w, fh) + off(h, fw));
}

/** Fixtures are drawn `w` across and `h` deep; turn one 90° when the box runs the other way. */
function rotationFor(type, w, h) {
  const [fw, fh] = E.FIXTURE_SIZES[type] || [24, 24];
  if (fw === fh) return 0;
  return fw > fh === w >= h ? 0 : 90;
}

function showFixtureMenu(x, y) {
  const box = E.selectionBoxIn();
  if (!box) return;
  const w = box[2] - box[0];
  const h = box[3] - box[1];
  const types = fixtureTypes()
    .filter((t) => t !== "generic")
    .sort((a, b) => sizeMismatch(a, w, h) - sizeMismatch(b, w, h))
    .concat("generic");
  showMenu(
    [
      { heading: `FF&E ${formatLength(w)} × ${formatLength(h)}: closest sizes first` },
      ...types.map((type, i) => ({
        label: fixtureLabel(type),
        accel: i < 9 ? String(i + 1) : undefined,
        run: () => convertSelection("fixture", { type, rot: rotationFor(type, w, h) }),
      })),
    ],
    x,
    y,
  );
}

// ---------------------------------------------------------------- converting

export async function convertSelection(to, extra = {}) {
  const keys = E.selectionInfo().planKeys;
  if (!keys.length) return;
  const state = await E.call("/api/op", { op: { op: "convert", items: keys.map(E.splitKey), to, ...extra } });
  if (!state) return;
  const gone = keys.filter((k) => !E.getObject(k));
  const made = state.ref ? E.describeKey(`${state.ref[0]}:${state.ref[1]}`) : to;
  const from = gone.length ? ` in place of ${shortList(gone)}` : "";
  const tip = to === "fixture" ? " R turns it; Enter types its size." : to === "door" ? " S and H flip it." : "";
  E.message(`Made ${made}${from}.${tip} Ctrl+Z undoes it.`);
}

function askLabelText(x, y) {
  popInput({
    x,
    y,
    label: "Label text (what the selected pieces spell)",
    hint: "Enter replaces them with a label sized to fit.",
    onCommit: (text) => text.trim() && convertSelection("label", { text: text.trim() }),
  });
}

export function showConvertMenu(x, y) {
  const info = E.selectionInfo();
  const keys = info.planKeys;
  if (!keys.length) return E.message("Select the pieces to convert first (drag across them).", true);
  if (x === undefined) [x, y] = E.selectionAnchor();
  const walls = keys.filter((k) => k.startsWith("walls:"));
  const allWalls = walls.length === keys.length;
  const what = keys.length === 1 ? E.describeKey(keys[0]) : `these ${keys.length}`;
  showMenu(
    [
      { heading: `Turn ${what} into` },
      allWalls && walls.length > 1
        ? { label: "One wall (merge them)", accel: "w", run: () => mergeSelectedWalls() }
        : { label: "A wall along the long side", accel: "w", run: () => convertSelection("wall") },
      { label: "A door in the doorway", accel: "d", title: "Fills the gap between two walls in line, or goes on the nearest wall", run: () => convertSelection("door") },
      { label: "A window", accel: "n", run: () => convertSelection("window") },
      { label: "A fixture…", accel: "x", run: () => showFixtureMenu(x, y) },
      { label: "A box counter", accel: "c", run: () => convertSelection("counter") },
      { label: "A counter outline", accel: "p", run: () => convertSelection("outline") },
      { label: "A label…", accel: "t", run: () => askLabelText(x, y) },
      { label: "An erase region (white out the sheet)", accel: "e", run: () => convertSelection("erase") },
    ],
    x,
    y,
  );
}

// ---------------------------------------------------------------- walls

export async function mergeSelectedWalls() {
  const walls = wallKeys();
  if (walls.length < 2) return E.message("Select two or more parallel walls to merge.", true);
  const state = await E.call("/api/op", { op: { op: "merge_walls", ids: idsOf(walls) } });
  if (!state || !state.ref) return;
  const wall = E.getObject(`walls:${state.ref[1]}`);
  E.message(`Merged ${walls.length} walls into ${state.ref[1]}, ${formatLength(wall.thickness)} thick. Thickness… (Shift+T) sets an exact thickness.`);
}

export async function mergeStacked() {
  const walls = wallKeys();
  const op = walls.length >= 2 ? { op: "merge_stacked", ids: idsOf(walls), max_gap: 6 } : { op: "merge_stacked" };
  const state = await E.call("/api/op", { op });
  if (!state) return;
  const n = state.refs ? state.refs.length : 1;
  E.message(`Merged ${plural(n, "group")} of walls into single walls (now selected). Ctrl+Z undoes it.`);
}

function faceChoices(walls) {
  const across = walls.every((w) => Math.abs(w.x2 - w.x1) >= Math.abs(w.y2 - w.y1));
  const upDown = walls.every((w) => Math.abs(w.x2 - w.x1) < Math.abs(w.y2 - w.y1));
  if (across) return [["center", "middle"], ["up", "top face"], ["down", "bottom face"]];
  if (upDown) return [["center", "middle"], ["left", "left face"], ["right", "right face"]];
  return [["center", "middle"], ["up", "top"], ["down", "bottom"], ["left", "left"], ["right", "right"]];
}

export function thicknessSelection() {
  const keys = wallKeys();
  if (!keys.length) return E.message("Select one or more walls first.", true);
  const walls = keys.map((k) => E.getObject(k));
  const same = walls.every((w) => w.thickness === walls[0].thickness);
  const choices = faceChoices(walls);
  const keep = choices.some(([v]) => v === E.app.opts.thicknessKeep) ? E.app.opts.thicknessKeep : "center";
  const [x, y] = E.selectionAnchor();
  popInput({
    x,
    y,
    label: `Thickness of ${keys.length === 1 ? E.describeKey(keys[0]) : `${keys.length} walls`}${same ? "" : " (now mixed)"}`,
    value: same ? formatLength(walls[0].thickness) : "",
    hint: "Inches, 4 1/2\", or math. Pick the face that stays put; the wall grows away from it.",
    choices,
    choice: keep,
    onCommit: (text, face) => {
      const v = parseLength(text);
      if (!(v > 0)) return E.message(`Can't use “${text}” as a thickness.`, true);
      E.app.opts.thicknessKeep = face;
      E.send({ op: "set_wall_thickness", ids: idsOf(keys), thickness: round2(v), keep: face || "center" });
    },
  });
}

// ---------------------------------------------------------------- finding what the tracer got wrong

export async function selectFound(what) {
  if (!E.app.plan) return;
  const walls = wallKeys();
  let groups;
  try {
    ({ groups } = await api("/api/analyze", { what, ids: walls.length ? idsOf(walls) : null }));
  } catch (err) {
    return E.message(err.message, true);
  }
  const keys = groups.flatMap((g) => g.ids.map((id) => `walls:${id}`));
  const where = walls.length ? " in the selection" : "";
  if (!keys.length) {
    return E.message(what === "stray" ? `No stray bits found${where}.` : `No stacked or broken walls found${where}.`);
  }
  E.selectKeys(keys);
  if (what === "stray") {
    E.message(`Selected ${plural(keys.length, "stray bit")} in ${plural(groups.length, "cluster")} (traced letters and symbol pieces). Delete removes them; Convert… (Shift+C) makes a label or erase region.`);
  } else {
    E.message(`Selected ${plural(groups.length, "group")} of walls that are one wall (${keys.length} walls). Merge stacked (Shift+J) merges each group.`);
  }
}
