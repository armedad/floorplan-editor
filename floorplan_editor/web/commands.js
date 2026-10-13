// Every action the editor offers. The keyboard, the command palette, the right-click menu, and the
// action bar over the selection all read this list, so new actions belong here, not in one of them.

import * as E from "./app.js";
import { addComment } from "./comments.js";
import { mergeSelectedWalls, mergeStacked, selectFound, showConvertMenu, thicknessSelection } from "./convert.js";
import { copyAllFlags, copyText, editFlagNote, flagList, flagText, getFlag, setFlagDone } from "./flags.js";
import { openPicker, showOpenMenu } from "./open.js";
import { openNewDialog, runTrace, showPanel } from "./panels.js";
import { $, el, hideMenu, showMenu } from "./ui.js";

const TOOLS = [
  ["select", "Select and move", "V"],
  ["wall", "Draw walls", "W"],
  ["door", "Add a door", "D"],
  ["window", "Add a window", "N"],
  ["counter", "Draw a box counter", "C"],
  ["polygon", "Draw a counter outline", "P"],
  ["fixture", "Place a fixture", "X"],
  ["label", "Add a text label", "T"],
  ["erase", "White out sheet ink", "E"],
  ["calibrate", "Set the scale from a known length", "K"],
  ["flag", "Drop a flag (click a spot or drag an area)", "F"],
];

const has = (c, kind) => c.kinds.has(kind);
const onlyFlags = (c) => c.n > 0 && c.flagKeys.length === c.n;
const singleKind = (c, kind) => c.n === 1 && c.kind === kind;
const isDoor = (c) => singleKind(c, "openings") && c.obj && c.obj.type === "door";
const isBoxCounter = (c) => singleKind(c, "counters") && c.obj && !c.obj.points;
const onlyWalls = (c) => c.n > 0 && c.kinds.size === 1 && c.kinds.has("walls");

