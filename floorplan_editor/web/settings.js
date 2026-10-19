// Settings: About, the command list, and the colors objects are drawn in.

import { api } from "./api.js";
import { app, call, message, send } from "./app.js";
import { COMMANDS } from "./commands.js";
import { $, el } from "./ui.js";

const KIND_LABELS = {
  walls: "Walls",
  doors: "Doors",
  windows: "Windows",
  counters: "Counters",
  fixtures: "Fixtures",
  furniture: "Furniture",
  equipment: "Equipment",
  labels: "Labels",
  measures: "Measurements",
};

let saveSeq = 0;
let pendingAppearance = null;
let postTimer = 0;
const planTimers = new Map();

export function applyAppearance(appearance, plan) {
  if (!appearance || !appearance.groups) return;
  app.appearance = appearance;
  const style = document.documentElement.style;
  style.setProperty("--select", appearance.selected || "#0d47a1");
  const group = appearance.groups.find((item) => (item.kinds || []).includes("measures"));
  if (!group) return;
  const overrides = (plan && plan.colors) || {};
  style.setProperty("--measure", overrides[group.id] || group.color);
}

export function initSettings() {
  $("about-btn").addEventListener("click", () => {
    showTab("about");
    $("save-folder-default").value = (app.state && app.state.save_folder) || "";
    $("settings-dialog").showModal();
  });
  $("save-folder-default").addEventListener("change", saveDefaultFolder);
  $("settings-dialog").addEventListener("click", (e) => {
    if (e.target === $("settings-dialog")) $("settings-dialog").close();
  });
  $("settings-close").addEventListener("click", () => $("settings-dialog").close());
  $("settings-form").addEventListener("submit", (e) => e.preventDefault());
  for (const tab of document.querySelectorAll("[data-settings-tab]")) {
    tab.addEventListener("click", () => showTab(tab.dataset.settingsTab));
  }
  $("new-color-group").addEventListener("click", addGroup);
  $("selection-color").addEventListener("input", () => {
    const appearance = { ...app.appearance, selected: $("selection-color").value };
    applyAppearance(appearance, app.plan);
    postAppearance(appearance);
  });
  $("add-rule").addEventListener("click", addRule);
  $("new-rule-text").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      addRule();
    }
  });
  $("pin-check-all").addEventListener("click", pinCheck);
}

async function saveDefaultFolder() {
  const folder = $("save-folder-default").value.trim().replace(/^"|"$/g, "");
  try {
    const saved = await api("/api/prefs", { save_folder: folder });
    if (app.state) app.state.save_folder = saved.save_folder;
    $("save-folder-default").value = saved.save_folder;
    message(saved.save_folder ? `Save as… will start in ${saved.save_folder}` : "Save as… will start in the folder you have open");
  } catch (err) {
    message(err.message === "Not Found" ? "Restart the editor to keep a default save folder." : err.message, true);
  }
}

function showTab(id) {
  for (const name of ["about", "commands", "customize", "rules"]) {
    $(`settings-${name}`).hidden = name !== id;
    document.querySelector(`[data-settings-tab="${name}"]`).classList.toggle("on", name === id);
    document.querySelector(`[data-settings-tab="${name}"]`).setAttribute("aria-selected", name === id ? "true" : "false");
  }
  if (id === "commands") renderCommands();
  if (id === "customize") renderCustomize();
  if (id === "rules") renderRules();
}

function renderCommands() {
  const host = $("settings-commands");
  if (host.childElementCount) return;
  const order = [];
  const byGroup = new Map();
  for (const cmd of COMMANDS) {
    if (!byGroup.has(cmd.group)) {
      byGroup.set(cmd.group, []);
      order.push(cmd.group);
    }
    byGroup.get(cmd.group).push(cmd);
  }
  for (const name of order) {
    host.append(el("h3", { text: name }));
    for (const cmd of byGroup.get(name)) {
      const keys = (cmd.keys || []).map((key) => el("kbd", { text: key }));
      host.append(el("div", { class: "cmd-row" }, el("span", { text: cmd.label }), el("span", { class: "cmd-keys" }, keys)));
    }
  }
}

