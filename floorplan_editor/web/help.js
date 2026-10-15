// A new button or setting needs an entry here keyed by its data-help or id, or at least a title;
// otherwise it gets no tooltip.

import { COMMANDS } from "./commands.js";
import { $, el } from "./ui.js";

const LENGTHS = "Type lengths the way you'd say them: 126, 10'6\", 10 ft 6 in, 6 1/2\", 320cm, or math like 10' - 4.5. Plain numbers are inches.";

export const HELP = {
  // ---------------------------------------------------------------- toolbar
  open: {
    tip: "Open a plan, a folder of plans, or a floor plan image",
    keys: "Ctrl+O",
    more: [
      "A plan .json opens in its folder, with the folder's other steps and its sheet image. A folder opens its newest step, or plan.json.",
      "An image (.png or .jpg) becomes a project: it's copied into a new folder under ~/floorplans, traced into walls, and scaled from how thick the walls are. Opening the same image again goes back to that project.",
      "You can also drop files anywhere on the window.",
    ],
  },
  "open-more": { tip: "Recent folders, a typed path, or the browser's own file picker" },
  "tool-select": {
    tip: "Click to select. Ctrl+drag a box to select an area (Shift+drag adds). Drag empty space to pan. Right-click for actions.",
    keys: "V",
    more: [
      "Click an object to select it; Shift+click or Ctrl+click adds or removes one. Click empty space to clear the selection.",
      "Ctrl+drag draws a box to select an area, starting anywhere, even on top of walls. Shift+drag adds the box to the selection. Left to right selects what's fully inside; right to left selects anything the box touches.",
      "Drag an object to move it (and everything else selected). Drag empty space to pan. Drag the round handles to reshape: wall ends, door edges, corners. Double-click a wall to split it.",
      "Click a measurement to type it, or drag it aside.",
    ],
  },
  "tool-wall": {
    tip: "Draw walls, one corner per click. Hold Ctrl to place a corner exactly",
    keys: "W",
    more: [
      "Click each corner. After the first, you can type instead: a length goes toward the pointer (10'6\"), a length and angle goes that way (8'<90, counterclockwise from right), and 48, -12 goes right 48 and up 12.",
      "A corner snaps to the nearest wall, counter, or fixture, and the segment straightens when it is close to horizontal or vertical. Hold Ctrl to leave it where the pointer is. Shift forces it straight even from farther away.",
      "Double-click, Enter, or Esc stops. The thickness is in the toolbar while the tool is on.",
    ],
  },
  "tool-door": {
    tip: "Add a door to a wall",
    keys: "D",
    more: [
      "Click a wall to put a door there. It snaps to the nearest wall; hold Ctrl to use only the wall directly under the pointer. Type a number first to change the width.",
      "Click in the gap between two walls in line (a doorway left by tracing) and the walls join into one with the door filling the gap exactly.",
      "With a door selected, S flips which way it swings and H flips the hinge side.",
    ],
  },
  "tool-window": {
    tip: "Add a window to a wall",
    keys: "N",
    more: ["Click a wall to put a window there. It snaps to the nearest wall; hold Ctrl to use only the wall directly under the pointer. Type a number first to change the width. Clicking in a gap between two walls in line fills the gap."],
  },
  "tool-counter": {
    tip: "Draw a rectangular counter",
    keys: "C",
    more: ["Drag a rectangle, or click one corner and type width, depth (25, 60). Each corner snaps to a nearby wall, counter, or fixture, lining up with horizontal and vertical edges. Hold Ctrl to place it exactly where the pointer is. For L-shapes and other outlines, use Outline (P)."],
  },
  "tool-polygon": {
    tip: "Draw a counter of any shape, corner by corner",
    keys: "P",
    more: [
      "Click each corner, or type edges like walls. A corner snaps to the nearest wall, counter, or fixture, and a side straightens when it is close to horizontal or vertical. Hold Ctrl to place it exactly where the pointer is.",
      "Double-click or Enter closes it; Backspace removes the last corner. Later, drag the dot in the middle of an edge to add a corner, or Alt+click a corner to remove it.",
    ],
  },
  "tool-fixture": {
    tip: "Place a toilet, tub, sink, range, and so on",
    keys: "X",
    more: [
      "Pick the type and rotation in the toolbar. Click to place it at its usual size, or drag a box over the printed symbol to fit it exactly. The point snaps to a nearby wall, counter, or fixture. Hold Ctrl to place it exactly where the pointer is.",
      "R turns the next placement, or a selected fixture, 90°. A fixture's center is the point it turns around.",
    ],
  },
  "tool-label": {
    tip: "Add a text label. Hold Ctrl to place it exactly",
    keys: "T",
    more: ["Click where it goes and type the text. The point snaps to a nearby wall, counter, or fixture; hold Ctrl to leave it where the pointer is. Text typed in the toolbar box is reused for every click, for dropping the same label several times."],
  },
  "tool-erase": {
    tip: "White out printed ink on the sheet",
    keys: "E",
    more: [
      "Drag a rectangle over ink you want gone, such as a fixture you removed or an old room name. Corners snap to nearby walls, counters, and fixtures; hold Ctrl to place them exactly where the pointer is. It's whited out in the editor and in every saved overlay.",
      "The sheet image itself is never changed. Erase regions are stored in the plan, so they can be moved, resized, or deleted later.",
    ],
  },
  "tool-calibrate": {
    tip: "Set the scale from a length printed on the sheet",
    keys: "K",
    more: ["Click both ends of a dimension printed on the sheet (a room's width, say), then type that length, such as 26'5\". This sets how many sheet pixels make an inch, so every size you see and type is right. Check it after tracing a new image."],
  },
  "tool-measure": {
    tip: "Measure a distance, or the gap to the nearest edges",
    keys: "M",
    more: [
      "Drag to measure. Each end snaps to the nearest wall, counter, or fixture, and the line straightens when it is close to horizontal or vertical. Hold Ctrl to measure exactly where you drag.",
      "Move the pointer and dotted lines show the distance to the first edge in each direction. Click without dragging to keep the horizontal and vertical ones.",
      "Drag an endpoint to move that end. Drag the line, or the handle in the middle, to move both ends together. M is Measure when nothing is selected, and Move… when something is.",
    ],
  },
  "tool-flag": {
    tip: "Drop a numbered flag to point Cursor at a spot or area",
    keys: "F",
    more: [
      "Click to drop a flag, or drag to flag an area. Add a note (\"make this toilet 2 inches narrower\"), then tell Cursor \"do what F3 says\" instead of describing where things are. Cancel the note, or leave it blank, and the new flag is removed.",
      "Cursor sees each flag's note, place, and what it sits on, and can drop its own flags to show you what it means. Flags are kept with the folder, never in a plan.",
    ],
  },
  "palette-btn": { tip: "Every command and its shortcut, searchable", keys: "Ctrl+K" },
  undo: { tip: "Undo", keys: "Ctrl+Z" },
  redo: { tip: "Redo", keys: "Ctrl+Y" },
  save: { tip: "Save over the open file", keys: "Ctrl+S", more: ["Saving also writes a matching .svg, .png, and -overlay.png. Edits are kept by the server until then, so reloading the page doesn't lose them, but stopping the server does."] },
  "save-more": { tip: "Save as the next step, or save a copy under a new name and folder" },
  "save-step": {
    tip: "Save as the next plan-stepN.json",
    keys: "Ctrl+Shift+S",
    more: [
      "Treat plan.json as the apartment as it is today, and save each idea as a step. The editor switches to the new step.",
      "Steps built from the same base can be combined later with Merge checked under Folder and steps.",
    ],
  },
  "toggle-side": { tip: "Show or hide the side panel", keys: "\\" },

  // ---------------------------------------------------------------- start screen
  "empty-open": { tip: "Pick a plan, folder, or image with the system dialog", keys: "Ctrl+O" },
  "empty-browse": { tip: "Pick files with the browser's own picker, when the system dialog can't open" },
  "empty-sheet": { tip: "Make a new plan.json for the image in this folder and trace its walls" },
  fit: { tip: "Fit the whole sheet in view", keys: "0" },
  "zoom-out": {
    tip: "Zoom out, centered on the view",
    keys: "−",
    more: ["Each click steps out. Hold the button to keep going. The scroll wheel still zooms toward the pointer."],
  },
  "zoom-in": {
    tip: "Zoom in, centered on the view",
    keys: "+",
    more: ["Each click steps in. Hold the button to keep going. The scroll wheel still zooms toward the pointer."],
  },
  "zoom-level": {
    tip: "Fit the whole sheet",
    keys: "0",
    more: ["The number is the current zoom. 100% is one sheet pixel per screen pixel. Click it to fit the sheet in the window."],
  },
  "copy-ids": { tip: "Copy the selected ids, to name them to Cursor", keys: "Ctrl+Shift+C" },

  // ---------------------------------------------------------------- selection actions
  dims: { more: ["Opens the main size for typing. Every measurement on the drawing can be clicked and typed too. " + LENGTHS] },
  convert: {
    tip: "Turn the selection into what it really is",
    more: [
      "Tracing turns everything into walls: door leaves, fixture outlines, letters. Select the pieces and pick what they are: one wall, a door, a window, a fixture, a box counter, a counter outline, a label, or an erase region. Each choice has a one-key shortcut while the menu is open.",
      "A door or window fills the gap between two selected walls in line, joining them into one. A fixture or counter fills the selection's box. A label asks for its text and matches the size.",
      "The selected pieces are replaced, and Ctrl+Z undoes it.",
    ],
  },
  "merge-walls": {
    tip: "Merge the selected walls into one",
    more: ["Walls running the same way become one wall. Its thickness reaches the outer faces of all of them, and the longest piece keeps its id. Doors and windows move onto it, and walls joined to the pieces stay joined. If one piece is much shorter (a chase beside a wall), the whole wall gets that thickness."],
  },
  thickness: {
    tip: "Set one thickness for the selected walls",
    more: ["Type the thickness. The side panel has the same field with Stays put, which picks the face that holds still (top or bottom, left or right), so a wall can thicken into one room without moving its other side."],
  },
  "glass-rail": {
    tip: "Draw the selected walls as glass railings",
    more: ["A glass rail is drawn as a thin outline with a line down the middle instead of a solid wall. Draw it with the Wall tool, then tick this. Untick to make it a solid wall again."],
  },
  "move-by": { more: ["Type how far to move: 6, -18 is 6 inches right and 18 up, and 3'<90 is 3 feet at 90° (straight up). The arrow keys nudge 1 inch (Shift 12, Alt ¼)."] },
  split: { tip: "Split the wall into two at its middle", more: ["Double-clicking a wall splits it right where you click."] },
  outline: { tip: "Turn the box counter into an outline so its corners can be moved one at a time" },
  "flag-selection": { tip: "Drop a flag on the selection, to tell Cursor about it" },
  "flag-copy": { tip: "Copy a one-line reference to paste into a Cursor chat" },
  "flag-done": { tip: "Mark the flag handled (or reopen it)" },
  "flag-note": { tip: "Edit the flag's note, which Cursor reads" },
  comment: {
    tip: "Write a comment about this object",
    keys: "Ctrl+/",
    more: [
      "A comment belongs to one object and is saved in the plan with it. It moves when the object moves, carries over when pieces are merged or converted, and goes away if the object is deleted. Copies don't get it. Ctrl+Z undoes adding, editing, or deleting one.",
      "Commented objects get a purple bubble with the count; click it to read them. The Comments panel lists every comment in the plan. Cursor reads them, writes its own (marked Cursor), and can edit or delete them.",
      "Flags are for pointing at a place for a task and marking it done; comments are lasting notes about a thing.",
    ],
  },

  // ---------------------------------------------------------------- side panel: plan
  "plan-up": {
    tip: "Which compass direction is at the top of the sheet",
    more: ["Measurements and Cursor's descriptions use compass sides (\"the north wall\"), so this needs to match the sheet's compass or its window labels."],
  },
  "plan-scale": {
    tip: "Sheet pixels per inch",
    more: ["Every size depends on this. The easiest way to set it is Set scale from a known length: click both ends of a printed dimension and type it."],
  },
  "size-field": {
    tip: "Type a size; Enter saves it",
    more: [
      LENGTHS,
      "Up and Down arrows nudge by 1\" (Shift 12\", Alt 1/4\") and apply at once. Esc puts the value back. Dragging the field's name sideways scrubs it.",
    ],
  },
  "stays-put": {
    tip: "What holds still when a size changes",
    more: ["Changing a length, width, or thickness has to move something. Pick the end, side, or face that stays where it is; the pick is remembered for next time."],
  },

  // ---------------------------------------------------------------- side panel: flags
  "show-flags": { tip: "Show or hide the flag pins and shaded areas on the drawing. The list stays either way." },
  "flags-add": { tip: "Drop a flag: click a spot or drag an area", keys: "F" },
  "flags-copy": { tip: "Copy every open flag, with notes, to paste into a Cursor chat" },
  "flags-clear": { tip: "Remove the flags marked done" },

  // ---------------------------------------------------------------- side panel: folder and steps
  "folder-input": { tip: "Type or paste a folder path, then Go" },
  "folder-go": { tip: "Open the folder typed to the left" },
  merge: {
    tip: "Combine the checked steps into plan-merged.json",
    more: [
      "Each checked step's changes from plan.json are applied together, matched by object id. If two steps change the same object differently, the merge stops and names both, so nothing is silently overwritten.",
      "Steps built one on top of another already include each other; merge steps that each started from plan.json.",
    ],
  },
  "save-as": {
    tip: "Save a copy under a name and folder you choose",
    more: ["The pictures (.png and the overlay) are written beside the file, and that file becomes the one open. A different folder is copied with its sheet images, then opened. The folder starts as the default from Settings, or the folder you have open."],
  },
  "save-folder-default": { tip: "Where Save as… starts. Blank means the folder you have open." },
  "save-folder": {
    tip: "Copy this file into a new folder as plan.json, with every floor's sheet image",
    more: [
      "Type a name (it becomes a folder next to this one) or a full path. The folder must be new or empty. Flags come along too, and the editor opens the new folder.",
      "One file can hold a whole building: add floors with + Floor on the bar over the drawing, and flip between them with PageUp and PageDown.",
    ],
  },
  revert: { tip: "Throw away unsaved edits and reload the saved file" },
  "view-overlay": { tip: "Open the open file drawn in red over the sheet, in a new tab", more: ["The overlay is the quickest check that the plan sits on the drawing. It's saved next to each plan as -overlay.png, with erase regions applied."] },
  "new-plan": { tip: "Start a plan from an image with your own settings", more: ["Usually you don't need this: Open an image and it's set up and traced for you. Use this to choose which way is up, give a known scale, or skip tracing."] },

  // ---------------------------------------------------------------- side panel: view
  "show-sheet": { tip: "Show the source sheet image behind the plan" },
  "sheet-opacity": { tip: "How strongly the sheet shows behind the plan" },
  "show-dims": { tip: "Show the clickable measurements on the selection", keys: "Shift+D" },
  "show-ids": {
    tip: "Show every object's id on the drawing",
    keys: "I",
    more: ["Ids are how Cursor names things, so you can say \"convert w74 and w75 into a door\". Ctrl+Shift+C copies the selected ids. Cursor can also see what you have selected, so \"make what I selected a toilet\" works too."],
  },
  "show-comments": { tip: "Show a purple bubble on each object that has comments; click one to read them" },
  "show-erase": { tip: "Show erase regions as blue boxes (the ink stays whited out either way)" },
  snap: { tip: "Snap points to wall ends and wall faces" },
  grid: { tip: "Snap points to whole inches" },

  // ---------------------------------------------------------------- side panel: auto-trace
  "tr-dark": { tip: "Gray level that counts as ink (0 is black)", more: ["Raise it if the sheet is faint and walls are missed; lower it if gray fills and hatching come out as walls."] },
  "tr-min": { tip: "Thinnest bar, in sheet pixels, that counts as a wall", more: ["Raise it to skip window, cabinet, and fixture lines. Lower it to pick up thin partitions, at the cost of much more cleanup."] },
  "tr-max": { tip: "Thickest bar, in sheet pixels, that counts as a wall", more: ["Lower it if solid filled areas (columns, chases) come out as giant walls."] },
  "tr-len": { tip: "Bars shorter than this, in sheet pixels, are dropped" },
  "tr-merge": { tip: "Join thin bars lying side by side into one thicker wall", more: ["Thick walls are often drawn as two outlines with a gap or fill between. With this on, bars side by side and about the same length become one wall with their combined thickness."] },
  "trace-preview": { tip: "Show what the tracer finds, in green over the sheet, without changing the plan" },
  "trace-clear": { tip: "Hide the green preview" },
  "trace-replace": { tip: "Delete the plan's existing walls before adding the traced ones" },
  "trace-apply": { tip: "Add the traced walls to the plan (Ctrl+Z undoes it)" },
  "clean-stacked": {
    tip: "Select walls that are really one wall",
    more: ["Finds thin bars side by side and walls broken in two along their length, so you can check them before merging. Breaks where another wall joins (a T) are left alone. Traced letters are skipped. With walls selected, it looks only among them."],
  },
  "clean-merge": { tip: "Merge each group of walls that are really one", keys: "Shift+J", more: ["The same groups Select stacked walls finds, each merged into a single wall. With walls selected, only those are considered. Ctrl+Z undoes it."] },
  "clean-stray": {
    tip: "Select traced letters and symbol pieces",
    more: ["Finds short, thin walls that touch no real wall: almost always the letters of room names and captions, or bits of a symbol. Look them over, then Delete them, or Convert them into a label or an erase region. Blocky pieces such as columns aren't included."],
  },

  // ---------------------------------------------------------------- new plan dialog
  "new-up": { tip: "Which compass direction is at the top of the sheet" },
  "new-ppi": { tip: "Sheet pixels per inch, if you know it", more: ["Leave it empty to guess from how thick the walls are, then check it with Scale (K) against a printed dimension."] },
  "new-auto": { tip: "Trace the walls right away" },
  "new-create": { tip: "Create the plan in the open folder" },
};

