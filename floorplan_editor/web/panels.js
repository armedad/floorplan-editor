// Side panels: properties of the selection, flags, the folder's steps, view settings, and auto-trace.

import { api, readFileBase64 } from "./api.js";
import * as E from "./app.js";
import { COMMANDS, context } from "./commands.js";
import { copyAllFlags, copyText, deleteFlags, editFlagNote, flagText, renderFlagsPanel, setFlagDone, zoomToFlag } from "./flags.js";
import { formatLength, parseLength, round2, wallAngle, wallFrame } from "./geom.js";
import { dimsFor, opForDim } from "./measure.js";
import { $, el } from "./ui.js";

const CARDINALS = ["north", "east", "south", "west"];
let app = null;

function confirmDiscard() {
  return !app.state || !app.state.dirty || confirm("You have unsaved changes. Discard them?");
}

export function showPanel(id, note) {
  if (document.body.classList.contains("side-hidden")) E.toggleSide();
  const panel = $(id);
  panel.open = true;
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  panel.classList.add("attention");
  setTimeout(() => panel.classList.remove("attention"), 1600);
  if (note) E.message(note);
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
        el("input", { type: "checkbox", "data-step": name, disabled: !mergeable, title: mergeable ? "include in merge" : "", "aria-label": `merge ${name}` }),
        el(
          "button",
          {
            type: "button",
            class: "file-name",
            title: `open ${name}`,
            on: { click: () => name !== s.file && confirmDiscard() && E.call("/api/open", { file: name }, { selectRef: false }) },
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
  if (s.file && !s.files.includes(s.file)) list.append(el("li", { class: "current" }, `${s.file} (not saved yet)`));
  $("save-step").title = `Save as ${s.next_step} (Ctrl+Shift+S)`;
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
  renderFlagsPanel();
}

// ---------------------------------------------------------------- fields

const fmt = (v) => String(round2(v));

/**
 * A length field: takes 10'6", 126, or an expression; Arrow keys nudge by 1" (Shift 12", Alt 1/4")
 * and apply at once; dragging the label scrubs the value.
 */
function lengthField(label, value, commit, { hint, unit = "length", min = -Infinity } = {}) {
  const parse = unit === "length" ? parseLength : (t) => Number(String(t).replace(/[°\s]/g, ""));
  const show = (v) => (unit === "length" ? formatLength(v) : unit === "angle" ? `${round2(v)}°` : String(round2(v)));
  const input = el("input", { value: fmt(value), spellcheck: "false", "aria-label": label });
  const note = el("span", { class: "hint" }, hint ?? show(value));
  const apply = (v) => {
    if (!Number.isFinite(v) || v < min) {
      E.message(`Can't use ${input.value} for ${label}.`, true);
      input.value = fmt(value);
      return;
    }
    if (Math.abs(v - value) < 1e-6) return;
    commit(round2(v));
  };
  input.addEventListener("change", () => apply(parse(input.value)));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      input.value = fmt(value);
      input.blur();
    } else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      e.preventDefault();
      const step = (e.shiftKey ? 12 : e.altKey ? 0.25 : 1) * (unit === "angle" ? (e.shiftKey ? 15 / 12 : 1) : 1) * (e.key === "ArrowUp" ? 1 : -1);
      const current = parse(input.value);
      apply((Number.isFinite(current) ? current : value) + step);
    }
  });
  const lab = el("label", { class: "scrub", title: `${label}: drag sideways to adjust, or type a value` }, label);
  lab.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    lab.setPointerCapture(e.pointerId);
    const x0 = e.clientX;
    let v = value;
    const move = (ev) => {
      const rate = ev.shiftKey ? 1 : ev.altKey ? 0.05 : 0.25;
      v = round2(value + (ev.clientX - x0) * rate);
      if (unit === "length" && min >= 0) v = Math.max(min, v);
      input.value = fmt(v);
      note.textContent = show(v);
    };
    const up = () => {
      lab.removeEventListener("pointermove", move);
      lab.removeEventListener("pointerup", up);
      if (v !== value) apply(v);
    };
    lab.addEventListener("pointermove", move);
    lab.addEventListener("pointerup", up);
  });
  return el("div", { class: "field" }, lab, input, note);
}