export const COMMANDS = [
  // File
  { id: "open", group: "File", label: "Open a plan, folder, or floor plan image…", keys: ["Ctrl+O"], run: () => openPicker("file") },
  { id: "open-folder", group: "File", label: "Open a folder…", run: () => openPicker("folder") },
  { id: "open-recent", group: "File", label: "Open a recent folder…", run: () => showOpenMenu($("open-more")) },
  { id: "upload", group: "File", label: "Choose a file in the browser (upload)…", run: () => $("upload").click() },
  { id: "save", group: "File", label: "Save", keys: ["Ctrl+S"], when: (c) => c.plan, run: () => E.save("overwrite") },
  { id: "save-step", group: "File", label: "Save as a new step", keys: ["Ctrl+Shift+S"], when: (c) => c.plan, run: () => E.save("step") },
  { id: "save-as", group: "File", label: "Save as…", when: (c) => c.plan, run: () => E.saveAs() },
  { id: "revert", group: "File", label: "Revert to the saved file", when: (c) => c.dirty, run: () => E.revert() },
  { id: "merge", group: "File", label: "Merge steps…", when: (c) => c.steps > 1, run: () => showPanel("files-panel", "Check the steps to merge, then press Merge checked.") },
  { id: "overlay", group: "File", label: "View the overlay picture", when: (c) => c.plan && c.sheet, run: () => window.open(`/api/overlay?t=${Date.now()}`, "_blank") },
  { id: "new-plan", group: "File", label: "New plan from a sheet image (manual setup)…", run: () => openNewDialog() },

  // Edit
  { id: "undo", group: "Edit", label: "Undo", keys: ["Ctrl+Z"], when: (c) => c.canUndo, run: () => E.undo() },
  { id: "redo", group: "Edit", label: "Redo", keys: ["Ctrl+Y", "Ctrl+Shift+Z"], when: (c) => c.canRedo, run: () => E.redo() },
  { id: "select-all", group: "Edit", label: "Select everything", keys: ["Ctrl+A"], when: (c) => c.plan, run: () => E.selectAll() },
  {
    id: "dims",
    group: "Edit",
    label: "Type an exact size…",
    barLabel: (c) => ({ walls: "Length…", openings: "Width…", counters: c.obj && c.obj.points ? "Edges…" : "Size…", fixtures: "Size…", erase: "Size…" })[c.kind],
    keys: ["Enter"],
    bar: true,
    when: (c) => c.n === 1 && ["walls", "openings", "counters", "fixtures", "erase"].includes(c.kind),
    run: () => E.editPrimaryDim(),
  },
  {
    id: "convert",
    group: "Edit",
    label: "Convert to a door, window, fixture, counter, label, or erase region…",
    barLabel: () => "Convert…",
    keys: ["Shift+C"],
    bar: true,
    when: (c) => c.planKeys.length > 0,
    run: () => showConvertMenu(),
  },
  { id: "merge-walls", group: "Edit", label: "Merge the walls into one", barLabel: () => "Merge", keys: ["J"], bar: true, when: (c) => onlyWalls(c) && c.n >= 2, run: () => mergeSelectedWalls() },
  { id: "thickness", group: "Edit", label: "Set wall thickness…", barLabel: () => "Thickness…", keys: ["Shift+T"], bar: true, when: onlyWalls, run: () => thicknessSelection() },
  { id: "move-by", group: "Edit", label: "Move by an exact distance…", barLabel: () => "Move…", keys: ["M"], bar: true, when: (c) => c.planKeys.length > 0, run: () => E.moveSelectionBy() },
  { id: "rotate", group: "Edit", label: "Rotate 90° clockwise", barLabel: () => "Rotate", keys: ["R"], bar: true, when: (c) => singleKind(c, "fixtures"), run: () => E.rotateSelection(90) },
  { id: "rotate-back", group: "Edit", label: "Rotate 90° counterclockwise", keys: ["Shift+R"], when: (c) => singleKind(c, "fixtures"), run: () => E.rotateSelection(-90) },
  { id: "flip-swing", group: "Edit", label: "Flip the door's swing", barLabel: () => "Flip swing", keys: ["S"], bar: true, when: isDoor, run: () => E.flipDoor("swing") },
  { id: "flip-hinge", group: "Edit", label: "Flip the door's hinge side", barLabel: () => "Flip hinge", keys: ["H"], bar: true, when: isDoor, run: () => E.flipDoor("hinge") },
  { id: "split", group: "Edit", label: "Split the wall in the middle", barLabel: () => "Split", bar: true, when: (c) => singleKind(c, "walls"), run: () => E.splitSelectedWall() },
  { id: "outline", group: "Edit", label: "Edit corners (turn the box into an outline)", barLabel: () => "Edit corners", bar: true, when: isBoxCounter, run: () => E.toOutline() },
  { id: "duplicate", group: "Edit", label: "Duplicate", barLabel: () => "Duplicate", keys: ["Ctrl+D"], bar: true, when: (c) => c.planKeys.length > 0, run: () => E.duplicateSelection() },
  { id: "copy", group: "Edit", label: "Copy", keys: ["Ctrl+C"], when: (c) => c.n > 0, run: () => (onlyFlags(E.selectionInfo()) ? copySelectedFlags() : E.copySelection()) },
  { id: "copy-ids", group: "Edit", label: "Copy the selection's ids for Cursor", keys: ["Ctrl+Shift+C"], when: (c) => c.planKeys.length > 0, run: () => copySelectedIds() },
  { id: "paste", group: "Edit", label: "Paste at the pointer", keys: ["Ctrl+V"], when: (c) => c.plan && c.clipboard, run: () => E.pasteClipboard() },
  { id: "comment", group: "Comments", label: "Comment on it…", barLabel: () => "Comment…", keys: ["Ctrl+/"], bar: true, when: (c) => c.n === 1 && c.planKeys.length === 1, run: () => addComment() },
  { id: "flag-selection", group: "Flags", label: "Flag the selection", barLabel: () => "Flag", keys: ["Shift+F"], bar: true, when: (c) => c.planKeys.length > 0, run: () => E.flagSelection() },
  { id: "delete", group: "Edit", label: "Delete", barLabel: () => "Delete", keys: ["Delete", "Backspace"], bar: true, danger: true, when: (c) => c.n > 0, run: () => E.deleteSelection() },

  // Flags
  { id: "flag-note", group: "Flags", label: "Edit the flag's note…", barLabel: () => "Note…", keys: ["Enter"], bar: true, when: (c) => c.n === 1 && c.kind === "flags", run: () => editFlagNote(E.selectionInfo().id) },
  {
    id: "flag-done",
    group: "Flags",
    label: "Mark flags done (or not)",
    barLabel: (c) => (c.n === 1 && getFlag(c.id) && getFlag(c.id).done ? "Reopen" : "Done"),
    bar: true,
    when: onlyFlags,
    run: () => {
      const ids = E.selectionInfo().flagKeys.map((k) => k.slice(6));
      const done = !ids.every((id) => getFlag(id) && getFlag(id).done);
      ids.forEach((id) => setFlagDone(id, done));
    },
  },
  { id: "flag-copy", group: "Flags", label: "Copy the flag reference for Cursor", barLabel: () => "Copy for Cursor", bar: true, when: onlyFlags, run: () => copySelectedFlags() },
  { id: "flags-copy-all", group: "Flags", label: "Copy all open flags for Cursor", when: (c) => c.flags > 0, run: () => copyAllFlags() },
  { id: "flags-clear", group: "Flags", label: "Remove done flags", when: (c) => c.doneFlags > 0, run: () => E.call("/api/flags", { action: "clear_done" }, { selectRef: false }) },
  { id: "flags-panel", group: "Flags", label: "Show the flag list", run: () => showPanel("flags-panel") },
  { id: "comments-panel", group: "Comments", label: "Show every comment in the plan", when: (c) => c.plan, run: () => showPanel("comments-panel") },

  // Tools
  ...TOOLS.map(([tool, label, key]) => ({ id: `tool-${tool}`, group: "Tools", label, keys: [key], when: (c) => c.plan, run: () => E.setTool(tool) })),

  // View
  { id: "fit", group: "View", label: "Fit the whole sheet", keys: ["0"], when: (c) => c.plan, run: () => E.fitView() },
  { id: "zoom-in", group: "View", label: "Zoom in", keys: ["+", "="], when: (c) => c.plan, run: () => E.zoomBy(1.25) },
  { id: "zoom-out", group: "View", label: "Zoom out", keys: ["-"], when: (c) => c.plan, run: () => E.zoomBy(0.8) },
  { id: "zoom-sel", group: "View", label: "Zoom to the selection", keys: ["Z"], when: (c) => c.n > 0, run: () => E.zoomToSelection() },
  { id: "toggle-panel", group: "View", label: "Show or hide the side panel", keys: ["\\"], run: () => E.toggleSide() },
  { id: "toggle-sheet", group: "View", label: "Show or hide the sheet image", run: () => E.toggleView("show-sheet") },
  { id: "toggle-dims", group: "View", label: "Show or hide measurements on the selection", keys: ["Shift+D"], run: () => E.toggleView("show-dims") },
  { id: "toggle-ids", group: "View", label: "Show or hide object ids on the drawing", keys: ["I"], when: (c) => c.plan, run: () => E.toggleView("show-ids") },
  { id: "toggle-comments", group: "View", label: "Show or hide comment markers", run: () => E.toggleView("show-comments") },
  { id: "toggle-erase", group: "View", label: "Show or hide erase regions", run: () => E.toggleView("show-erase") },
  { id: "toggle-snap", group: "View", label: "Snap to corners and walls on or off", run: () => E.toggleView("snap") },
  { id: "toggle-grid", group: "View", label: "Snap to the 1\" grid on or off", run: () => E.toggleView("grid") },

  // Scale and tracing
  { id: "set-ppi", group: "Scale", label: "Type the scale (sheet pixels per inch)…", when: (c) => c.plan, run: () => E.typeScale() },
  { id: "trace-preview", group: "Trace", label: "Auto-trace walls: preview", when: (c) => c.plan && c.sheet, run: () => runTrace("preview") },
  { id: "trace-apply", group: "Trace", label: "Auto-trace walls: add them", when: (c) => c.plan && c.sheet, run: () => runTrace("apply") },
  { id: "merge-stacked", group: "Clean up", label: "Merge stacked walls (each group in the selection, or the whole plan)", keys: ["Shift+J"], when: (c) => c.plan, run: () => mergeStacked() },
  { id: "select-stacked", group: "Clean up", label: "Select walls that are really one (side by side or broken)", when: (c) => c.plan, run: () => selectFound("stacked") },
  { id: "select-stray", group: "Clean up", label: "Select stray bits (traced letters and symbol pieces)", when: (c) => c.plan, run: () => selectFound("stray") },

  // Help
  { id: "palette", group: "Help", label: "Show every command and shortcut", keys: ["Ctrl+K", "Ctrl+Shift+P", "?"], run: () => openPalette() },
];

