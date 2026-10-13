// Floor plan editor: draws the server's scene as SVG and turns pointer gestures and typed values into edit operations.

import { api } from "./api.js";
import { handleKey, initCommands, renderActionBar, showContextMenu } from "./commands.js";
import { commentsMarkup, renderCommentsPanel, revealComments } from "./comments.js";
import { addFlag, deleteFlags, editFlagNote, flagsMarkup, getFlag, moveFlag, renderFlagsPanel } from "./flags.js";
import {
  Canvas,
  arcPath,
  formatLength,
  hitArea,
  indexScene,
  lockAxis,
  parseLength,
  parseTyped,
  pick,
  project,
  rotate,
  round2,
  snapPoint,
  wallLength,
} from "./geom.js";
import { dimsFor, opForDim } from "./measure.js";
import { initOpen, renderEmpty } from "./open.js";
import { loadSheetInk, placeLabels } from "./placement.js";
import { initPanels, renderFiles, renderProps } from "./panels.js";
import { hideTip, initTips } from "./help.js";
import { $, popInput, popOpen } from "./ui.js";

const svg = $("svg");
const world = $("world");
const sheetEl = $("sheet");
const paper = $("paper");
const planLayer = $("plan-layer");
const eraseLayer = $("erase-layer");
const traceLayer = $("trace-layer");
const commentLayer = $("comment-layer");
const flagLayer = $("flag-layer");
const idLayer = $("id-layer");
const uiLayer = $("ui-layer");

export const FIXTURE_SIZES = {
  refrigerator: [36, 30],
  range: [30, 26],
  oven: [30, 26],
  dishwasher: [24, 24],
  toilet: [20, 28],
  tub: [60, 30],
  soaking_tub: [66, 32],
  sink: [30, 20],
  washer: [27, 28],
  dryer: [27, 28],
  washer_dryer: [27, 30],
  stool: [16, 16],
  generic: [24, 24],
};

const RED = "#c40000";
const RED_FILL = "rgba(196,0,0,0.43)";
const TOOLS = new Set(["select", "wall", "door", "window", "counter", "polygon", "fixture", "label", "erase", "calibrate", "flag"]);
const TYPABLE = /^[0-9.'"<(\-]$/;

export const app = {
  state: null,
  plan: null,
  canvas: null,
  objects: new Map(),
  sel: null,
  selection: new Set(),
  clipboard: null,
  tool: "select",
  view: { x: 20, y: 20, z: 1 },
  show: { sheet: true, opacity: 0.55, erase: true, snap: true, grid: true, dims: true, ids: false, comments: true },
  opts: { thickness: 4, doorWidth: 30, windowWidth: 36, fixtureType: "refrigerator", fixtureRot: 0, labelText: "", anchor: {}, thicknessKeep: "center" },
  drag: null,
  draft: null,
  cursor: null,
  hoverEnd: null,
  handles: [],
  dims: [],
  // Screen-pixel offsets of measurement labels from their spot on the line, by "kind:id:dimKey":
  // where the user dragged them, and where they were last placed automatically.
  dimNudge: new Map(),
  dimAuto: new Map(),
  sheetKey: "",
  sheetVersion: 0,
  fittedFor: null,
  traceWalls: null,
  spaceDown: false,
  busy: false,
  syncing: false,
  serverVersion: 0,
  uiQueue: [],
};

// ---------------------------------------------------------------- server calls

export function message(text, isError = false) {
  const node = $("status-msg");
  node.textContent = text || "";
  node.className = isError ? "error" : "";
  node.title = text || "";
}

export async function call(path, body, extra = {}) {
  try {
    app.busy = true;
    const state = await api(path, body);
    setState(state, extra);
    return state;
  } catch (err) {
    message(err.message, true);
    if (err.errors) renderProps(app, err.errors);
    return null;
  } finally {
    app.busy = false;
    syncFromServer();
  }
}

// The document can change from outside this page. Replacing state mid-drag or mid-request
// would drop that work, so calls and pointerup retry the sync once idle.
async function syncFromServer() {
  if (app.drag || app.busy || app.syncing) return;
  if (app.state && app.state.version >= app.serverVersion) return drainUi();
  app.syncing = true;
  try {
    const before = app.state;
    const state = await api("/api/state");
    setState(state, { selectRef: false });
    if (before && (before.file !== state.file || before.folder !== state.folder)) {
      message(`Opened ${state.file || state.folder} from another client.`);
    }
  } catch (err) {
    message(err.message, true);
  } finally {
    app.syncing = false;
  }
  syncFromServer();
}

function drainUi() {
  while (app.uiQueue.length) runUiCommand(app.uiQueue.shift());
}

function runUiCommand(cmd) {
  const set = (id, value) => {
    if (value === undefined || value === null) return;
    const node = $(id);
    if (node.type === "checkbox") node.checked = !!value;
    else node.value = value;
    node.dispatchEvent(new Event(node.type === "checkbox" ? "change" : "input"));
  };
  switch (cmd.action) {
    case "select":
      if (Array.isArray(cmd.keys)) selectKeys(cmd.keys, { zoom: !!cmd.zoom });
      else {
        select(cmd.key && getObject(cmd.key) ? cmd.key : null);
        if (cmd.zoom && app.sel) zoomToSelection();
      }
      break;
    case "zoom":
      if (cmd.key && getObject(cmd.key)) {
        select(cmd.key);
        zoomToSelection();
      } else if (cmd.box && app.canvas) {
        const [x0, y0] = app.canvas.pt(cmd.box[0], cmd.box[1]);
        const [x1, y1] = app.canvas.pt(cmd.box[2], cmd.box[3]);
        zoomToBox([Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)]);
      }
      break;
    case "fit":
      fitView();
      break;
    case "tool":
      if (TOOLS.has(cmd.tool)) setTool(cmd.tool);
      break;
    case "view":
      set("show-sheet", cmd.sheet);
      set("show-erase", cmd.erase);
      set("snap", cmd.snap);
      set("grid", cmd.grid);
      set("sheet-opacity", cmd.opacity);
      if (cmd.panel !== undefined && cmd.panel === document.body.classList.contains("side-hidden")) toggleSide();
      break;
    case "message":
      message(cmd.text || "", !!cmd.error);
      break;
    case "reload":
      location.reload();
      break;
  }
}

function followServer() {
  const events = new EventSource("/api/events");
  events.addEventListener("state", (e) => {
    app.serverVersion = JSON.parse(e.data).version;
    syncFromServer();
  });
  events.addEventListener("ui", (e) => {
    app.uiQueue.push(JSON.parse(e.data));
    syncFromServer();
  });
}

export async function send(op, { selectRef = true } = {}) {
  const state = await call("/api/op", { op }, { selectRef });
  return state ? state.ref : undefined;
}

const keyOf = (ref) => `${ref[0]}:${ref[1]}`;

export function setState(state, { selectRef = true } = {}) {
  app.state = state;
  app.plan = state.plan;
  app.canvas = state.plan ? new Canvas(state.plan) : null;
  app.objects = state.plan ? indexScene(state.scene) : new Map();
  if (selectRef && state.refs) setSelection(state.refs.map(keyOf));
  else if (selectRef && state.ref) setSelection([keyOf(state.ref)]);
  else if (state.ref === null && "ref" in state) setSelection([...app.selection].filter((k) => k.startsWith("flags:")));
  if (state.flag) setSelection([`flags:${state.flag}`]);
  setSelection([...app.selection].filter((k) => getObject(k)), app.sel);
  $("empty").hidden = !!state.plan;
  if (!state.plan) renderEmpty();
  if (state.plan) {
    const fitKey = `${state.folder}|${app.canvas.width}x${app.canvas.height}`;
    if (app.fittedFor !== fitKey) fitView();
    app.fittedFor = fitKey;
  }
  renderAll();
  renderFiles(app);
  renderProps(app);
  renderFlagsPanel();
  renderCommentsPanel();
  if (state.saved) {
    const warn = state.saved.warnings.length ? ` (${state.saved.warnings.join("; ")})` : "";
    message(`Saved ${state.saved.files.join(", ")}${warn}`);
  }
}

// ---------------------------------------------------------------- objects and selection

export function splitKey(key) {
  const [kind, ...rest] = key.split(":");
  const id = rest.join(":");
  return [kind, kind === "labels" ? Number(id) : id];
}

export function getObject(key) {
  if (!app.plan || !key) return null;
  const [kind, id] = splitKey(key);
  if (kind === "flags") return getFlag(id);
  const list = app.plan[kind] || [];
  if (kind === "labels") return list[id] || null;
  return list.find((item) => item.id === id) || null;
}

const named = (type, id) => (String(id).toLowerCase().startsWith(type.split(" ")[0]) ? String(id) : `${type} ${id}`);

export function describeKey(key) {
  const [kind, id] = splitKey(key);
  const o = getObject(key);
  if (kind === "walls") return named("wall", id);
  if (kind === "openings") return named(o ? o.type : "opening", id);
  if (kind === "counters") return named(o && o.points ? "counter outline" : "counter", id);
  if (kind === "fixtures") return named(o ? o.type.replace(/_/g, " ") : "fixture", id);
  if (kind === "labels") return o ? `label “${o.text}”` : `label ${id}`;
  if (kind === "erase") return `erase region ${id}`;
  if (kind === "flags") return `flag ${id}`;
  return key;
}

function setSelection(keys, primary = null) {
  app.selection = new Set(keys);
  app.sel = primary && app.selection.has(primary) ? primary : keys.length ? keys[keys.length - 1] : null;
  reportSelection();
}

let reported = "";
let reportTimer = 0;

/** Tell the server what is selected, so Cursor can act on "what I selected". */
function reportSelection() {
  clearTimeout(reportTimer);
  reportTimer = setTimeout(() => {
    const keys = [...app.selection];
    const text = keys.join("|");
    if (text === reported) return;
    reported = text;
    api("/api/selection", { keys }).catch(() => {
      reported = "";
    });
  }, 150);
}

export function selectKeys(keys, { zoom = false } = {}) {
  setSelection(keys.filter((k) => getObject(k)));
  afterSelect();
  if (zoom && app.selection.size) zoomToSelection();
}

/** The selection's box in plan inches. */
export function selectionBoxIn() {
  const box = unionBox(selectionInfo().planKeys);
  if (!box || !app.canvas) return null;
  return [...app.canvas.toIn(box[0], box[1]), ...app.canvas.toIn(box[2], box[3])];
}

/** A screen point just below the selection, for menus and popovers about it. */
export function selectionAnchor() {
  const box = unionBox(selectionInfo().planKeys);
  if (!box) return stageCenter();
  const [sx, sy] = toScreen((box[0] + box[2]) / 2, box[3]);
  const r = svg.getBoundingClientRect();
  return [Math.max(r.left + 40, Math.min(r.right - 40, sx)), Math.max(r.top + 40, Math.min(r.bottom - 120, sy + 14))];
}

export function selectionInfo() {
  const keys = [...app.selection];
  const kinds = new Set(keys.map((k) => k.split(":")[0]));
  const single = keys.length === 1 ? keys[0] : null;
  const [kind, id] = single ? splitKey(single) : [kinds.size === 1 ? [...kinds][0] : null, null];
  return {
    plan: !!app.plan,
    keys,
    n: keys.length,
    kinds,
    kind,
    id,
    obj: single ? getObject(single) : null,
    single,
    planKeys: keys.filter((k) => !k.startsWith("flags:")),
    flagKeys: keys.filter((k) => k.startsWith("flags:")),
  };
}

