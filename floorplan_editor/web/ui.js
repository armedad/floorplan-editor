// Small DOM helpers shared by the editor: element building, the inline value popover, and menus.

export const $ = (id) => document.getElementById(id);

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "on") for (const [ev, fn] of Object.entries(v)) node.addEventListener(ev, fn);
    else if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v === true ? "" : v);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

let popDone = null;

/**
 * A small box at (x, y) on screen for typing one value. Enter calls onCommit(text, choice);
 * Escape or clicking elsewhere calls onCancel. `choices` adds a row of options (an anchor, say).
 */
export function popInput({ x, y, label, value = "", hint = "", choices = null, choice = null, multiline = false, selectAll = true, onCommit, onCancel }) {
  closePop();
  const pop = $("pop");
  let picked = choice;
  const input = multiline ? el("textarea", { rows: 3, spellcheck: "false" }) : el("input", { spellcheck: "false", autocomplete: "off" });
  input.value = value;
  const start = input.value;
  const changed = () => input.value !== start || picked !== choice;
  const shield = $("pop-shield");
  const warn = el("div", { class: "pop-warn", role: "alert", hidden: true }, "You changed this. Save it, or Cancel to throw the change away.");
  const syncShield = () => {
    shield.hidden = !changed();
    if (shield.hidden) warn.hidden = true;
  };
  const insist = () => {
    warn.hidden = false;
    pop.classList.remove("insist");
    void pop.offsetWidth;
    pop.classList.add("insist");
    input.focus();
  };
  // Capture phase, so the click that dismisses an unchanged popup still reaches what was clicked.
  const onDown = (e) => {
    if (pop.contains(e.target) || e.target === shield) return;
    if (changed()) {
      e.preventDefault();
      e.stopPropagation();
      insist();
    } else finish(false);
  };
  const finish = (ok) => {
    if (!popDone) return;
    popDone = null;
    window.removeEventListener("pointerdown", onDown, true);
    shield.onpointerdown = null;
    shield.hidden = true;
    pop.hidden = true;
    pop.classList.remove("insist");
    pop.replaceChildren();
    if (ok) onCommit && onCommit(input.value, picked);
    else onCancel && onCancel();
  };
  popDone = finish;
  window.addEventListener("pointerdown", onDown, true);
  shield.onpointerdown = (e) => {
    e.preventDefault();
    e.stopPropagation();
    insist();
  };
  input.addEventListener("input", syncShield);
  const row = choices
    ? el(
        "div",
        { class: "pop-choices", role: "radiogroup" },
        choices.map(([v, text]) =>
          el(
            "button",
            {
              type: "button",
              class: v === picked ? "active" : "",
              role: "radio",
              "aria-checked": String(v === picked),
              on: {
                click: (e) => {
                  picked = v;
                  for (const b of e.currentTarget.parentNode.children) {
                    b.classList.toggle("active", b === e.currentTarget);
                    b.setAttribute("aria-checked", String(b === e.currentTarget));
                  }
                  syncShield();
                  input.focus();
                },
              },
            },
            text,
          ),
        ),
      )
    : null;
  pop.replaceChildren(
    ...[
      el("label", { class: "pop-label" }, label || ""),
      el("div", { class: "pop-row" }, input),
      row,
      hint ? el("div", { class: "pop-hint" }, hint) : null,
      warn,
      el(
        "div",
        { class: "pop-buttons" },
        el("button", { type: "button", title: "Close without changing anything (Esc)", on: { click: () => finish(false) } }, "Cancel"),
        el("button", { type: "button", class: "primary", title: "Save (Enter)", on: { click: () => finish(true) } }, "Save"),
      ),
    ].filter(Boolean),
  );
  pop.hidden = false;
  const w = pop.offsetWidth;
  const h = pop.offsetHeight;
  pop.style.left = `${Math.max(8, Math.min(window.innerWidth - w - 8, x - w / 2))}px`;
  pop.style.top = `${Math.max(8, Math.min(window.innerHeight - h - 8, y))}px`;
  pop.onkeydown = (e) => {
    e.stopPropagation();
    if (e.key === "Escape") {
      e.preventDefault();
      finish(false);
    } else if (e.key === "Enter" && e.target === input && !(multiline && e.shiftKey)) {
      e.preventDefault();
      finish(true);
    }
  };
  input.focus();
  if (selectAll) input.select();
  else input.setSelectionRange(input.value.length, input.value.length);
  return input;
}