function renderCustomize() {
  const appearance = app.appearance;
  if (!appearance) return;
  const overrides = (app.plan && app.plan.colors) || {};
  const host = $("color-groups");
  host.replaceChildren(
    ...appearance.groups.map((group) => {
      const planColor = overrides[group.id] || group.color;
      const overridden = Object.prototype.hasOwnProperty.call(overrides, group.id);
      return el(
        "section",
        { class: "color-group", "data-group": group.id },
        el("input", {
          class: "group-name",
          value: group.name,
          "aria-label": "Group name",
          on: { change: () => saveGroups() },
        }),
        chips(group),
        el(
          "div",
          { class: "color-row" },
          el("label", {}, "Editor", el("input", {
            type: "color",
            "data-role": "default",
            value: group.color,
            on: { input: (e) => setGroupColor(group.id, e.target.value) },
          })),
          el("label", {}, el("input", {
            type: "checkbox",
            "data-role": "override",
            checked: overridden,
            disabled: !app.plan,
            on: { change: (e) => setPlanOverride(group.id, e.target.checked) },
          }), "This plan"),
          el("input", {
            type: "color",
            "data-role": "plan",
            value: planColor,
            disabled: !overridden,
            "aria-label": `Color for this plan, ${group.name}`,
            on: { input: (e) => sendPlanColor(group.id, e.target.value) },
          }),
          group.kinds.length
            ? null
            : el("button", { type: "button", on: { click: () => removeGroup(group.id) } }, "Remove group"),
        ),
      );
    }),
  );
  $("selection-color").value = appearance.selected || "#0d47a1";
}