function copySelectedIds() {
  const keys = E.selectionInfo().planKeys;
  const ids = keys.map((k) => (k.startsWith("labels:") ? k : E.splitKey(k)[1]));
  copyText(ids.join(", "), keys.length === 1 ? E.describeKey(keys[0]) : `${keys.length} ids`);
}

function copySelectedFlags() {
  const flags = E.selectionInfo()
    .flagKeys.map((k) => getFlag(k.slice(6)))
    .filter(Boolean);
  copyText(flags.map(flagText).join("\n"), flags.map((f) => f.id).join(", "));
}

export function context() {
  const info = E.selectionInfo();
  const s = E.app.state;
  const flags = flagList();
  return {
    ...info,
    dirty: !!(s && s.dirty),
    canUndo: !!(s && s.can_undo),
    canRedo: !!(s && s.can_redo),
    sheet: !!(s && s.sheet),
    steps: s ? s.files.length : 0,
    clipboard: !!(E.app.clipboard && E.app.clipboard.length),
    flags: flags.filter((f) => !f.done).length,
    doneFlags: flags.filter((f) => f.done).length,
  };
}

const available = (cmd, c) => !cmd.when || cmd.when(c);
export const keysText = (cmd) => (cmd.keys ? cmd.keys[0] : "");

export function run(id) {
  const cmd = COMMANDS.find((c) => c.id === id);
  if (cmd && available(cmd, context())) cmd.run();
}