export function selectionTitle() {
  const info = selectionInfo();
  if (!info.n) return "";
  if (info.n > 1) return `${info.n} selected`;
  const base = describeKey(info.single);
  const o = info.obj;
  if (!o) return base;
  if (info.kind === "walls") return `${base} · ${formatLength(wallLength(o))}`;
  if (info.kind === "openings") return `${base} · ${formatLength(o.width)}`;
  if (o.w !== undefined && info.kind !== "flags") return `${base} · ${formatLength(o.w)} × ${formatLength(o.h)}`;
  return base;
}

export function select(key, { toggle = false, add = false } = {}) {
  if (!key) setSelection([]);
  else if (toggle) {
    const keys = new Set(app.selection);
    if (keys.has(key)) keys.delete(key);
    else keys.add(key);
    setSelection([...keys], keys.has(key) ? key : null);
  } else if (add) setSelection([...app.selection, key], key);
  else setSelection([key]);
  afterSelect();
}

export function selectAll() {
  if (!app.plan) return;
  setSelection([...app.objects.keys()]);
  afterSelect();
  message(`Selected ${app.selection.size} objects. Delete, Duplicate, Move…, or drag them together.`);
}

function afterSelect() {
  markSelected();
  renderMarkers();
  renderIds();
  renderUI();
  renderProps(app);
  renderFlagsPanel();
  renderCommentsPanel();
}

function markSelected() {
  for (const node of document.querySelectorAll("#plan-layer [data-key], #erase-layer [data-key]")) {
    const on = app.selection.has(node.dataset.key);
    node.classList.toggle("selected", on);
    if (on) node.setAttribute("aria-label", `selected ${node.dataset.key}`);
    else node.removeAttribute("aria-label");
  }
}

function eraseEntries() {
  if (!app.plan || !app.show.erase) return [];
  return (app.plan.erase || []).map((r) => {
    const [x0, y0] = app.canvas.pt(r.x, r.y);
    const [x1, y1] = app.canvas.pt(r.x + r.w, r.y + r.h);
    return {
      ...hitArea(`erase:${r.id}`, [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]),
      box: [x0, y0, x1, y1],
    };
  });
}

function doorwayEntries() {
  if (!app.plan) return [];
  const walls = new Map((app.plan.walls || []).map((w) => [w.id, w]));
  const entries = [];
  for (const o of app.plan.openings || []) {
    const w = walls.get(o.wall);
    const len = w && wallLength(w);
    if (!len) continue;
    const ux = (w.x2 - w.x1) / len;
    const uy = (w.y2 - w.y1) / len;
    const nx = (-uy * w.thickness) / 2;
    const ny = (ux * w.thickness) / 2;
    const a = [w.x1 + ux * o.offset, w.y1 + uy * o.offset];
    const b = [w.x1 + ux * (o.offset + o.width), w.y1 + uy * (o.offset + o.width)];
    const pts = [
      [a[0] + nx, a[1] + ny],
      [b[0] + nx, b[1] + ny],
      [b[0] - nx, b[1] - ny],
      [a[0] - nx, a[1] - ny],
    ].map(([x, y]) => app.canvas.pt(x, y));
    entries.push(hitArea(`openings:${o.id}`, pts));
  }
  return entries;
}

/** Bounding box of one object in sheet pixels. */
export function keyBox(key) {
  if (key.startsWith("flags:")) {
    const f = getFlag(key.slice(6));
    if (!f) return null;
    const k = 1 / app.view.z;
    const [x, y] = f.px;
    const [w, h] = f.size || [0, 0];
    return [x - 11 * k, y - 30 * k, x + Math.max(w, 11 * k), y + h];
  }
  const entry = app.objects.get(key) || eraseEntries().find((e) => e.key === key);
  return entry ? entry.box : null;
}

function unionBox(keys) {
  let box = null;
  for (const key of keys) {
    const b = keyBox(key);
    if (!b) continue;
    box = box ? [Math.min(box[0], b[0]), Math.min(box[1], b[1]), Math.max(box[2], b[2]), Math.max(box[3], b[3])] : [...b];
  }
  return box;
}

// ---------------------------------------------------------------- drawing

const esc = (t) => String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const f2 = (v) => v.toFixed(2);

function sheetOn() {
  return app.show.sheet && app.state && app.state.sheet;
}

function colors(op) {
  if (!sheetOn()) return { stroke: op.stroke, fill: op.fill };
  if (op.op === "text") return { fill: RED };
  return { stroke: RED, fill: op.role === "wall" ? RED_FILL : null };
}

function opMarkup(op) {
  const c = colors(op);
  if (op.op === "polygon") {
    const pts = op.points.map(([x, y]) => `${f2(x)},${f2(y)}`).join(" ");
    const cls = op.role === "wall" ? ' class="wall"' : "";
    return `<polygon${cls} points="${pts}" fill="${c.fill || "none"}" stroke="${c.stroke}" stroke-width="${op.width}" stroke-linejoin="miter"/>`;
  }
  if (op.op === "line") {
    return `<line x1="${f2(op.a[0])}" y1="${f2(op.a[1])}" x2="${f2(op.b[0])}" y2="${f2(op.b[1])}" stroke="${c.stroke}" stroke-width="${op.width}"/>`;
  }
  if (op.op === "arc") return `<path d="${arcPath(op)}" fill="none" stroke="${c.stroke}" stroke-width="${op.width}"/>`;
  return `<text x="${f2(op.at[0])}" y="${f2(op.at[1])}" fill="${c.fill}" font-size="${op.size.toFixed(1)}" text-anchor="middle" dominant-baseline="middle">${esc(op.text)}</text>`;
}

function renderSheet() {
  const s = app.state;
  const show = sheetOn();
  if (app.plan) {
    paper.setAttribute("width", app.canvas.width);
    paper.setAttribute("height", app.canvas.height);
  }
  paper.style.display = app.plan ? "" : "none";
  if (!s || !s.sheet) {
    sheetEl.style.display = "none";
    return;
  }
  const key = `${s.folder}|${s.sheet.name}|${s.sheet.mtime}|${JSON.stringify(app.plan ? app.plan.erase || [] : [])}|${app.plan ? JSON.stringify(app.plan.canvas) : ""}`;
  if (key !== app.sheetKey) {
    app.sheetKey = key;
    app.sheetVersion += 1;
    sheetEl.setAttribute("href", `/api/sheet?v=${app.sheetVersion}`);
    loadSheetInk(`/api/sheet?v=${app.sheetVersion}`, s.sheet.width, () => renderUI());
  }
  sheetEl.setAttribute("width", s.sheet.width);
  sheetEl.setAttribute("height", s.sheet.height);
  sheetEl.style.display = show ? "" : "none";
  sheetEl.setAttribute("opacity", app.show.opacity);
}

function renderPlan() {
  const parts = [];
  for (const entry of app.objects.values()) {
    const on = app.selection.has(entry.key);
    const sel = on ? ` selected" aria-label="selected ${esc(entry.key)}` : "";
    parts.push(`<g class="obj${sel}" data-key="${esc(entry.key)}">${entry.ops.map(opMarkup).join("")}</g>`);
  }
  planLayer.innerHTML = parts.join("");
}

function renderErase() {
  if (!app.plan || !app.show.erase) {
    eraseLayer.innerHTML = "";
    return;
  }
  eraseLayer.innerHTML = eraseEntries()
    .map(({ key, box: [x0, y0, x1, y1] }) => {
      const sel = app.selection.has(key) ? ` selected" aria-label="selected ${esc(key)}` : "";
      return `<rect class="erase-rect${sel}" data-key="${esc(key)}" x="${f2(x0)}" y="${f2(y0)}" width="${f2(x1 - x0)}" height="${f2(y1 - y0)}" vector-effect="non-scaling-stroke"/>`;
    })
    .join("");
}

export function renderTrace() {
  if (!app.traceWalls || !app.plan) {
    traceLayer.innerHTML = "";
    return;
  }
  const c = app.canvas;
  traceLayer.innerHTML = app.traceWalls
    .map((w) => {
      const [x1, y1] = c.pt(w.x1, w.y1);
      const [x2, y2] = c.pt(w.x2, w.y2);
      const t = w.thickness * c.scale;
      const horizontal = Math.abs(y2 - y1) < Math.abs(x2 - x1);
      const x = horizontal ? Math.min(x1, x2) : x1 - t / 2;
      const y = horizontal ? y1 - t / 2 : Math.min(y1, y2);
      const w2 = horizontal ? Math.abs(x2 - x1) : t;
      const h2 = horizontal ? t : Math.abs(y2 - y1);
      return `<rect class="trace-wall" x="${f2(x)}" y="${f2(y)}" width="${f2(w2)}" height="${f2(h2)}" vector-effect="non-scaling-stroke"/>`;
    })
    .join("");
}

function renderMarkers() {
  commentLayer.innerHTML = commentsMarkup();
  flagLayer.innerHTML = flagsMarkup();
}

const ID_LIMIT = 900;

/** Each object's id written on it, for naming objects to Cursor. Only what is in view, at a fixed screen size. */
function renderIds() {
  if (!app.plan || !app.show.ids) {
    idLayer.innerHTML = "";
    return;
  }
  const rect = svg.getBoundingClientRect();
  const { x, y, z } = app.view;
  const view = [-x / z, -y / z, (rect.width - x) / z, (rect.height - y) / z];
  const k = 1 / z;
  const size = (b) => (b[2] - b[0]) + (b[3] - b[1]);
  const entries = [...app.objects.values(), ...eraseEntries()]
    .filter((e) => e.box && !(e.box[2] < view[0] || e.box[0] > view[2] || e.box[3] < view[1] || e.box[1] > view[3]))
    .sort((a, b) => app.selection.has(b.key) - app.selection.has(a.key) || size(b.box) - size(a.box));
  const placed = [];
  const parts = [];
  for (const entry of entries) {
    const [kind, id] = splitKey(entry.key);
    const o = getObject(entry.key);
    if (!o) continue;
    const b = entry.box;
    const [cx, cy] = kind === "walls" ? app.canvas.pt((o.x1 + o.x2) / 2, (o.y1 + o.y2) / 2) : [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2];
    const text = kind === "labels" ? `label ${id}` : String(id);
    const sx = cx * z + x;
    const sy = cy * z + y;
    const half = text.length * 3.6 + 3;
    if (placed.some(([px, py, ph]) => Math.abs(px - sx) < ph + half && Math.abs(py - sy) < 14)) continue;
    placed.push([sx, sy, half]);
    const cls = app.selection.has(entry.key) ? "id-tag selected" : "id-tag";
    parts.push(`<text class="${cls}" transform="translate(${f2(cx)} ${f2(cy)}) scale(${k})">${esc(text)}</text>`);
    if (parts.length >= ID_LIMIT) break;
  }
  idLayer.innerHTML = parts.join("");
}

export function renderAll() {
  renderSheet();
  renderPlan();
  renderErase();
  renderTrace();
  applyView();
  updateToolbar();
}

export function applyView() {
  const v = app.view;
  world.setAttribute("transform", `translate(${v.x} ${v.y}) scale(${v.z})`);
  $("status-zoom").textContent = `${Math.round(v.z * 100)}%`;
  renderMarkers();
  renderIds();
  renderUI();
}

