// Comments: notes about one object each, kept in the plan with that object (c1, c2, ...).

import { app, describeKey, getObject, keyBox, message, select, selectionAnchor, selectionInfo, send, splitKey, toScreen, zoomToSelection } from "./app.js";
import { showPanel } from "./panels.js";
import { $, el, popInput } from "./ui.js";

const KINDS = ["walls", "openings", "counters", "fixtures", "labels", "erase"];
const esc = (t) => String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

export function commentsOn(key) {
  const o = key ? getObject(key) : null;
  return (o && o.comments) || [];
}

export function allComments() {
  if (!app.plan) return [];
  const out = [];
  for (const kind of KINDS) {
    (app.plan[kind] || []).forEach((o, i) => {
      for (const comment of o.comments || []) out.push({ key: `${kind}:${kind === "labels" ? i : o.id}`, comment });
    });
  }
  return out;
}

function when(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function byline(c) {
  return [c.id, c.by === "cursor" ? "Cursor" : "you", c.created ? when(c.created) : null, c.edited ? "edited" : null].filter(Boolean).join(" · ");
}

function anchorFor(key) {
  const box = keyBox(key);
  if (!box) return selectionAnchor();
  const [sx, sy] = toScreen((box[0] + box[2]) / 2, box[3]);
  return [sx, sy + 14];
}

export function addComment(key = selectionInfo().single) {
  if (!key || key.startsWith("flags:") || !getObject(key)) return message("Select one object to comment on.", true);
  const [kind, id] = splitKey(key);
  const [x, y] = anchorFor(key);
  popInput({
    x,
    y,
    label: `Comment on ${describeKey(key)}`,
    multiline: true,
    hint: "Enter saves; Shift+Enter for a new line; Esc cancels. Cursor can read and answer it.",
    onCommit: (text) => {
      if (text.trim()) send({ op: "add_comment", kind, id, text: text.trim(), by: "user" });
    },
  });
}

export function editComment(key, c) {
  const [x, y] = anchorFor(key);
  popInput({
    x,
    y,
    label: `Edit ${c.id} on ${describeKey(key)}`,
    value: c.text,
    multiline: true,
    hint: "Enter saves; clear the text to delete it; Esc cancels.",
    onCommit: (text) => {
      if (!text.trim()) deleteComment(c.id);
      else if (text.trim() !== c.text) send({ op: "update_comment", comment: c.id, text: text.trim() });
    },
  });
}

export function deleteComment(id) {
  return send({ op: "delete_comments", comments: [id] }).then((ref) => ref !== undefined && message(`Deleted comment ${id}. Ctrl+Z brings it back.`));
}

/** Show the selected object's comments in the side panel. */
export function revealComments() {
  showPanel("props-panel");
  const block = document.querySelector("#props .comments");
  if (block) block.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

export function commentsMarkup() {
  if (!app.plan || !app.show.comments) return "";
  const byKey = new Map();
  for (const { key, comment } of allComments()) {
    if (!byKey.has(key)) byKey.set(key, []);
    byKey.get(key).push(comment);
  }
  const k = 1 / app.view.z;
  const parts = [];
  for (const [key, list] of byKey) {
    const box = keyBox(key);
    if (!box) continue;
    const n = String(list.length);
    const w = 10 + n.length * 7;
    const sel = app.selection.has(key) ? " selected" : "";
    const title = list.map((c) => `${c.id}: ${c.text}`).join("\n");
    parts.push(
      `<g class="comment-tag${sel}" data-key="${esc(key)}" role="button" aria-label="${n} comment${list.length > 1 ? "s" : ""} on ${esc(describeKey(key))}" transform="translate(${box[2].toFixed(2)} ${box[1].toFixed(2)}) scale(${k})">` +
        `<title>${esc(title)}</title><path d="M1,-5 L0,0 L7,-5 Z"/><rect x="0" y="-19" width="${w}" height="15" rx="4"/>` +
        `<text x="${w / 2}" y="-11.5">${n}</text></g>`,
    );
  }
  return parts.join("");
}

function commentItem(key, c, { showOn = false } = {}) {
  return el(
    "li",
    { class: `comment-item${app.selection.has(key) ? " selected" : ""}` },
    showOn
      ? el("button", { type: "button", class: "link comment-on", title: `Select and zoom to ${describeKey(key)}`, on: { click: () => (select(key), zoomToSelection()) } }, describeKey(key))
      : null,
    el("div", { class: "comment-text" }, c.text),
    el(
      "div",
      { class: "comment-meta" },
      el("span", {}, byline(c)),
      el(
        "span",
        { class: "comment-actions" },
        el("button", { type: "button", class: "link", title: "Edit this comment", on: { click: () => (showOn && select(key), editComment(key, c)) } }, "Edit"),
        el("button", { type: "button", class: "link danger", title: "Delete this comment (Ctrl+Z undoes it)", on: { click: () => deleteComment(c.id) } }, "Delete"),
      ),
    ),
  );
}

/** The comments block in the selected object's properties. */
export function commentsBlock(key) {
  const list = commentsOn(key);
  return el(
    "div",
    { class: "comments" },
    el("h3", {}, list.length ? `Comments (${list.length})` : "Comments"),
    list.length ? el("ul", { class: "comment-list" }, list.map((c) => commentItem(key, c))) : null,
    el("button", { type: "button", "data-help": "comment", on: { click: () => addComment(key) } }, "Add a comment… (Ctrl+/)"),
  );
}

export function renderCommentsPanel() {
  const list = $("comments-list");
  if (!list) return;
  const all = allComments();
  $("comment-count").textContent = all.length ? String(all.length) : "";
  list.replaceChildren(...all.map(({ key, comment }) => commentItem(key, comment, { showOn: true })));
  if (!all.length) list.append(el("li", { class: "note" }, "No comments yet. Select an object and press Ctrl+/ to write one."));
}
