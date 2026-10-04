/* Agent Reach dashboard. Vanilla JS, no build step.
   Every DOM node is built with createElement/textContent: no innerHTML. */
(() => {
  "use strict";

  // ── Token: read from the launch URL fragment, keep in sessionStorage ──
  const TOKEN_KEY = "agent-reach-ui-token";
  (function captureToken() {
    const m = /(?:^|[#&])token=([A-Za-z0-9_\-]+)/.exec(location.hash);
    if (m) {
      try { sessionStorage.setItem(TOKEN_KEY, m[1]); } catch (_) { /* storage blocked */ }
      window.__agentReachToken = m[1];
      history.replaceState(null, "", location.pathname + location.search);
    }
  })();
  function token() {
    if (window.__agentReachToken) return window.__agentReachToken;
    try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch (_) { return ""; }
  }

  // ── State ──
  const STATUS = {
    ok: { glyph: "✓", label: "Ready", group: "ready" },
    warn: { glyph: "!", label: "Warning", group: "attention" },
    error: { glyph: "×", label: "Error", group: "attention" },
    timeout: { glyph: "!", label: "Timed out", group: "attention" },
    off: { glyph: "○", label: "Not set up", group: "setup" },
    pending: { glyph: "·", label: "Checking", group: "pending" },
  };
  const GROUPS = [
    ["attention", "Needs attention"],
    ["ready", "Ready"],
    ["setup", "Not set up"],
    ["pending", "Checking"],
  ];

  const state = {
    status: null,
    channels: [],
    byName: new Map(),
    results: null,
    checkedAt: null,
    doctorRunning: false,
    doctorError: null,
    selected: null,
    filter: "",
    jobs: [],
    jobLogs: new Map(),  // id -> {offset, text, status, ...}
    activeJob: null,
    pollTimer: null,
    offline: false,
    testing: new Set(),
  };

  const $ = (id) => document.getElementById(id);

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v == null || v === false) continue;
        if (k === "class") node.className = v;
        else if (k === "text") node.textContent = v;
        else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
        else if (k === "dataset") Object.assign(node.dataset, v);
        else node.setAttribute(k, v === true ? "" : String(v));
      }
    }
    for (const c of children.flat()) {
      if (c == null || c === false) continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  }

  // ── API ──
  class ApiError extends Error {
    constructor(status, message) { super(message); this.status = status; }
  }

  async function api(path, { method = "GET", body } = {}) {
    const headers = { "X-Agent-Reach-Token": token() };
    const init = { method, headers, cache: "no-store", credentials: "same-origin" };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(path, init);
    } catch (_) {
      setOffline(true);
      throw new ApiError(0, "The dashboard server is not responding.");
    }
    setOffline(false);
    let data = null;
    try { data = await res.json(); } catch (_) { /* empty */ }
    if (!res.ok) {
      const msg = (data && data.error) || `Request failed (${res.status})`;
      if (res.status === 401) showUnauthorized();
      throw new ApiError(res.status, msg);
    }
    return data;
  }

  function setOffline(off) {
    if (state.offline === off) return;
    state.offline = off;
    const banner = $("banner");
    banner.hidden = !off;
    $("banner-text").textContent = off
      ? "Can't reach the dashboard server. Is agent-reach ui still running in your terminal?"
      : "";
  }

  // ── Toasts ──
  function toast(message, kind = "ok") {
    const glyph = kind === "ok" ? "✓" : kind === "warn" ? "!" : "×";
    const node = el("div", { class: "toast", role: kind === "error" ? "alert" : "status" },
      el("span", { class: `glyph ${kind}`, "aria-hidden": "true", text: glyph }),
      el("span", { text: message }));
    $("toasts").append(node);
    const ttl = kind === "error" ? 7000 : 4000;
    let timer = setTimeout(dismiss, ttl);
    node.addEventListener("mouseenter", () => clearTimeout(timer));
    node.addEventListener("mouseleave", () => { timer = setTimeout(dismiss, 1500); });
    function dismiss() {
      node.classList.add("leaving");
      setTimeout(() => node.remove(), 220);
    }
    const all = $("toasts").children;
    if (all.length > 4) all[0].remove();
  }

  // ── Helpers ──
  function statusOf(name) {
    if (!state.results) return "pending";
    const r = state.results[name];
    return r && STATUS[r.status] ? r.status : "pending";
  }

  function relTime(ts) {
    if (!ts) return "";
    const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
    if (s < 10) return "Checked just now";
    if (s < 60) return `Checked ${s}s ago`;
    const m = Math.round(s / 60);
    if (m < 60) return `Checked ${m} min ago`;
    const h = Math.round(m / 60);
    return `Checked ${h} h ago`;
  }

  function glyph(status, extraClass = "") {
    const s = STATUS[status] || STATUS.pending;
    return el("span", { class: `glyph ${status} ${extraClass}`.trim(), "aria-hidden": "true", text: s.glyph });
  }

  function badge(status) {
    const s = STATUS[status] || STATUS.pending;
    return el("span", { class: `badge ${status}` }, glyph(status), s.label);
  }

  async function copyText(text, button) {
    let ok = false;
    try {
      await navigator.clipboard.writeText(text);
      ok = true;
    } catch (_) {
      const ta = el("textarea", { class: "visually-hidden", "aria-hidden": "true" });
      ta.value = text;
      document.body.append(ta);
      ta.select();
      try { ok = document.execCommand("copy"); } catch (_) { ok = false; }
      ta.remove();
    }
    if (button) {
      button.dataset.copied = ok ? "true" : "false";
      button.textContent = ok ? "Copied" : "Failed";
      clearTimeout(button._t);
      button._t = setTimeout(() => { button.dataset.copied = ""; button.textContent = "Copy"; }, 1600);
    }
    if (!ok) toast("Could not copy. Select the text and copy it manually.", "error");
  }

  function codeBlock(code, label) {
    const btn = el("button", { type: "button", class: "btn btn-small copy", "aria-label": label ? `Copy: ${label}` : "Copy command", text: "Copy" });
    btn.addEventListener("click", () => copyText(code, btn));
    return el("div", { class: "codeblock" }, el("pre", null, el("code", { text: code })), btn);
  }

  // ── Safe markdown renderer ──
  // Supports headings, paragraphs, lists, fenced code, blockquotes, tables,
  // inline code, bold, italic and http(s) links. Output is DOM nodes only.
  function renderInline(text) {
    const frag = document.createDocumentFragment();
    const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\[[^\]]+\]\([^)\s]+\))|(https?:\/\/[^\s<>()]+[^\s<>().,;:!?'"])|(\*[^*\s][^*]*\*)/g;
    let last = 0;
    let m;
    while ((m = re.exec(text))) {
      if (m.index > last) frag.append(text.slice(last, m.index));
      const tok = m[0];
      if (m[1]) frag.append(el("code", { text: tok.slice(1, -1) }));
      else if (m[2]) frag.append(el("strong", null, renderInline(tok.slice(2, -2))));
      else if (m[3]) {
        const lm = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(tok);
        frag.append(link(lm[1], lm[2]));
      } else if (m[4]) frag.append(link(tok, tok));
      else if (m[5]) frag.append(el("em", null, tok.slice(1, -1)));
      last = m.index + tok.length;
    }
    if (last < text.length) frag.append(text.slice(last));
    return frag;
  }

  function link(label, href) {
    let url = null;
    try { url = new URL(href, "https://invalid.example/"); } catch (_) { url = null; }
    if (!url || !/^(https?:|mailto:)$/.test(url.protocol) || url.hostname === "invalid.example") {
      return document.createTextNode(label);  // relative / unsafe links render as text
    }
    return el("a", { href: url.href, target: "_blank", rel: "noopener noreferrer" }, renderInline(label));
  }

  function renderMarkdown(src) {
    const root = el("div", { class: "md" });
    const lines = src.replace(/\r\n/g, "\n").split("\n");
    let i = 0;
    let firstHeadingSkipped = false;
    const isBlockStart = (l) => /^(#{1,6}\s|```|>|\s*[-*]\s|\s*\d+\.\s|\|)/.test(l);
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      const fence = /^\s*```\s*([\w+-]*)\s*$/.exec(line);
      if (fence) {
        const body = [];
        i++;
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) { body.push(lines[i]); i++; }
        i++;
        root.append(codeBlock(body.join("\n")));
        continue;
      }
      const h = /^(#{1,6})\s+(.*)$/.exec(line);
      if (h) {
        if (h[1].length === 1 && !firstHeadingSkipped) { firstHeadingSkipped = true; i++; continue; }
        const level = Math.min(5, h[1].length + 1);
        root.append(el(`h${level}`, null, renderInline(h[2])));
        i++;
        continue;
      }
      if (/^>/.test(line)) {
        const body = [];
        while (i < lines.length && /^>/.test(lines[i])) { body.push(lines[i].replace(/^>\s?/, "")); i++; }
        const bq = el("blockquote");
        bq.append(...renderMarkdown(body.join("\n")).childNodes);
        root.append(bq);
        continue;
      }
      if (/^\|/.test(line)) {
        const rows = [];
        while (i < lines.length && /^\|/.test(lines[i])) { rows.push(lines[i]); i++; }
        root.append(renderTable(rows));
        continue;
      }
      const li = /^(\s*)([-*]|\d+\.)\s+(.*)$/.exec(line);
      if (li) {
        const ordered = /\d/.test(li[2]);
        const list = el(ordered ? "ol" : "ul");
        if (ordered && parseInt(li[2], 10) > 1) list.setAttribute("start", String(parseInt(li[2], 10)));
        let item = null;
        while (i < lines.length) {
          const cur = lines[i];
          if (!cur.trim()) {
            // A blank line ends the list unless the list (or the item's code) continues.
            let j = i + 1;
            while (j < lines.length && !lines[j].trim()) j++;
            const next = lines[j] || "";
            const sameKind = ordered ? /^\s*\d+\.\s/.test(next) : /^\s*[-*]\s/.test(next);
            if (item && (sameKind || /^\s*```/.test(next))) { i = j; continue; }
            break;
          }
          const m = /^(\s*)([-*]|\d+\.)\s+(.*)$/.exec(cur);
          if (m) { item = el("li", null, renderInline(m[3])); list.append(item); i++; continue; }
          if (/^\s*```/.test(cur) && item) {
            const body = [];
            i++;
            while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) { body.push(lines[i].replace(/^\s{0,4}/, "")); i++; }
            i++;
            item.append(codeBlock(body.join("\n")));
            continue;
          }
          if (cur.trim() && /^\s{2,}/.test(cur) && item) { item.append(" ", renderInline(cur.trim())); i++; continue; }
          break;
        }
        root.append(list);
        continue;
      }
      const para = [];
      while (i < lines.length && lines[i].trim() && !(para.length && isBlockStart(lines[i]))) {
        para.push(lines[i].trim());
        i++;
        if (i < lines.length && isBlockStart(lines[i])) break;
      }
      root.append(el("p", null, renderInline(para.join(" "))));
    }
    return root;
  }

  function renderTable(rows) {
    const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
    const table = el("table");
    const body = rows.filter((r) => !/^\|\s*:?-{2,}/.test(r));
    body.forEach((r, idx) => {
      const tr = el("tr");
      for (const c of cells(r)) tr.append(el(idx === 0 ? "th" : "td", null, renderInline(c)));
      table.append(tr);
    });
    return table;
  }

  // ── Header ──
  function renderHeader() {
    const total = state.channels.length;
    const countEl = $("health-count");
    const bar = $("health-bar");
    countEl.replaceChildren();
    bar.replaceChildren();
    if (!total) {
      countEl.append(el("span", { class: "skeleton skeleton-text w-6rem" }));
      return;
    }
    const ordered = orderedChannels();
    if (state.results) {
      const ready = state.channels.filter((c) => statusOf(c.name) === "ok").length;
      countEl.textContent = `${ready} of ${total} ready`;
    } else {
      countEl.textContent = state.doctorError ? "Status unavailable" : "Checking channels";
    }
    for (const c of ordered) {
      const st = statusOf(c.name);
      bar.append(el("span", { class: `seg ${st}`, title: `${c.display_name}: ${STATUS[st].label}` }));
    }
    const counts = {};
    for (const c of state.channels) { const g = STATUS[statusOf(c.name)].label; counts[g] = (counts[g] || 0) + 1; }
    bar.setAttribute("aria-label", "Channel health: " + Object.entries(counts).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(", "));
    $("health-checked").textContent = state.doctorRunning ? "Running doctor…" : relTime(state.checkedAt);

    const btn = $("run-doctor");
    btn.disabled = state.doctorRunning;
    btn.setAttribute("aria-busy", state.doctorRunning ? "true" : "false");
    btn.querySelector(".btn-label").textContent = state.doctorRunning ? "Checking" : "Run doctor";
  }

  // ── Channel list ──
  function groupOf(name) { return STATUS[statusOf(name)].group; }

  function orderedChannels() {
    const out = [];
    for (const [g] of GROUPS) for (const c of state.channels) if (groupOf(c.name) === g) out.push(c);
    return out;
  }

  function visibleChannels() {
    const q = state.filter.trim().toLowerCase();
    return orderedChannels().filter((c) => !q
      || c.display_name.toLowerCase().includes(q)
      || c.name.includes(q)
      || c.description.toLowerCase().includes(q)
      || c.backends.some((b) => b.toLowerCase().includes(q)));
  }

  function renderList() {
    const list = $("channel-list");
    list.replaceChildren();
    if (!state.channels.length) {
      for (let k = 0; k < 9; k++) {
        list.append(el("div", { class: "skeleton-row", "aria-hidden": "true" },
          el("span", { class: "skeleton" }), el("span", { class: `skeleton skeleton-text ${["w-60", "w-80", "w-40"][k % 3]}` })));
      }
      return;
    }
    const visible = visibleChannels();
    if (!visible.length) {
      list.append(el("p", { class: "empty-list", text: `No channels match “${state.filter.trim()}”.` }));
      return;
    }
    for (const [g, title] of GROUPS) {
      const items = visible.filter((c) => groupOf(c.name) === g);
      if (!items.length) continue;
      const headId = `group-${g}`;
      const group = el("div", { role: "group", "aria-labelledby": headId },
        el("div", { class: "group-head", id: headId }, el("span", { text: title }), el("span", { class: "n", text: String(items.length) })));
      for (const c of items) {
        const st = statusOf(c.name);
        const row = el("div", {
          class: "row", role: "option", id: `row-${c.name}`, tabindex: "-1",
          "aria-selected": c.name === state.selected ? "true" : "false",
          dataset: { name: c.name },
        }, glyph(st), el("span", { class: "row-name", text: c.display_name }), el("span", { class: "row-label", text: STATUS[st].label }));
        row.addEventListener("click", () => { select(c.name); openDetailOnMobile(); });
        group.append(row);
      }
      list.append(group);
    }
    if (state.selected) list.setAttribute("aria-activedescendant", `row-${state.selected}`);
  }

  function select(name, { scroll = true } = {}) {
    if (!state.byName.has(name)) return;
    state.selected = name;
    renderList();
    renderDetail();
    if (scroll) {
      const row = $(`row-${name}`);
      if (row) row.scrollIntoView({ block: "nearest" });
    }
  }

  function moveSelection(delta) {
    const visible = visibleChannels();
    if (!visible.length) return;
    let idx = visible.findIndex((c) => c.name === state.selected);
    idx = idx < 0 ? 0 : Math.max(0, Math.min(visible.length - 1, idx + delta));
    select(visible[idx].name);
  }

  const mobile = () => window.matchMedia("(max-width: 820px)").matches;
  function openDetailOnMobile() {
    if (!mobile()) return;
    document.body.dataset.view = "detail";
    window.scrollTo(0, 0);
    $("detail").focus({ preventScroll: true });
  }
  function backToList() {
    document.body.dataset.view = "list";
    const row = state.selected && $(`row-${state.selected}`);
    if (row) row.scrollIntoView({ block: "center" });
    $("channel-list").focus({ preventScroll: true });
  }

  // ── Detail ──
  function renderDetail() {
    const root = $("detail");
    root.replaceChildren();
    const inner = el("div", { class: "detail-inner" });
    root.append(inner);

    if (!state.channels.length) {
      inner.append(state.loadError ? errorState(state.loadError) : detailSkeleton());
      return;
    }
    const c = state.byName.get(state.selected);
    if (!c) {
      inner.append(el("div", { class: "empty-state" }, el("h2", { text: "Pick a channel" }),
        el("p", { text: "Choose a channel on the left to see its status, set it up and copy commands for your agent." })));
      return;
    }
    const st = statusOf(c.name);
    const result = state.results && state.results[c.name];

    inner.append(el("button", { type: "button", class: "btn btn-quiet btn-small back", onclick: backToList, text: "‹ All channels" }));

    const testing = state.testing.has(c.name);
    const testBtn = el("button", {
      type: "button", class: "btn", disabled: testing || state.doctorRunning,
      "aria-busy": testing ? "true" : "false", onclick: () => testChannel(c.name),
    }, el("span", { class: "btn-label", text: testing ? "Testing" : "Test" }));
    const actions = el("div", { class: "detail-actions" }, testBtn);
    if (c.install) {
      const running = Boolean(runningJob());
      actions.append(
        el("button", { type: "button", class: "btn", disabled: running, onclick: () => startInstall(c, true), text: "Preview" }),
        el("button", { type: "button", class: "btn btn-primary", disabled: running, onclick: () => startInstall(c, false), text: c.install.channels.length ? "Install" : "Install core tools" }),
      );
    }

    inner.append(el("div", { class: "detail-head" },
      el("div", null,
        el("div", { class: "detail-title" }, el("h1", { text: c.display_name }), badge(st)),
        el("p", { class: "detail-sub", text: c.description })),
      actions));

    if (result) {
      inner.append(el("div", { class: `message ${st}` }, result.message || STATUS[st].label));
    } else if (state.doctorRunning || !state.results) {
      inner.append(el("div", { class: "message", "aria-hidden": "true" },
        el("span", { class: "skeleton skeleton-block w-80" }), el("span", { class: "skeleton skeleton-block w-40" })));
    }

    const active = result && result.active_backend;
    const backends = el("div", { class: "backends" },
      c.backends.map((b) => el("span", { class: `backend${b === active ? " active" : ""}`, text: b })));
    inner.append(el("dl", { class: "facts" },
      el("div", { class: "fact" }, el("dt", { text: "Active backend" }),
        el("dd", null, active ? active : el("span", { class: "muted", text: result ? "None" : "Unknown until checked" }))),
      el("div", { class: "fact" }, el("dt", { text: "Setup" }), el("dd", { text: c.tier_label })),
      el("div", { class: "fact" }, el("dt", { text: c.backends.length > 1 ? "Backends, in order" : "Backend" }), el("dd", null, backends))));

    // Set up
    const setup = el("section", { class: "section", "aria-labelledby": "setup-h" },
      el("div", { class: "section-head" }, el("h2", { id: "setup-h", text: "Set up" }),
        el("p", { text: c.needs_login ? "Needs a login, key or extra tool." : "Works without configuration." })));
    if (c.config_keys.length) {
      setup.append(el("div", { class: "keys", "aria-label": "Config keys" },
        c.config_keys.map((k) => el("span", { class: `key ${k.set ? "set" : "unset"}` }, k.key,
          el("span", { class: "state", text: k.set ? (k.source === "env" ? "Set (env)" : "Set") : "Not set" })))));
    }
    const forms = el("div", { class: "forms" });
    for (const f of c.configure) forms.append(configureForm(f, c.name));
    if (c.browser_import) forms.append(browserImportForm(c));
    if (forms.childNodes.length) setup.append(forms);
    if (c.guide) setup.append(guideBlock("Setup guide", c.guide, !forms.childNodes.length));
    else if (c.reference) setup.append(guideBlock("Reference", c.reference, false));
    if (!c.config_keys.length && !forms.childNodes.length && !c.guide && !c.reference) {
      setup.append(el("p", { class: "detail-sub", text: "Nothing to configure for this channel." }));
    }
    inner.append(setup);

    // Use with your agent
    const use = el("section", { class: "section", "aria-labelledby": "use-h" },
      el("div", { class: "section-head" }, el("h2", { id: "use-h", text: "Use with your agent" }),
        el("p", { text: "From the installed agent skill. Agents run these directly." })));
    if (c.about) use.append(el("p", { class: "agent-note" }, renderInline(c.about)));
    if (c.snippets.length) {
      const list = el("div", { class: "snippets" });
      for (const s of c.snippets) {
        list.append(el("div", { class: "snippet" }, s.title ? el("div", { class: "snippet-title", text: s.title }) : null, codeBlock(s.code, s.title)));
      }
      use.append(list);
    } else {
      use.append(el("p", { class: "detail-sub", text: "No commands documented for this channel yet." }));
    }
    inner.append(use);
  }

  function detailSkeleton() {
    return el("div", { "aria-hidden": "true" },
      el("span", { class: "skeleton", "aria-hidden": "true" }),
      el("span", { class: "skeleton skeleton-block w-40" }),
      el("span", { class: "skeleton skeleton-block w-90" }),
      el("span", { class: "skeleton skeleton-block w-80" }),
      el("span", { class: "skeleton skeleton-block w-60" }));
  }

  function errorState(message) {
    return el("div", { class: "empty-state", role: "alert" },
      el("h2", { text: "Could not load channels" }),
      el("p", { text: message }),
      el("button", { type: "button", class: "btn", onclick: boot, text: "Try again" }));
  }

  function guideBlock(title, markdown, open) {
    const d = el("details", { class: "guide" }, el("summary", { text: title }));
    if (open) d.open = true;
    let rendered = false;
    const render = () => { if (!rendered && d.open) { d.append(renderMarkdown(markdown)); rendered = true; } };
    d.addEventListener("toggle", render);
    render();
    return d;
  }

  function configureForm(f, channelName) {
    const id = `cfg-${channelName}-${f.cli_key}`;
    const result = el("p", { class: "form-result", "aria-live": "polite" });
    let input;
    if (f.kind === "select") {
      input = el("select", { class: "input", id });
      input.append(el("option", { value: "", text: "Choose a browser" }));
      for (const o of f.options) input.append(el("option", { value: o, text: o }));
    } else {
      input = el("input", {
        class: "input", id, type: "password", autocomplete: "off", spellcheck: "false",
        placeholder: f.keys.some((k) => k.set) ? "Saved. Paste a new value to replace it" : "Paste value",
      });
    }
    const save = el("button", { type: "submit", class: "btn btn-primary" }, el("span", { class: "btn-label", text: "Save" }));
    const form = el("form", { class: "form", novalidate: true },
      el("label", { for: id, text: f.label }),
      el("p", { class: "help", id: `${id}-help`, text: f.help }),
      el("div", { class: "form-row" }, input, save),
      el("p", { class: "cmd" }, "Runs ", el("code", { text: `${f.command} --stdin` }), ". The value never leaves this computer."),
      result);
    input.setAttribute("aria-describedby", `${id}-help`);
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const value = input.value;
      if (!value.trim()) { result.className = "form-result bad"; result.textContent = "Enter a value first."; input.focus(); return; }
      save.disabled = true; save.setAttribute("aria-busy", "true");
      result.className = "form-result"; result.textContent = "";
      try {
        const res = await api("/api/configure", { method: "POST", body: { key: f.cli_key, value } });
        input.value = "";
        result.className = res.ok ? "form-result" : "form-result bad";
        result.textContent = res.output || (res.ok ? "Saved." : "Configure failed.");
        toast(res.ok ? `${f.label} saved` : `${f.label} was not saved`, res.ok ? "ok" : "error");
        if (res.ok) { await refreshChannels(); if (channelName !== "_settings") testChannel(channelName, { quiet: true }); }
      } catch (err) {
        result.className = "form-result bad"; result.textContent = err.message;
      } finally {
        save.disabled = false; save.setAttribute("aria-busy", "false");
      }
    });
    return form;
  }

  function browserImportForm(c) {
    const id = `import-${c.name}`;
    const select = el("select", { class: "input", id });
    for (const b of c.browser_import.browsers) select.append(el("option", { value: b, text: b }));
    const go = el("button", { type: "submit", class: "btn", disabled: Boolean(runningJob()), text: "Import" });
    const form = el("form", { class: "form", novalidate: true },
      el("label", { for: id, text: "Import cookies from a browser" }),
      el("p", { class: "help", text: `Reads ${c.display_name} cookies from one browser you pick. Only runs when you click Import.` }),
      el("div", { class: "form-row" }, select, go),
      el("p", { class: "cmd" }, "Runs ", el("code", { text: c.browser_import.command })));
    form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const browser = select.value;
      confirmRun({
        title: `Import ${c.display_name} cookies?`,
        text: `Agent Reach will read ${c.display_name} cookies from ${browser} and save them to ~/.agent-reach/config.yaml.`,
        command: `agent-reach configure --from-browser ${browser} --platform ${c.name}`,
        ok: "Import",
      }).then((yes) => { if (yes) startJob("configure_browser", { browser, platform: c.name }); });
    });
    return form;
  }

  // ── Doctor & test ──
  async function runDoctor() {
    if (state.doctorRunning) return;
    state.doctorRunning = true;
    state.doctorError = null;
    renderHeader(); renderDetail();
    try {
      const res = await api("/api/doctor", { method: "POST", body: {} });
      state.results = res.results;
      state.checkedAt = res.checked_at;
      const ready = Object.values(res.results).filter((r) => r.status === "ok").length;
      toast(`Doctor finished: ${ready} of ${Object.keys(res.results).length} ready`);
    } catch (err) {
      state.doctorError = err.message;
      toast(`Doctor failed: ${err.message}`, "error");
    } finally {
      state.doctorRunning = false;
      renderAll();
    }
  }

  async function testChannel(name, { quiet = false } = {}) {
    if (state.testing.has(name)) return;
    state.testing.add(name);
    renderDetail();
    try {
      const res = await api(`/api/channels/${encodeURIComponent(name)}/check`, { method: "POST", body: {} });
      state.results = Object.assign({}, state.results || {}, { [name]: res.result });
      const c = state.byName.get(name);
      if (!quiet) toast(`${c.display_name}: ${STATUS[res.result.status].label}`, res.result.status === "ok" ? "ok" : res.result.status === "off" ? "warn" : res.result.status === "warn" ? "warn" : "error");
    } catch (err) {
      if (!quiet) toast(`Test failed: ${err.message}`, "error");
    } finally {
      state.testing.delete(name);
      renderAll();
    }
  }

  // ── Jobs ──
  function runningJob() { return state.jobs.find((j) => j.status === "running"); }

  function confirmRun({ title, text, command, ok }) {
    const dlg = $("confirm");
    $("confirm-title").textContent = title;
    $("confirm-text").textContent = text;
    $("confirm-command").textContent = command;
    $("confirm-ok").textContent = ok || "Run";
    return new Promise((resolve) => {
      const done = (v) => { cleanup(); dlg.close(); resolve(v); };
      const yes = () => done(true);
      const no = () => done(false);
      const cancel = (e) => { e.preventDefault(); done(false); };
      function cleanup() {
        $("confirm-ok").removeEventListener("click", yes);
        $("confirm-cancel").removeEventListener("click", no);
        dlg.removeEventListener("cancel", cancel);
      }
      $("confirm-ok").addEventListener("click", yes);
      $("confirm-cancel").addEventListener("click", no);
      dlg.addEventListener("cancel", cancel);
      dlg.showModal();
      $("confirm-cancel").focus();
    });
  }

  async function startInstall(c, dryRun) {
    const command = c.install.command.replace("--system", dryRun ? "--dry-run" : "--system");
    if (!dryRun) {
      const yes = await confirmRun({
        title: c.install.channels.length ? `Install ${c.display_name}?` : "Install core tools?",
        text: "This runs the Agent Reach installer on this computer. It may install tools with your package managers and registers the agent skill.",
        command,
        ok: "Install",
      });
      if (!yes) return;
    }
    startJob("install", { channels: c.install.channels, dry_run: dryRun });
  }

  async function startJob(action, params) {
    try {
      const job = await api("/api/jobs", { method: "POST", body: { action, params } });
      upsertJob(job);
      state.jobLogs.set(job.id, { offset: 0, text: "" });
      state.activeJob = job.id;
      openJobs();
      pollJob(job.id);
      renderAll();
    } catch (err) {
      toast(err.status === 409 ? "Another job is still running. Wait for it to finish." : err.message, "error");
    }
  }

  function upsertJob(job) {
    const idx = state.jobs.findIndex((j) => j.id === job.id);
    const meta = Object.assign({}, job); delete meta.output;
    if (idx >= 0) state.jobs[idx] = Object.assign(state.jobs[idx], meta); else state.jobs.push(meta);
  }

  async function pollJob(id) {
    clearTimeout(state.pollTimer);
    const log = state.jobLogs.get(id) || { offset: 0, text: "" };
    state.jobLogs.set(id, log);
    try {
      const job = await api(`/api/jobs/${encodeURIComponent(id)}?offset=${log.offset}`);
      log.text += job.output;
      log.offset = job.offset;
      const before = (state.jobs.find((j) => j.id === id) || {}).status;
      upsertJob(job);
      renderJobs({ appendOnly: before === job.status });
      if (job.status === "running") {
        state.pollTimer = setTimeout(() => pollJob(id), 600);
      } else {
        renderAll();
        const kind = job.status === "succeeded" ? "ok" : "error";
        toast(`${job.label}: ${jobStatusLabel(job.status).toLowerCase()}`, kind);
        if (job.action !== "check_update" && job.status === "succeeded") {
          refreshChannels();
          if ((job.action === "install" && !job.command.includes("--dry-run")) || job.action === "configure_browser") runDoctor();
        }
      }
    } catch (_) {
      state.pollTimer = setTimeout(() => pollJob(id), 2000);
    }
  }

  function jobStatusLabel(s) {
    return { running: "Running", succeeded: "Succeeded", failed: "Failed", timeout: "Timed out", cancelled: "Cancelled" }[s] || s;
  }
  function jobGlyph(s) {
    return { running: "pending", succeeded: "ok", failed: "error", timeout: "timeout", cancelled: "off" }[s] || "pending";
  }

  function renderJobs({ appendOnly = false } = {}) {
    const badgeEl = $("jobs-badge");
    const running = runningJob();
    badgeEl.hidden = !running;
    badgeEl.textContent = running ? "1" : "0";
    const body = $("jobs-body");
    const tabs = $("jobs-tabs");
    const job = state.jobs.find((j) => j.id === state.activeJob) || state.jobs[state.jobs.length - 1];
    const log = job && state.jobLogs.get(job.id);

    if (appendOnly && job && body.dataset.job === job.id) {
      const pre = body.querySelector(".job-log");
      if (pre && log) {
        const stick = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 24;
        pre.textContent = log.text || "Waiting for output…";
        if (stick) pre.scrollTop = pre.scrollHeight;
      }
      return;
    }

    tabs.replaceChildren();
    for (const j of state.jobs.slice().reverse()) {
      const st = jobGlyph(j.status);
      const t = el("button", { type: "button", class: "jobs-tab", role: "tab", "aria-selected": job && j.id === job.id ? "true" : "false" },
        glyph(st), j.label);
      t.addEventListener("click", () => { state.activeJob = j.id; if (!state.jobLogs.has(j.id) || j.status !== "running") pollJob(j.id); renderJobs(); });
      tabs.append(t);
    }
    body.replaceChildren();
    if (!job) {
      body.dataset.job = "";
      body.append(el("p", { class: "jobs-empty", text: "No jobs yet. Install, import cookies or check for updates to see live output here." }));
      return;
    }
    body.dataset.job = job.id;
    const st = jobGlyph(job.status);
    body.append(el("div", { class: "job-meta" },
      el("span", { class: `badge ${st}` }, glyph(st), jobStatusLabel(job.status)),
      el("code", { text: job.command }),
      el("span", { class: "spacer" }),
      job.exit_code != null ? el("span", { text: `Exit code ${job.exit_code}` }) : null));
    const pre = el("pre", { class: "job-log", tabindex: "0", "aria-label": "Job output", "aria-live": "off" });
    pre.textContent = (log && log.text) || (job.status === "running" ? "Waiting for output…" : "No output.");
    body.append(pre);
    requestAnimationFrame(() => { pre.scrollTop = pre.scrollHeight; });
  }

  function openJobs() {
    $("jobs").hidden = false;
    $("open-jobs").setAttribute("aria-expanded", "true");
    document.body.classList.add("jobs-open");
    renderJobs();
  }
  function closeJobs() {
    $("jobs").hidden = true;
    $("open-jobs").setAttribute("aria-expanded", "false");
    document.body.classList.remove("jobs-open");
  }

  // ── Settings ──
  function renderSettings() {
    const body = $("settings-body");
    body.replaceChildren();
    const s = state.status;
    if (!s) { body.append(detailSkeleton()); return; }
    const running = Boolean(runningJob());
    body.append(
      el("section", { class: "section" },
        el("div", { class: "section-head" }, el("h2", { text: "Agent skill" }), el("p", { text: "SKILL.md teaches your agents these commands." })),
        el("div", { class: "actions-row" },
          el("button", { type: "button", class: "btn btn-primary", disabled: running, onclick: () => { $("settings").close(); startJob("skill_install", {}); }, text: "Install skill" }),
          el("button", { type: "button", class: "btn btn-danger", disabled: running, onclick: async () => {
            $("settings").close();
            if (await confirmRun({ title: "Remove the agent skill?", text: "Agents will stop seeing Agent Reach instructions until you install it again.", command: "agent-reach skill --uninstall", ok: "Remove" })) startJob("skill_uninstall", {});
          }, text: "Remove skill" }),
          el("button", { type: "button", class: "btn", disabled: running, onclick: () => { $("settings").close(); startJob("check_update", {}); }, text: "Check for updates" }))),
      el("section", { class: "section" },
        el("div", { class: "section-head" }, el("h2", { text: "Keys and proxy" })),
        el("div", { class: "keys" }, s.settings.flatMap((f) => f.keys).map((k) =>
          el("span", { class: `key ${k.set ? "set" : "unset"}` }, k.key, el("span", { class: "state", text: k.set ? "Set" : "Not set" })))),
        el("div", { class: "forms" }, s.settings.map((f) => configureForm(f, "_settings")))),
      el("section", { class: "section" },
        el("div", { class: "section-head" }, el("h2", { text: "Environment" })),
        el("dl", { class: "env-list" },
          el("dt", { text: "Version" }), el("dd", { text: s.version }),
          el("dt", { text: "System" }), el("dd", { text: `${s.os} ${s.os_release}` }),
          el("dt", { text: "Python" }), el("dd", { text: s.python }),
          el("dt", { text: "Detected as" }), el("dd", { text: s.environment === "server" ? "Server" : s.environment === "local" ? "Local computer" : "Unknown" }),
          el("dt", { text: "Config file" }), el("dd", { text: s.config_exists ? "~/.agent-reach/config.yaml" : "Not created yet" }))));
  }

  // ── Unauthorized ──
  function showUnauthorized() {
    if (state.unauthorized) return;
    state.unauthorized = true;
    const root = $("detail");
    root.replaceChildren(el("div", { class: "detail-inner" }, el("div", { class: "empty-state", role: "alert" },
      el("h2", { text: "Open the link from your terminal" }),
      el("p", { text: "This dashboard needs the one-time link that agent-reach ui printed when it started. Each launch uses a new link." }),
      el("pre", { class: "code-inline-block", text: "agent-reach ui" }))));
    $("channel-list").replaceChildren();
    $("health-count").textContent = "Not connected";
    $("health-bar").replaceChildren();
    document.body.dataset.view = "detail";
  }

  // ── Boot ──
  function renderAll() { renderHeader(); renderList(); renderDetail(); renderJobs(); }

  async function refreshChannels() {
    const res = await api("/api/channels");
    state.channels = res.channels;
    state.byName = new Map(res.channels.map((c) => [c.name, c]));
    try { state.status = await api("/api/status"); } catch (_) { /* keep old */ }
    renderAll();
    if ($("settings").open) renderSettings();
  }

  async function boot() {
    state.loadError = null;
    if (!token()) { showUnauthorized(); return; }
    renderAll();
    try {
      const [status, channels, doctor, jobs] = await Promise.all([
        api("/api/status"), api("/api/channels"), api("/api/doctor"), api("/api/jobs"),
      ]);
      state.status = status;
      $("version").textContent = `v${status.version}`;
      state.channels = channels.channels;
      state.byName = new Map(channels.channels.map((c) => [c.name, c]));
      state.jobs = jobs.jobs;
      if (doctor.results) { state.results = doctor.results; state.checkedAt = doctor.checked_at; }
      if (!state.selected) {
        state.selected = mobile() ? null : (orderedChannels()[0] || {}).name;
      }
      renderAll();
      const running = runningJob();
      if (running) { state.activeJob = running.id; pollJob(running.id); }
      if (!state.results) runDoctor();
    } catch (err) {
      if (err.status === 401) return;
      state.loadError = err.message;
      renderAll();
    }
  }

  function isTyping(target) {
    return target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName));
  }

  document.addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const dialogOpen = $("settings").open || $("confirm").open;
    if (e.key === "Escape") {
      if (document.activeElement === $("filter") && $("filter").value) { $("filter").value = ""; state.filter = ""; renderList(); return; }
      if (!dialogOpen && !$("jobs").hidden) { closeJobs(); return; }
      if (!dialogOpen && mobile() && document.body.dataset.view === "detail") { backToList(); return; }
    }
    if (dialogOpen) return;
    if (document.activeElement === $("filter") && (e.key === "ArrowDown" || e.key === "Enter")) {
      e.preventDefault();
      moveSelection(0);
      $("channel-list").focus();
      return;
    }
    if (isTyping(e.target)) return;
    if (e.key === "/") { e.preventDefault(); $("filter").focus(); $("filter").select(); return; }
    if (e.key === "r") { e.preventDefault(); runDoctor(); return; }
    const inList = e.target === $("channel-list") || e.target === document.body || (e.target.closest && e.target.closest(".sidebar"));
    if (!inList) return;
    if (e.key === "ArrowDown" || e.key === "j") { e.preventDefault(); moveSelection(1); $("channel-list").focus(); }
    else if (e.key === "ArrowUp" || e.key === "k") { e.preventDefault(); moveSelection(-1); $("channel-list").focus(); }
    else if (e.key === "Enter" && state.selected) {
      e.preventDefault();
      if (mobile()) openDetailOnMobile(); else $("detail").focus();
    }
  });

  $("filter").addEventListener("input", (e) => { state.filter = e.target.value; renderList(); });
  $("channel-list").addEventListener("focus", () => { if (!state.selected) moveSelection(0); });
  $("run-doctor").addEventListener("click", runDoctor);
  $("open-jobs").addEventListener("click", () => ($("jobs").hidden ? openJobs() : closeJobs()));
  $("close-jobs").addEventListener("click", closeJobs);
  $("open-settings").addEventListener("click", () => { renderSettings(); $("settings").showModal(); });
  $("close-settings").addEventListener("click", () => $("settings").close());
  $("settings").addEventListener("click", (e) => { if (e.target === $("settings")) $("settings").close(); });
  $("banner-retry").addEventListener("click", () => { api("/api/status").then(() => { if (!state.channels.length) boot(); }).catch(() => {}); });

  setInterval(() => { if (!state.doctorRunning) $("health-checked").textContent = relTime(state.checkedAt); }, 30000);
  setInterval(() => { if (state.offline) api("/api/status").catch(() => {}); }, 5000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) api("/api/status").catch(() => {}); });

  boot();
})();