export function fitView() {
  if (!app.canvas) return;
  const rect = svg.getBoundingClientRect();
  const w = rect.width || 800;
  const h = rect.height || 600;
  const z = Math.min(w / app.canvas.width, h / app.canvas.height) * 0.95;
  app.view = { z, x: (w - app.canvas.width * z) / 2, y: (h - app.canvas.height * z) / 2 };
  applyView();
}

export function zoomBy(factor) {
  const rect = svg.getBoundingClientRect();
  zoomAround(rect.width / 2, rect.height / 2, factor);
}

function zoomAround(mx, my, factor) {
  const z = Math.max(0.05, Math.min(40, app.view.z * factor));
  const k = z / app.view.z;
  app.view.x = mx - (mx - app.view.x) * k;
  app.view.y = my - (my - app.view.y) * k;
  app.view.z = z;
  applyView();
}

export function zoomToSelection() {
  const box = unionBox(app.selection);
  if (!box) return message("Select something first; Z zooms to it.", true);
  zoomToBox(box);
}

export function zoomToBox([x0, y0, x1, y1]) {
  const rect = svg.getBoundingClientRect();
  const fit = Math.min((rect.width - 80) / Math.max(x1 - x0, 1), (rect.height - 80) / Math.max(y1 - y0, 1));
  const z = Math.max(0.05, Math.min(4, fit));
  app.view = { z, x: rect.width / 2 - ((x0 + x1) / 2) * z, y: rect.height / 2 - ((y0 + y1) / 2) * z };
  applyView();
}

/** Sheet pixels to page (client) coordinates. */
export function toScreen(wx, wy) {
  const rect = svg.getBoundingClientRect();
  return [rect.left + app.view.x + wx * app.view.z, rect.top + app.view.y + wy * app.view.z];
}

function updateToolbar() {
  const s = app.state;
  $("undo").disabled = !s || !s.can_undo;
  $("redo").disabled = !s || !s.can_redo;
  $("save").disabled = !s || !s.plan;
  $("save-step").disabled = !s || !s.plan;
  for (const b of document.querySelectorAll("#tools button")) {
    const on = b.dataset.tool === app.tool;
    b.classList.toggle("active", on);
    b.setAttribute("aria-pressed", String(on));
  }
  const file = s && s.file ? `${s.file}${s.dirty ? " •" : ""}` : "no plan";
  $("status-file").textContent = file;
  document.title = `${s && s.dirty ? "• " : ""}${s && s.file ? s.file : "floorplan editor"}`;
}

// ---------------------------------------------------------------- UI layer: handles, dimensions, ghosts, snap marks

function px(z = 1) {
  return z / app.view.z;
}

function handlesFor(key) {
  const obj = getObject(key);
  if (!obj) return [];
  const [kind] = splitKey(key);
  const c = app.canvas;
  const hs = [];
  if (kind === "walls") {
    hs.push({ type: "wall-end", end: "start", at: c.pt(obj.x1, obj.y1) });
    hs.push({ type: "wall-end", end: "end", at: c.pt(obj.x2, obj.y2) });
  } else if (kind === "openings") {
    const wall = getObject(`walls:${obj.wall}`);
    if (wall) {
      const len = wallLength(wall) || 1;
      const ux = (wall.x2 - wall.x1) / len;
      const uy = (wall.y2 - wall.y1) / len;
      const a = obj.offset;
      const b = obj.offset + obj.width;
      hs.push({ type: "open-end", which: "start", at: c.pt(wall.x1 + ux * a, wall.y1 + uy * a) });
      hs.push({ type: "open-end", which: "end", at: c.pt(wall.x1 + ux * b, wall.y1 + uy * b) });
    }
  } else if (kind === "counters" && obj.points) {
    const n = obj.points.length;
    obj.points.forEach(([x, y], i) => {
      hs.push({ type: "vertex", index: i, at: c.pt(x, y) });
      const [nx, ny] = obj.points[(i + 1) % n];
      hs.push({ type: "mid", index: i, at: c.pt((x + nx) / 2, (y + ny) / 2), cls: "mid" });
    });
  } else if (kind === "counters" || kind === "erase") {
    const corners = [
      [obj.x, obj.y],
      [obj.x + obj.w, obj.y],
      [obj.x + obj.w, obj.y + obj.h],
      [obj.x, obj.y + obj.h],
    ];
    corners.forEach(([x, y], i) => hs.push({ type: "box-corner", corner: i, corners, at: c.pt(x, y) }));
  } else if (kind === "fixtures") {
    const rot = obj.rot || 0;
    const [rx, ry] = rotate(obj.w / 2, obj.h / 2, rot);
    hs.push({ type: "fix-resize", at: c.pt(obj.x + rx, obj.y + ry) });
    const off = obj.h / 2 + px(22) / c.scale;
    const [tx, ty] = rotate(0, -off, rot);
    hs.push({ type: "fix-rotate", at: c.pt(obj.x + tx, obj.y + ty), cls: "rotate" });
  }
  return hs;
}

/** Highlight measurement `i`: its line, its label, and the wall it measures to. */
function setHotDim(i) {
  if (app.hotDim === i) return;
  app.hotDim = i;
  for (const n of document.querySelectorAll(".hot-dim")) n.classList.remove("hot-dim");
  for (const n of document.querySelectorAll(".dim-ref")) {
    n.classList.remove("dim-ref");
    n.style.removeProperty("--dim");
  }
  const d = i == null ? null : app.dims[i];
  if (!d) return;
  for (const n of uiLayer.querySelectorAll(`[data-dim="${i}"], [data-dim-line="${i}"]`)) n.classList.add("hot-dim");
  for (const n of document.querySelectorAll(`#side [data-dim-key="${d.key}"]`)) n.classList.add("hot-dim");
  const ref = d.ref && document.querySelector(`#plan-layer [data-key="${CSS.escape(d.ref)}"]`);
  if (ref) {
    ref.classList.add("dim-ref");
    ref.style.setProperty("--dim", d.color);
  }
}

function hotDimAt(target) {
  const label = target.closest && target.closest("[data-dim]");
  if (label) return Number(label.dataset.dim);
  const field = target.closest && target.closest("#side [data-dim-key]");
  if (!field) return null;
  const i = app.dims.findIndex((d) => d.key === field.dataset.dimKey);
  return i < 0 ? null : i;
}

document.addEventListener("pointerover", (e) => setHotDim(hotDimAt(e.target)));
document.addEventListener("focusin", (e) => setHotDim(hotDimAt(e.target)));

/** Dimension lines on the selected object. Each label is a button that opens a box to type the value. */
function dimsMarkup() {
  setHotDim(null);
  app.dims = [];
  const info = selectionInfo();
  const busy = app.drag && app.drag.type !== "pan" && app.drag.type !== "dim-drag";
  if (!app.show.dims || app.tool !== "select" || info.n !== 1 || !info.planKeys.length || busy) return "";
  app.dims = dimsFor(app.plan, info.kind, info.id);
  const c = app.canvas;
  const geo = app.dims.map((d) => {
    const [ax, ay] = c.pt(...d.a);
    const [bx, by] = c.pt(...d.b);
    const [nx, ny] = d.normal;
    const off = nx || ny ? (d.pad || 0) * c.scale + px(d.gap ? 16 : 24) : 0;
    const A = [ax + nx * off, ay + ny * off];
    const B = [bx + nx * off, by + ny * off];
    const len = Math.hypot(B[0] - A[0], B[1] - A[1]);
    const [ux, uy] = len ? [(B[0] - A[0]) / len, (B[1] - A[1]) / len] : [1, 0];
    const w = d.label.length * 7 + 12;
    let home = [(A[0] + B[0]) / 2, (A[1] + B[1]) / 2];
    const need = (w + 8) * px(1);
    if (d.gap && len < need) {
      const before = d.key.endsWith("start");
      const [ex, ey] = before ? A : B;
      const k = (before ? -1 : 1) * (need / 2 + px(2));
      home = [ex + ux * k, ey + uy * k];
    }
    d.nudgeKey = `${info.kind}:${info.id}:${d.key}`;
    const nudge = app.dimNudge.get(d.nudgeKey);
    return { a: [ax, ay], b: [bx, by], A, B, off, len, u: [ux, uy], w, home, nudge, mid: [(A[0] + B[0]) / 2, (A[1] + B[1]) / 2] };
  });
  const spots = placeLabels(
    geo.map((g) => ({ home: g.home, w: g.w * px(1), h: 18 * px(1), fixed: g.nudge ? [g.home[0] + g.nudge[0] * px(1), g.home[1] + g.nudge[1] * px(1)] : null })),
    labelScene(geo),
  );
  const sw = px(1);
  return app.dims
    .map((d, i) => {
      const g = geo[i];
      const { A, B, off, len } = g;
      const [nx, ny] = d.normal;
      const [ux, uy] = g.u;
      const t = px(4);
      const tick = ([x, y]) => {
        const [tx, ty] = [(ux - uy) * t, (uy + ux) * t];
        return `<line x1="${f2(x - tx)}" y1="${f2(y - ty)}" x2="${f2(x + tx)}" y2="${f2(y + ty)}"/>`;
      };
      let s = `<g class="dim${d.gap ? " gap" : ""}" data-dim-line="${i}" style="--dim: ${d.color}" stroke-width="${sw}">`;
      if (off) {
        for (const [p, q] of [
          [g.a, A],
          [g.b, B],
        ]) {
          s += `<line class="ext" x1="${f2(p[0] + nx * px(3))}" y1="${f2(p[1] + ny * px(3))}" x2="${f2(q[0] + nx * px(4))}" y2="${f2(q[1] + ny * px(4))}"/>`;
        }
      }
      if (len > px(2)) s += `<line x1="${f2(A[0])}" y1="${f2(A[1])}" x2="${f2(B[0])}" y2="${f2(B[1])}"/>${tick(A)}${tick(B)}`;
      const [mx, my] = spots[i];
      d.offset = [(mx - g.home[0]) / px(1), (my - g.home[1]) / px(1)];
      if (!g.nudge) app.dimAuto.set(d.nudgeKey, d.offset);
      if (Math.hypot(...d.offset) > LEADER_MIN) {
        const [lx, ly] = g.mid;
        s +=
          `<line class="leader" x1="${f2(lx)}" y1="${f2(ly)}" x2="${f2(mx)}" y2="${f2(my)}" style="stroke-width: ${px(1.5)}; stroke-dasharray: ${px(1)} ${px(4)}"/>` +
          `<circle class="leader-dot" cx="${f2(lx)}" cy="${f2(ly)}" r="${f2(px(3))}"/>`;
      }
      s += "</g>";
      const tipText = `${d.title}: ${d.what} ${d.effect} Click to type a value; drag to move the label${g.nudge ? " (drop it back on its line to let it find its own spot again)" : ""}.`;
      const w = g.w;
      s +=
        `<g class="dim-label${d.gap ? " gap" : ""}${g.nudge ? " nudged" : ""}" data-dim="${i}" role="button" aria-label="${esc(d.title)}: ${esc(d.label)}" data-tip="${esc(tipText)}" style="--dim: ${d.color}" transform="translate(${f2(mx)} ${f2(my)}) scale(${px(1)})">` +
        `<rect x="${-w / 2}" y="-9" width="${w}" height="18" rx="9"/><text y="4" text-anchor="middle">${esc(d.label)}</text></g>`;
      return s;
    })
    .join("");
}

