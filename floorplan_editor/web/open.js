// Opening things: one Open for plans, folders, and floor plan images, plus drag and drop anywhere.
// The server decides what a file is and what to do with it; this module gets files to it.

import { api, readFileBase64 } from "./api.js";
import { app, fitView, message, setState } from "./app.js";
import { $, el, showMenu } from "./ui.js";

const ACCEPT = /\.(json|png|jpe?g)$/i;

async function request(path, body) {
  app.busy = true;
  try {
    return await api(path, body);
  } finally {
    app.busy = false;
  }
}

function okToLeave() {
  return !app.state || !app.state.dirty || confirm("You have unsaved changes. Open something else and drop them?");
}

function opened(state) {
  if (!state) return;
  app.fittedFor = null;
  app.traceWalls = null;
  app.selection.clear();
  app.sel = null;
  setState(state, { selectRef: false });
  fitView();
  const info = state.opened || {};
  if (info.message) message(info.message);
  if (info.how === "new_from_image") {
    const scale = $("tool-calibrate");
    scale.classList.add("attention");
    setTimeout(() => scale.classList.remove("attention"), 8000);
  }
}

export async function openPicker(kind = "file") {
  if (!okToLeave()) return;
  message(kind === "folder" ? "Pick a folder in the Open dialog…" : "Pick a plan or a floor plan image in the Open dialog… (it can open behind this window)");
  try {
    const result = await request("/api/pick", { kind });
    if (result.cancelled) return message("");
    opened(result);
  } catch (err) {
    if (/no file dialog/i.test(err.message)) {
      message("No system dialog here; choose the file in the browser instead.");
      $("upload").click();
    } else message(err.message, true);
  }
}

export async function openFiles(fileList) {
  const files = [...fileList].filter((f) => ACCEPT.test(f.name));
  if (!files.length) return message("Drop a plan .json or a .png/.jpg floor plan image.", true);
  if (!okToLeave()) return;
  message(`Opening ${files.map((f) => f.name).join(" and ")}…`);
  const payload = await Promise.all(files.map(async (f) => ({ filename: f.name, data: await readFileBase64(f) })));
  try {
    opened(await request("/api/open-any", { files: payload }));
  } catch (err) {
    message(err.message, true);
  }
}

export async function openPath(path) {
  if (!okToLeave()) return;
  try {
    opened(await request("/api/open-any", { path }));
  } catch (err) {
    message(err.message, true);
  }
}

export function showOpenMenu(anchor) {
  const r = anchor.getBoundingClientRect();
  const recent = ((app.state && app.state.recent) || []).filter((p) => !app.state || p !== app.state.folder);
  showMenu(
    [
      { label: "Open a plan or floor plan image…", keys: "Ctrl+O", run: () => openPicker("file") },
      { label: "Open a folder…", run: () => openPicker("folder") },
      { label: "Choose a file in the browser…", title: "Upload from this browser (when the system dialog can't open)", run: () => $("upload").click() },
      { label: "Type a path…", run: typePath },
      ...(recent.length ? ["-", { heading: "Recent folders" }, ...recent.slice(0, 8).map((p) => ({ label: shortPath(p), title: p, run: () => openPath(p) }))] : []),
      "-",
      { heading: `New projects go in ${app.state ? shortPath(app.state.workspace) : "~/floorplans"}` },
    ],
    r.left,
    r.bottom + 2,
  );
}

function typePath() {
  const path = prompt("Path to a plan folder, a plan .json, or a floor plan image", app.state ? app.state.folder : "");
  if (path && path.trim()) openPath(path.trim().replace(/^"|"$/g, ""));
}

function shortPath(p) {
  const parts = p.split(/[\\/]/).filter(Boolean);
  return parts.length > 3 ? `…\\${parts.slice(-3).join("\\")}` : p;
}

export function renderEmpty() {
  const box = $("empty-recent");
  const recent = (app.state && app.state.recent) || [];
  box.replaceChildren(
    ...(recent.length
      ? [el("h3", {}, "Recent"), el("ul", {}, recent.slice(0, 6).map((p) => el("li", {}, el("button", { type: "button", class: "link", title: p, on: { click: () => openPath(p) } }, shortPath(p)))))]
      : []),
  );
  const hasSheet = app.state && app.state.sheet;
  $("empty-sheet").hidden = !hasSheet;
  if (hasSheet) $("empty-sheet").textContent = `Start a plan on ${app.state.sheet.name}`;
}

export function initOpen() {
  $("open").addEventListener("click", () => openPicker("file"));
  $("open-more").addEventListener("click", (e) => showOpenMenu(e.currentTarget));
  $("empty-open").addEventListener("click", () => openPicker("file"));
  $("empty-browse").addEventListener("click", () => $("upload").click());
  $("empty-sheet").addEventListener("click", async () => {
    try {
      opened(await request("/api/new", { auto: true }));
    } catch (err) {
      message(err.message, true);
    }
  });
  $("upload").addEventListener("change", (e) => {
    if (e.target.files.length) openFiles(e.target.files);
    e.target.value = "";
  });
  let depth = 0;
  const zone = $("dropzone");
  const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes("Files");
  window.addEventListener("dragenter", (e) => {
    if (!hasFiles(e)) return;
    depth++;
    zone.hidden = false;
  });
  window.addEventListener("dragleave", (e) => {
    if (!hasFiles(e)) return;
    depth = Math.max(0, depth - 1);
    if (!depth) zone.hidden = true;
  });
  window.addEventListener("dragover", (e) => {
    if (hasFiles(e)) e.preventDefault();
  });
  window.addEventListener("drop", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth = 0;
    zone.hidden = true;
    openFiles(e.dataTransfer.files);
  });
}