// ---------------------------------------------------------------- the tooltip

const SHOW_DELAY = 450;
const HIDE_DELAY = 200;

let tip = null;
let current = null;
let showTimer = 0;
let hideTimer = 0;
let lastHidden = 0;

const commandLookup = (id) => COMMANDS.find((c) => c.id === id) || null;

function helpFor(node) {
  const key = node.dataset.help || node.id;
  const entry = key ? HELP[key] : null;
  const cmd = key ? commandLookup(key) : null;
  const title = node.dataset.tip || "";
  if (!entry && !cmd && !title) return null;
  return {
    tip: (entry && entry.tip) || (cmd && cmd.label) || title,
    keys: (entry && entry.keys) || (cmd && cmd.keys && cmd.keys[0]) || "",
    more: (entry && entry.more) || null,
  };
}

/** The element a pointer or focus target gets help from: itself or an ancestor, or a label's control. */
function helpTarget(node) {
  for (let n = node; n && n !== document.body && n.nodeType === 1; n = n.parentElement) {
    if (n.id === "tip") return null;
    if (n.title) {
      n.dataset.tip = n.title;
      n.removeAttribute("title");
    }
    if (n.dataset.help || n.dataset.tip || (n.id && (HELP[n.id] || commandLookup(n.id)))) return n;
    if (n.tagName === "LABEL") {
      const control = n.control || n.querySelector("input, select, textarea");
      if (control && control.id && HELP[control.id]) return control;
    }
    if (n.matches("#svg, #side, #toolbar, #menu, #palette")) return null;
  }
  return null;
}

