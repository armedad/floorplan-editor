// Side panels: folder and steps, properties of the selection, new plan, auto-trace.

import { api, readFileBase64 } from "./api.js";
import { formatLength, parseLength, round2, wallLength } from "./geom.js";

const $ = (id) => document.getElementById(id);
const CARDINALS = ["north", "east", "south", "west"];
let A = null;
let app = null;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "on") for (const [ev, fn] of Object.entries(v)) node.addEventListener(ev, fn);
    else if (k === "class") node.className = v;
    else if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v === true ? "" : v);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function confirmDiscard() {
  return !app.state || !app.state.dirty || confirm("You have unsaved changes. Discard them?");
}

// ---------------------------------------------------------------- folder and steps

export function renderFiles(appRef) {
  app = appRef;
  const s = app.state;
  if (!s) return;
  const input = $("folder-input");
  if (document.activeElement !== input) input.value = s.folder;
  const list = $("file-list");
  list.replaceChildren();
  for (const name of s.files) {
    const mergeable = name !== "plan.json" && name !== "plan-merged.json";
    const stem = name.replace(/\.json$/, "");
    list.append(
      el(
        "li",
        { class: name === s.file ? "current" : "" },
        el("input", { type: "checkbox", "data-step": name, disabled: !mergeable, title: mergeable ? "include in merge" : "" }),
        el(
          "button",
          {
            type: "button",
            class: "file-name",
            title: `open ${name}`,
            on: {
              click: () => {
                if (name !== s.file && confirmDiscard()) A.call("/api/open", { file: name }, { selectRef: false });
              },
            },
          },
          name === s.file && s.dirty ? `${name} •` : name,
        ),
        el(
          "span",
          { class: "links" },
          el("a", { href: `/api/file/${encodeURIComponent(stem)}.png`, target: "_blank" }, "png"),
          " ",
          el("a", { href: `/api/file/${encodeURIComponent(stem)}-overlay.png`, target: "_blank" }, "overlay"),
        ),
      ),
    );
  }
  if (!s.files.length) list.append(el("li", { class: "note" }, "No plan files here yet."));
  if (s.file && !s.files.includes(s.file)) list.append(el("li", { class: "current" }, `${s.file} (new, unsaved)`));
  $("save-step").title = `Save as ${s.next_step}`;
  $("save-step").textContent = `Save as ${s.next_step.replace(".json", "")}`;
  const saved = $("saved-files");
  saved.replaceChildren();
  if (s.saved) {
    saved.append("Wrote ");
    s.saved.files.forEach((f, i) => {
      if (i) saved.append(", ");
      saved.append(el("a", { href: `/api/file/${encodeURIComponent(f)}`, target: "_blank" }, f));
    });
    if (s.saved.warnings.length) saved.append(el("div", { class: "error" }, s.saved.warnings.join("; ")));
  }
}

// ---------------------------------------------------------------- properties

function lengthField(label, value, commit, { hint = true } = {}) {
  const input = el("input", { value: round2(value) });
  input.addEventListener("change", () => {
    const v = parseLength(input.value);
    if (!Number.isFinite(v)) {
      A.message(`Cannot read a length from ${input.value}`, true);
      input.value = round2(value);
      return;
    }
    commit(round2(v));
  });
  return el("div", { class: "field" }, el("label", {}, label), input, el("span", { class: "hint" }, hint ? formatLength(value) : ""));
}

function numberField(label, value, commit, unit = "") {
  const input = el("input", { value: round2(value) });
  input.addEventListener("change", () => {
    const v = Number(input.value);
    if (!Number.isFinite(v)) {
      input.value = round2(value);
      return;
    }
    commit(v);
  });
  return el("div", { class: "field" }, el("label", {}, label), input, el("span", { class: "hint" }, unit));
}

function textField(label, value, commit) {
  const input = el("input", { value: value ?? "" });
  input.addEventListener("change", () => commit(input.value));
  return el("div", { class: "field" }, el("label", {}, label), input, el("span"));
}

function selectField(label, value, options, commit) {
  const select = el(
    "select",
    { on: { change: () => commit(select.value) } },
    options.map(([v, text]) => el("option", { value: v, selected: v === value }, text)),
  );
  return el("div", { class: "field" }, el("label", {}, label), select, el("span"));
}

function buttons(...items) {
  return el("div", { class: "row wrap" }, items.map(([text, fn, cls]) => el("button", { class: cls || "", on: { click: fn } }, text)));
}

function update(kind, id, fields) {
  return A.send({ op: "update", kind, id, fields });
}