function chips(group) {
  const zone = el(
    "div",
    { class: "kind-chips" },
    ...group.kinds.map((kind) =>
      el("span", {
        class: "kind-chip",
        draggable: "true",
        "data-kind": kind,
        text: KIND_LABELS[kind] || kind,
        on: { dragstart: (e) => e.dataTransfer.setData("text/plain", kind) },
      }),
    ),
  );
  zone.addEventListener("dragover", (e) => {
    e.preventDefault();
    zone.classList.add("drop");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("drop"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("drop");
    moveKind(e.dataTransfer.getData("text/plain"), group.id);
  });
  return zone;
}

function moveKind(kind, groupId) {
  if (!kind || !app.appearance) return;
  const appearance = structuredClone(app.appearance);
  let moved = false;
  for (const group of appearance.groups) {
    const index = group.kinds.indexOf(kind);
    if (index < 0) continue;
    if (group.id === groupId) return;
    group.kinds.splice(index, 1);
    moved = true;
  }
  const target = appearance.groups.find((group) => group.id === groupId);
  if (!moved || !target) return;
  target.kinds.push(kind);
  commitGroups(appearance);
}

function setGroupColor(groupId, color) {
  const appearance = structuredClone(app.appearance);
  const group = appearance.groups.find((item) => item.id === groupId);
  if (!group) return;
  group.color = color;
  applyAppearance(appearance, app.plan);
  postAppearance(appearance);
}

function saveGroups() {
  const appearance = structuredClone(app.appearance);
  for (const card of $("color-groups").querySelectorAll(".color-group")) {
    const group = appearance.groups.find((item) => item.id === card.dataset.group);
    const name = card.querySelector(".group-name").value.trim();
    if (group && name) group.name = name;
  }
  commitGroups(appearance);
}

function addGroup() {
  const appearance = structuredClone(app.appearance);
  let n = 1;
  const ids = new Set(appearance.groups.map((group) => group.id));
  while (ids.has(`group-${n}`)) n += 1;
  appearance.groups.push({ id: `group-${n}`, name: "New group", color: "#546e7a", kinds: [] });
  commitGroups(appearance);
}

function removeGroup(groupId) {
  const appearance = structuredClone(app.appearance);
  appearance.groups = appearance.groups.filter((group) => group.id !== groupId || group.kinds.length);
  commitGroups(appearance);
}

function commitGroups(appearance) {
  applyAppearance(appearance, app.plan);
  renderCustomize();
  postAppearance(appearance);
}

function postAppearance(appearance) {
  pendingAppearance = appearance;
  clearTimeout(postTimer);
  postTimer = setTimeout(flushAppearance, 180);
}

async function flushAppearance() {
  const appearance = pendingAppearance;
  const seq = ++saveSeq;
  try {
    const saved = await api("/api/appearance", appearance);
    if (seq !== saveSeq || !saved) return;
    app.appearance = saved;
  } catch (err) {
    if (seq === saveSeq) message(err.message, true);
  }
}

function setPlanOverride(groupId, on) {
  const group = app.appearance.groups.find((item) => item.id === groupId);
  const input = document.querySelector(`.color-group[data-group="${groupId}"] [data-role=plan]`);
  if (input) input.disabled = !on;
  sendPlanColor(groupId, on ? (input && input.value) || group.color : null);
}

function sendPlanColor(groupId, color) {
  clearTimeout(planTimers.get(groupId));
  planTimers.set(
    groupId,
    setTimeout(() => {
      if (app.plan) send({ op: "set_colors", colors: { [groupId]: color } }, { selectRef: false });
    }, 180),
  );
}

export function syncPlanOverrides() {
  const dialog = $("settings-dialog");
  if (!dialog || !dialog.open || $("settings-customize").hidden) return;
  const overrides = (app.plan && app.plan.colors) || {};
  for (const card of $("color-groups").querySelectorAll(".color-group")) {
    const box = card.querySelector("[data-role=override]");
    const input = card.querySelector("[data-role=plan]");
    if (document.activeElement === input || document.activeElement === box) continue;
    const color = overrides[card.dataset.group];
    const on = typeof color === "string";
    box.checked = on;
    input.disabled = !on;
    if (on) input.value = color;
  }
}

let ruleRows = [];
const shownRules = new Set();

const PROPERTIES = {
  "side-clearance": "Distance from the fixture's center to the nearest wall face beside it.",
  "wall-overlap": "How far the fixture's box overlaps a wall.",
  jamb: "A door fails when both jambs are open.",
  length: "The length of a wall.",
};

function validCode(rule) {
  const code = rule && rule.code;
  if (!code || typeof code.type !== "string" || !code.type.trim()) return null;
  if (typeof code.property !== "string" || !Object.hasOwn(PROPERTIES, code.property)) return null;
  if (code.compare !== "at-least" && code.compare !== "at-most") return null;
  const inches = code.inches;
  if (typeof inches !== "number" || !Number.isFinite(inches) || inches < 0) return null;
  const compare = code.compare === "at-least" ? "at least" : "at most";
  return {
    line: `${code.type}, ${code.property}, ${compare} ${inches} inches`,
    sentence: PROPERTIES[code.property],
  };
}

async function renderRules() {
  const host = $("rule-rows");
  try {
    ruleRows = (await api("/api/rules")).rules;
  } catch (err) {
    host.replaceChildren(el("p", { class: "note", text: err.message === "Not Found" ? "Restart the editor to edit placement rules." : err.message }));
    return;
  }
  paintRules();
}

function paintRules() {
  const enabled = new Set((app.plan && app.plan.enabled_rules) || []);
  const pin = $("pin-check-all");
  if (pin) pin.disabled = !app.plan;
  $("rule-rows").replaceChildren(
    ...ruleRows.map((rule) => {
      const sentence = el("textarea", { rows: "2", "aria-label": `Sentence for ${rule.id}`, spellcheck: "true" });
      sentence.value = rule.text;
      sentence.addEventListener("change", () => saveSentence(rule.id, sentence.value));
      const code = validCode(rule);
      if (!code) shownRules.delete(rule.id);
      const open = code && shownRules.has(rule.id);
      return el(
        "div",
        { class: "rule-row" },
        el(
          "label",
          { class: "rule-on" },
          el("input", {
            type: "checkbox",
            checked: enabled.has(rule.id),
            disabled: !app.plan,
            "aria-label": `This plan: ${rule.id}`,
            on: { change: (e) => enableRule(rule.id, e.target.checked) },
          }),
          "This plan",
        ),
        el(
          "div",
          {},
          sentence,
          code
            ? el("button", {
              type: "button",
              class: "rule-show",
              on: { click: () => { shownRules.has(rule.id) ? shownRules.delete(rule.id) : shownRules.add(rule.id); paintRules(); } },
            }, open ? "Hide rule" : "Show rule")
            : el("p", { class: "note", text: "No check yet. The pinned check converts this sentence." }),
          open ? el("div", { class: "rule-code" }, el("div", { text: code.line }), el("p", { text: code.sentence })) : null,
          /^r\d+$/.test(rule.id) ? el("button", { type: "button", on: { click: () => removeRule(rule.id) } }, "Remove") : null,
        ),
      );
    }),
  );
}

async function saveSentence(id, text) {
  const current = ruleRows.find((rule) => rule.id === id);
  if (current && current.text === text.trim()) return;
  try {
    ruleRows = (await api("/api/rules", { action: "text", id, text })).rules;
    paintRules();
  } catch (err) {
    message(err.message, true);
    paintRules();
  }
}

async function enableRule(id, on) {
  try {
    await send({ op: "set_enabled_rules", id, on }, { selectRef: false });
  } catch (err) {
    message(err.message, true);
  }
  paintRules();
}

async function addRule() {
  const text = $("new-rule-text").value.trim();
  if (!text) return;
  try {
    ruleRows = (await api("/api/rules", { action: "add", text })).rules;
    $("new-rule-text").value = "";
    paintRules();
  } catch (err) {
    message(err.message, true);
  }
}

async function removeRule(id) {
  try {
    ruleRows = (await api("/api/rules", { action: "delete", id })).rules;
    paintRules();
  } catch (err) {
    message(err.message, true);
  }
}

async function pinCheck() {
  const state = await call("/api/flags", { action: "pin_check" }, { selectRef: false });
  if (state && state.flag) {
    message(`Pinned ${state.flag}. A sweep converts sentences with no check, then measures this floor.`);
  }
}