const LEADER_MIN = 12;

/** What measurement labels should avoid, in world units. */
function labelScene(geo) {
  const rect = svg.getBoundingClientRect();
  const z = app.view.z;
  const toWorldBox = (r) => [(r.left - rect.left - app.view.x) / z, (r.top - rect.top - app.view.y) / z, (r.right - rect.left - app.view.x) / z, (r.bottom - rect.top - app.view.y) / z];
  const blocked = ["actionbar", "hint"].map($).filter((n) => n && !n.hidden).map((n) => toWorldBox(n.getBoundingClientRect()));
  const lines = [];
  geo.forEach((g, i) => {
    if (g.len > px(2)) lines.push([g.A, g.B, i]);
    if (g.off) lines.push([g.a, g.A, i], [g.b, g.B, i]);
  });
  return {
    px: px(1),
    boxes: [...app.objects.values()].map((e) => e.box).filter(Boolean),
    lines,
    view: toWorldBox({ left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom }),
    blocked,
    useInk: !!sheetOn(),
  };
}

export function renderUI() {
  if (!app.plan) {
    uiLayer.innerHTML = "";
    renderActionBar(null);
    updateHint();
    return;
  }
  const parts = [];
  const r = px(5);
  const sw = px(1.5);
  const d = app.drag;
  const ghost = d && d.ghost ? d.ghost : app.draft && app.draft.ghost ? app.draft.ghost : app.hoverGhost;
  parts.push(dimsMarkup());
  if (ghost) parts.push(ghost);
  const single = app.selection.size === 1 && !app.sel.startsWith("flags:");
  app.handles = app.tool === "select" && single && !(d && (d.type === "move" || d.type === "marquee")) ? handlesFor(app.sel) : [];
  app.handles.forEach((h, i) => {
    const [x, y] = h.at;
    const name = esc(`${h.type} ${h.end || h.which || (h.index ?? h.corner ?? "")} handle`.replace(/\s+/g, " "));
    parts.push(
      `<circle class="handle ${h.cls || ""}" data-h="${i}" role="button" aria-label="${name}" cx="${f2(x)}" cy="${f2(y)}" r="${r}" stroke-width="${sw}"/>`,
    );
  });
  const snap = (d && d.snap) || (app.cursor && app.cursor.snap);
  if (snap && (snap.kind === "endpoint" || snap.kind === "wall")) {
    const [sx, sy] = app.canvas.pt(snap.x, snap.y);
    const s = px(7);
    parts.push(`<rect class="snap-mark" x="${f2(sx - s)}" y="${f2(sy - s)}" width="${2 * s}" height="${2 * s}" stroke-width="${sw}"/>`);
  }
  uiLayer.innerHTML = parts.join("");
  $("status-snap").textContent = snap && snap.kind !== "none" ? `snap: ${snap.kind}` : "";
  const box = app.selection.size ? unionBox(app.selection) : null;
  if (box) {
    const pad = app.dims.length ? 40 : 0;
    const [x0, y0] = toScreen(box[0], box[1]);
    const [x1, y1] = toScreen(box[2], box[3]);
    renderActionBar([x0 - pad, y0 - pad, x1 + pad, y1 + pad]);
  } else renderActionBar(null);
  updateHint();
}

function measureText(x, y, text) {
  const size = px(13);
  return `<text class="measure" x="${f2(x)}" y="${f2(y - px(10))}" font-size="${size}" stroke-width="${px(3)}" text-anchor="middle">${esc(text)}</text>`;
}

function lineGhost(a, b, thicknessIn, label) {
  const [ax, ay] = app.canvas.pt(...a);
  const [bx, by] = app.canvas.pt(...b);
  const w = Math.max(px(2), (thicknessIn || 0) * app.canvas.scale);
  let s = `<line class="ghost-line" x1="${f2(ax)}" y1="${f2(ay)}" x2="${f2(bx)}" y2="${f2(by)}" stroke-width="${w}"/>`;
  if (label) s += measureText((ax + bx) / 2, (ay + by) / 2, label);
  return s;
}

function polyGhost(pointsIn, closed = true) {
  const pts = pointsIn.map(([x, y]) => app.canvas.pt(x, y).map(f2).join(",")).join(" ");
  const tag = closed ? "polygon" : "polyline";
  return `<${tag} class="ghost" points="${pts}" stroke-width="${px(1.5)}"/>`;
}

function rectGhost(a, b) {
  const x = Math.min(a[0], b[0]);
  const y = Math.min(a[1], b[1]);
  const w = Math.abs(b[0] - a[0]);
  const h = Math.abs(b[1] - a[1]);
  return (
    polyGhost([
      [x, y],
      [x + w, y],
      [x + w, y + h],
      [x, y + h],
    ]) + measureText(...app.canvas.pt(x + w / 2, y), `${formatLength(w)} × ${formatLength(h)}`)
  );
}

function fixtureGhost(x, y, w, h, rot) {
  const pts = [
    [-w / 2, -h / 2],
    [w / 2, -h / 2],
    [w / 2, h / 2],
    [-w / 2, h / 2],
  ].map(([lx, ly]) => {
    const [rx, ry] = rotate(lx, ly, rot);
    return [x + rx, y + ry];
  });
  return polyGhost(pts);
}

// ---------------------------------------------------------------- the hint over the drawing

function toolPrompt() {
  const d = app.draft;
  const typing = "or type a length (10'6\"), length<angle (8'<90), or right, down (48, 0) and press Enter";
  switch (app.tool) {
    case "select":
      if (app.selection.size) return "Drag it to move · Ctrl+drag a box to select instead, Shift+drag to add · Shift+click adds or removes one · click a measurement to type it, or drag it aside";
      return "Click to select · Ctrl+drag a box to select an area (Shift+drag adds) · drag empty space to pan · right-click for actions";
    case "wall":
      return d ? `Click the next corner, ${typing}. Double-click, Enter, or Esc stops.` : "Click the first corner of the wall. Shift keeps it straight.";
    case "polygon":
      return d && d.pts.length ? `Click the next corner, ${typing}. Enter or double-click closes; Backspace removes a corner.` : "Click the first corner of the counter outline.";
    case "door":
    case "window":
      return `Click a wall to add a ${app.tool} ${formatLength(app.tool === "door" ? app.opts.doorWidth : app.opts.windowWidth)} wide (type a number to change it), or click in a doorway gap to fill it exactly.`;
    case "counter":
    case "erase":
      return d && d.type === "rect"
        ? "Click the opposite corner, or type width, depth (25, 60) and press Enter."
        : `Drag a rectangle${app.tool === "erase" ? " over ink to white it out" : ""}, or click one corner and then type width, depth.`;
    case "fixture":
      return `Click to place a ${app.opts.fixtureType.replace(/_/g, " ")}, or drag over its symbol on the sheet to fit it. R turns it 90°.`;
    case "label":
      return "Click where the label goes.";
    case "calibrate":
      return d ? "Click the other end. Shift keeps it straight." : "Click one end of a length printed on the sheet (a wall dimension), then the other end.";
    case "flag":
      return "Click to drop a flag, or drag to flag an area. Then refer to it in Cursor by number (F1, F2…). Esc stops.";
  }
  return "";
}

let lastHint = "";
function updateHint() {
  const hint = $("hint");
  const text = app.plan && !app.drag ? toolPrompt() : "";
  if (text === lastHint) return;
  lastHint = text;
  hint.textContent = text;
  hint.hidden = !text;
}

// ---------------------------------------------------------------- pointer helpers

function toWorld(e) {
  const rect = svg.getBoundingClientRect();
  return [(e.clientX - rect.left - app.view.x) / app.view.z, (e.clientY - rect.top - app.view.y) / app.view.z];
}

function snapTolIn() {
  return px(9) / app.canvas.scale;
}

/** Cursor position in inches, snapped and optionally axis-locked to an anchor (inches). */
function snapped(e, anchor = null, skip = new Set()) {
  const [wx, wy] = toWorld(e);
  let [x, y] = app.canvas.toIn(wx, wy);
  let snap = { x, y, kind: "none" };
  if (app.show.snap || app.show.grid) {
    const tol = app.show.snap ? snapTolIn() : 0;
    snap = snapPoint(app.plan, x, y, tol, { grid: app.show.grid ? 1 : 0, skip });
  }
  [x, y] = [snap.x, snap.y];
  if (anchor && e.shiftKey) {
    [x, y] = lockAxis(anchor, x, y);
    snap = { x, y, kind: "straight" };
  }
  return { x: round2(x), y: round2(y), snap };
}

function nearestWall(x, y) {
  let best = null;
  for (const w of app.plan.walls || []) {
    const p = project(w, x, y);
    const d = Math.hypot(p.x - x, p.y - y);
    const tol = w.thickness / 2 + snapTolIn();
    if (d <= tol && (!best || d < best.d)) best = { d, wall: w, p };
  }
  return best;
}

function pickAt(wx, wy) {
  return pick(app.objects, wx, wy, px(6), [...doorwayEntries(), ...eraseEntries()]);
}

function inField(e) {
  const t = e.target;
  return t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
}

function stageCenter() {
  const r = svg.getBoundingClientRect();
  return [r.left + r.width / 2, r.top + r.height / 2];
}

function pointerScreen() {
  return app.cursor && app.cursor.onStage ? toScreen(...app.cursor.world) : stageCenter();
}

// ---------------------------------------------------------------- tools

export function setTool(tool) {
  app.tool = tool;
  app.draft = null;
  app.drag = null;
  app.hoverGhost = null;
  if (tool === "erase" && !app.show.erase) {
    app.show.erase = true;
    $("show-erase").checked = true;
    renderErase();
  }
  renderToolOptions();
  updateToolbar();
  renderUI();
  svg.style.cursor = tool === "select" ? "default" : "crosshair";
}

function renderToolOptions() {
  const box = $("tool-options");
  const o = app.opts;
  const lenInput = (label, key) =>
    `<label>${label} <input data-opt="${key}" value="${formatLength(o[key])}" title="inches, feet and inches like 2'6&quot;, or math like 30 + 2"/></label>`;
  if (app.tool === "wall") box.innerHTML = lenInput("Thickness", "thickness");
  else if (app.tool === "door") box.innerHTML = lenInput("Width", "doorWidth");
  else if (app.tool === "window") box.innerHTML = lenInput("Width", "windowWidth");
  else if (app.tool === "fixture") {
    const types = (app.state && app.state.fixture_types) || Object.keys(FIXTURE_SIZES);
    box.innerHTML =
      `<label>Type <select data-opt="fixtureType">${types
        .map((t) => `<option value="${t}" ${t === o.fixtureType ? "selected" : ""}>${t.replace(/_/g, " ")}</option>`)
        .join("")}</select></label>` +
      `<label>Rotation <select data-opt="fixtureRot">${[0, 90, 180, 270]
        .map((r) => `<option ${r === o.fixtureRot ? "selected" : ""}>${r}</option>`)
        .join("")}</select></label>`;
  } else if (app.tool === "label") {
    box.innerHTML = `<label>Text <input data-opt="labelText" value="${esc(o.labelText).replace(/"/g, "&quot;")}" placeholder="asked on click if empty"/></label>`;
  } else box.innerHTML = "";
  for (const node of box.querySelectorAll("[data-opt]")) {
    if (node.dataset.opt === "labelText") {
      node.addEventListener("input", () => (o.labelText = node.value));
      continue;
    }
    node.addEventListener("change", () => {
      const key = node.dataset.opt;
      if (key === "fixtureType") o[key] = node.value;
      else if (key === "fixtureRot") o[key] = Number(node.value);
      else {
        const v = parseLength(node.value);
        if (Number.isFinite(v) && v > 0) o[key] = round2(v);
        node.value = formatLength(o[key]);
      }
      renderUI();
    });
  }
}