function idField(kind, obj) {
  return textField("id", obj.id, (v) => update(kind, obj.id, { id: v.trim() }));
}

function wallProps(w) {
  const k = "walls";
  const len = wallLength(w);
  return [
    idField(k, w),
    lengthField("x1", w.x1, (v) => update(k, w.id, { x1: v })),
    lengthField("y1", w.y1, (v) => update(k, w.id, { y1: v })),
    lengthField("x2", w.x2, (v) => update(k, w.id, { x2: v })),
    lengthField("y2", w.y2, (v) => update(k, w.id, { y2: v })),
    lengthField("thickness", w.thickness, (v) => update(k, w.id, { thickness: v })),
    lengthField("length", len, (v) => {
      if (v <= 0) return;
      const ux = (w.x2 - w.x1) / len;
      const uy = (w.y2 - w.y1) / len;
      A.send({ op: "move_wall_end", id: w.id, end: "end", x: round2(w.x1 + ux * v), y: round2(w.y1 + uy * v), join: true });
    }),
    el("p", { class: "note" }, "Drag an end to stretch it; ends that meet another wall move together (hold Alt to pull it free). Double-click the wall to split it."),
    buttons(
      ["Split in the middle", () => A.send({ op: "split_wall", id: w.id, x: (w.x1 + w.x2) / 2, y: (w.y1 + w.y2) / 2 })],
      ["Delete", () => A.deleteSelection()],
    ),
  ];
}

function openingProps(o) {
  const k = "openings";
  const door = o.type === "door";
  const items = [
    idField(k, o),
    selectField(
      "type",
      o.type,
      [
        ["door", "door"],
        ["window", "window"],
      ],
      (v) => update(k, o.id, v === "door" ? { type: "door", hinge: "start", swing: "ccw" } : { type: "window", hinge: null, swing: null }),
    ),
    textField("wall", o.wall, (v) => update(k, o.id, { wall: v.trim() })),
    lengthField("offset", o.offset, (v) => update(k, o.id, { offset: v })),
    lengthField("width", o.width, (v) => update(k, o.id, { width: v })),
  ];
  if (door) {
    items.push(
      selectField("hinge", o.hinge || "start", [["start", "start"], ["end", "end"]], (v) => update(k, o.id, { hinge: v })),
      selectField("swing", o.swing || "ccw", [["ccw", "ccw"], ["cw", "cw"]], (v) => update(k, o.id, { swing: v })),
      buttons(["Flip hinge (H)", () => A.flipDoor("hinge")], ["Flip swing (F)", () => A.flipDoor("swing")], ["Delete", () => A.deleteSelection()]),
    );
  } else items.push(buttons(["Delete", () => A.deleteSelection()]));
  items.push(el("p", { class: "note" }, "Drag it along its wall; drag its end dots to change the width."));
  return items;
}

function counterProps(c) {
  const k = "counters";
  if (c.points) {
    const area = el("textarea", { spellcheck: "false" }, c.points.map(([x, y]) => `${x}, ${y}`).join("\n"));
    area.addEventListener("change", () => {
      const points = area.value
        .split(/\n+/)
        .map((line) => line.split(/[,\s]+/).filter(Boolean).map(Number))
        .filter((p) => p.length === 2 && p.every(Number.isFinite));
      if (points.length < 3) return A.message("An outline needs at least 3 corners, one 'x, y' per line.", true);
      update(k, c.id, { points });
    });
    return [
      idField(k, c),
      el("label", {}, "Corners (x, y in inches)"),
      area,
      el("p", { class: "note" }, "Drag a corner to move it, drag a midpoint dot to add a corner, Alt+click a corner to remove it."),
      buttons(["Delete", () => A.deleteSelection()]),
    ];
  }
  const over = c.overhang || null;
  return [
    idField(k, c),
    lengthField("x", c.x, (v) => update(k, c.id, { x: v })),
    lengthField("y", c.y, (v) => update(k, c.id, { y: v })),
    lengthField("w", c.w, (v) => update(k, c.id, { w: v })),
    lengthField("h", c.h, (v) => update(k, c.id, { h: v })),
    selectField(
      "overhang",
      over ? over.edge : "",
      [["", "none"], ...CARDINALS.map((d) => [d, d])],
      (v) => update(k, c.id, { overhang: v ? { edge: v, inches: over ? over.inches : 12 } : null }),
    ),
    over ? lengthField("overhang in", over.inches, (v) => update(k, c.id, { overhang: { edge: over.edge, inches: v } })) : null,
    buttons(["Edit as outline", () => A.send({ op: "box_to_polygon", id: c.id })], ["Delete", () => A.deleteSelection()]),
  ];
}

