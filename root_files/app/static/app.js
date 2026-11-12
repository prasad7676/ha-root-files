const state = {
  places: [],
  log: [],
  placeId: localStorage.getItem("rootfiles-place") || "config",
  path: "",
  entries: [],
  truncated: false,
  writable: true,
  usage: null,
  display: "",
  selected: new Set(),
  anchor: null,
  filter: "",
  deep: false,
  searchHits: null,
  view: localStorage.getItem("rootfiles-view") || "list",
  showHidden: localStorage.getItem("rootfiles-hidden") !== "0",
  sort: "name",
  sortDir: 1,
  preview: null,
  clip: null,
  busy: false,
  blocked: "",
  scanning: false,
};

let listToken = 0;
let previewToken = 0;
let toastTimer = 0;
let menuGuard = false;

const $ = (id) => document.getElementById(id);

const clickHandlers = new WeakMap();

function bindClick(node, fn) {
  clickHandlers.set(node, fn);
  node.addEventListener("click", (event) => {
    if (event.__filesHandled) return;
    event.__filesHandled = true;
    fn(event);
  });
}

window.addEventListener("click", (event) => {
  let el = event.target instanceof Element ? event.target : event.target && event.target.parentElement;
  while (el) {
    const fn = clickHandlers.get(el);
    if (fn) {
      if (!event.__filesHandled) {
        event.__filesHandled = true;
        fn(event);
      }
      return;
    }
    el = el.parentElement;
  }
}, true);

function h(tag, attrs, kids) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "onclick" && typeof value === "function") bindClick(node, value);
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const kid of kids || []) {
    if (kid == null || kid === false) continue;
    node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

function rootUrl() {
  const path = window.location.pathname;
  return path.endsWith("/") ? path : path.slice(0, path.lastIndexOf("/") + 1);
}

function api(path) {
  return rootUrl() + String(path).replace(/^\//, "");
}

async function apiJson(method, path, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(api(path), options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "That request failed.");
  return data;
}

function toast(message) {
  const el = $("toast");
  el.hidden = false;
  el.textContent = message;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.hidden = true;
  }, 2800);
}

function joinPath(dir, name) {
  return dir ? `${dir}/${name}` : name;
}

function parentPath(path) {
  const index = path.lastIndexOf("/");
  return index === -1 ? "" : path.slice(0, index);
}

function entryKey(entry) {
  return entry.path || joinPath(state.path, entry.name);
}

function currentPlace() {
  return state.places.find((place) => place.id === state.placeId) || null;
}

function formatBytes(size) {
  if (size == null) return "—";
  let value = size;
  const units = ["B", "KB", "MB", "GB", "TB"];
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  const digits = index === 0 || value >= 10 ? 0 : 1;
  return `${value.toFixed(digits)} ${units[index]}`;
}