function textField(label, value, commit, { multiline = false } = {}) {
  const input = multiline ? el("textarea", { rows: 3, "aria-label": label }) : el("input", { value: value ?? "", "aria-label": label });
  if (multiline) input.value = value ?? "";
  input.addEventListener("change", () => commit(input.value));
  return el("div", { class: `field${multiline ? " wide" : ""}` }, el("label", {}, label), input, el("span"));
}

function selectField(label, value, options, commit) {
  const select = el(
    "select",
    { "aria-label": label, on: { change: () => commit(select.value) } },
    options.map(([v, text]) => el("option", { value: v, selected: v === value }, text)),
  );
  return el("div", { class: "field" }, el("label", {}, label), select, el("span"));
}

function anchorRow(key, anchors) {
  const current = app.opts.anchor[key] || (key.startsWith("fixtures:") ? "center" : "start");
  return el(
    "div",
    { class: "anchor-row", role: "radiogroup", "aria-label": "which side stays put" },
    el("span", { class: "hint" }, "Stays put:"),
    ...["start", "center", "end"].map((a) =>
      el(
        "button",
        {
          type: "button",
          role: "radio",
          "aria-checked": String(a === current),
          class: a === current ? "active" : "",
          title: anchors[a],
          on: { click: () => ((app.opts.anchor[key] = a), renderProps(app)) },
        },
        anchors[a].replace(/^Keep (the )?/, ""),
      ),
    ),
  );
}

function dimFields(kind, id, dims, keys) {
  const out = [];
  for (const d of dims.filter((x) => keys.includes(x.key) || (keys.includes("gaps") && x.gap && !x.key.startsWith("gap-start") && !x.key.startsWith("gap-end")))) {
    const label = d.gap ? gapLabel(d) : { length: "length", width: "width", w: "width", h: "depth" }[d.key] || d.key;
    const anchorKey = `${kind}:${d.key}`;
    out.push(
      lengthField(label, d.value, (v) => {
        const anchor = app.opts.anchor[anchorKey] || (kind === "fixtures" ? "center" : "start");
        const op = opForDim(app.plan, kind, id, d, v, anchor);
        if (op) E.send(op);
        else E.message(`${label} can't be ${formatLength(v)}.`, true);
      }, { min: d.gap ? 0 : 0.5 }),
    );
    if (d.anchors) out.push(anchorRow(anchorKey, d.anchors));
  }
  return out;
}

function gapLabel(d) {
  if (d.key === "gap-start" || d.key === "gap-end") return d.title.replace("Distance from the ", "from ");
  const dir = d.key.slice(4);
  if (["left", "right", "up", "down"].includes(dir)) return `clear ${dir}`;
  const m = d.title.match(/to ([^;]+);/);
  return m ? `clear to ${m[1]}` : "clear";
}

function update(kind, id, fields) {
  return E.send({ op: "update", kind, id, fields });
}

function idField(kind, obj) {
  return textField("id", obj.id, (v) => v.trim() && update(kind, obj.id, { id: v.trim() }));
}

function more(title, ...items) {
  return el("details", { class: "more" }, el("summary", {}, title), ...items.flat().filter(Boolean));
}

function actionsRow() {
  const c = context();
  const cmds = COMMANDS.filter((cmd) => cmd.bar && (!cmd.when || cmd.when(c)));
  return el(
    "div",
    { class: "row wrap actions" },
    cmds.map((cmd) =>
      el("button", { type: "button", class: cmd.danger ? "danger" : "", title: `${cmd.label}${cmd.keys ? ` (${cmd.keys[0]})` : ""}`, on: { click: () => cmd.run() } }, cmd.barLabel ? cmd.barLabel(c) : cmd.label),
    ),
  );
}