function finishDraft() {
  const d = app.draft;
  app.draft = null;
  app.hoverGhost = null;
  if (d && d.type === "polygon") {
    if (d.pts.length >= 3) send({ op: "add", kind: "counters", obj: { points: d.pts } }).then(() => setTool("select"));
    else message("A counter outline needs at least 3 corners.", true);
  }
  renderUI();
}

function pushPoint(list, pt) {
  const last = list[list.length - 1];
  if (last && Math.hypot(last[0] - pt[0], last[1] - pt[1]) < 0.5) return false;
  list.push(pt);
  return true;
}

/** Add a corner to the wall or outline being drawn. Each wall segment is saved as it is drawn. */
async function addPathPoint(pt) {
  const p = [round2(pt[0]), round2(pt[1])];
  if (!app.draft) {
    app.draft = { type: app.tool === "wall" ? "wall" : "polygon", pts: [p] };
    return;
  }
  if (!pushPoint(app.draft.pts, p)) return;
  if (app.draft.type === "wall") {
    const [a, b] = app.draft.pts.slice(-2);
    await send({ op: "add", kind: "walls", obj: { x1: a[0], y1: a[1], x2: b[0], y2: b[1], thickness: app.opts.thickness } }, { selectRef: false });
  }
  app.hoverGhost = null;
  renderUI();
}

async function placeRect(kind, a, b) {
  const w = Math.abs(b[0] - a[0]);
  const h = Math.abs(b[1] - a[1]);
  const obj = { x: round2(Math.min(a[0], b[0])), y: round2(Math.min(a[1], b[1])), w: round2(w), h: round2(h) };
  const ref = await send({ op: "add", kind, obj });
  if (ref) setTool("select");
}

/** A click places the fixture at its usual size; a drag fits it to the box drawn over the sheet's symbol. */
async function placeFixture(a, b) {
  const type = app.opts.fixtureType;
  const bw = Math.abs(b[0] - a[0]);
  const bh = Math.abs(b[1] - a[1]);
  let obj;
  if (bw < 3 || bh < 3) {
    const [w, h] = FIXTURE_SIZES[type] || [24, 24];
    obj = { type, x: a[0], y: a[1], w, h };
    if (app.opts.fixtureRot) obj.rot = app.opts.fixtureRot;
  } else {
    const turned = app.opts.fixtureRot % 180 !== 0;
    obj = { type, x: round2((a[0] + b[0]) / 2), y: round2((a[1] + b[1]) / 2), w: round2(turned ? bh : bw), h: round2(turned ? bw : bh) };
    if (app.opts.fixtureRot) obj.rot = app.opts.fixtureRot;
  }
  const ref = await send({ op: "add", kind: "fixtures", obj });
  if (ref) setTool("select");
}

function askLabel(p) {
  const [sx, sy] = toScreen(...app.canvas.pt(p.x, p.y));
  popInput({
    x: sx,
    y: sy + 12,
    label: "Label text",
    hint: "Enter adds it; Esc cancels.",
    onCommit: async (text) => {
      if (!text.trim()) return;
      await send({ op: "add", kind: "labels", obj: { text: text.trim(), x: p.x, y: p.y } });
      setTool("select");
    },
  });
}