function fixtureProps(f) {
  const k = "fixtures";
  const types = (app.state && app.state.fixture_types) || [];
  return [
    idField(k, f),
    selectField("type", f.type, types.map((t) => [t, t]), (v) => update(k, f.id, { type: v })),
    lengthField("x (center)", f.x, (v) => update(k, f.id, { x: v })),
    lengthField("y (center)", f.y, (v) => update(k, f.id, { y: v })),
    lengthField("w", f.w, (v) => update(k, f.id, { w: v })),
    lengthField("h", f.h, (v) => update(k, f.id, { h: v })),
    numberField("rotation", f.rot || 0, (v) => update(k, f.id, { rot: v ? ((v % 360) + 360) % 360 : null }), "degrees"),
    el("p", { class: "note" }, "Drag the corner dot to resize, the yellow dot to rotate (snaps to 15°, Alt for free)."),
    buttons(["Rotate 90° (R)", () => A.rotateSelection(90)], ["Delete", () => A.deleteSelection()]),
  ];
}

function labelProps(label, index) {
  const k = "labels";
  return [
    textField("text", label.text, (v) => v.trim() && update(k, index, { text: v.trim() })),
    lengthField("x", label.x, (v) => update(k, index, { x: v })),
    lengthField("y", label.y, (v) => update(k, index, { y: v })),
    numberField("size", label.size_px || 16, (v) => v > 0 && update(k, index, { size_px: v }), "px"),
    buttons(["Delete", () => A.deleteSelection()]),
  ];
}

function eraseProps(r) {
  const k = "erase";
  return [
    idField(k, r),
    lengthField("x", r.x, (v) => update(k, r.id, { x: v })),
    lengthField("y", r.y, (v) => update(k, r.id, { y: v })),
    lengthField("w", r.w, (v) => update(k, r.id, { w: v })),
    lengthField("h", r.h, (v) => update(k, r.id, { h: v })),
    el("p", { class: "note" }, "Whites out the sheet under it, in the editor and in saved overlays."),
    buttons(["Delete", () => A.deleteSelection()]),
  ];
}

function planProps(plan) {
  const counts = ["walls", "openings", "counters", "fixtures", "labels", "erase"]
    .map((k) => `${(plan[k] || []).length} ${k}`)
    .join(", ");
  const c = plan.compass;
  return [
    textField("name", plan.name || "", (v) => A.send({ op: "set_meta", name: v.trim() })),
    textField("notes", plan.notes || "", (v) => A.send({ op: "set_meta", notes: v.trim() })),
    selectField("up is", c.up, CARDINALS.map((d) => [d, d]), (v) => A.send({ op: "set_compass", up: v })),
    el("div", { class: "note" }, `Right is ${c.right}, down is ${c.down}, left is ${c.left}.`),
    el("div", { class: "field" }, el("label", {}, "scale"), el("span", {}, `${plan.canvas.px_per_inch.toFixed(4)} px per inch`), el("span")),
    buttons(["Set scale (K)", () => A.setTool("calibrate")], ["Fit view (0)", () => A.fitView()]),
    el("p", { class: "note" }, counts),
  ];
}

export function renderProps(appRef, extraErrors = null) {
  app = appRef;
  const box = $("props");
  box.replaceChildren();
  const plan = app.plan;
  if (!plan) {
    $("props-title").textContent = "Plan";
    box.append(el("p", { class: "note" }, "No plan open."));
    return;
  }
  let title = "Plan";
  let items;
  if (app.sel) {
    const [kind, id] = A.splitKey(app.sel);
    const obj = A.getObject(app.sel);
    title = { walls: "Wall", openings: obj.type === "door" ? "Door" : "Window", counters: "Counter", fixtures: "Fixture", labels: "Label", erase: "Erase region" }[kind];
    if (kind === "walls") items = wallProps(obj);
    else if (kind === "openings") items = openingProps(obj);
    else if (kind === "counters") items = counterProps(obj);
    else if (kind === "fixtures") items = fixtureProps(obj);
    else if (kind === "labels") items = labelProps(obj, id);
    else items = eraseProps(obj);
  } else items = planProps(plan);
  $("props-title").textContent = title;
  box.append(...items.filter(Boolean));
  const errors = extraErrors || (app.state && app.state.errors) || [];
  if (errors.length) {
    box.append(el("h2", { class: "error" }, `${errors.length} problem${errors.length > 1 ? "s" : ""} (fix before saving)`));
    box.append(el("ul", { class: "errors" }, errors.slice(0, 40).map((e) => el("li", {}, e))));
  }
}