// ---------------------------------------------------------------- properties by kind

function wallProps(w) {
  const k = "walls";
  const dims = dimsFor(app.plan, k, w.id);
  const f = wallFrame(w);
  return [
    ...dimFields(k, w.id, dims, ["length"]),
    lengthField(
      "angle",
      wallAngle(w),
      (deg) => {
        const a = (deg * Math.PI) / 180;
        E.send({ op: "move_wall_end", id: w.id, end: "end", x: round2(w.x1 + f.len * Math.cos(a)), y: round2(w.y1 - f.len * Math.sin(a)), join: true });
      },
      { unit: "angle", hint: "° from right, counterclockwise" },
    ),
    lengthField("thickness", w.thickness, (v) => update(k, w.id, { thickness: v }), { min: 0.25 }),
    ...dimFields(k, w.id, dims, ["gaps"]),
    more(
      "Ends and id",
      lengthField("start x", w.x1, (v) => update(k, w.id, { x1: v })),
      lengthField("start y", w.y1, (v) => update(k, w.id, { y1: v })),
      lengthField("end x", w.x2, (v) => update(k, w.id, { x2: v })),
      lengthField("end y", w.y2, (v) => update(k, w.id, { y2: v })),
      idField(k, w),
    ),
  ];
}

function openingProps(o) {
  const k = "openings";
  const door = o.type === "door";
  const dims = dimsFor(app.plan, k, o.id);
  return [
    selectField("type", o.type, [["door", "door"], ["window", "window"]], (v) =>
      update(k, o.id, v === "door" ? { type: "door", hinge: "start", swing: "ccw" } : { type: "window", hinge: null, swing: null }),
    ),
    ...dimFields(k, o.id, dims, ["gap-start", "width", "gap-end"]),
    door ? selectField("hinge", o.hinge || "start", [["start", "start end of the wall"], ["end", "far end of the wall"]], (v) => update(k, o.id, { hinge: v })) : null,
    door ? selectField("swing", o.swing || "ccw", [["ccw", "counterclockwise"], ["cw", "clockwise"]], (v) => update(k, o.id, { swing: v })) : null,
    more("Wall, offset, and id", textField("wall", o.wall, (v) => update(k, o.id, { wall: v.trim() })), lengthField("offset", o.offset, (v) => update(k, o.id, { offset: v }), { min: 0 }), idField(k, o)),
  ];
}

function counterProps(c) {
  const k = "counters";
  const dims = dimsFor(app.plan, k, c.id);
  if (c.points) {
    const area = el("textarea", { spellcheck: "false", "aria-label": "corners" }, c.points.map(([x, y]) => `${x}, ${y}`).join("\n"));
    area.addEventListener("change", () => {
      const points = area.value
        .split(/\n+/)
        .map((line) => line.split(/[,\s]+/).filter(Boolean).map(Number))
        .filter((p) => p.length === 2 && p.every(Number.isFinite));
      if (points.length < 3) return E.message("An outline needs at least 3 corners, one 'x, y' per line.", true);
      update(k, c.id, { points });
    });
    const edges = dims.filter((d) => d.key.startsWith("edge-"));
    return [
      ...edges.map((d) => lengthField(`edge ${Number(d.key.slice(5)) + 1}`, d.value, (v) => E.send(opForDim(app.plan, k, c.id, d, v)), { min: 0.5 })),
      ...dimFields(k, c.id, dims, ["gaps"]),
      el("p", { class: "note" }, "Typing an edge length moves the corner at its end. Drag a corner to move it, drag a midpoint dot to add one, Alt+click a corner to remove it."),
      more("Corners (x, y in inches) and id", area, idField(k, c)),
    ];
  }
  const over = c.overhang || null;
  return [
    ...dimFields(k, c.id, dims, ["w", "h"]),
    ...dimFields(k, c.id, dims, ["gaps"]),
    selectField("overhang", over ? over.edge : "", [["", "none"], ...CARDINALS.map((d) => [d, `${d} side`])], (v) =>
      update(k, c.id, { overhang: v ? { edge: v, inches: over ? over.inches : 12 } : null }),
    ),
    over ? lengthField("overhang", over.inches, (v) => update(k, c.id, { overhang: { edge: over.edge, inches: v } }), { min: 0 }) : null,
    more("Position and id", lengthField("left x", c.x, (v) => update(k, c.id, { x: v })), lengthField("top y", c.y, (v) => update(k, c.id, { y: v })), idField(k, c)),
  ];
}