export function closePop() {
  if (popDone) popDone(false);
}

export function popOpen() {
  return !!popDone;
}

let menuCleanup = null;

/**
 * A menu at (x, y). Items are {label, keys, accel, run, disabled} or "-" for a divider, or {heading}.
 * `accel` is a single key that picks the item while the menu is open.
 */
export function showMenu(items, x, y) {
  hideMenu();
  const menu = $("menu");
  const buttons = [];
  menu.replaceChildren(
    ...items.map((item) => {
      if (item === "-") return el("div", { class: "menu-sep", role: "separator" });
      if (item.heading) return el("div", { class: "menu-heading" }, item.heading);
      const b = el(
        "button",
        {
          type: "button",
          role: "menuitem",
          disabled: item.disabled,
          title: item.title || null,
          on: {
            click: () => {
              hideMenu();
              item.run();
            },
          },
        },
        el("span", { class: "menu-label" }, item.label),
        item.keys || item.accel ? el("kbd", {}, item.keys || item.accel.toUpperCase()) : null,
      );
      buttons.push(b);
      return b;
    }),
  );
  menu.hidden = false;
  const w = menu.offsetWidth;
  const h = menu.offsetHeight;
  menu.style.left = `${Math.max(4, Math.min(window.innerWidth - w - 4, x))}px`;
  menu.style.top = `${Math.max(4, Math.min(window.innerHeight - h - 4, y))}px`;
  const onDown = (e) => {
    if (!menu.contains(e.target)) hideMenu();
  };
  const onKey = (e) => {
    const i = buttons.indexOf(document.activeElement);
    const picked = !e.ctrlKey && !e.metaKey && !e.altKey && items.find((item) => item.accel && !item.disabled && item.accel.toLowerCase() === e.key.toLowerCase());
    if (picked) {
      e.preventDefault();
      e.stopImmediatePropagation();
      hideMenu();
      picked.run();
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopImmediatePropagation();
      hideMenu();
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      e.stopPropagation();
      const step = e.key === "ArrowDown" ? 1 : -1;
      const enabled = buttons.filter((b) => !b.disabled);
      const at = enabled.indexOf(buttons[i]);
      enabled[(at + step + enabled.length) % enabled.length]?.focus();
    }
  };
  setTimeout(() => window.addEventListener("pointerdown", onDown, true), 0);
  window.addEventListener("keydown", onKey, true);
  menuCleanup = () => {
    window.removeEventListener("pointerdown", onDown, true);
    window.removeEventListener("keydown", onKey, true);
  };
  buttons.find((b) => !b.disabled)?.focus();
}

export function hideMenu() {
  const menu = $("menu");
  if (menuCleanup) menuCleanup();
  menuCleanup = null;
  if (menu) menu.hidden = true;
}

/**
 * A filterable dropdown. `groups` is [{name, items: [{id, name}]}].
 * The field is editable to filter the list; the chevron (or focus) opens the full menu.
 * Choosing an item calls onPick(id).
 */