function formatTime(seconds) {
  if (!seconds) return "—";
  return new Date(seconds * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function ext(name) {
  const index = name.lastIndexOf(".");
  return index === -1 ? "" : name.slice(index + 1).toLowerCase();
}

function kindOf(name) {
  const type = ext(name);
  if (["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(type)) return "image";
  if (["mp4", "webm", "mov"].includes(type)) return "video";
  if (["mp3", "wav", "ogg"].includes(type)) return "audio";
  if (type === "pdf") return "pdf";
  if (["zip", "tar", "tgz", "gz", "bz2", "tbz2", "xz"].includes(type)) return "archive";
  return "file";
}

function icon(type) {
  const folder = type === "dir";
  const path = folder
    ? "M2 5.5A2.5 2.5 0 0 1 4.5 3H9l1.6 1.8H17.5A2.5 2.5 0 0 1 20 7.3V16a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 2 16V5.5Z"
    : "M5 2.5h6.2L17 8.2V19a1.5 1.5 0 0 1-1.5 1.5h-10A1.5 1.5 0 0 1 4 19V4A1.5 1.5 0 0 1 5.5 2.5H5Zm6 1.2V8h4.4";
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", folder ? "ico" : "ico file");
  svg.setAttribute("viewBox", "0 0 22 22");
  svg.setAttribute("aria-hidden", "true");
  const shape = document.createElementNS("http://www.w3.org/2000/svg", "path");
  shape.setAttribute("fill", "currentColor");
  shape.setAttribute("d", path);
  svg.append(shape);
  return svg;
}

function visibleEntries() {
  let rows = state.searchHits || state.entries;
  if (!state.showHidden) rows = rows.filter((entry) => !entry.hidden);
  if (!state.searchHits && state.filter.trim()) {
    const query = state.filter.trim().toLowerCase();
    rows = rows.filter((entry) => entry.name.toLowerCase().includes(query));
  }
  const dir = state.sortDir;
  return rows.slice().sort((a, b) => {
    if (a.type === "dir" && b.type !== "dir") return -1;
    if (b.type === "dir" && a.type !== "dir") return 1;
    let cmp = 0;
    if (state.sort === "size") cmp = (a.size || 0) - (b.size || 0);
    else if (state.sort === "mtime") cmp = (a.mtime || 0) - (b.mtime || 0);
    else if (state.sort === "mode") cmp = (a.mode || "").localeCompare(b.mode || "");
    else cmp = a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
    return cmp * dir;
  });
}

function selectedEntries() {
  const rows = visibleEntries();
  const picked = rows.filter((entry) => state.selected.has(entryKey(entry)));
  if (picked.length) return picked;
  if (state.preview) {
    const match = rows.find((entry) => entryKey(entry) === state.preview.path);
    if (match) return [match];
  }
  return [];
}

function ask({ title, body, value, ok, danger }) {
  return new Promise((resolve) => {
    const root = $("modal");
    const input = value == null ? null : h("input", { type: "text", value });
    const finish = (result) => {
      root.hidden = true;
      root.replaceChildren();
      resolve(result);
    };
    const dialog = h("div", { class: "dialog", role: "dialog" }, [
      h("h2", {}, [title]),
      body ? h("p", {}, [body]) : null,
      input,
      h("div", { class: "actions" }, [
        h("button", { type: "button", onclick: () => finish(null) }, ["Cancel"]),
        h("button", {
          type: "button",
          class: danger ? "danger" : "primary",
          onclick: () => {
            if (input && !input.value.trim()) {
              input.focus();
              return;
            }
            finish(input ? input.value.trim() : true);
          },
        }, [ok || "OK"]),
      ]),
    ]);
    root.replaceChildren(dialog);
    root.hidden = false;
    if (input) {
      input.focus();
      input.select();
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") dialog.querySelector(".primary, .danger").click();
        if (event.key === "Escape") finish(null);
      });
    }
  });
}

async function confirmDiscard() {
  if (!state.preview?.dirty) return true;
  const ok = await ask({
    title: "Discard edits?",
    body: `${state.preview.name} has unsaved edits.`,
    ok: "Discard",
    danger: true,
  });
  return !!ok;
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("rootfiles-theme", theme);
  $("theme").textContent = theme === "dark" ? "Light" : "Dark";
}

function shortHost(place) {
  if (!place || place.available) return "Host system is visible.";
  if ((place.reason || "").includes("Protection mode")) return "Turn Protection mode off, then restart.";
  return place.reason || "Host system is hidden.";
}

function renderPlaces() {
  const nav = $("places");
  const groups = [];
  for (const place of state.places) {
    let group = groups.find((item) => item.name === place.group);
    if (!group) {
      group = { name: place.group, items: [] };
      groups.push(group);
    }
    group.items.push(place);
  }
  if (!groups.some((group) => group.name === "USB")) {
    const insertAt = groups.findIndex((group) => group.name === "System");
    groups.splice(insertAt === -1 ? groups.length : insertAt, 0, { name: "USB", items: [] });
  }
  nav.replaceChildren();
  for (const group of groups) {
    const head = h("div", { class: "group-head" }, [h("span", {}, [group.name])]);
    if (group.name === "USB") {
      head.append(h("button", { type: "button", class: "texty", onclick: () => rescan() }, [
        state.scanning ? "Looking…" : "Rescan",
      ]));
    }
    const box = h("div", { class: "group" }, [head]);
    if (!group.items.length) {
      box.append(h("p", { class: "hint" }, [
        "Plug in a FAT, exFAT, NTFS, or ext4 drive, then Rescan. Disks added in Settings → System → Storage show up here too. Mac APFS drives do not.",
      ]));
    }
    for (const place of group.items) {
      const row = h("div", { class: "place-row" });
      row.append(h("button", {
        type: "button",
        class: `place${place.id === state.placeId ? " active" : ""}${place.available ? "" : " off"}`,
        onclick: () => selectPlace(place),
      }, [
        h("span", { class: "place-name" }, [place.name]),
        place.detail ? h("span", { class: "place-detail" }, [place.detail]) : null,
      ]));
      if (place.ejectable) {
        row.append(h("button", {
          type: "button",
          class: "eject",
          title: "Eject",
          onclick: () => eject(place),
        }, ["Eject"]));
      }
      box.append(row);
    }
    nav.append(box);
  }
  const host = state.places.find((place) => place.kind === "host");
  $("host-note").textContent = shortHost(host);
  const active = nav.querySelector(".place.active");
  if (active && nav.scrollWidth > nav.clientWidth) {
    const box = active.getBoundingClientRect();
    const frame = nav.getBoundingClientRect();
    if (box.right > frame.right || box.left < frame.left) {
      nav.scrollLeft += box.left - frame.left - 8;
    }
  }
}

function renderCrumbs() {
  const place = currentPlace();
  const box = $("crumbs");
  const parts = state.path ? state.path.split("/") : [];
  const nodes = [
    h("button", {
      type: "button",
      class: `crumb${parts.length ? "" : " current"}`,
      onclick: () => goTo(""),
    }, [place ? place.name : "Files"]),
  ];
  parts.forEach((part, index) => {
    const path = parts.slice(0, index + 1).join("/");
    nodes.push(h("span", { class: "sep" }, ["/"]));
    nodes.push(h("button", {
      type: "button",
      class: `crumb${index === parts.length - 1 ? " current" : ""}`,
      onclick: () => goTo(path),
    }, [part]));
  });
  box.replaceChildren(...nodes);
}

function tool(label, action, options = {}) {
  return h("button", {
    type: "button",
    class: `tool${options.primary ? " primary" : ""}${options.danger ? " danger" : ""}`,
    disabled: options.disabled || false,
    onclick: action,
  }, [label]);
}

function renderToolbar() {
  const picked = selectedEntries();
  const one = picked.length === 1 ? picked[0] : null;
  const bar = $("toolbar");
  const buttons = [
    tool("Upload", () => $("file-input").click(), { primary: true, disabled: !state.writable || state.blocked }),
    tool("New folder", () => createFolder(), { disabled: !state.writable || state.blocked }),
    tool("New file", () => createFile(), { disabled: !state.writable || state.blocked }),
    tool(state.showHidden ? "Hide dotfiles" : "Show dotfiles", () => {
      state.showHidden = !state.showHidden;
      localStorage.setItem("rootfiles-hidden", state.showHidden ? "1" : "0");
      renderToolbar();
      renderList();
      renderStatus();
    }),
    tool(state.view === "list" ? "Icons" : "List", () => {
      state.view = state.view === "list" ? "grid" : "list";
      localStorage.setItem("rootfiles-view", state.view);
      renderToolbar();
      renderList();
    }),
  ];
  if (picked.length) {
    buttons.push(tool("Download", () => downloadSelected()));
    buttons.push(tool("Copy", () => hold("copy")));
    buttons.push(tool("Cut", () => hold("cut"), { disabled: !state.writable }));
    if (one) buttons.push(tool("Rename", () => renameSelected(one), { disabled: !state.writable }));
    if (one && kindOf(one.name) === "archive") {
      buttons.push(tool("Unpack", () => unpack(one), { disabled: !state.writable }));
    }
    buttons.push(tool("Copy path", () => copyText(state.display && one ? pathLabel(one) : state.display)));
    buttons.push(tool("Delete", () => removeSelected(), { danger: true, disabled: !state.writable }));
  } else if (state.display) {
    buttons.push(tool("Copy path", () => copyText(state.display)));
  }
  bar.replaceChildren(...buttons);
}

function pathLabel(entry) {
  const place = currentPlace();
  const path = entryKey(entry);
  if (!place) return path;
  if (place.kind === "host") return path ? `/${path}` : "/";
  if (!state.path) return path ? `${state.display}/${path}` : state.display;
  const root = state.display.slice(0, -state.path.length).replace(/\/$/, "");
  return path ? `${root}/${path}` : root;
}

function renderClip() {
  const bar = $("clipbar");
  if (!state.clip) {
    bar.hidden = true;
    bar.replaceChildren();
    return;
  }
  const verb = state.clip.op === "cut" ? "Move" : "Copy";
  bar.hidden = false;
  bar.replaceChildren(
    `${verb} ${state.clip.label}`,
    h("button", { type: "button", onclick: () => paste() }, ["Paste here"]),
    h("button", { type: "button", onclick: () => { state.clip = null; renderClip(); } }, ["Clear"]),
  );
}

function renderBanner() {
  const banner = $("banner");
  const place = currentPlace();
  if (place && place.kind === "host" && place.available && !state.blocked) {
    banner.hidden = false;
    banner.textContent = "This is the operating system. Deleting a system folder here can stop Home Assistant from booting.";
  } else {
    banner.hidden = true;
  }
}

function sortHeader(label, key, className) {
  const mark = state.sort === key ? (state.sortDir > 0 ? " ↑" : " ↓") : "";
  return h("button", { type: "button", class: className || "", onclick: () => {
    if (state.sort === key) state.sortDir *= -1;
    else {
      state.sort = key;
      state.sortDir = 1;
    }
    renderList();
  } }, [`${label}${mark}`]);
}

function renderList() {
  const list = $("list");
  const place = currentPlace();
  if (state.blocked) {
    const details = state.log.length
      ? h("details", {}, [h("summary", {}, ["Mount log"]), h("pre", {}, [state.log.join("\n")])])
      : null;
    list.replaceChildren(h("div", { class: "empty" }, [
      h("h2", {}, [place ? place.name : "Unavailable"]),
      h("p", {}, [state.blocked]),
      details,
    ]));
    return;
  }
  const rows = visibleEntries();
  if (!rows.length) {
    const title = state.searchHits ? "No matches" : "Empty folder";
    const body = state.searchHits
      ? "Nothing in this folder matches."
      : "Drop files here, or use Upload.";
    list.replaceChildren(h("div", { class: "empty" }, [h("h2", {}, [title]), h("p", {}, [body])]));
    return;
  }
  if (state.view === "grid") {
    const grid = h("div", { class: "grid" });
    for (const entry of rows) grid.append(card(entry));
    list.replaceChildren(grid);
    return;
  }
  const head = h("div", { class: "cols" }, [
    h("span"),
    h("span"),
    sortHeader("Name", "name"),
    sortHeader("Size", "size", "col-size"),
    sortHeader("Modified", "mtime", "col-time"),
    sortHeader("Mode", "mode", "col-mode"),
  ]);
  const body = h("div");
  for (const entry of rows) body.append(row(entry));
  list.replaceChildren(head, body);
  if (state.truncated) {
    list.append(h("p", { class: "hint" }, ["This folder is showing the first 5,000 names."]));
  }
}

function row(entry) {
  const key = entryKey(entry);
  const selected = state.selected.has(key);
  const cut = state.clip && state.clip.op === "cut" && state.clip.placeId === state.placeId && state.clip.paths.includes(key);
  const line = h("div", {
    class: `row${selected ? " selected" : ""}${cut ? " cut" : ""}`,
    oncontextmenu: (event) => openMenu(event, entry),
  }, [
    h("button", {
      type: "button",
      class: "tick",
      "aria-label": `Select ${entry.name}`,
      onclick: (event) => {
        event.stopPropagation();
        toggleSelect(entry, event.shiftKey);
      },
    }, [selected ? "✓" : ""]),
    icon(entry.type),
    h("button", { type: "button", class: "name", onclick: () => activate(entry) }, [
      entry.name,
      state.searchHits && entry.path ? h("span", { class: "sub" }, [parentPath(entry.path) || "/"]) : null,
    ]),
    h("span", { class: "size" }, [entry.type === "dir" ? "—" : formatBytes(entry.size)]),
    h("span", { class: "time" }, [formatTime(entry.mtime)]),
    h("span", { class: "mode" }, [entry.mode || ""]),
  ]);
  return line;
}

function card(entry) {
  const key = entryKey(entry);
  const selected = state.selected.has(key);
  const showImage = entry.type === "file" && kindOf(entry.name) === "image" && (entry.size || 0) < 12_000_000;
  const thumb = showImage
    ? h("img", { src: rawUrl(entry, false), alt: "" })
    : icon(entry.type);
  return h("div", { class: `card${selected ? " selected" : ""}`, oncontextmenu: (event) => openMenu(event, entry) }, [
    h("button", {
      type: "button",
      class: "tick",
      "aria-label": `Select ${entry.name}`,
      onclick: (event) => toggleSelect(entry, event.shiftKey),
    }, [selected ? "✓" : ""]),
    h("button", { type: "button", class: "thumb", onclick: () => activate(entry) }, [thumb]),
    h("button", { type: "button", class: "label", onclick: () => activate(entry) }, [entry.name]),
  ]);
}

function renderStatus() {
  const rows = visibleEntries();
  const count = state.selected.size;
  let text = state.busy
    ? "Opening…"
    : count
      ? `${count} selected`
      : `${rows.length} item${rows.length === 1 ? "" : "s"}`;
  if (state.truncated) text += " · list shortened";
  if (state.usage) text += ` · ${formatBytes(state.usage.free)} free`;
  $("status").textContent = text;
}

function paint() {
  renderPlaces();
  renderCrumbs();
  renderBanner();
  renderToolbar();
  renderClip();
  renderList();
  renderStatus();
}

function toggleSelect(entry, shift) {
  const key = entryKey(entry);
  const keys = visibleEntries().map(entryKey);
  if (shift && state.anchor) {
    const start = keys.indexOf(state.anchor);
    const end = keys.indexOf(key);
    if (start >= 0 && end >= 0) {
      const [from, to] = start < end ? [start, end] : [end, start];
      for (let index = from; index <= to; index += 1) state.selected.add(keys[index]);
    }
  } else if (state.selected.has(key)) state.selected.delete(key);
  else state.selected.add(key);
  state.anchor = key;
  renderList();
  renderToolbar();
  renderStatus();
}

async function activate(entry) {
  if (entry.type === "dir") {
    await openDir(entry);
    return;
  }
  state.selected.clear();
  state.selected.add(entryKey(entry));
  renderList();
  renderToolbar();
  renderStatus();
  await openPreview(entry);
}

async function openDir(entry) {
  if (!(await confirmDiscard())) return;
  closePreview();
  state.path = state.searchHits && entry.path ? entry.path : joinPath(state.path, entry.name);
  state.searchHits = null;
  state.filter = "";
  $("filter").value = "";
  state.selected.clear();
  await loadList();
}

async function goTo(path) {
  if (path === state.path) return;
  if (!(await confirmDiscard())) return;
  closePreview();
  state.path = path;
  state.searchHits = null;
  state.selected.clear();
  await loadList();
}

async function selectPlace(place) {
  if (!(await confirmDiscard())) return;
  closePreview();
  state.placeId = place.id;
  state.path = "";
  state.selected.clear();
  state.searchHits = null;
  state.filter = "";
  $("filter").value = "";
  localStorage.setItem("rootfiles-place", place.id);
  if (!place.available) {
    state.blocked = place.reason || "That folder is not available.";
    state.entries = [];
    state.display = "";
    state.usage = null;
    paint();
    return;
  }
  state.blocked = "";
  await loadList();
}

async function loadPlaces() {
  const data = await apiJson("GET", "api/places");
  state.places = data.places || [];
  state.log = data.log || [];
}

async function loadList() {
  const token = ++listToken;
  state.busy = true;
  renderStatus();
  try {
    const query = new URLSearchParams({ place: state.placeId, path: state.path });
    const data = await apiJson("GET", `api/list?${query}`);
    if (token !== listToken) return;
    state.entries = data.entries || [];
    state.truncated = !!data.truncated;
    state.writable = !!data.writable;
    state.usage = data.usage;
    state.display = data.display || "";
    state.blocked = "";
    state.busy = false;
    paint();
  } catch (error) {
    if (token !== listToken) return;
    state.busy = false;
    state.blocked = error.message;
    paint();
    toast(error.message);
  }
}

function closePreview() {
  previewToken += 1;
  state.preview = null;
  $("preview").hidden = true;
  $("preview").replaceChildren();
  $("stage").classList.remove("split");
}

async function openPreview(entry) {
  const token = ++previewToken;
  const path = entryKey(entry);
  state.preview = { name: entry.name, path, kind: "loading", text: "", dirty: false };
  paintPreview();
  const visual = kindOf(entry.name);
  if (visual === "image" || visual === "video" || visual === "audio" || visual === "pdf") {
    state.preview.kind = visual;
    if (token !== previewToken) return;
    paintPreview();
    return;
  }
  try {
    const query = new URLSearchParams({ place: state.placeId, path });
    const response = await fetch(api(`api/text?${query}`));
    if (token !== previewToken) return;
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      state.preview.kind = "binary";
      state.preview.message = data.error || "No preview for this file.";
      paintPreview();
      return;
    }
    state.preview.kind = "text";
    state.preview.text = await response.text();
    paintPreview();
  } catch (error) {
    if (token === previewToken) toast(error.message);
  }
}

function rawUrl(entry, download) {
  const query = new URLSearchParams({
    place: state.placeId,
    path: entryKey(entry),
    download: download ? "1" : "0",
  });
  return api(`api/raw?${query}`);
}

function paintPreview() {
  const pane = $("preview");
  const item = state.preview;
  if (!item) {
    pane.hidden = true;
    $("stage").classList.remove("split");
    return;
  }
  pane.hidden = false;
  $("stage").classList.add("split");
  const close = h("button", { type: "button", onclick: () => closePreview() }, ["Close"]);
  let body;
  if (item.kind === "loading") body = h("p", {}, ["Opening…"]);
  else if (item.kind === "image") body = h("img", { src: rawUrl(item, false), alt: item.name });
  else if (item.kind === "video") body = h("video", { src: rawUrl(item, false), controls: true });
  else if (item.kind === "audio") body = h("audio", { src: rawUrl(item, false), controls: true });
  else if (item.kind === "pdf") body = h("iframe", { src: rawUrl(item, false), title: item.name });
  else if (item.kind === "text") {
    const editor = h("textarea", { id: "editor", spellcheck: "false", wrap: "off" }, [item.text]);
    editor.addEventListener("input", () => {
      item.dirty = editor.value !== item.text;
      const save = $("save");
      if (save) save.disabled = !item.dirty;
    });
    editor.addEventListener("keydown", (event) => {
      if (event.key === "Tab") {
        event.preventDefault();
        const start = editor.selectionStart;
        editor.setRangeText("  ", start, editor.selectionEnd, "end");
        editor.dispatchEvent(new Event("input"));
      }
      if ((event.metaKey || event.ctrlKey) && event.key === "s") {
        event.preventDefault();
        saveEditor();
      }
    });
    body = editor;
  } else body = h("p", {}, [item.message || "No preview. Download the file to open it."]);
  const save = item.kind === "text"
    ? h("button", { type: "button", id: "save", disabled: true, onclick: () => saveEditor() }, ["Save"])
    : null;
  pane.replaceChildren(
    h("div", { class: "preview-head" }, [h("strong", {}, [item.name]), h("span", {}, [save, close])]),
    h("div", { class: item.kind === "text" ? "preview-body" : "preview-body" }, [body]),
  );
}

async function saveEditor() {
  const editor = $("editor");
  if (!editor || !state.preview) return;
  try {
    const query = new URLSearchParams({ place: state.placeId, path: state.preview.path });
    const response = await fetch(api(`api/text?${query}`), {
      method: "POST",
      headers: { "Content-Type": "text/plain; charset=utf-8" },
      body: editor.value,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "Could not save.");
    state.preview.text = editor.value;
    state.preview.dirty = false;
    $("save").disabled = true;
    toast("Saved");
    await loadList();
  } catch (error) {
    toast(error.message);
  }
}

async function createFolder() {
  const name = await ask({ title: "New folder", value: "", ok: "Create" });
  if (!name) return;
  try {
    await apiJson("POST", "api/mkdir", { place: state.placeId, path: state.path, name });
    await loadList();
  } catch (error) {
    toast(error.message);
  }
}

async function createFile() {
  const name = await ask({ title: "New file", value: "", ok: "Create" });
  if (!name) return;
  try {
    const query = new URLSearchParams({ place: state.placeId, path: joinPath(state.path, name) });
    const response = await fetch(api(`api/text?${query}`), {
      method: "POST",
      headers: { "Content-Type": "text/plain; charset=utf-8" },
      body: "",
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "Could not create that file.");
    await loadList();
    const entry = state.entries.find((item) => item.name === name);
    if (entry) await openPreview(entry);
  } catch (error) {
    toast(error.message);
  }
}

async function renameSelected(entry) {
  const name = await ask({ title: "Rename", value: entry.name, ok: "Rename" });
  if (!name || name === entry.name) return;
  try {
    await apiJson("POST", "api/rename", { place: state.placeId, path: entryKey(entry), name });
    state.selected.clear();
    closePreview();
    await loadList();
  } catch (error) {
    toast(error.message);
  }
}

async function removeSelected() {
  const items = selectedEntries();
  if (!items.length) return;
  const place = currentPlace();
  const names = items.map((entry) => entry.name).join("\n");
  const ok = await ask({
    title: place && place.kind === "host" ? "Delete from the host?" : `Delete ${items.length} item${items.length === 1 ? "" : "s"}?`,
    body: place && place.kind === "host"
      ? `These files are on the machine itself. This cannot be undone.\n\n${names}`
      : names,
    ok: "Delete",
    danger: true,
  });
  if (!ok) return;
  try {
    const paths = items.map(entryKey);
    await apiJson("POST", "api/delete", { place: state.placeId, paths });
    if (state.preview && paths.includes(state.preview.path)) closePreview();
    state.selected.clear();
    await loadList();
    toast("Deleted");
  } catch (error) {
    toast(error.message);
  }
}

function hold(op) {
  const items = selectedEntries();
  if (!items.length) return;
  state.clip = {
    op,
    placeId: state.placeId,
    paths: items.map(entryKey),
    label: items.length === 1 ? items[0].name : `${items.length} items`,
  };
  renderClip();
  renderList();
  toast(op === "cut" ? "Ready to move" : "Ready to copy");
}

async function paste() {
  if (!state.clip || state.blocked) return;
  try {
    await apiJson("POST", "api/transfer", {
      op: state.clip.op,
      from_place: state.clip.placeId,
      paths: state.clip.paths,
      to_place: state.placeId,
      to_path: state.path,
    });
    const verb = state.clip.op === "cut" ? "Moved" : "Copied";
    if (state.clip.op === "cut") state.clip = null;
    await loadList();
    toast(verb);
  } catch (error) {
    toast(error.message);
  }
}

async function downloadSelected() {
  const items = selectedEntries();
  if (!items.length) return;
  if (items.length === 1 && items[0].type !== "dir") {
    const link = h("a", { href: rawUrl(items[0], true), download: items[0].name });
    document.body.append(link);
    link.click();
    link.remove();
    return;
  }
  try {
    const response = await fetch(api("api/zip"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ place: state.placeId, paths: items.map(entryKey) }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || "Could not download that folder.");
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = h("a", { href: url, download: "files.zip" });
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  } catch (error) {
    toast(error.message);
  }
}

async function unpack(entry) {
  try {
    const result = await apiJson("POST", "api/extract", { place: state.placeId, path: entryKey(entry) });
    await loadList();
    const skipped = result.skipped ? `, skipped ${result.skipped} links` : "";
    toast(`Unpacked ${result.files} file${result.files === 1 ? "" : "s"}${skipped}`);
  } catch (error) {
    toast(error.message);
  }
}

async function copyText(text) {
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = h("textarea", {}, [text]);
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  toast("Path copied");
}

async function uploadFiles(fileList) {
  const files = [...fileList];
  if (!files.length || !state.writable) return;
  const existing = new Set(state.entries.map((entry) => entry.name));
  const clashes = files.filter((file) => existing.has(file.name));
  if (clashes.length) {
    const ok = await ask({
      title: "Replace files?",
      body: clashes.map((file) => file.name).join("\n"),
      ok: "Replace",
      danger: true,
    });
    if (!ok) return;
  }
  let done = 0;
  for (const file of files) {
    toast(`Uploading ${file.name}`);
    const query = new URLSearchParams({ place: state.placeId, path: state.path, name: file.name });
    const response = await fetch(api(`api/upload?${query}`), { method: "POST", body: file });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      toast(data.error || `Could not upload ${file.name}`);
      break;
    }
    done += 1;
  }
  await loadList();
  if (done === files.length) toast(`Uploaded ${done} file${done === 1 ? "" : "s"}`);
}

async function rescan() {
  state.scanning = true;
  renderPlaces();
  try {
    const data = await apiJson("POST", "api/rescan", {});
    state.log = data.log || state.log;
    await loadPlaces();
    const place = state.places.find((item) => item.id === state.placeId);
    if (place && !place.available) {
      state.blocked = place.reason || state.blocked;
    }
    paint();
    const last = (data.log || []).filter(Boolean).pop();
    if (last) toast(last);
  } catch (error) {
    toast(error.message);
  } finally {
    state.scanning = false;
    renderPlaces();
  }
}

async function eject(place) {
  const ok = await ask({
    title: `Eject ${place.name}?`,
    body: "Wait until this finishes before unplugging the drive.",
    ok: "Eject",
  });
  if (!ok) return;
  try {
    await apiJson("POST", "api/eject", { place: place.id });
    if (state.placeId === place.id) {
      state.placeId = "config";
      state.path = "";
    }
    await loadPlaces();
    const next = state.places.find((item) => item.id === state.placeId) || state.places[0];
    if (next) await selectPlace(next);
    toast(`Ejected ${place.name}`);
  } catch (error) {
    toast(error.message);
  }
}

function openMenu(event, entry) {
  event.preventDefault();
  const key = entryKey(entry);
  if (!state.selected.has(key)) {
    state.selected.clear();
    state.selected.add(key);
    renderList();
    renderToolbar();
  }
  const items = [
    { label: entry.type === "dir" ? "Open" : "Preview", run: () => activate(entry) },
    { label: "Download", run: () => downloadSelected() },
    { label: "Rename", run: () => renameSelected(entry) },
    { label: "Copy", run: () => hold("copy") },
    { label: "Cut", run: () => hold("cut") },
  ];
  if (kindOf(entry.name) === "archive") items.push({ label: "Unpack", run: () => unpack(entry) });
  items.push({ label: "Delete", run: () => removeSelected(), danger: true });
  const menu = $("menu");
  menu.replaceChildren(...items.map((item) => h("button", {
    type: "button",
    class: item.danger ? "danger" : "",
    onclick: () => {
      menu.hidden = true;
      item.run();
    },
  }, [item.label])));
  menu.hidden = false;
  menu.style.left = `${Math.min(event.clientX, window.innerWidth - 200)}px`;
  menu.style.top = `${Math.min(event.clientY, window.innerHeight - 220)}px`;
  menuGuard = true;
  setTimeout(() => {
    menuGuard = false;
  }, 0);
}

let searchTimer = 0;
function onFilter() {
  state.filter = $("filter").value;
  clearTimeout(searchTimer);
  if (state.deep && state.filter.trim()) {
    searchTimer = setTimeout(runSearch, 300);
    return;
  }
  state.searchHits = null;
  renderList();
  renderStatus();
}

async function runSearch() {
  const query = state.filter.trim();
  if (!state.deep || !query) {
    state.searchHits = null;
    renderList();
    return;
  }
  try {
    const params = new URLSearchParams({
      place: state.placeId,
      path: state.path,
      q: query,
      hidden: state.showHidden ? "1" : "0",
    });
    const data = await apiJson("GET", `api/search?${params}`);
    state.searchHits = data.entries || [];
    renderList();
    renderStatus();
  } catch (error) {
    toast(error.message);
  }
}

function bindDrop() {
  const workspace = $("workspace");
  let depth = 0;
  workspace.addEventListener("dragenter", (event) => {
    if (![...event.dataTransfer.types].includes("Files")) return;
    event.preventDefault();
    depth += 1;
    $("drop").hidden = !state.writable;
  });
  workspace.addEventListener("dragover", (event) => event.preventDefault());
  workspace.addEventListener("dragleave", () => {
    depth -= 1;
    if (depth <= 0) {
      depth = 0;
      $("drop").hidden = true;
    }
  });
  workspace.addEventListener("drop", (event) => {
    event.preventDefault();
    depth = 0;
    $("drop").hidden = true;
    if (event.dataTransfer.files.length) uploadFiles(event.dataTransfer.files);
  });
}

function bindKeys() {
  document.addEventListener("keydown", (event) => {
    const typing = event.target.closest("input, textarea");
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s" && $("editor")) {
      event.preventDefault();
      saveEditor();
      return;
    }
    if (!$("modal").hidden) {
      if (event.key === "Escape") $("modal").querySelector("button").click();
      return;
    }
    if (typing) return;
    if (event.key === "Backspace") {
      event.preventDefault();
      if (state.path) goTo(parentPath(state.path));
    } else if (event.key === "Delete" || event.key === "Backspace" && (event.metaKey || event.ctrlKey)) {
      removeSelected();
    } else if (event.key === "Escape") {
      $("menu").hidden = true;
    } else if (event.key === "/") {
      event.preventDefault();
      $("filter").focus();
    }
  });
  document.addEventListener("click", () => {
    if (menuGuard) return;
    $("menu").hidden = true;
  });
}

async function boot() {
  const saved = localStorage.getItem("rootfiles-theme");
  const theme = saved || (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  applyTheme(theme);
  bindClick($("theme"), () => {
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  });
  $("filter").addEventListener("input", onFilter);
  $("filter").addEventListener("keydown", (event) => {
    if (event.key === "Enter") runSearch();
  });
  $("deep").addEventListener("change", () => {
    state.deep = $("deep").checked;
    onFilter();
  });
  const input = h("input", { id: "file-input", type: "file", multiple: true, hidden: true });
  input.addEventListener("change", () => {
    uploadFiles(input.files);
    input.value = "";
  });
  document.body.append(input);
  bindDrop();
  bindKeys();
  try {
    await loadPlaces();
    const place = state.places.find((item) => item.id === state.placeId && item.available)
      || state.places.find((item) => item.available)
      || state.places[0];
    if (place) await selectPlace(place);
    else paint();
  } catch (error) {
    toast(error.message);
  }
}

boot();