function fixtureProps(f) {
  const k = "fixtures";
  const types = (app.state && app.state.fixture_types) || [];
  const dims = dimsFor(app.plan, k, f.id);
  return [
    selectField("type", f.type, types.map((t) => [t, t.replace("_", " ")]), (v) => update(k, f.id, { type: v })),
    ...dimFields(k, f.id, dims, ["w", "h"]),
    lengthField("rotation", f.rot || 0, (v) => update(k, f.id, { rot: v ? ((v % 360) + 360) % 360 : null }), { unit: "angle", hint: "° clockwise" }),
    ...dimFields(k, f.id, dims, ["gaps"]),
    more("Center and id", lengthField("center x", f.x, (v) => update(k, f.id, { x: v })), lengthField("center y", f.y, (v) => update(k, f.id, { y: v })), idField(k, f)),
  ];
}

function labelProps(label, index) {
  const k = "labels";
  return [
    textField("text", label.text, (v) => v.trim() && update(k, index, { text: v.trim() })),
    lengthField("center x", label.x, (v) => update(k, index, { x: v })),
    lengthField("center y", label.y, (v) => update(k, index, { y: v })),
    lengthField("size", label.size_px || 16, (v) => v > 0 && update(k, index, { size_px: v }), { unit: "number", hint: "px" }),
  ];
}

function eraseProps(r) {
  const k = "erase";
  const dims = dimsFor(app.plan, k, r.id);
  return [
    ...dimFields(k, r.id, dims, ["w", "h"]),
    el("p", { class: "note" }, "Whites out the sheet under it, in the editor and in saved overlays."),
    more("Position and id", lengthField("left x", r.x, (v) => update(k, r.id, { x: v })), lengthField("top y", r.y, (v) => update(k, r.id, { y: v })), idField(k, r)),
  ];
}

function flagProps(f) {
  const on = f.target ? E.describeKey(`${f.target[0]}:${f.target[1]}`) : null;
  return [
    textField("note", f.note || "", (v) => E.call("/api/flags", { action: "update", id: f.id, note: v }, { selectRef: false }), { multiline: true }),
    el(
      "label",
      { class: "row" },
      el("input", { type: "checkbox", checked: !!f.done, on: { change: (e) => setFlagDone(f.id, e.target.checked) } }),
      " Done (handled)",
    ),
    on ? el("p", { class: "note" }, `Dropped on ${on}.`) : null,
    f.x !== undefined ? lengthField("x", f.x, (v) => E.call("/api/flags", { action: "update", id: f.id, x: v, y: f.y }, { selectRef: false })) : null,
    f.y !== undefined ? lengthField("y", f.y, (v) => E.call("/api/flags", { action: "update", id: f.id, x: f.x, y: v }, { selectRef: false })) : null,
    el("p", { class: "note" }, `${f.by === "cursor" ? "Placed by Cursor" : "Tell Cursor about it by number"}: “${f.id}”. Its note, place, and what's under it are what Cursor sees.`),
    el(
      "div",
      { class: "row wrap" },
      el("button", { type: "button", on: { click: () => copyText(flagText(f), f.id) } }, "Copy for Cursor"),
      el("button", { type: "button", on: { click: () => zoomToFlag(f.id) } }, "Zoom to it"),
      el("button", { type: "button", on: { click: () => editFlagNote(f.id) } }, "Edit note…"),
      el("button", { type: "button", class: "danger", on: { click: () => deleteFlags([f.id]) } }, "Remove"),
    ),
  ];
}

