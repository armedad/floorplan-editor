// Floors: one file can hold a whole building. The bar over the drawing flips between floors, adds a
// floor from another plan or a traced image, and exports every floor stacked in one JPEG.

import { api, readFileBase64 } from "./api.js";
import { app, call, message, toast } from "./app.js";
import { $, el, popInput, showMenu } from "./ui.js";

const IMAGE = /\.(png|jpe?g)$/i;
const PLAN_OR_IMAGE = /\.(json|png|jpe?g)$/i;

export const floors = () => (app.state && app.state.floors) || [];
export const isBuilding = () => !!(app.state && app.state.building);

export function switchFloor(index) {
  if (!app.state || index === app.state.floor) return;
  if (index < 0 || index >= floors().length) return;
  return call("/api/floor", { index }, { selectRef: false });
}

/** Up is the next floor in the file: floors run bottom to top. */
export function stepFloor(by) {
  const n = floors().length;
  if (n < 2) return message("This file has one floor. + Floor adds another.");
  const to = app.state.floor + by;
  if (to < 0 || to >= n) return message(by > 0 ? "This is the top floor." : "This is the bottom floor.");
  switchFloor(to);
}

async function floorsCall(body, busyText) {
  if (busyText) message(busyText);
  const state = await call("/api/floors", body, { selectRef: false });
  if (state && state.floors_changed && state.floors_changed.message) message(state.floors_changed.message);
  return state;
}

function centerOfStage() {
  const r = $("stage").getBoundingClientRect();
  return [r.left + r.width / 2, r.top + 80];
}

export function renameFloor(index = app.state.floor) {
  const floor = floors()[index];
  if (!floor) return;
  const [x, y] = centerOfStage();
  popInput({
    x,
    y,
    label: "Floor name",
    value: floor.name,
    hint: "Like Main floor, Upper floor, or Basement. Enter renames it.",
    onCommit: (text) => text.trim() && floorsCall({ action: "rename", index, name: text.trim() }),
  });
}

export function renameBuilding() {
  const [x, y] = centerOfStage();
  popInput({
    x,
    y,
    label: "Building name",
    value: (app.state.building && app.state.building.name) || "",
    hint: "The title on the exported JPEG. Enter renames it.",
    onCommit: (text) => text.trim() && floorsCall({ action: "building", name: text.trim() }),
  });
}

export function moveFloor(index, to) {
  if (to < 0 || to >= floors().length) return;
  floorsCall({ action: "move", index, to });
}

export function deleteFloor(index = app.state.floor) {
  const floor = floors()[index];
  if (!floor) return;
  if (floors().length === 1) return message("That's the only floor.", true);
  if (!confirm(`Remove ${floor.name} from this file? Undo (Ctrl+Z) brings it back until you save.`)) return;
  floorsCall({ action: "delete", index });
}

async function addFromPicker(kind) {
  message(kind === "folder" ? "Pick a plan folder in the Open dialog…" : "Pick a floor plan image or a plan .json in the Open dialog… (it can open behind this window)");
  try {
    const result = await api("/api/pick", { kind, path_only: true });
    if (result.cancelled) return message("");
    const tracing = IMAGE.test(result.path);
    await floorsCall({ action: "add", path: result.path }, tracing ? "Tracing the new floor…" : "Adding the floor…");
  } catch (err) {
    if (/no file dialog/i.test(err.message)) {
      message("No system dialog here; choose the file in the browser instead.");
      $("floor-upload").click();
    } else message(err.message, true);
  }
}

export async function addFromFiles(fileList) {
  const files = [...fileList].filter((f) => PLAN_OR_IMAGE.test(f.name));
  if (!files.length) return message("Add a floor from a .png/.jpg floor plan image or a plan .json.", true);
  const payload = await Promise.all(files.map(async (f) => ({ filename: f.name, data: await readFileBase64(f) })));
  const tracing = !files.some((f) => /\.json$/i.test(f.name));
  await floorsCall({ action: "add", files: payload }, tracing ? `Tracing ${files.map((f) => f.name).join(", ")}…` : "Adding the floor…");
}

function typedPath() {
  const path = prompt("Path to a floor plan image, a plan .json, or a plan folder", app.state ? app.state.folder : "");
  if (path && path.trim()) floorsCall({ action: "add", path: path.trim().replace(/^"|"$/g, "") }, "Adding the floor…");
}