// ---------------------------------------------------------------- keyboard

export function comboOf(e) {
  let key = e.key;
  if (key === " ") key = "Space";
  const named = key.length > 1;
  const letter = /^[a-z]$/i.test(key);
  if (letter) key = key.toUpperCase();
  const parts = [];
  if (e.ctrlKey || e.metaKey) parts.push("Ctrl");
  if (e.altKey) parts.push("Alt");
  if (e.shiftKey && (letter || named)) parts.push("Shift");
  parts.push(key);
  return parts.join("+");
}

/** Run the command bound to this key press, if one applies right now. Returns true when handled. */
export function handleKey(e) {
  const combo = comboOf(e);
  const c = context();
  const cmd = COMMANDS.find((x) => x.keys && x.keys.includes(combo) && available(x, c));
  if (!cmd) return false;
  e.preventDefault();
  remember(cmd.id);
  cmd.run();
  return true;
}

// ---------------------------------------------------------------- command palette

const recent = [];

function remember(id) {
  const i = recent.indexOf(id);
  if (i >= 0) recent.splice(i, 1);
  recent.unshift(id);
  recent.length = Math.min(recent.length, 6);
}

export function openPalette(query = "") {
  hideMenu();
  const box = $("palette");
  const input = $("palette-input");
  const list = $("palette-list");
  let items = [];
  let at = 0;
  const draw = () => {
    const c = context();
    const words = input.value.toLowerCase().split(/\s+/).filter(Boolean);
    const match = (cmd) => words.every((w) => `${cmd.group} ${cmd.label} ${(cmd.keys || []).join(" ")}`.toLowerCase().includes(w));
    const ordered = words.length ? COMMANDS : [...recent.map((id) => COMMANDS.find((x) => x.id === id)), ...COMMANDS.filter((x) => !recent.includes(x.id))];
    items = ordered.filter((cmd) => cmd && available(cmd, c) && match(cmd));
    at = Math.min(at, Math.max(0, items.length - 1));
    list.replaceChildren(
      ...items.map((cmd, i) =>
        el(
          "li",
          {
            role: "option",
            "aria-selected": String(i === at),
            class: i === at ? "active" : "",
            on: { mousedown: (e) => (e.preventDefault(), choose(i)), mousemove: () => i !== at && ((at = i), draw()) },
          },
          el("span", { class: "pal-group" }, cmd.group),
          el("span", { class: "pal-label" }, cmd.label),
          cmd.keys ? el("kbd", {}, cmd.keys.join("  ")) : null,
        ),
      ),
    );
    if (!items.length) list.append(el("li", { class: "note" }, "No command matches."));
    list.children[at]?.scrollIntoView({ block: "nearest" });
  };
  const close = () => {
    box.hidden = true;
    input.oninput = input.onkeydown = null;
  };
  const choose = (i) => {
    const cmd = items[i];
    close();
    if (cmd) {
      remember(cmd.id);
      cmd.run();
    }
  };
  input.value = query;
  input.oninput = () => {
    at = 0;
    draw();
  };
  input.onkeydown = (e) => {
    e.stopPropagation();
    if (e.key === "ArrowDown") at = Math.min(items.length - 1, at + 1);
    else if (e.key === "ArrowUp") at = Math.max(0, at - 1);
    else if (e.key === "Enter") return choose(at);
    else if (e.key === "Escape") return close();
    else return;
    e.preventDefault();
    draw();
  };
  box.hidden = false;
  box.onmousedown = (e) => e.target === box && close();
  draw();
  input.focus();
}