function planProps(plan) {
  const counts = ["walls", "openings", "counters", "fixtures", "labels", "erase"]
    .map((k) => `${(plan[k] || []).length} ${k}`)
    .join(", ");
  const c = plan.compass;
  return [
    textField("name", plan.name || "", (v) => E.send({ op: "set_meta", name: v.trim() })),
    textField("notes", plan.notes || "", (v) => E.send({ op: "set_meta", notes: v.trim() }), { multiline: true }),
    selectField("up is", c.up, CARDINALS.map((d) => [d, d]), (v) => E.send({ op: "set_compass", up: v })),
    el("div", { class: "note" }, `Right is ${c.right}, down is ${c.down}, left is ${c.left}.`),
    lengthField(
      "scale",
      plan.canvas.px_per_inch,
      (v) => v > 0 && E.send({ op: "calibrate", p1: [0, 0], p2: [v, 0], length: 1 }),
      { unit: "number", hint: "sheet px per inch" },
    ),
    el(
      "div",
      { class: "row wrap" },
      el("button", { type: "button", title: "Click two ends of a length you know (K)", on: { click: () => E.setTool("calibrate") } }, "Set scale from a known length (K)"),
      el("button", { type: "button", on: { click: () => E.fitView() } }, "Fit view (0)"),
    ),
    el("p", { class: "note" }, counts),
    el("p", { class: "note" }, "Select something to see and type its sizes. Ctrl+K lists every command."),
  ];
}

function multiProps(info) {
  const counts = {};
  for (const key of info.keys) {
    const kind = key.split(":")[0];
    counts[kind] = (counts[kind] || 0) + 1;
  }
  return [
    el("p", {}, Object.entries(counts).map(([k, n]) => `${n} ${n === 1 ? k.replace(/s$/, "") : k}`).join(", ")),
    el("p", { class: "note" }, "Drag any of them to move them together, use the arrow keys to nudge, or Move… for an exact distance. Shift+click adds or removes one."),
  ];
}