function place(anchor) {
  const r = anchor.getBoundingClientRect();
  const w = tip.offsetWidth;
  const h = tip.offsetHeight;
  let top = r.bottom + 8;
  if (top + h > window.innerHeight - 6) top = Math.max(6, r.top - h - 8);
  const left = Math.max(6, Math.min(window.innerWidth - w - 6, r.left + r.width / 2 - w / 2));
  tip.style.left = `${left}px`;
  tip.style.top = `${top}px`;
}

function show(anchor) {
  const help = helpFor(anchor);
  if (!help) return;
  clearTimeout(hideTimer);
  current = anchor;
  const body = el("div", { class: "tip-more", hidden: true }, (help.more || []).map((p) => el("p", {}, p)));
  const learn = help.more
    ? el(
        "button",
        {
          type: "button",
          class: "link tip-learn",
          on: {
            click: (e) => {
              e.stopPropagation();
              body.hidden = false;
              learn.remove();
              tip.classList.add("expanded");
              place(anchor);
            },
          },
        },
        "Learn more",
      )
    : null;
  tip.replaceChildren(...[el("div", { class: "tip-main" }, el("span", {}, help.tip), help.keys ? el("kbd", {}, help.keys) : null), learn, body].filter(Boolean));
  tip.classList.remove("expanded");
  tip.hidden = false;
  anchor.setAttribute("aria-describedby", "tip");
  place(anchor);
}