// ---------------------------------------------------------------- right-click menu and the action bar

function selectionCommands(c) {
  return COMMANDS.filter((cmd) => cmd.bar && available(cmd, c));
}

export function showContextMenu(clientX, clientY, at) {
  const c = context();
  const items = selectionCommands(c).map((cmd) => ({ label: cmd.label, keys: keysText(cmd), run: () => (remember(cmd.id), cmd.run()) }));
  if (c.n) items.push({ label: "Copy", keys: "Ctrl+C", run: () => run("copy") }, { label: "Zoom to the selection", keys: "Z", run: () => run("zoom-sel") }, "-");
  items.push(
    { label: "Drop a flag here", keys: "F", run: () => E.flagAt(at) },
    { label: "Paste here", keys: "Ctrl+V", disabled: !c.clipboard, run: () => E.pasteClipboard(at) },
    "-",
    { label: "Select everything", keys: "Ctrl+A", run: () => E.selectAll() },
    { label: "Fit the whole sheet", keys: "0", run: () => E.fitView() },
    { label: "All commands…", keys: "Ctrl+K", run: () => openPalette() },
  );
  showMenu(items, clientX, clientY);
}

let barKey = "";

/** The floating bar of actions next to the selection, placed above it (or below near the top edge). */
export function renderActionBar(box) {
  const bar = $("actionbar");
  const c = context();
  if (!box || !c.n || E.app.tool !== "select" || E.app.drag) {
    bar.hidden = true;
    barKey = "";
    return;
  }
  const key = `${c.keys.join("|")}#${E.app.state.version}`;
  if (key !== barKey) {
    barKey = key;
    bar.replaceChildren(
      el("span", { class: "bar-title" }, E.selectionTitle()),
      ...selectionCommands(c).map((cmd) =>
        el(
          "button",
          {
            type: "button",
            class: cmd.danger ? "danger" : "",
            "data-help": cmd.id,
            on: { click: () => (remember(cmd.id), cmd.run()) },
          },
          cmd.barLabel ? cmd.barLabel(c) : cmd.label,
          cmd.keys ? el("kbd", {}, cmd.keys[0].replace("Delete", "Del")) : null,
        ),
      ),
    );
  }
  bar.hidden = false;
  const stage = $("stage").getBoundingClientRect();
  const [x0, y0, x1, y1] = box;
  const w = bar.offsetWidth;
  const h = bar.offsetHeight;
  let top = y0 - stage.top - h - 14;
  if (top < 44) top = y1 - stage.top + 14;
  top = Math.max(44, Math.min(stage.height - h - 6, top));
  const left = Math.max(6, Math.min(stage.width - w - 6, (x0 + x1) / 2 - stage.left - w / 2));
  bar.style.left = `${left}px`;
  bar.style.top = `${top}px`;
}

export function initCommands() {
  $("palette-btn").addEventListener("click", () => openPalette());
}
