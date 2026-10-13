// Flags: numbered pins dropped on the drawing to point at a place or an area, so a note to
// yourself or to Cursor can say "F3" instead of describing where something is.

import { app, call, describeKey, message, select, toScreen, zoomToBox } from "./app.js";
import { formatLength } from "./geom.js";
import { $, el, popInput } from "./ui.js";

const esc = (t) => String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

export function flagList() {
  return (app.state && app.state.flags) || [];
}

export function getFlag(id) {
  return flagList().find((f) => f.id === id) || null;
}

export function flagsMarkup() {
  if (!app.plan) return "";
  const k = 1 / app.view.z;
  const regions = [];
  const pins = [];
  for (const f of flagList()) {
    const key = `flags:${f.id}`;
    const cls = `${f.done ? " done" : ""}${app.selection.has(key) ? " selected" : ""}`;
    const [x, y] = f.px;
    if (f.size) {
      regions.push(`<rect class="flag-region${cls}" x="${x}" y="${y}" width="${f.size[0]}" height="${f.size[1]}" vector-effect="non-scaling-stroke"/>`);
    }
    const tip = f.note ? `: ${esc(f.note)}` : "";
    pins.push(
      `<g class="flag${cls}" data-key="${key}" role="button" aria-label="flag ${f.id}${f.done ? " (done)" : ""}" transform="translate(${x} ${y}) scale(${k})">` +
        `<title>${f.id}${tip}</title>` +
        `<path d="M0,0 C-4,-6 -11,-11 -11,-19 A11,11 0 1,1 11,-19 C11,-11 4,-6 0,0 Z"/>` +
        `<text x="0" y="-15">${esc(f.id.slice(1))}</text></g>`,
    );
  }
  return regions.join("") + pins.join("");
}

export function flagText(f) {
  const on = f.target ? ` on ${describeKey(`${f.target[0]}:${f.target[1]}`)}` : "";
  const where = f.x !== undefined ? ` at x ${formatLength(f.x)}, y ${formatLength(f.y)}` : "";
  const area = f.w ? `, a ${formatLength(f.w)} × ${formatLength(f.h)} area` : "";
  return `${f.id}${on}${where}${area}${f.note ? ` — ${f.note}` : ""}`;
}

export async function copyText(text, what) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = el("textarea", {}, text);
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  message(`Copied ${what}. Paste it into Cursor.`);
}

export function copyAllFlags() {
  const open = flagList().filter((f) => !f.done);
  if (!open.length) return message("No open flags to copy.", true);
  const lines = open.map((f) => `- ${flagText(f)}`);
  copyText(`Flags in ${app.state.folder} (${app.state.file}):\n${lines.join("\n")}`, `${open.length} flag${open.length > 1 ? "s" : ""}`);
}

export async function addFlag(a, b, targetKey) {
  const body = { action: "add", x: a[0], y: a[1] };
  if (b && Math.abs(b[0] - a[0]) > 1 && Math.abs(b[1] - a[1]) > 1) {
    Object.assign(body, { x: Math.min(a[0], b[0]), y: Math.min(a[1], b[1]), w: Math.abs(b[0] - a[0]), h: Math.abs(b[1] - a[1]) });
  }
  if (targetKey) {
    const [kind, ...rest] = targetKey.split(":");
    const id = rest.join(":");
    body.target = [kind, kind === "labels" ? Number(id) : id];
  }
  const state = await call("/api/flags", body, { selectRef: false });
  if (state && state.flag) editFlagNote(state.flag, true);
}

export function editFlagNote(id, fresh = false) {
  const f = getFlag(id);
  if (!f) return;
  const [sx, sy] = toScreen(f.px[0], f.px[1]);
  popInput({
    x: sx,
    y: sy + 8,
    label: fresh ? `${f.id} placed${f.target ? ` on ${describeKey(`${f.target[0]}:${f.target[1]}`)}` : ""}. Note (optional):` : `Note for ${f.id}`,
    value: f.note || "",
    multiline: true,
    hint: "Say what should change here. Enter saves; Shift+Enter for a new line; Esc skips.",
    onCommit: (text) => {
      if (text.trim() !== (f.note || "")) call("/api/flags", { action: "update", id: f.id, note: text }, { selectRef: false });
    },
  });
}

export function setFlagDone(id, done) {
  return call("/api/flags", { action: "update", id, done }, { selectRef: false });
}

export function deleteFlags(ids) {
  return call("/api/flags", { action: "delete", ids }, { selectRef: false }).then((s) => s && message(`Removed ${ids.join(", ")}.`));
}

export function moveFlag(id, x, y) {
  return call("/api/flags", { action: "update", id, x, y }, { selectRef: false });
}

export function zoomToFlag(id) {
  const f = getFlag(id);
  if (!f) return;
  select(`flags:${id}`);
  const pad = 120;
  const [x, y] = f.px;
  const [w, h] = f.size || [0, 0];
  zoomToBox([x - pad, y - pad, x + w + pad, y + h + pad]);
}

export function renderFlagsPanel() {
  const list = $("flags-list");
  if (!list) return;
  const flags = flagList();
  const open = flags.filter((f) => !f.done).length;
  $("flag-count").textContent = flags.length ? `${open} open${flags.length > open ? `, ${flags.length - open} done` : ""}` : "";
  list.replaceChildren(
    ...flags.map((f) => {
      const key = `flags:${f.id}`;
      const meta = [f.target ? `on ${describeKey(`${f.target[0]}:${f.target[1]}`)}` : null, f.w ? "area" : null, f.by === "cursor" ? "from Cursor" : null]
        .filter(Boolean)
        .join(" · ");
      return el(
        "li",
        { class: `flag-item${f.done ? " done" : ""}${app.selection.has(key) ? " selected" : ""}` },
        el("button", { type: "button", class: "flag-pin", title: `Zoom to ${f.id}`, on: { click: () => zoomToFlag(f.id) } }, f.id),
        el(
          "button",
          { type: "button", class: "flag-body", title: "Edit the note", on: { click: () => (select(key), editFlagNote(f.id)) } },
          el("span", { class: f.note ? "" : "muted" }, f.note || "(no note)"),
          meta ? el("span", { class: "flag-meta" }, meta) : null,
        ),
        el(
          "span",
          { class: "flag-actions" },
          el("button", { type: "button", title: f.done ? "Mark not done" : "Mark done", "aria-label": `${f.done ? "reopen" : "done"} ${f.id}`, on: { click: () => setFlagDone(f.id, !f.done) } }, f.done ? "↺" : "✓"),
          el("button", { type: "button", title: "Copy a reference for Cursor", "aria-label": `copy ${f.id}`, on: { click: () => copyText(flagText(f), f.id) } }, "⧉"),
          el("button", { type: "button", title: "Remove", "aria-label": `remove ${f.id}`, on: { click: () => deleteFlags([f.id]) } }, "×"),
        ),
      );
    }),
  );
  if (!flags.length) list.append(el("li", { class: "note" }, "No flags yet. Press F, then click a spot (or drag an area) to drop one."));
  $("flags-copy").disabled = !open;
  $("flags-clear").disabled = flags.length === open;
}