function askCalibration(p1, p2) {
  const pixels = Math.hypot(p2[0] - p1[0], p2[1] - p1[1]);
  const now = formatLength(pixels / app.canvas.scale);
  const [sx, sy] = toScreen((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2);
  popInput({
    x: sx,
    y: sy + 16,
    label: `Real length between the two points (now ${now})`,
    hint: "Type what the sheet says, like 26'5\" or 318. Enter sets the scale.",
    onCommit: async (text) => {
      const inches = parseLength(text);
      if (!Number.isFinite(inches) || inches <= 0) return message(`Can't read a length from “${text}”.`, true);
      const ref = await send({ op: "calibrate", p1, p2, length: inches });
      if (ref !== undefined) message(`Scale set: ${app.plan.canvas.px_per_inch.toFixed(4)} sheet pixels per inch.`);
      setTool("select");
    },
    onCancel: () => setTool("select"),
  });
}

async function toolDown(e) {
  const tool = app.tool;
  if (tool === "wall" || tool === "polygon") {
    const anchor = app.draft ? app.draft.pts[app.draft.pts.length - 1] : null;
    const p = snapped(e, anchor);
    await addPathPoint([p.x, p.y]);
    return;
  }
  if (tool === "door" || tool === "window") {
    const [wx, wy] = toWorld(e);
    const [x, y] = app.canvas.toIn(wx, wy);
    const hit = nearestWall(x, y);
    if (!hit) {
      const state = await call("/api/op", { op: { op: "convert", to: tool, at: [round2(x), round2(y)], gap_only: true } });
      if (!state) return;
      const o = getObject(`openings:${state.ref[1]}`);
      message(`The doorway gap now has a ${formatLength(o.width)} ${tool}; the walls on each side joined into ${o.wall}.`);
      setTool("select");
      return;
    }
    const obj = { wall: hit.wall.id, type: tool, width: tool === "door" ? app.opts.doorWidth : app.opts.windowWidth, at: [x, y] };
    if (tool === "door") Object.assign(obj, { hinge: "start", swing: "ccw" });
    const ref = await send({ op: "add", kind: "openings", obj });
    if (ref) setTool("select");
    return;
  }
  if (tool === "counter" || tool === "erase") {
    const p = snapped(e);
    const kind = tool === "counter" ? "counters" : "erase";
    if (app.draft && app.draft.type === "rect") {
      const start = app.draft.start;
      app.draft = null;
      await placeRect(kind, start, [p.x, p.y]);
      return;
    }
    app.drag = { type: "rect", kind, start: [p.x, p.y], cur: [p.x, p.y] };
    return;
  }
  if (tool === "fixture") {
    const p = snapped(e);
    app.drag = { type: "rect", kind: "fixtures", start: [p.x, p.y], cur: [p.x, p.y] };
    return;
  }
  if (tool === "label") {
    const p = snapped(e);
    const text = app.opts.labelText.trim();
    if (!text) return askLabel(p);
    await send({ op: "add", kind: "labels", obj: { text, x: p.x, y: p.y } });
    setTool("select");
    return;
  }
  if (tool === "flag") {
    const [wx, wy] = toWorld(e);
    app.drag = { type: "flag-new", startWorld: [wx, wy], start: app.canvas.toIn(wx, wy).map(round2), cur: null, sx: e.clientX, sy: e.clientY };
    return;
  }
  if (tool === "calibrate") {
    const [wx, wy] = toWorld(e);
    let pt = [wx, wy];
    if (app.draft && e.shiftKey) pt = lockAxis(app.draft.pts[0], wx, wy);
    if (!app.draft) {
      app.draft = { type: "calibrate", pts: [pt] };
      return;
    }
    const [p1] = app.draft.pts;
    app.draft = null;
    askCalibration(p1, pt);
  }
}

function toolHover(e) {
  const tool = app.tool;
  const d = app.draft;
  app.hoverGhost = null;
  app.hoverEnd = null;
  if (tool === "select") {
    const onHandle = e.target.dataset && e.target.dataset.h !== undefined;
    svg.style.cursor = onHandle ? "" : e.shiftKey || e.ctrlKey || e.metaKey ? "crosshair" : pickAt(...app.cursor.world) ? "move" : "grab";
  }
  if ((tool === "wall" || tool === "polygon") && d && d.pts.length) {
    const a = d.pts[d.pts.length - 1];
    const p = snapped(e, a);
    app.cursor.snap = p.snap;
    app.hoverEnd = [p.x, p.y];
    const label = formatLength(Math.hypot(p.x - a[0], p.y - a[1]));
    app.hoverGhost = tool === "wall" ? lineGhost(a, [p.x, p.y], app.opts.thickness, label) : polyGhost([...d.pts, [p.x, p.y]], false) + lineGhost(a, [p.x, p.y], 0, label);
  } else if (["wall", "polygon", "counter", "erase", "label", "fixture"].includes(tool)) {
    const p = snapped(e);
    app.cursor.snap = p.snap;
    app.hoverEnd = [p.x, p.y];
    if (d && d.type === "rect") app.hoverGhost = rectGhost(d.start, [p.x, p.y]);
    if (tool === "fixture") {
      const [w, h] = FIXTURE_SIZES[app.opts.fixtureType] || [24, 24];
      app.hoverGhost = fixtureGhost(p.x, p.y, w, h, app.opts.fixtureRot);
    }
  } else if (tool === "door" || tool === "window") {
    const [x, y] = app.cursor.in;
    const hit = nearestWall(x, y);
    if (hit) {
      const w = hit.wall;
      const width = Math.min(tool === "door" ? app.opts.doorWidth : app.opts.windowWidth, hit.p.len);
      const t0 = Math.max(0, Math.min(hit.p.len - width, hit.p.t - width / 2));
      const ux = (w.x2 - w.x1) / hit.p.len;
      const uy = (w.y2 - w.y1) / hit.p.len;
      app.hoverGhost = lineGhost([w.x1 + ux * t0, w.y1 + uy * t0], [w.x1 + ux * (t0 + width), w.y1 + uy * (t0 + width)], w.thickness, formatLength(width));
    }
  } else if (tool === "calibrate" && d) {
    const [wx, wy] = toWorld(e);
    const p2 = e.shiftKey ? lockAxis(d.pts[0], wx, wy) : [wx, wy];
    const [ax, ay] = d.pts[0];
    const len = Math.hypot(p2[0] - ax, p2[1] - ay);
    app.hoverGhost =
      `<line class="ghost-line" x1="${f2(ax)}" y1="${f2(ay)}" x2="${f2(p2[0])}" y2="${f2(p2[1])}" stroke-width="${px(2)}"/>` +
      measureText((ax + p2[0]) / 2, (ay + p2[1]) / 2, `${formatLength(len / app.canvas.scale)} at the current scale`);
  }
  renderUI();
}

// ---------------------------------------------------------------- typing values while drawing

function typedTarget() {
  const d = app.draft;
  if ((app.tool === "wall" || app.tool === "polygon") && d && d.pts.length) return "path";
  if ((app.tool === "counter" || app.tool === "erase") && d && d.type === "rect") return "rect";
  if (app.tool === "door" || app.tool === "window") return "width";
  return null;
}

function openTyped(first) {
  const target = typedTarget();
  const [sx, sy] = pointerScreen();
  const d = app.draft;
  if (target === "path") {
    const from = d.pts[d.pts.length - 1];
    popInput({
      x: sx,
      y: sy + 20,
      label: "Next corner",
      value: first,
      selectAll: false,
      hint: "A length goes toward the pointer: 10'6\". With an angle: 8'<90 (degrees counterclockwise from right). Or right, down: 48, -12.",
      onCommit: (text) => {
        const dir = app.hoverEnd ? [app.hoverEnd[0] - from[0], app.hoverEnd[1] - from[1]] : [1, 0];
        const pt = parseTyped(text, from, dir);
        if (!pt) return message(`Can't read “${text}”. Try 10'6", 8'<90, or 48, 0.`, true);
        addPathPoint(pt);
      },
    });
  } else if (target === "rect") {
    const start = d.start;
    popInput({
      x: sx,
      y: sy + 20,
      label: "Size: width, depth",
      value: first,
      selectAll: false,
      hint: "Like 25, 60 or 2'1\", 5'. One number makes a square. It grows toward the pointer.",
      onCommit: (text) => {
        const parts = text.split(",").map(parseLength);
        const [w, h] = parts.length === 1 ? [parts[0], parts[0]] : parts;
        if (!(w > 0) || !(h > 0)) return message(`Can't read a width and depth from “${text}”.`, true);
        const toward = app.hoverEnd || [start[0] + 1, start[1] + 1];
        const sxn = Math.sign(toward[0] - start[0]) || 1;
        const syn = Math.sign(toward[1] - start[1]) || 1;
        app.draft = null;
        placeRect(app.tool === "counter" ? "counters" : "erase", start, [start[0] + sxn * w, start[1] + syn * h]);
      },
    });
  } else if (target === "width") {
    const key = app.tool === "door" ? "doorWidth" : "windowWidth";
    popInput({
      x: sx,
      y: sy + 20,
      label: `${app.tool === "door" ? "Door" : "Window"} width`,
      value: first,
      selectAll: false,
      hint: "Then click a wall to place it.",
      onCommit: (text) => {
        const v = parseLength(text);
        if (!(v > 0)) return message(`Can't read a width from “${text}”.`, true);
        app.opts[key] = round2(v);
        renderToolOptions();
        renderUI();
      },
    });
  }
}

// ---------------------------------------------------------------- typing exact sizes

function openDimInput(kind, id, d, at = null) {
  const c = app.canvas;
  const [mx, my] = c.pt((d.a[0] + d.b[0]) / 2, (d.a[1] + d.b[1]) / 2);
  const [sx, sy] = at ? [at[0], at[1] - 8] : toScreen(mx, my);
  const anchorKey = `${kind}:${d.key}`;
  const choice = app.opts.anchor[anchorKey] || (kind === "fixtures" ? "center" : "start");
  popInput({
    x: sx,
    y: sy + 18,
    label: d.title,
    value: formatLength(d.value),
    hint: `${d.anchors ? "" : `${d.effect} `}Type 10'6", 126, or math like 10' - 4.5.${d.anchors ? " The highlighted side stays put." : ""} Enter saves; Esc cancels.`,
    choices: d.anchors ? ["start", "center", "end"].map((a) => [a, d.anchors[a].replace(/^Keep (the )?/, "")]) : null,
    choice,
    onCommit: (text, anchor) => {
      const v = parseLength(text);
      if (!Number.isFinite(v)) return message(`Can't read a length from “${text}”.`, true);
      if (d.anchors && anchor) app.opts.anchor[anchorKey] = anchor;
      const op = opForDim(app.plan, kind, id, d, v, anchor || choice);
      if (!op) return message(`${d.title} can't be ${formatLength(v)}.`, true);
      send(op);
    },
  });
}

export function editPrimaryDim() {
  const info = selectionInfo();
  if (info.n !== 1 || !info.planKeys.length) return;
  const dims = dimsFor(app.plan, info.kind, info.id);
  const d = dims.find((x) => !x.gap);
  if (!d) return message("This has no size to type. Use Move… to place it exactly.", true);
  openDimInput(info.kind, info.id, d);
}

// ---------------------------------------------------------------- drags in the select tool

function startHandleDrag(e, h) {
  const [kind, id] = splitKey(app.sel);
  app.drag = { type: "handle", h, kind, id, obj: structuredClone(getObject(app.sel)), alt: e.altKey, moved: false, sx: e.clientX, sy: e.clientY };
}

function handleMove(e) {
  const d = app.drag;
  const { h, obj } = d;
  const c = app.canvas;
  d.moved = true;
  if (h.type === "wall-end") {
    const fixed = h.end === "start" ? [obj.x2, obj.y2] : [obj.x1, obj.y1];
    const p = snapped(e, fixed, new Set([obj.id]));
    d.result = [p.x, p.y];
    d.snap = p.snap;
    d.ghost = lineGhost(fixed, d.result, obj.thickness, formatLength(Math.hypot(p.x - fixed[0], p.y - fixed[1])));
  } else if (h.type === "open-end") {
    const wall = getObject(`walls:${obj.wall}`);
    const [x, y] = app.cursor.in;
    const t = project(wall, x, y).t;
    let offset = obj.offset;
    let width = obj.width;
    if (h.which === "start") {
      const end = obj.offset + obj.width;
      offset = Math.min(t, end - 1);
      width = end - offset;
    } else width = Math.max(1, t - obj.offset);
    if (app.show.grid) {
      offset = Math.round(offset * 2) / 2;
      width = Math.round(width * 2) / 2;
    }
    d.result = { offset: round2(offset), width: round2(width) };
    const len = wallLength(wall);
    const ux = (wall.x2 - wall.x1) / len;
    const uy = (wall.y2 - wall.y1) / len;
    d.ghost = lineGhost([wall.x1 + ux * offset, wall.y1 + uy * offset], [wall.x1 + ux * (offset + width), wall.y1 + uy * (offset + width)], wall.thickness, formatLength(width));
  } else if (h.type === "vertex" || h.type === "mid") {
    const p = snapped(e);
    d.snap = p.snap;
    d.result = [p.x, p.y];
    const pts = obj.points.map((q) => [...q]);
    if (h.type === "vertex") pts[h.index] = d.result;
    else pts.splice(h.index + 1, 0, d.result);
    d.ghost = polyGhost(pts);
  } else if (h.type === "box-corner") {
    const p = snapped(e);
    d.snap = p.snap;
    const opp = h.corners[(h.corner + 2) % 4];
    const x = Math.min(opp[0], p.x);
    const y = Math.min(opp[1], p.y);
    d.result = { x: round2(x), y: round2(y), w: round2(Math.max(0.5, Math.abs(p.x - opp[0]))), h: round2(Math.max(0.5, Math.abs(p.y - opp[1]))) };
    d.ghost = rectGhost(opp, [p.x, p.y]);
  } else if (h.type === "fix-resize") {
    const [x, y] = app.cursor.in;
    const [lx, ly] = rotate(x - obj.x, y - obj.y, -(obj.rot || 0));
    let w = Math.max(1, 2 * Math.abs(lx));
    let hh = Math.max(1, 2 * Math.abs(ly));
    if (app.show.grid) [w, hh] = [Math.round(w), Math.round(hh)];
    d.result = { w, h: hh };
    d.ghost = fixtureGhost(obj.x, obj.y, w, hh, obj.rot || 0) + measureText(...c.pt(obj.x, obj.y), `${formatLength(w)} × ${formatLength(hh)}`);
  } else if (h.type === "fix-rotate") {
    const [x, y] = app.cursor.in;
    let rot = (Math.atan2(y - obj.y, x - obj.x) * 180) / Math.PI + 90;
    if (!e.altKey) rot = Math.round(rot / 15) * 15;
    rot = ((rot % 360) + 360) % 360;
    d.result = rot;
    d.ghost = fixtureGhost(obj.x, obj.y, obj.w, obj.h, rot) + measureText(...c.pt(obj.x, obj.y), `${Math.round(rot)}°`);
  }
  renderUI();
}

async function handleUp(e) {
  const d = app.drag;
  app.drag = null;
  const { h, kind, id } = d;
  if (!d.moved) {
    if (h.type === "vertex" && e.altKey) await send({ op: "delete_vertex", id, index: h.index });
    renderUI();
    return;
  }
  if (d.result === undefined) return renderUI();
  if (h.type === "wall-end") await send({ op: "move_wall_end", id, end: h.end, x: d.result[0], y: d.result[1], join: !e.altKey && !d.alt });
  else if (h.type === "open-end") await send({ op: "update", kind, id, fields: d.result });
  else if (h.type === "vertex") await send({ op: "move_vertex", id, index: h.index, x: d.result[0], y: d.result[1] });
  else if (h.type === "mid") await send({ op: "insert_vertex", id, index: h.index, x: d.result[0], y: d.result[1] });
  else if (h.type === "box-corner") await send({ op: "update", kind, id, fields: d.result });
  else if (h.type === "fix-resize") await send({ op: "update", kind, id, fields: d.result });
  else if (h.type === "fix-rotate") await send({ op: "update", kind, id, fields: { rot: d.result } });
  renderUI();
}

function moveMove(e) {
  const d = app.drag;
  const [wx, wy] = toWorld(e);
  let dx = wx - d.start[0];
  let dy = wy - d.start[1];
  if (!d.moved && Math.hypot(dx, dy) * app.view.z < 3) return;
  d.moved = true;
  if (e.shiftKey) {
    if (Math.abs(dx) >= Math.abs(dy)) dy = 0;
    else dx = 0;
  }
  const step = app.show.grid ? 0.5 : 0.01;
  d.dIn = [Math.round(dx / app.canvas.scale / step) * step, Math.round(dy / app.canvas.scale / step) * step];
  const tx = d.dIn[0] * app.canvas.scale;
  const ty = d.dIn[1] * app.canvas.scale;
  for (const key of d.keys) {
    const node = document.querySelector(`#plan-layer [data-key="${CSS.escape(key)}"], #erase-layer [data-key="${CSS.escape(key)}"]`);
    if (node) node.setAttribute("transform", `translate(${tx} ${ty})`);
  }
  d.ghost = measureText(wx, wy, `${formatLength(d.dIn[0])} right, ${formatLength(d.dIn[1])} down`);
  renderUI();
}

function moveKeys(keys, dx, dy, join = true) {
  if (keys.length === 1) {
    const [kind, id] = splitKey(keys[0]);
    return send({ op: "move", kind, id, dx, dy, join });
  }
  return send({ op: "move_group", items: keys.map(splitKey), dx, dy, join });
}

async function moveUp(e) {
  const d = app.drag;
  app.drag = null;
  if (!d.moved || !d.dIn || (d.dIn[0] === 0 && d.dIn[1] === 0)) {
    renderAll();
    return;
  }
  await moveKeys(d.keys, round2(d.dIn[0]), round2(d.dIn[1]), !e.altKey);
}

function marqueeMove(e) {
  const d = app.drag;
  d.cur = toWorld(e);
  const [ax, ay] = d.start;
  const [bx, by] = d.cur;
  const crossing = bx < ax;
  d.ghost = `<rect class="marquee${crossing ? " crossing" : ""}" x="${f2(Math.min(ax, bx))}" y="${f2(Math.min(ay, by))}" width="${f2(Math.abs(bx - ax))}" height="${f2(Math.abs(by - ay))}" stroke-width="${px(1)}"/>`;
  renderUI();
}

function marqueeUp() {
  const d = app.drag;
  app.drag = null;
  const [ax, ay] = d.start;
  const [bx, by] = d.cur || d.start;
  if (Math.hypot(bx - ax, by - ay) * app.view.z < 4) {
    if (d.toggle) select(d.toggle, { toggle: true });
    else renderUI();
    return;
  }
  const [x0, y0, x1, y1] = [Math.min(ax, bx), Math.min(ay, by), Math.max(ax, bx), Math.max(ay, by)];
  const crossing = bx < ax;
  const hit = (b) => (crossing ? b[0] <= x1 && b[2] >= x0 && b[1] <= y1 && b[3] >= y0 : b[0] >= x0 && b[2] <= x1 && b[1] >= y0 && b[3] <= y1);
  const keys = [];
  for (const entry of [...app.objects.values(), ...eraseEntries()]) if (hit(entry.box)) keys.push(entry.key);
  for (const f of app.state.flags || []) {
    const b = keyBox(`flags:${f.id}`);
    if (b && hit(b)) keys.push(`flags:${f.id}`);
  }
  setSelection(d.add ? [...new Set([...app.selection, ...keys])] : keys);
  afterSelect();
  message(app.selection.size ? `${app.selection.size} selected.` : "Nothing there. Left-to-right selects whole objects inside; right-to-left selects anything touched.");
}

function flagNewUp(e) {
  const d = app.drag;
  app.drag = null;
  const moved = Math.hypot(e.clientX - d.sx, e.clientY - d.sy) > 5;
  if (moved && d.cur) addFlag(d.start, d.cur, null);
  else {
    const hit = pickAt(...d.startWorld);
    addFlag(d.start, null, hit ? hit.key : null);
  }
  renderUI();
}

function flagMoveUp() {
  const d = app.drag;
  app.drag = null;
  if (!d.moved) return renderUI();
  const [x, y] = app.canvas.toIn(d.orig[0] + d.dx, d.orig[1] + d.dy);
  moveFlag(d.id, round2(x), round2(y));
}

// ---------------------------------------------------------------- selection actions

export async function deleteSelection() {
  const info = selectionInfo();
  if (!info.n) return;
  const n = info.n;
  if (info.planKeys.length === 1) {
    const [kind, id] = splitKey(info.planKeys[0]);
    await send({ op: "delete", kind, id });
  } else if (info.planKeys.length) await send({ op: "delete_many", items: info.planKeys.map(splitKey) });
  if (info.flagKeys.length) await deleteFlags(info.flagKeys.map((k) => k.slice(6)));
  setSelection([]);
  afterSelect();
  message(`Deleted ${n === 1 ? describeKey(info.keys[0]) : `${n} objects`}. Ctrl+Z brings ${n === 1 ? "it" : "them"} back.`);
}

export async function duplicateSelection() {
  const keys = selectionInfo().planKeys;
  if (!keys.length) return;
  const state = await call("/api/op", { op: { op: "duplicate", items: keys.map(splitKey), dx: 12, dy: 12 } });
  if (state) message(`Duplicated ${keys.length === 1 ? describeKey(keys[0]) : `${keys.length} objects`} 1' right and down. Drag or Move… to place.`);
}

export function copySelection() {
  const keys = selectionInfo().planKeys;
  if (!keys.length) return;
  app.clipboard = keys;
  message(`Copied ${keys.length === 1 ? describeKey(keys[0]) : `${keys.length} objects`}. Ctrl+V pastes at the pointer.`);
}

export async function pasteClipboard(at = null) {
  const keys = (app.clipboard || []).filter((k) => getObject(k));
  if (!keys.length) return message("Nothing copied (or the copied objects are gone).", true);
  const box = unionBox(keys);
  let dx = 12;
  let dy = 12;
  const target = at || (app.cursor && app.cursor.onStage ? app.cursor.in : null);
  if (box && target) {
    const [cx, cy] = app.canvas.toIn((box[0] + box[2]) / 2, (box[1] + box[3]) / 2);
    dx = round2(target[0] - cx);
    dy = round2(target[1] - cy);
  }
  await call("/api/op", { op: { op: "duplicate", items: keys.map(splitKey), dx, dy } });
}

export async function rotateSelection(deg = 90) {
  const info = selectionInfo();
  if (info.n !== 1 || info.kind !== "fixtures") return;
  await send({ op: "update", kind: "fixtures", id: info.id, fields: { rot: ((((info.obj.rot || 0) + deg) % 360) + 360) % 360 } });
}

export async function flipDoor(what) {
  const info = selectionInfo();
  if (info.n !== 1 || info.kind !== "openings" || info.obj.type !== "door") return;
  await send({ op: "flip_door", id: info.id, what });
}

export async function splitSelectedWall() {
  const info = selectionInfo();
  if (info.n !== 1 || info.kind !== "walls") return;
  const w = info.obj;
  await send({ op: "split_wall", id: w.id, x: round2((w.x1 + w.x2) / 2), y: round2((w.y1 + w.y2) / 2) });
  message("Wall split in the middle. The second half is selected; double-click a wall to split it somewhere else.");
}

export async function toOutline() {
  const info = selectionInfo();
  if (info.n !== 1 || info.kind !== "counters") return;
  await send({ op: "box_to_polygon", id: info.id });
  message("Now an outline: drag corners, drag a midpoint to add one, Alt+click a corner to remove it.");
}

export function moveSelectionBy() {
  const info = selectionInfo();
  if (!info.planKeys.length) return;
  const box = unionBox(info.planKeys);
  const [sx, sy] = box ? toScreen((box[0] + box[2]) / 2, box[3]) : stageCenter();
  const what = info.planKeys.length === 1 ? describeKey(info.planKeys[0]) : `${info.planKeys.length} objects`;
  popInput({
    x: sx,
    y: sy + 12,
    label: `Move ${what} by`,
    hint: "Right, down: 6, -1'6\" (negative goes left or up). Or a distance and angle: 3'<90 moves 3' up.",
    onCommit: (text) => {
      const p = parseTyped(text, [0, 0], [1, 0]);
      if (!p) return message(`Can't read “${text}”. Try 6, 0 or 3'<90.`, true);
      moveKeys(info.planKeys, round2(p[0]), round2(p[1]), true);
    },
  });
}

export function flagSelection() {
  const info = selectionInfo();
  if (!info.planKeys.length) return;
  const box = unionBox(info.planKeys);
  if (!box) return;
  const a = app.canvas.toIn(box[0], box[1]).map(round2);
  const b = app.canvas.toIn(box[2], box[3]).map(round2);
  if (info.planKeys.length === 1) addFlag([round2((a[0] + b[0]) / 2), round2((a[1] + b[1]) / 2)], null, info.planKeys[0]);
  else addFlag(a, b, null);
}

export function flagAt(at) {
  const [wx, wy] = app.canvas.pt(at[0], at[1]);
  const hit = pickAt(wx, wy);
  addFlag(at, null, hit ? hit.key : null);
}

// ---------------------------------------------------------------- file and history

export async function save(mode = "overwrite", name) {
  if (!app.plan) return;
  const s = app.state;
  if (mode === "overwrite" && s.file === "plan.json" && s.files.some((f) => f !== "plan.json")) {
    if (!confirm("Save over plan.json, the traced base the steps were built from?")) return;
  }
  await call("/api/save", { mode, name }, { selectRef: false });
}

export function saveAs() {
  if (!app.plan) return;
  const [sx, sy] = stageCenter();
  popInput({
    x: sx,
    y: sy - 60,
    label: "Save as (a .json name in this folder)",
    value: app.state.file || "plan.json",
    hint: "Enter saves. Rendered PNG and overlay files are written next to it.",
    onCommit: (text) => text.trim() && save("as", text.trim()),
  });
}

export function revert() {
  if (!app.state || !app.state.dirty) return;
  if (confirm("Throw away unsaved changes and reload the saved file?")) call("/api/revert", {}, { selectRef: false });
}

export function undo() {
  return call("/api/undo", {}, { selectRef: false });
}

export function redo() {
  return call("/api/redo", {}, { selectRef: false });
}

export function typeScale() {
  if (!app.plan) return;
  const [sx, sy] = stageCenter();
  popInput({
    x: sx,
    y: sy - 60,
    label: "Scale: sheet pixels per inch",
    value: String(app.plan.canvas.px_per_inch),
    hint: "If you know a length on the sheet instead, use the Scale tool (K) and click its two ends.",
    onCommit: (text) => {
      const v = Number(text);
      if (!(v > 0)) return message(`Can't use “${text}” as a scale.`, true);
      send({ op: "calibrate", p1: [0, 0], p2: [v, 0], length: 1 }, { selectRef: false });
    },
  });
}

// ---------------------------------------------------------------- view toggles

export function toggleSide() {
  const before = svg.getBoundingClientRect();
  document.body.classList.toggle("side-hidden");
  const after = svg.getBoundingClientRect();
  app.view.x += (after.width - before.width) / 2;
  app.view.y += (after.height - before.height) / 2;
  applyView();
}

export function toggleView(id) {
  const box = $(id);
  box.checked = !box.checked;
  box.dispatchEvent(new Event("change"));
  message(`${box.closest("label").textContent.trim()}: ${box.checked ? "on" : "off"}`);
}

// ---------------------------------------------------------------- events

svg.addEventListener("pointerdown", async (e) => {
  if (!app.plan || app.busy) return;
  try {
    svg.setPointerCapture(e.pointerId);
  } catch {
    // synthetic events have no active pointer to capture
  }
  // The default mousedown would move focus after this handler and close any popover it opens.
  const focused = document.activeElement;
  if (focused && focused !== document.body && focused.blur) focused.blur();
  e.preventDefault();
  if (e.button === 1 || (e.button === 0 && app.spaceDown)) {
    app.drag = { type: "pan", sx: e.clientX, sy: e.clientY, vx: app.view.x, vy: app.view.y };
    svg.classList.add("panning");
    e.preventDefault();
    return;
  }
  if (e.button !== 0) return;
  const dimEl = e.target.closest && e.target.closest("[data-dim]");
  if (dimEl) {
    const d = app.dims[Number(dimEl.dataset.dim)];
    if (d) app.drag = { type: "dim-drag", dim: d, sx: e.clientX, sy: e.clientY, from: d.offset || [0, 0], moved: false };
    return;
  }
  const flagEl = e.target.closest && e.target.closest("g.flag");
  if (flagEl && (app.tool === "select" || app.tool === "flag")) {
    const key = flagEl.dataset.key;
    if (e.shiftKey || e.ctrlKey) select(key, { toggle: true });
    else if (!app.selection.has(key)) select(key);
    const f = getFlag(key.slice(6));
    if (f) app.drag = { type: "flag-move", id: f.id, el: flagEl, orig: [...f.px], start: toWorld(e), dx: 0, dy: 0, moved: false };
    return;
  }
  const tagEl = e.target.closest && e.target.closest("g.comment-tag");
  if (tagEl && app.tool === "select") {
    if (e.shiftKey || e.ctrlKey) select(tagEl.dataset.key, { toggle: true });
    else {
      select(tagEl.dataset.key);
      revealComments();
    }
    return;
  }
  if (app.tool !== "select") {
    await toolDown(e);
    renderUI();
    return;
  }
  const handleIndex = e.target.dataset ? e.target.dataset.h : undefined;
  if (handleIndex !== undefined) {
    startHandleDrag(e, app.handles[Number(handleIndex)]);
    return;
  }
  const [wx, wy] = toWorld(e);
  const hit = pickAt(wx, wy);
  if (e.shiftKey || e.ctrlKey || e.metaKey) {
    app.drag = { type: "marquee", start: [wx, wy], cur: null, add: e.shiftKey, toggle: hit ? hit.key : null };
  } else if (hit) {
    if (!app.selection.has(hit.key)) select(hit.key);
    const keys = [...app.selection].filter((k) => !k.startsWith("flags:"));
    app.drag = { type: "move", keys, start: [wx, wy], moved: false };
  } else {
    app.drag = { type: "pan", sx: e.clientX, sy: e.clientY, vx: app.view.x, vy: app.view.y, clickClears: true };
    svg.classList.add("panning");
  }
});

svg.addEventListener("dragstart", (e) => e.preventDefault());

window.addEventListener("pointermove", (e) => {
  if (!app.plan) return;
  const [wx, wy] = toWorld(e);
  const inch = app.canvas.toIn(wx, wy);
  const onStage = e.target === svg || svg.contains(e.target);
  app.cursor = { world: [wx, wy], in: inch, snap: null, onStage };
  $("status-cursor").textContent = `x ${formatLength(inch[0])}  y ${formatLength(inch[1])}`;
  if (!app.drag) {
    if (onStage) toolHover(e);
    return;
  }
  dragTo(e);
});

function dragTo(e) {
  const d = app.drag;
  if (d.type === "pan") {
    app.view.x = d.vx + e.clientX - d.sx;
    app.view.y = d.vy + e.clientY - d.sy;
    applyView();
  } else if (d.type === "move") moveMove(e);
  else if (d.type === "handle") handleMove(e);
  else if (d.type === "marquee") marqueeMove(e);
  else if (d.type === "rect") {
    const p = snapped(e);
    d.cur = [p.x, p.y];
    d.snap = p.snap;
    d.ghost = rectGhost(d.start, d.cur);
    renderUI();
  } else if (d.type === "flag-new") {
    const [wx, wy] = toWorld(e);
    d.cur = app.canvas.toIn(wx, wy).map(round2);
    const [ax, ay] = d.startWorld;
    d.ghost = `<rect class="flag-ghost" x="${f2(Math.min(ax, wx))}" y="${f2(Math.min(ay, wy))}" width="${f2(Math.abs(wx - ax))}" height="${f2(Math.abs(wy - ay))}" stroke-width="${px(1.5)}"/>`;
    renderUI();
  } else if (d.type === "dim-drag") {
    const dx = e.clientX - d.sx;
    const dy = e.clientY - d.sy;
    if (!d.moved && Math.hypot(dx, dy) < DRAG_SLOP) return;
    if (!d.moved) {
      d.moved = true;
      hideTip();
      svg.classList.add("dragging-dim");
    }
    app.dimNudge.set(d.dim.nudgeKey, [d.from[0] + dx, d.from[1] + dy]);
    renderUI();
  } else if (d.type === "flag-move") {
    const [wx, wy] = toWorld(e);
    d.dx = wx - d.start[0];
    d.dy = wy - d.start[1];
    if (!d.moved && Math.hypot(d.dx, d.dy) * app.view.z < 3) return;
    d.moved = true;
    d.el.setAttribute("transform", `translate(${d.orig[0] + d.dx} ${d.orig[1] + d.dy}) scale(${px(1)})`);
  }
}

const DRAG_SLOP = 4;
const REATTACH = 14;

/** A press on a measurement label that never moved is a click: open the box to type a value. */
function dimDragUp(d) {
  app.drag = null;
  svg.classList.remove("dragging-dim");
  const info = selectionInfo();
  if (!d.moved) {
    if (!info.single) return;
    const label = uiLayer.querySelector(`[data-dim="${app.dims.indexOf(d.dim)}"]`);
    const r = label && label.getBoundingClientRect();
    openDimInput(info.kind, info.id, d.dim, r ? [r.left + r.width / 2, r.bottom] : null);
    return;
  }
  const nudge = app.dimNudge.get(d.dim.nudgeKey);
  const auto = app.dimAuto.get(d.dim.nudgeKey) || [0, 0];
  if (nudge && (Math.hypot(...nudge) < REATTACH || Math.hypot(nudge[0] - auto[0], nudge[1] - auto[1]) < REATTACH)) app.dimNudge.delete(d.dim.nudgeKey);
}

window.addEventListener("pointerup", async (e) => {
  const d = app.drag;
  if (!d) return;
  svg.classList.remove("panning");
  // The release point counts even when no pointermove arrived (native drag-and-drop swallows them).
  if (d.type !== "handle" || e.clientX !== d.sx || e.clientY !== d.sy) dragTo(e);
  if (d.type === "pan") {
    app.drag = null;
    if (d.clickClears && Math.hypot(e.clientX - d.sx, e.clientY - d.sy) < 4) select(null);
  } else if (d.type === "move") await moveUp(e);
  else if (d.type === "handle") await handleUp(e);
  else if (d.type === "marquee") marqueeUp();
  else if (d.type === "flag-new") flagNewUp(e);
  else if (d.type === "flag-move") flagMoveUp();
  else if (d.type === "dim-drag") dimDragUp(d);
  else if (d.type === "rect" && d.kind === "fixtures") {
    app.drag = null;
    await placeFixture(d.start, d.cur);
  } else if (d.type === "rect") {
    app.drag = null;
    const [a, b] = [d.start, d.cur];
    if (Math.abs(b[0] - a[0]) < 0.5 || Math.abs(b[1] - a[1]) < 0.5) app.draft = { type: "rect", kind: d.kind, start: a, pts: [a] };
    else await placeRect(d.kind, a, b);
  }
  renderUI();
  syncFromServer();
});

svg.addEventListener("dblclick", async (e) => {
  if (!app.plan || (e.target.closest && e.target.closest("[data-dim]"))) return;
  if (app.tool === "wall") {
    app.draft = null;
    app.hoverGhost = null;
    renderUI();
    return;
  }
  if (app.tool === "polygon") {
    finishDraft();
    return;
  }
  if (app.tool !== "select") return;
  if (e.target.closest && e.target.closest("g.flag")) {
    editFlagNote(e.target.closest("g.flag").dataset.key.slice(6));
    return;
  }
  const [wx, wy] = toWorld(e);
  const hit = pickAt(wx, wy);
  if (hit && hit.kind === "walls") {
    const [x, y] = app.canvas.toIn(wx, wy);
    await send({ op: "split_wall", id: hit.id, x: round2(x), y: round2(y) });
    message("Wall split. The second half is selected.");
  } else if (hit && hit.kind !== "labels") editPrimaryDim();
});

svg.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  if (!app.plan) return;
  if (app.tool === "polygon" && app.draft) return finishDraft();
  if (app.draft) {
    app.draft = null;
    app.hoverGhost = null;
    renderUI();
    return;
  }
  const [wx, wy] = toWorld(e);
  const flagEl = e.target.closest && e.target.closest("g.flag");
  const hit = flagEl ? null : pickAt(wx, wy);
  const key = flagEl ? flagEl.dataset.key : hit ? hit.key : null;
  if (key && !app.selection.has(key)) select(key);
  showContextMenu(e.clientX, e.clientY, app.canvas.toIn(wx, wy).map(round2));
});