export function showAddMenu(anchor) {
  const r = anchor ? anchor.getBoundingClientRect() : { left: centerOfStage()[0], bottom: centerOfStage()[1] };
  showMenu(
    [
      { heading: "Add a floor to this file" },
      {
        label: "From a floor plan image or a plan .json…",
        title: "An image is traced into walls; a plan .json brings its floor (or every floor of a building)",
        accel: "i",
        run: () => addFromPicker("file"),
      },
      { label: "From a plan folder (its newest plan)…", accel: "f", run: () => addFromPicker("folder") },
      { label: "Choose in the browser…", title: "Upload from this browser (when the system dialog can't open)", run: () => $("floor-upload").click() },
      { label: "Type a path…", run: typedPath },
    ],
    r.left,
    r.bottom + 2,
  );
}

function showFloorMenu(index, x, y) {
  const n = floors().length;
  showMenu(
    [
      { label: "Rename…", accel: "r", run: () => renameFloor(index) },
      { label: "Move up a floor", disabled: index >= n - 1, run: () => moveFloor(index, index + 1) },
      { label: "Move down a floor", disabled: index <= 0, run: () => moveFloor(index, index - 1) },
      "-",
      { label: "Add a floor…", run: () => showAddMenu(null) },
      ...(isBuilding() ? [{ label: "Rename the building…", run: renameBuilding }] : []),
      "-",
      { label: "Remove this floor", disabled: n < 2, run: () => deleteFloor(index) },
    ],
    x,
    y,
  );
}

export async function exportJpeg() {
  if (!app.plan) return;
  message("Exporting every floor…");
  const state = await call("/api/export", {}, { selectRef: false });
  const out = state && state.exported;
  if (!out) return;
  message(`Exported ${out.file}: ${out.floors} floor${out.floors === 1 ? "" : "s"}, ${out.width} × ${out.height} px. Opening it in a new tab.`);
  window.open(`/api/file/${encodeURIComponent(out.file)}?t=${Date.now()}`, "_blank");
}

export function saveToFolder() {
  if (!app.plan) return;
  const [x, y] = centerOfStage();
  const s = app.state;
  const suggested = ((s.building && s.building.name) || (s.plan && s.plan.name) || "").toLowerCase().replace(/[^\w]+/g, "-").replace(/^-|-$/g, "");
  popInput({
    x,
    y,
    label: "Save to a new folder",
    value: suggested,
    hint: "A name makes a folder next to this one; or give a full path. It gets plan.json with every floor, plus their sheet images.",
    onCommit: async (text) => {
      if (!text.trim()) return;
      const state = await call("/api/save", { mode: "folder", path: text.trim().replace(/^"|"$/g, "") }, { selectRef: false });
      if (!state) toast("Save failed; the reason is in the status bar", true);
    },
  });
}

let barKey = "";

export function renderFloorBar() {
  const bar = $("floorbar");
  const s = app.state;
  if (!s || !s.plan) {
    bar.hidden = true;
    barKey = "";
    return;
  }
  const list = floors();
  const key = JSON.stringify([s.building, list, s.floor]);
  bar.hidden = false;
  if (key === barKey) return;
  barKey = key;
  const tabs = [...list].reverse().map((f) =>
    el(
      "button",
      {
        type: "button",
        role: "tab",
        class: f.index === s.floor ? "floor-tab active" : "floor-tab",
        "aria-selected": String(f.index === s.floor),
        title: `${f.name}${f.sheet ? ` (sheet ${f.sheet})` : ""}. Right-click to rename, move, or remove. PageUp and PageDown flip floors.`,
        on: {
          click: () => switchFloor(f.index),
          dblclick: () => renameFloor(f.index),
          contextmenu: (e) => {
            e.preventDefault();
            showFloorMenu(f.index, e.clientX, e.clientY);
          },
        },
      },
      f.name,
    ),
  );
  bar.replaceChildren(
    ...(s.building
      ? [el("button", { type: "button", class: "floor-building link", title: "Rename the building (the export's title)", on: { click: renameBuilding } }, s.building.name || "Building")]
      : []),
    el("div", { class: "floor-tabs", role: "tablist", "aria-label": "floors, top floor first" }, tabs),
    el("button", { type: "button", class: "floor-add", title: "Add a floor from another plan or a floor plan image", on: { click: (e) => showAddMenu(e.currentTarget) } }, "+ Floor"),
    el("button", { type: "button", class: "floor-export", title: "Every floor, drawn clean at one scale, stacked top floor first", on: { click: exportJpeg } }, "Export JPEG"),
  );
}

export function initFloors() {
  $("floor-upload").addEventListener("change", (e) => {
    if (e.target.files.length) addFromFiles(e.target.files);
    e.target.value = "";
  });
  $("save-folder").addEventListener("click", saveToFolder);
}