export function combobox({ label, value, groups, onPick, ariaLabel = label }) {
  const flat = groups.flatMap((g) => g.items.map((item) => ({ ...item, group: g.name })));
  const labelOf = (id) => (flat.find((item) => item.id === id) || { name: id || "" }).name;
  const input = el("input", {
    type: "text",
    value: labelOf(value),
    "aria-label": ariaLabel,
    "aria-autocomplete": "list",
    "aria-expanded": "false",
    autocomplete: "off",
    spellcheck: "false",
    placeholder: "Type to filter…",
  });
  const toggle = el("button", {
    type: "button",
    class: "combo-toggle",
    "aria-label": "Show options",
    tabIndex: -1,
    text: "▾",
  });
  const list = el("div", { class: "combo-list", hidden: true, role: "listbox" });
  const box = el("div", { class: "combo-box" }, input, toggle, list);
  let open = false;
  let filtered = flat;
  let typing = false;

  const placeList = () => {
    const rect = box.getBoundingClientRect();
    list.style.position = "fixed";
    list.style.left = `${Math.round(rect.left)}px`;
    list.style.width = `${Math.max(rect.width, 180)}px`;
    list.style.right = "auto";
    const spaceBelow = window.innerHeight - rect.bottom - 8;
    const spaceAbove = rect.top - 8;
    const preferBelow = spaceBelow >= 160 || spaceBelow >= spaceAbove;
    if (preferBelow) {
      list.style.top = `${Math.round(rect.bottom + 2)}px`;
      list.style.bottom = "auto";
      list.style.maxHeight = `${Math.min(280, Math.max(120, spaceBelow))}px`;
    } else {
      list.style.bottom = `${Math.round(window.innerHeight - rect.top + 2)}px`;
      list.style.top = "auto";
      list.style.maxHeight = `${Math.min(280, Math.max(120, spaceAbove))}px`;
    }
  };

  const close = () => {
    open = false;
    typing = false;
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.value = labelOf(value);
    list.style.position = "";
    list.style.left = "";
    list.style.top = "";
    list.style.bottom = "";
    list.style.width = "";
    list.style.right = "";
    list.style.maxHeight = "";
  };

  const paint = () => {
    list.replaceChildren();
    let lastGroup = null;
    for (const item of filtered) {
      if (item.group !== lastGroup) {
        lastGroup = item.group;
        list.append(el("div", { class: "combo-group", text: item.group }));
      }
      list.append(
        el(
          "button",
          {
            type: "button",
            class: `combo-option${item.id === value ? " on" : ""}`,
            role: "option",
            "aria-selected": item.id === value ? "true" : "false",
            on: {
              mousedown: (e) => e.preventDefault(),
              click: () => {
                value = item.id;
                onPick(item.id);
                close();
              },
            },
          },
          item.name,
        ),
      );
    }
    if (!filtered.length) list.append(el("div", { class: "combo-empty", text: "No matches" }));
    const selected = list.querySelector(".combo-option.on");
    if (selected) {
      selected.classList.add("active");
      selected.scrollIntoView({ block: "nearest" });
    } else {
      const first = list.querySelector(".combo-option");
      if (first) first.classList.add("active");
    }
  };

  const applyFilter = (q) => {
    const needle = q.trim().toLowerCase();
    filtered = needle
      ? flat.filter(
          (item) =>
            item.name.toLowerCase().includes(needle) ||
            item.id.toLowerCase().includes(needle) ||
            item.group.toLowerCase().includes(needle),
        )
      : flat;
  };

  const openList = ({ filter = false, selectText = false } = {}) => {
    open = true;
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    if (filter) applyFilter(input.value);
    else {
      typing = false;
      filtered = flat;
    }
    paint();
    placeList();
    if (selectText) requestAnimationFrame(() => input.select());
  };

  input.addEventListener("focus", () => openList({ filter: false, selectText: true }));
  input.addEventListener("input", () => {
    typing = true;
    openList({ filter: true });
  });
  input.addEventListener("blur", () => setTimeout(close, 150));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
      input.blur();
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!open) openList({ filter: typing });
      const options = [...list.querySelectorAll(".combo-option")];
      if (!options.length) return;
      const i = options.findIndex((node) => node.classList.contains("active"));
      const start = i >= 0 ? i : options.findIndex((node) => node.classList.contains("on"));
      const next =
        e.key === "ArrowDown"
          ? (start + 1) % options.length
          : (start <= 0 ? options.length : start) - 1;
      options.forEach((node) => node.classList.toggle("active", false));
      options[next].classList.add("active");
      options[next].scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") {
      e.preventDefault();
      const pick = list.querySelector(".combo-option.active") || list.querySelector(".combo-option.on");
      if (pick) {
        pick.click();
        return;
      }
      const first = filtered[0];
      if (first) {
        value = first.id;
        onPick(first.id);
        close();
        input.blur();
      }
    }
  });
  toggle.addEventListener("mousedown", (e) => e.preventDefault());
  toggle.addEventListener("click", () => {
    if (open) {
      close();
      return;
    }
    input.focus();
    openList({ filter: false, selectText: true });
  });
  window.addEventListener("resize", () => {
    if (open) placeList();
  });
  window.addEventListener(
    "scroll",
    () => {
      if (open) placeList();
    },
    true,
  );

  return el("div", { class: "field" }, el("label", {}, label), box, el("span"));
}