export function renderProps(appRef, extraErrors = null) {
  app = appRef;
  const box = $("props");
  box.replaceChildren();
  const plan = app.plan;
  if (!plan) {
    $("props-title").textContent = "Plan";
    box.append(el("p", { class: "note" }, "No plan open. Open one, or drop a plan or floor plan image anywhere."));
    return;
  }
  const info = E.selectionInfo();
  let title = "Plan";
  let items;
  if (info.n > 1) {
    title = `${info.n} selected`;
    items = multiProps(info);
  } else if (info.n === 1 && info.obj) {
    title = E.selectionTitle();
    const { kind, id, obj } = info;
    if (kind === "walls") items = wallProps(obj);
    else if (kind === "openings") items = openingProps(obj);
    else if (kind === "counters") items = counterProps(obj);
    else if (kind === "fixtures") items = fixtureProps(obj);
    else if (kind === "labels") items = labelProps(obj, id);
    else if (kind === "flags") items = flagProps(obj);
    else items = eraseProps(obj);
  } else items = planProps(plan);
  $("props-title").textContent = title;
  box.append(...items.flat().filter(Boolean));
  if (info.n && info.planKeys.length) box.append(actionsRow());
  const errors = extraErrors || (app.state && app.state.errors) || [];
  if (errors.length) {
    box.append(el("h3", { class: "error" }, `${errors.length} problem${errors.length > 1 ? "s" : ""} (fix before saving)`));
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

export async function runTrace(mode) {
  if (!app.plan) return;
  showPanel("trace-panel");
  if (mode === "preview") {
    try {
      const result = await api("/api/trace", { preview: true, settings: traceSettings() });
      app.traceWalls = result.walls;
      E.renderTrace();
      $("trace-note").textContent = `${result.walls.length} walls found (green). Adjust and preview again, or Add them.`;
    } catch (err) {
      E.message(err.message, true);
    }
    return;
  }
  const replace = $("trace-replace").checked;
  if (replace && !confirm("Replace all existing walls (and their doors and windows) with the traced walls?")) return;
  const state = await E.call("/api/trace", { preview: false, replace, settings: traceSettings() }, { selectRef: false });
  if (state) {
    app.traceWalls = null;
    E.renderTrace();
    $("trace-note").textContent = "";
    E.message(`Added ${state.traced} traced walls. Undo (Ctrl+Z) takes them all back out.`);
  }
}

export async function openNewDialog() {
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
    px_per_inch: parseLength($("new-ppi").value) || null,
    auto: $("new-auto").checked,
  };
  if (file) body.image = { filename: file.name, data: await readFileBase64(file) };
  let state = null;
  try {
    state = await api("/api/new", body);
  } catch (err) {
    if (/already exists/.test(err.message) && confirm(`${body.name} already exists. Replace it when you save?`)) {
      state = await api("/api/new", { ...body, overwrite: true }).catch((e) => (E.message(e.message, true), null));
    } else E.message(err.message, true);
  }
  if (!state) return;
  app.fittedFor = null;
  app.selection.clear();
  app.sel = null;
  E.setState(state);
  E.message(
    state.traced !== undefined
      ? `New plan: traced ${state.traced} walls${state.estimated_px_per_inch ? ", scale guessed from wall thickness" : ""}. Check the scale with Scale (K), then Save.`
      : "New plan created. Next: Scale (K) with a known dimension, then Auto-trace or draw walls, then Save.",
  );
}

export function initPanels(appRef) {
  app = appRef;
  $("folder-form").addEventListener("submit", (e) => {
    e.preventDefault();
    if (!confirmDiscard()) return;
    app.selection.clear();
    app.sel = null;
    app.traceWalls = null;
    E.call("/api/folder", { path: $("folder-input").value.trim().replace(/^"|"$/g, "") }, { selectRef: false });
  });
  $("merge").addEventListener("click", () => {
    const steps = [...document.querySelectorAll("#file-list input[data-step]:checked")].map((c) => c.dataset.step);
    if (!steps.length) return E.message("Check the steps to merge first.", true);
    if (!confirmDiscard()) return;
    E.call("/api/merge", { steps }, { selectRef: false });
  });
  $("save-as").addEventListener("click", () => E.saveAs());
  $("revert").addEventListener("click", () => E.revert());
  $("view-overlay").addEventListener("click", () => window.open(`/api/overlay?t=${Date.now()}`, "_blank"));
  $("new-plan").addEventListener("click", openNewDialog);
  $("new-form").addEventListener("submit", (e) => {
    if (e.submitter && e.submitter.value === "create") createPlan();
  });
  for (const id of ["tr-dark", "tr-min", "tr-max", "tr-len"]) {
    const input = $(id);
    const out = document.querySelector(`output[for="${id}"]`);
    out.textContent = input.value;
    input.addEventListener("input", () => (out.textContent = input.value));
    input.addEventListener("change", () => app.traceWalls && runTrace("preview"));
  }
  $("trace-preview").addEventListener("click", () => runTrace("preview"));
  $("trace-clear").addEventListener("click", () => {
    app.traceWalls = null;
    E.renderTrace();
    $("trace-note").textContent = "";
  });
  $("trace-apply").addEventListener("click", () => runTrace("apply"));
  $("flags-add").addEventListener("click", () => E.setTool("flag"));
  $("flags-copy").addEventListener("click", copyAllFlags);
  $("flags-clear").addEventListener("click", () => E.call("/api/flags", { action: "clear_done" }, { selectRef: false }));
}
