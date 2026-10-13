// Floor plan editor: draws the server's scene as SVG and turns mouse gestures into edit operations.

import { api } from "./api.js";
import {
  Canvas,
  arcPath,
  formatLength,
  hitArea,
  indexScene,
  lockAxis,
  parseLength,
  pick,
  project,
  rotate,
  round2,
  snapPoint,
  wallLength,
} from "./geom.js";
import { initPanels, renderFiles, renderProps } from "./panels.js";

const $ = (id) => document.getElementById(id);
const svg = $("svg");
const world = $("world");
const sheetEl = $("sheet");
const paper = $("paper");
const planLayer = $("plan-layer");
const eraseLayer = $("erase-layer");
const traceLayer = $("trace-layer");
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
const ONE_SHOT = new Set(["door", "window", "counter", "fixture", "label", "erase"]);
const TOOL_HINTS = {
  select: "Click to select; drag to move (Alt detaches joined walls, Shift locks the axis). Drag handles to reshape. Double-click a wall to split it. Drag empty space to pan; wheel zooms.",
  wall: "Click each corner of the wall. Shift keeps it straight. Double-click, right-click, or Esc to stop.",
  door: "Click on a wall to drop a door there.",
  window: "Click on a wall to drop a window there.",
  counter: "Drag a rectangle (or click two corners) for a box counter.",
  polygon: "Click each corner of the counter outline. Double-click or Enter closes it; Backspace removes the last corner.",
  fixture: "Pick a fixture type, then click to place it. R rotates it 90°.",
  label: "Type the text in the toolbar, then click where the label goes.",
  erase: "Drag a rectangle (or click two corners) over printed ink to white it out on the sheet.",
  calibrate: "Click two points a known distance apart (Shift keeps it straight), then type the real length, e.g. 26'5\".",
};

export const app = {
  state: null,
  plan: null,
  canvas: null,
  objects: new Map(),
  sel: null,
  tool: "select",
  view: { x: 20, y: 20, z: 1 },
  show: { sheet: true, opacity: 0.55, erase: true, snap: true, grid: true },
  opts: { thickness: 4, doorWidth: 30, windowWidth: 36, fixtureType: "refrigerator", fixtureRot: 0, labelText: "" },
  drag: null,
  draft: null,
  cursor: null,
  handles: [],
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
  const el = $("status-msg");
  el.textContent = text || "";
  el.className = isError ? "error" : "";
  el.title = text || "";
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

// Other clients (Cursor through the MCP server) change the same document. The server
// streams its version and UI commands; catch up between our own requests and drags.
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
    const el = $(id);
    if (el.type === "checkbox") el.checked = !!value;
    else el.value = value;
    el.dispatchEvent(new Event(el.type === "checkbox" ? "change" : "input"));
  };
  switch (cmd.action) {
    case "select":
      select(cmd.key && getObject(cmd.key) ? cmd.key : null);
      if (cmd.zoom && app.sel) zoomToSelection();
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
      if (TOOL_HINTS[cmd.tool]) setTool(cmd.tool);
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

export function setState(state, { selectRef = true } = {}) {
  app.state = state;
  app.plan = state.plan;
  app.canvas = state.plan ? new Canvas(state.plan) : null;
  app.objects = state.plan ? indexScene(state.scene) : new Map();
  if (selectRef && state.ref) app.sel = `${state.ref[0]}:${state.ref[1]}`;
  else if (state.ref === null && "ref" in state) app.sel = null;
  if (app.sel && !getObject(app.sel)) app.sel = null;
  $("empty").hidden = !!state.plan;
  if (state.plan) {
    const fitKey = `${state.folder}|${app.canvas.width}x${app.canvas.height}`;
    if (app.fittedFor !== fitKey) fitView();
    app.fittedFor = fitKey;
  }
  renderAll();
  renderFiles(app);
  renderProps(app);
  if (state.saved) {
    const warn = state.saved.warnings.length ? ` (${state.saved.warnings.join("; ")})` : "";
    message(`Saved ${state.saved.files.join(", ")}${warn}`);
  }
}

// ---------------------------------------------------------------- objects

export function getObject(key) {
  if (!app.plan || !key) return null;
  const [kind, ...rest] = key.split(":");
  const id = rest.join(":");
  const list = app.plan[kind] || [];
  if (kind === "labels") return list[Number(id)] || null;
  return list.find((item) => item.id === id) || null;
}

export function splitKey(key) {
  const [kind, ...rest] = key.split(":");
  const id = rest.join(":");
  return [kind, kind === "labels" ? Number(id) : id];
}

export function select(key) {
  app.sel = key;
  for (const el of document.querySelectorAll("#plan-layer .selected, #erase-layer .selected")) {
    el.classList.remove("selected");
    el.removeAttribute("role");
    el.removeAttribute("aria-label");
  }
  if (key) {
    const el = document.querySelector(`[data-key="${CSS.escape(key)}"]`);
    if (el) {
      el.classList.add("selected");
      el.setAttribute("role", "button");
      el.setAttribute("aria-label", `selected ${key}`);
    }
  }
  renderUI();
  renderProps(app);
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

// ---------------------------------------------------------------- drawing

const esc = (t) => String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
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
  }
  sheetEl.setAttribute("width", s.sheet.width);
  sheetEl.setAttribute("height", s.sheet.height);
  sheetEl.style.display = show ? "" : "none";
  sheetEl.setAttribute("opacity", app.show.opacity);
}