svg.addEventListener(
  "wheel",
  (e) => {
    e.preventDefault();
    const rect = svg.getBoundingClientRect();
    zoomAround(e.clientX - rect.left, e.clientY - rect.top, Math.exp(-e.deltaY * 0.0015));
  },
  { passive: false },
);

function nudge(key, e) {
  const step = e.shiftKey ? 12 : e.altKey ? 0.25 : 1;
  const [dx, dy] = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[key];
  const info = selectionInfo();
  if (info.planKeys.length) return moveKeys(info.planKeys, dx, dy, true);
  if (info.flagKeys.length === 1) {
    const f = getFlag(info.flagKeys[0].slice(6));
    if (f) moveFlag(f.id, round2(f.x + dx), round2(f.y + dy));
  }
}

function cancelGesture() {
  if (app.drag || app.draft) {
    app.draft = null;
    app.drag = null;
    app.hoverGhost = null;
    renderAll();
    return true;
  }
  return false;
}

window.addEventListener("keydown", (e) => {
  if (popOpen() || !$("palette").hidden) return;
  if (e.key === " " && !inField(e)) {
    app.spaceDown = true;
    svg.classList.add("pan-ready");
    e.preventDefault();
    return;
  }
  const ctrl = e.ctrlKey || e.metaKey;
  if (inField(e)) {
    if (ctrl && /^[sk]$/i.test(e.key)) handleKey(e);
    return;
  }
  const key = e.key;
  if (key === "Escape") {
    if (cancelGesture()) return;
    if (app.tool !== "select") setTool("select");
    else if (app.selection.size) select(null);
    return;
  }
  if (app.plan && !ctrl) {
    if (key === "Enter" && app.tool === "polygon" && app.draft) return finishDraft();
    if (key === "Enter" && app.tool === "wall" && app.draft) {
      app.draft = null;
      app.hoverGhost = null;
      return renderUI();
    }
    if (key === "Backspace" && app.draft && app.draft.type === "polygon") {
      e.preventDefault();
      app.draft.pts.pop();
      if (!app.draft.pts.length) app.draft = null;
      return renderUI();
    }
    if (typedTarget() && TYPABLE.test(key) && !e.altKey) {
      e.preventDefault();
      return openTyped(key);
    }
    if (key.startsWith("Arrow") && app.selection.size) {
      e.preventDefault();
      return nudge(key, e);
    }
    if (app.tool === "fixture" && key.toLowerCase() === "r") {
      app.opts.fixtureRot = (app.opts.fixtureRot + (e.shiftKey ? 270 : 90)) % 360;
      renderToolOptions();
      return;
    }
  }
  handleKey(e);
});

