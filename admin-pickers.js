/* Tappable category and hashtag pickers for /admin.
 *
 * A picker shows every existing choice as a large chip. Tapping toggles it;
 * chips already on the video start selected; typing filters the chips and
 * Enter (or "Add") creates a new one. The picker keeps a plain input in sync
 * (comma-separated) so the rest of the page reads values the same way as before.
 */
(() => {
  "use strict";
  const norm = value => String(value ?? "").trim().toLowerCase();

  function cleanValue(kind, raw) {
    let v = String(raw ?? "").trim();
    if (kind === "tag") v = v.replace(/^#+/, "").replace(/[\s,]+/g, "");
    else v = v.replace(/,/g, " ").replace(/\s+/g, " ");
    return v.slice(0, 100);
  }

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  let counter = 0;

  /**
   * options: {
   *   kind: "category" | "tag",
   *   label: visible label,
   *   input: the input to keep in sync (optional),
   *   choices: () => [{value, count}] existing choices, most used first,
   *   limit: how many unselected suggestions to show before "Show all" (optional),
   *   onChange: (values) => void
   * }
   */
  function ChipPicker(options) {
    const kind = options.kind === "tag" ? "tag" : "category";
    const id = "chips-" + (++counter);
    let selected = [];
    let showAll = false;

    const root = el("div", "chip-picker");
    root.dataset.kind = kind;
    const head = el("div", "chip-head");
    const label = el("span", "chip-label", options.label || (kind === "tag" ? "Hashtags" : "Categories"));
    label.id = id + "-label";
    const count = el("span", "chip-count");
    head.append(label, count);
    const list = el("div", "chip-list");
    list.setAttribute("role", "group");
    list.setAttribute("aria-labelledby", label.id);
    const addRow = el("div", "chip-add");
    const field = el("input");
    field.type = "text";
    field.id = id + "-new";
    field.autocomplete = "off";
    field.setAttribute("autocapitalize", kind === "tag" ? "none" : "sentences");
    field.spellcheck = false;
    field.placeholder = kind === "tag" ? "Find or type a new hashtag" : "Find or type a new category";
    field.setAttribute("aria-label", field.placeholder);
    field.enterKeyHint = "done";
    const addButton = el("button", "chip-add-button", "Add");
    addButton.type = "button";
    addRow.append(field, addButton);
    const more = el("button", "chip-more");
    more.type = "button";
    more.hidden = true;
    const hint = el("p", "help chip-hint");
    root.append(head, list, addRow, more, hint);

    function choices() {
      const seen = new Map();
      for (const c of (options.choices ? options.choices() : [])) {
        const v = cleanValue(kind, c.value);
        if (v && !seen.has(norm(v))) seen.set(norm(v), { value: v, count: c.count || 0 });
      }
      return [...seen.values()];
    }

    function sync(notify) {
      const text = kind === "tag" ? selected.map(t => "#" + t).join(" ") : selected.join(", ");
      if (options.input) {
        options.input.value = text;
        if (notify) options.input.dispatchEvent(new Event("input", { bubbles: true }));
      }
      count.textContent = selected.length ? selected.length + " selected" : "None selected";
      if (notify && options.onChange) options.onChange(selected.slice());
    }

    function toggle(value) {
      const i = selected.findIndex(s => norm(s) === norm(value));
      if (i >= 0) selected.splice(i, 1); else selected.push(value);
      sync(true);
      render();
    }

    function addTyped() {
      const v = cleanValue(kind, field.value);
      if (!v) { hint.textContent = kind === "tag" ? "Type a hashtag first." : "Type a category name first."; return; }
      const existing = choices().find(c => norm(c.value) === norm(v));
      const value = existing ? existing.value : v;
      if (!selected.some(s => norm(s) === norm(value))) selected.push(value);
      field.value = "";
      hint.textContent = existing ? `“${value}” selected.` : `New ${kind === "tag" ? "hashtag" : "category"} “${value}” added.`;
      sync(true);
      render();
    }

    function chip(value, on, isNew) {
      const b = el("button", "chip cat-chip" + (isNew ? " chip-new" : ""));
      b.type = "button";
      b.setAttribute("aria-pressed", on ? "true" : "false");
      b.textContent = (kind === "tag" ? "#" : "") + value;
      b.addEventListener("click", () => toggle(value));
      return b;
    }

    function render() {
      const query = norm(cleanValue(kind, field.value));
      const all = choices();
      const known = new Set(all.map(c => norm(c.value)));
      const nodes = [];
      // Selected first (including brand-new ones), then the rest.
      for (const s of selected) {
        if (query && !norm(s).includes(query)) continue;
        nodes.push(chip(s, true, !known.has(norm(s))));
      }
      let rest = all.filter(c => !selected.some(s => norm(s) === norm(c.value)) && (!query || norm(c.value).includes(query)));
      const limit = options.limit || 0;
      const hidden = !query && !showAll && limit && rest.length > limit ? rest.length - limit : 0;
      if (hidden) rest = rest.slice(0, limit);
      for (const c of rest) nodes.push(chip(c.value, false, false));
      if (!nodes.length) {
        nodes.push(el("p", "help chip-empty", query
          ? `No match. Tap Add to create “${cleanValue(kind, field.value)}”.`
          : kind === "tag" ? "No hashtags yet. Type one and tap Add." : "No categories yet. Type one and tap Add."));
      }
      list.replaceChildren(...nodes);
      more.hidden = !hidden && !showAll;
      more.textContent = hidden ? `Show all (${hidden} more)` : "Show fewer";
      if (!limit) more.hidden = true;
      addButton.textContent = query && !known.has(query) && !selected.some(s => norm(s) === query) ? "Add new" : "Add";
    }

    field.addEventListener("input", () => { hint.textContent = ""; render(); });
    field.addEventListener("keydown", e => {
      if (e.key === "Enter") { e.preventDefault(); addTyped(); }
    });
    addButton.addEventListener("click", addTyped);
    more.addEventListener("click", () => { showAll = !showAll; render(); });

    const api = {
      el: root,
      get: () => selected.slice(),
      set(values) {
        selected = [];
        for (const raw of values || []) {
          const v = cleanValue(kind, raw);
          if (v && !selected.some(s => norm(s) === norm(v))) selected.push(v);
        }
        field.value = ""; hint.textContent = ""; showAll = false;
        sync(false);
        render();
      },
      refresh: render,
      disable(on) {
        root.classList.toggle("is-disabled", !!on);
        root.querySelectorAll("button,input").forEach(n => { n.disabled = !!on; });
      }
    };
    api.set(options.initial || []);
    return api;
  }

  window.WAWChipPicker = ChipPicker;
})();