// ---------------------------------------------------------------- new plan and trace

function traceSettings() {
  return {
    dark: Number($("tr-dark").value),
    min_thickness_px: Number($("tr-min").value),
    max_thickness_px: Number($("tr-max").value),
    min_length_px: Number($("tr-len").value),
  };
}

async function tracePreview() {
  if (!app.plan) return;
  try {
    const result = await api("/api/trace", { preview: true, settings: traceSettings() });
    app.traceWalls = result.walls;
    A.renderTrace();
    $("trace-note").textContent = `${result.walls.length} walls found (green). Adjust and preview again, or Apply.`;
  } catch (err) {
    A.message(err.message, true);
  }
}

async function openNewDialog() {
  if (!confirmDiscard()) return;
  const s = app.state;
  $("new-sheet-note").textContent =
    s && s.sheet ? `Leave empty to use ${s.sheet.name} from the folder (${s.sheet.width}×${s.sheet.height}).` : "This folder has no sheet image yet; choose one.";
  $("new-name").value = s && s.files.includes("plan.json") ? s.next_step.replace("step", "new") : "plan.json";
  $("new-dialog").showModal();
}

async function createPlan() {
  const file = $("new-image").files[0];
  const body = {
    name: $("new-name").value.trim() || "plan.json",
    up: $("new-up").value,
    px_per_inch: Number($("new-ppi").value) || 1,
  };
  if (file) body.image = { filename: file.name, data: await readFileBase64(file) };
  let state = null;
  try {
    state = await api("/api/new", body);
  } catch (err) {
    if (/already exists/.test(err.message) && confirm(`${body.name} already exists. Replace it when you save?`)) {
      state = await api("/api/new", { ...body, overwrite: true }).catch((e) => (A.message(e.message, true), null));
    } else A.message(err.message, true);
  }
  if (!state) return;
  app.fittedFor = null;
  app.sel = null;
  A.setState(state);
  A.message("New plan created. Next: Scale (K) with a known dimension, then Auto-trace or draw walls, then Save.");
  $("trace-panel").scrollIntoView({ behavior: "smooth" });
}

export function initPanels(appRef, actions) {
  app = appRef;
  A = actions;
  $("folder-form").addEventListener("submit", (e) => {
    e.preventDefault();
    if (!confirmDiscard()) return;
    app.sel = null;
    app.traceWalls = null;
    A.call("/api/folder", { path: $("folder-input").value.trim() }, { selectRef: false });
  });
  $("merge").addEventListener("click", () => {
    const steps = [...document.querySelectorAll("#file-list input[data-step]:checked")].map((c) => c.dataset.step);
    if (!steps.length) return A.message("Check the steps to merge first.", true);
    if (!confirmDiscard()) return;
    A.call("/api/merge", { steps }, { selectRef: false });
  });
  $("save-as").addEventListener("click", () => {
    const name = prompt("Save as (plan-<name>.json)", "plan-");
    if (name) A.save("as", name);
  });
  $("revert").addEventListener("click", () => {
    if (confirm("Reload the file from disk and drop unsaved changes?")) A.call("/api/revert", {}, { selectRef: false });
  });
  $("view-overlay").addEventListener("click", () => window.open(`/api/overlay?t=${Date.now()}`, "_blank"));
  $("new-plan").addEventListener("click", openNewDialog);
  $("empty-new").addEventListener("click", openNewDialog);
  $("new-form").addEventListener("submit", (e) => {
    if (e.submitter && e.submitter.value === "create") createPlan();
  });
  for (const id of ["tr-dark", "tr-min", "tr-max", "tr-len"]) {
    const input = $(id);
    const out = document.querySelector(`output[for="${id}"]`);
    out.textContent = input.value;
    input.addEventListener("input", () => (out.textContent = input.value));
    input.addEventListener("change", () => app.traceWalls && tracePreview());
  }
  $("trace-preview").addEventListener("click", tracePreview);
  $("trace-clear").addEventListener("click", () => {
    app.traceWalls = null;
    A.renderTrace();
    $("trace-note").textContent = "";
  });
  $("trace-apply").addEventListener("click", async () => {
    const replace = $("trace-replace").checked;
    if (replace && !confirm("Replace all existing walls (and their doors and windows) with the traced walls?")) return;
    const state = await A.call("/api/trace", { preview: false, replace, settings: traceSettings() }, { selectRef: false });
    if (state) {
      app.traceWalls = null;
      A.renderTrace();
      $("trace-note").textContent = "";
      A.message(`Added ${state.traced} traced walls. Undo (Ctrl+Z) takes them all back out.`);
    }
  });
}