export function hideTip() {
  clearTimeout(showTimer);
  clearTimeout(hideTimer);
  if (!tip || tip.hidden) return;
  tip.hidden = true;
  if (current) current.removeAttribute("aria-describedby");
  current = null;
  lastHidden = Date.now();
}

function scheduleHide() {
  clearTimeout(showTimer);
  clearTimeout(hideTimer);
  hideTimer = setTimeout(hideTip, tip.classList.contains("expanded") ? HIDE_DELAY * 2 : HIDE_DELAY);
}

export function initTips() {
  tip = $("tip");
  document.addEventListener("pointerover", (e) => {
    if (e.pointerType === "touch") return;
    if (tip.contains(e.target)) {
      clearTimeout(hideTimer);
      return;
    }
    const anchor = helpTarget(e.target);
    if (anchor === current && anchor) {
      clearTimeout(hideTimer);
      return;
    }
    if (!anchor) {
      if (current) scheduleHide();
      return;
    }
    clearTimeout(showTimer);
    const quick = !tip.hidden || Date.now() - lastHidden < 400;
    if (!tip.hidden && tip.classList.contains("expanded")) return scheduleHide();
    showTimer = setTimeout(() => show(anchor), quick ? 60 : SHOW_DELAY);
  });
  document.addEventListener("pointerdown", (e) => {
    if (!tip.contains(e.target)) hideTip();
  });
  document.addEventListener("focusin", (e) => {
    if (!e.target.matches || !e.target.matches(":focus-visible")) return;
    const anchor = helpTarget(e.target);
    if (anchor) show(anchor);
  });
  document.addEventListener("focusout", () => setTimeout(() => !tip.contains(document.activeElement) && current && !current.matches(":hover") && hideTip(), 0));
  window.addEventListener(
    "keydown",
    (e) => {
      if (tip.hidden) return;
      if (e.key === "Escape") {
        if (tip.classList.contains("expanded")) e.stopImmediatePropagation();
        hideTip();
      } else if (e.key === "F1" && current) {
        e.preventDefault();
        tip.querySelector(".tip-learn")?.click();
      }
    },
    true,
  );
  window.addEventListener("scroll", hideTip, true);
}