function renderPlan() {
  const parts = [];
  for (const entry of app.objects.values()) {
    const sel = entry.key === app.sel ? ` selected" role="button" aria-label="selected ${esc(entry.key)}` : "";
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
      const sel = key === app.sel ? ` selected" role="button" aria-label="selected ${esc(key)}` : "";
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

function updateToolbar() {
  const s = app.state;
  $("undo").disabled = !s || !s.can_undo;
  $("redo").disabled = !s || !s.can_redo;
  $("save").disabled = !s || !s.plan;
  $("save-step").disabled = !s || !s.plan;
  for (const b of document.querySelectorAll("#tools button")) b.classList.toggle("active", b.dataset.tool === app.tool);
  const file = s && s.file ? `${s.file}${s.dirty ? " •" : ""}` : "no plan";
  $("status-file").textContent = file;
  document.title = `${s && s.dirty ? "• " : ""}${s && s.file ? s.file : "floorplan editor"}`;
}

// ---------------------------------------------------------------- UI layer: handles, ghosts, snap marks

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

export function renderUI() {
  if (!app.plan) {
    uiLayer.innerHTML = "";
    return;
  }
  const parts = [];
  const r = px(5);
  const sw = px(1.5);
  const d = app.drag;
  const ghost = d && d.ghost ? d.ghost : app.draft && app.draft.ghost ? app.draft.ghost : app.hoverGhost;
  if (ghost) parts.push(ghost);
  app.handles = app.tool === "select" && app.sel && !(d && d.type === "move") ? handlesFor(app.sel) : [];
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
  message(TOOL_HINTS[tool] || "");
  svg.style.cursor = tool === "select" ? "default" : "crosshair";
}

function renderToolOptions() {
  const box = $("tool-options");
  const o = app.opts;
  const lenInput = (label, key) =>
    `<label>${label} <input data-opt="${key}" value="${o[key]}" title="inches, or feet and inches like 2'6&quot;"/></label>`;
  if (app.tool === "wall") box.innerHTML = lenInput("Thickness", "thickness");
  else if (app.tool === "door") box.innerHTML = lenInput("Width", "doorWidth");
  else if (app.tool === "window") box.innerHTML = lenInput("Width", "windowWidth");
  else if (app.tool === "fixture") {
    const types = (app.state && app.state.fixture_types) || Object.keys(FIXTURE_SIZES);
    box.innerHTML =
      `<label>Type <select data-opt="fixtureType">${types
        .map((t) => `<option ${t === o.fixtureType ? "selected" : ""}>${t}</option>`)
        .join("")}</select></label>` +
      `<label>Rotation <select data-opt="fixtureRot">${[0, 90, 180, 270]
        .map((r) => `<option ${r === o.fixtureRot ? "selected" : ""}>${r}</option>`)
        .join("")}</select></label>`;
  } else if (app.tool === "label") {
    box.innerHTML = `<label>Text <input data-opt="labelText" value="${esc(o.labelText).replace(/"/g, "&quot;")}" placeholder="e.g. PAN"/></label>`;
  } else box.innerHTML = "";
  for (const el of box.querySelectorAll("[data-opt]")) {
    if (el.dataset.opt === "labelText") {
      el.addEventListener("input", () => (o.labelText = el.value));
      continue;
    }
    el.addEventListener("change", () => {
      const key = el.dataset.opt;
      if (key === "fixtureType") o[key] = el.value;
      else if (key === "fixtureRot") o[key] = Number(el.value);
      else {
        const v = parseLength(el.value);
        if (Number.isFinite(v) && v > 0) o[key] = v;
        el.value = o[key];
      }
    });
  }
}

function finishDraft() {
  const d = app.draft;
  app.draft = null;
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

async function toolDown(e) {
  const plan = app.plan;
  const tool = app.tool;
  if (tool === "wall") {
    const anchor = app.draft ? app.draft.pts[app.draft.pts.length - 1] : null;
    const p = snapped(e, anchor);
    if (!app.draft) {
      app.draft = { type: "wall", pts: [[p.x, p.y]] };
      return;
    }
    if (!pushPoint(app.draft.pts, [p.x, p.y])) return;
    const [a, b] = app.draft.pts.slice(-2);
    await send(
      { op: "add", kind: "walls", obj: { x1: a[0], y1: a[1], x2: b[0], y2: b[1], thickness: app.opts.thickness } },
      { selectRef: false },
    );
    return;
  }
  if (tool === "door" || tool === "window") {
    const [wx, wy] = toWorld(e);
    const [x, y] = app.canvas.toIn(wx, wy);
    const hit = nearestWall(x, y);
    if (!hit) {
      message("Click on a wall.", true);
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
      app.drag = { type: "rect", kind, start: app.draft.start, cur: [p.x, p.y] };
      app.draft = null;
      await rectUp();
      return;
    }
    app.drag = { type: "rect", kind, start: [p.x, p.y], cur: [p.x, p.y] };
    return;
  }
  if (tool === "polygon") {
    const anchor = app.draft ? app.draft.pts[app.draft.pts.length - 1] : null;
    const p = snapped(e, anchor);
    if (!app.draft) app.draft = { type: "polygon", pts: [] };
    pushPoint(app.draft.pts, [p.x, p.y]);
    return;
  }
  if (tool === "fixture") {
    const p = snapped(e);
    const [w, h] = FIXTURE_SIZES[app.opts.fixtureType] || [24, 24];
    const obj = { type: app.opts.fixtureType, x: p.x, y: p.y, w, h };
    if (app.opts.fixtureRot) obj.rot = app.opts.fixtureRot;
    const ref = await send({ op: "add", kind: "fixtures", obj });
    if (ref) setTool("select");
    return;
  }
  if (tool === "label") {
    const p = snapped(e);
    const text = app.opts.labelText.trim() || prompt("Label text");
    if (text && text.trim()) {
      await send({ op: "add", kind: "labels", obj: { text: text.trim(), x: p.x, y: p.y } });
      setTool("select");
    }
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
    const pixels = Math.hypot(pt[0] - p1[0], pt[1] - p1[1]);
    const now = formatLength(pixels / app.canvas.scale);
    const text = prompt(`Real length between the two points? (now ${now}) e.g. 26'5"`);
    if (!text) return;
    const inches = parseLength(text);
    if (!Number.isFinite(inches) || inches <= 0) {
      message(`Cannot read a length from ${text}`, true);
      return;
    }
    const ref = await send({ op: "calibrate", p1, p2: pt, length: inches });
    if (ref !== undefined) message(`Scale set: ${app.plan.canvas.px_per_inch.toFixed(4)} px per inch.`);
    setTool("select");
  }
}

function toolHover(e) {
  const tool = app.tool;
  const d = app.draft;
  app.hoverGhost = null;
  if (tool === "wall" && d) {
    const a = d.pts[d.pts.length - 1];
    const p = snapped(e, a);
    app.cursor.snap = p.snap;
    app.hoverGhost = lineGhost(a, [p.x, p.y], app.opts.thickness, formatLength(Math.hypot(p.x - a[0], p.y - a[1])));
  } else if (tool === "wall" || tool === "polygon" || tool === "counter" || tool === "erase" || tool === "label") {
    const anchor = d && d.pts.length ? d.pts[d.pts.length - 1] : null;
    const p = snapped(e, anchor);
    app.cursor.snap = p.snap;
    if (tool === "polygon" && d && d.pts.length) {
      app.hoverGhost = polyGhost([...d.pts, [p.x, p.y]], false) + lineGhost(anchor, [p.x, p.y], 0, formatLength(Math.hypot(p.x - anchor[0], p.y - anchor[1])));
    } else if (d && d.type === "rect") {
      app.hoverGhost = rectGhost(d.start, [p.x, p.y]);
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
  } else if (tool === "fixture") {
    const p = snapped(e);
    const [w, h] = FIXTURE_SIZES[app.opts.fixtureType] || [24, 24];
    app.hoverGhost = fixtureGhost(p.x, p.y, w, h, app.opts.fixtureRot);
  } else if (tool === "calibrate" && d) {
    const [wx, wy] = toWorld(e);
    const p2 = e.shiftKey ? lockAxis(d.pts[0], wx, wy) : [wx, wy];
    const [ax, ay] = d.pts[0];
    const len = Math.hypot(p2[0] - ax, p2[1] - ay);
    app.hoverGhost =
      `<line class="ghost-line" x1="${f2(ax)}" y1="${f2(ay)}" x2="${f2(p2[0])}" y2="${f2(p2[1])}" stroke-width="${px(2)}"/>` +
      measureText((ax + p2[0]) / 2, (ay + p2[1]) / 2, `${len.toFixed(1)} px`);
  }
  renderUI();
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
  const el = document.querySelector(`[data-key="${CSS.escape(d.key)}"]`);
  if (el) el.setAttribute("transform", `translate(${tx} ${ty})`);
  d.ghost = measureText(wx, wy, `${formatLength(d.dIn[0])}, ${formatLength(d.dIn[1])}`);
  renderUI();
}

async function moveUp(e) {
  const d = app.drag;
  app.drag = null;
  if (!d.moved || !d.dIn || (d.dIn[0] === 0 && d.dIn[1] === 0)) {
    renderAll();
    return;
  }
  const [kind, id] = splitKey(d.key);
  await send({ op: "move", kind, id, dx: d.dIn[0], dy: d.dIn[1], join: !e.altKey });
}

async function rectUp() {
  const d = app.drag;
  app.drag = null;
  const [a, b] = [d.start, d.cur];
  const w = Math.abs(b[0] - a[0]);
  const h = Math.abs(b[1] - a[1]);
  if (w < 0.5 || h < 0.5) {
    app.draft = { type: "rect", kind: d.kind, start: a, pts: [a] };
    message("Now click the opposite corner (or drag next time).");
    renderUI();
    return;
  }
  const obj = { x: round2(Math.min(a[0], b[0])), y: round2(Math.min(a[1], b[1])), w: round2(w), h: round2(h) };
  const ref = await send({ op: "add", kind: d.kind, obj });
  if (ref) setTool("select");
}

// ---------------------------------------------------------------- events

svg.addEventListener("pointerdown", async (e) => {
  if (!app.plan || app.busy) return;
  try {
    svg.setPointerCapture(e.pointerId);
  } catch {
    // synthetic events have no active pointer to capture
  }
  if (e.button === 1 || (e.button === 0 && app.spaceDown)) {
    app.drag = { type: "pan", sx: e.clientX, sy: e.clientY, vx: app.view.x, vy: app.view.y };
    e.preventDefault();
    return;
  }
  if (e.button !== 0) return;
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
  if (hit) {
    if (hit.key !== app.sel) select(hit.key);
    app.drag = { type: "move", key: hit.key, start: [wx, wy], moved: false };
  } else {
    if (app.sel) select(null);
    app.drag = { type: "pan", sx: e.clientX, sy: e.clientY, vx: app.view.x, vy: app.view.y };
  }
});

window.addEventListener("pointermove", (e) => {
  if (!app.plan) return;
  const [wx, wy] = toWorld(e);
  const inch = app.canvas.toIn(wx, wy);
  app.cursor = { world: [wx, wy], in: inch, snap: null };
  $("status-cursor").textContent = `x ${formatLength(inch[0])}  y ${formatLength(inch[1])}`;
  if (!app.drag) {
    if (e.target === svg || svg.contains(e.target)) toolHover(e);
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
  else if (d.type === "rect") {
    const p = snapped(e);
    d.cur = [p.x, p.y];
    d.snap = p.snap;
    d.ghost = rectGhost(d.start, d.cur);
    renderUI();
  }
}

window.addEventListener("pointerup", async (e) => {
  const d = app.drag;
  if (!d) return;
  // The release point counts even when no pointermove arrived (native drag-and-drop swallows them).
  if (d.type !== "handle" || e.clientX !== d.sx || e.clientY !== d.sy) dragTo(e);
  if (d.type === "pan") app.drag = null;
  else if (d.type === "move") await moveUp(e);
  else if (d.type === "handle") await handleUp(e);
  else if (d.type === "rect") await rectUp();
  renderUI();
  syncFromServer();
});

svg.addEventListener("dblclick", async (e) => {
  if (!app.plan) return;
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
  const [wx, wy] = toWorld(e);
  const hit = pickAt(wx, wy);
  if (hit && hit.kind === "walls") {
    const [x, y] = app.canvas.toIn(wx, wy);
    await send({ op: "split_wall", id: hit.id, x, y });
    message("Wall split. The second half is selected.");
  }
});

svg.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  if (app.tool === "polygon") finishDraft();
  else if (app.draft) {
    app.draft = null;
    app.hoverGhost = null;
    renderUI();
  }
});

svg.addEventListener(
  "wheel",
  (e) => {
    e.preventDefault();
    const rect = svg.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const factor = Math.exp(-e.deltaY * 0.0015);
    const z = Math.max(0.05, Math.min(40, app.view.z * factor));
    const k = z / app.view.z;
    app.view.x = mx - (mx - app.view.x) * k;
    app.view.y = my - (my - app.view.y) * k;
    app.view.z = z;
    applyView();
  },
  { passive: false },
);

function zoomBy(factor) {
  const rect = svg.getBoundingClientRect();
  const mx = rect.width / 2;
  const my = rect.height / 2;
  const z = Math.max(0.05, Math.min(40, app.view.z * factor));
  const k = z / app.view.z;
  app.view.x = mx - (mx - app.view.x) * k;
  app.view.y = my - (my - app.view.y) * k;
  app.view.z = z;
  applyView();
}

function zoomToSelection() {
  const entry = app.sel && app.objects.get(app.sel);
  if (!entry) return message("Select something first; Z zooms to it.", true);
  zoomToBox(entry.box);
}

function zoomToBox([x0, y0, x1, y1]) {
  const rect = svg.getBoundingClientRect();
  const fit = Math.min((rect.width - 80) / Math.max(x1 - x0, 1), (rect.height - 80) / Math.max(y1 - y0, 1));
  const z = Math.max(0.05, Math.min(4, fit));
  app.view = { z, x: rect.width / 2 - ((x0 + x1) / 2) * z, y: rect.height / 2 - ((y0 + y1) / 2) * z };
  applyView();
}

export async function deleteSelection() {
  if (!app.sel) return;
  const [kind, id] = splitKey(app.sel);
  await send({ op: "delete", kind, id });
}

export async function rotateSelection(deg = 90) {
  if (!app.sel) return;
  const [kind, id] = splitKey(app.sel);
  if (kind !== "fixtures") return;
  const obj = getObject(app.sel);
  await send({ op: "update", kind, id, fields: { rot: (((obj.rot || 0) + deg) % 360 + 360) % 360 } });
}

export async function flipDoor(what) {
  if (!app.sel) return;
  const [kind, id] = splitKey(app.sel);
  if (kind !== "openings" || getObject(app.sel).type !== "door") return;
  await send({ op: "flip_door", id, what });
}

export async function save(mode = "overwrite", name) {
  if (!app.plan) return;
  const s = app.state;
  if (mode === "overwrite" && s.file === "plan.json" && s.files.some((f) => f !== "plan.json")) {
    if (!confirm("Save over plan.json, the traced base the steps were built from?")) return;
  }
  await call("/api/save", { mode, name });
}

const TOOL_KEYS = { v: "select", w: "wall", d: "door", n: "window", c: "counter", p: "polygon", x: "fixture", t: "label", e: "erase", k: "calibrate" };

window.addEventListener("keydown", async (e) => {
  if (e.key === " " && !inField(e)) {
    app.spaceDown = true;
    e.preventDefault();
    return;
  }
  const ctrl = e.ctrlKey || e.metaKey;
  if (ctrl && e.key.toLowerCase() === "s") {
    e.preventDefault();
    await save("overwrite");
    return;
  }
  if (inField(e)) return;
  if (ctrl && e.key.toLowerCase() === "z") {
    e.preventDefault();
    await call(e.shiftKey ? "/api/redo" : "/api/undo", {}, { selectRef: false });
    return;
  }
  if (ctrl && e.key.toLowerCase() === "y") {
    e.preventDefault();
    await call("/api/redo", {}, { selectRef: false });
    return;
  }
  if (ctrl || !app.plan) return;
  const key = e.key;
  if (key === "Escape") {
    if (app.draft || app.drag) {
      app.draft = null;
      app.drag = null;
      app.hoverGhost = null;
      renderAll();
    } else if (app.tool !== "select") setTool("select");
    else select(null);
    return;
  }
  if (key === "Enter" && app.tool === "polygon") return finishDraft();
  if (key === "Enter" && app.tool === "wall") {
    app.draft = null;
    return renderUI();
  }
  if (key === "Backspace" && app.draft && app.draft.type === "polygon") {
    app.draft.pts.pop();
    return renderUI();
  }
  if ((key === "Delete" || key === "Backspace") && app.sel) {
    e.preventDefault();
    return deleteSelection();
  }
  if (key.startsWith("Arrow") && app.sel) {
    e.preventDefault();
    const step = e.shiftKey ? 12 : e.altKey ? 0.25 : 1;
    const [dx, dy] = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[key];
    const [kind, id] = splitKey(app.sel);
    return send({ op: "move", kind, id, dx, dy, join: true });
  }
  const lower = key.toLowerCase();
  if (lower === "r") {
    if (app.tool === "fixture") {
      app.opts.fixtureRot = (app.opts.fixtureRot + 90) % 360;
      renderToolOptions();
      return;
    }
    return rotateSelection(e.shiftKey ? -90 : 90);
  }
  if (lower === "f" && app.sel) return flipDoor("swing");
  if (lower === "h" && app.sel) return flipDoor("hinge");
  if (key === "\\") return toggleSide();
  if (key === "0") return fitView();
  if (key === "+" || key === "=") return zoomBy(1.25);
  if (key === "-") return zoomBy(0.8);
  if (lower === "z") return zoomToSelection();
  if (TOOL_KEYS[lower]) setTool(TOOL_KEYS[lower]);
});

window.addEventListener("keyup", (e) => {
  if (e.key === " ") app.spaceDown = false;
});

window.addEventListener("beforeunload", (e) => {
  if (app.state && app.state.dirty) {
    e.preventDefault();
    e.returnValue = "";
  }
});

window.addEventListener("resize", () => applyView());

function toggleSide() {
  const before = svg.getBoundingClientRect();
  document.body.classList.toggle("side-hidden");
  const after = svg.getBoundingClientRect();
  app.view.x += (after.width - before.width) / 2;
  app.view.y += (after.height - before.height) / 2;
  applyView();
}

for (const b of document.querySelectorAll("#tools button")) b.addEventListener("click", () => setTool(b.dataset.tool));
$("toggle-side").addEventListener("click", toggleSide);
$("undo").addEventListener("click", () => call("/api/undo", {}, { selectRef: false }));
$("redo").addEventListener("click", () => call("/api/redo", {}, { selectRef: false }));
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
$("snap").addEventListener("change", (e) => (app.show.snap = e.target.checked));
$("grid").addEventListener("change", (e) => (app.show.grid = e.target.checked));
app.show.opacity = Number($("sheet-opacity").value);

initPanels(app, { call, send, setState, select, setTool, message, save, deleteSelection, rotateSelection, flipDoor, fitView, renderTrace, getObject, splitKey });
setTool("select");
api("/api/state")
  .then((state) => {
    setState(state);
    followServer();
  })
  .catch((err) => message(err.message, true));