window.addEventListener("keyup", (e) => {
  if (e.key === " ") {
    app.spaceDown = false;
    svg.classList.remove("pan-ready");
  }
});

window.addEventListener("beforeunload", (e) => {
  if (app.state && app.state.dirty) {
    e.preventDefault();
    e.returnValue = "";
  }
});

window.addEventListener("resize", () => applyView());

for (const b of document.querySelectorAll("#tools button")) b.addEventListener("click", () => setTool(b.dataset.tool));
$("toggle-side").addEventListener("click", toggleSide);
$("undo").addEventListener("click", undo);
$("redo").addEventListener("click", redo);
$("save").addEventListener("click", () => save("overwrite"));
$("save-step").addEventListener("click", () => save("step"));

$("show-sheet").addEventListener("change", (e) => {
  app.show.sheet = e.target.checked;
  renderAll();
});
$("sheet-opacity").addEventListener("input", (e) => {
  app.show.opacity = Number(e.target.value);
  sheetEl.setAttribute("opacity", app.show.opacity);
});
$("show-erase").addEventListener("change", (e) => {
  app.show.erase = e.target.checked;
  renderErase();
});
$("show-dims").addEventListener("change", (e) => {
  app.show.dims = e.target.checked;
  renderUI();
});
$("show-ids").addEventListener("change", (e) => {
  app.show.ids = e.target.checked;
  renderIds();
});
$("show-comments").addEventListener("change", (e) => {
  app.show.comments = e.target.checked;
  renderMarkers();
});
$("snap").addEventListener("change", (e) => (app.show.snap = e.target.checked));
$("grid").addEventListener("change", (e) => (app.show.grid = e.target.checked));
app.show.opacity = Number($("sheet-opacity").value);

initPanels(app);
initOpen();
initCommands();
initTips();
setTool("select");
api("/api/state")
  .then((state) => {
    setState(state);
    followServer();
  })
  .catch((err) => message(err.message, true));
