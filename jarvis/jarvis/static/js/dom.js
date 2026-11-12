// Small DOM helpers. Text is always set as text, never as HTML.

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key === "on") for (const [ev, fn] of Object.entries(value)) el.addEventListener(ev, fn);
    else if (key === "style") for (const [prop, v] of Object.entries(value)) el.style.setProperty(prop, v);
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key in el && typeof value !== "string") el[key] = value;
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function svg(tag, attrs = {}, ...children) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, String(value));
  for (const child of children.flat()) if (child) el.append(child);
  return el;
}

export const $ = (selector, root = document) => root.querySelector(selector);

export function clear(el) {
  while (el.firstChild) el.firstChild.remove();
  return el;
}

export function toast(message, kind = "info") {
  const box = document.getElementById("toasts");
  const el = h("div", { class: `toast ${kind === "bad" ? "bad" : ""}`, role: "status" }, message);
  box.append(el);
  setTimeout(() => el.remove(), kind === "bad" ? 6500 : 3500);
}

export function modal({ title, body, actions = [], onClose }) {
  const close = () => {
    backdrop.remove();
    document.removeEventListener("keydown", onKey);
    if (onClose) onClose();
  };
  const onKey = (e) => {
    if (e.key === "Escape") close();
  };
  const buttons = actions.map((a) =>
    h("button", { class: `btn ${a.kind || ""}`, type: "button", on: { click: () => a.onClick(close) } }, a.label),
  );
  const box = h("div", { class: "modal", role: "dialog", "aria-modal": "true" }, h("h3", {}, title), body, h("div", { class: "actions" }, buttons));
  const backdrop = h("div", { class: "backdrop", on: { mousedown: (e) => e.target === backdrop && close() } }, box);
  document.body.append(backdrop);
  document.addEventListener("keydown", onKey);
  const first = box.querySelector("input, select, textarea, button");
  if (first) first.focus();
  return { close, box };
}

export function confirmDialog(title, text, label = "Bestätigen") {
  return new Promise((resolve) => {
    let decided = false;
    modal({
      title,
      body: h("p", { class: "muted" }, text),
      actions: [
        { label: "Abbrechen", onClick: (close) => close() },
        { label, kind: "primary", onClick: (close) => { decided = true; close(); } },
      ],
      onClose: () => resolve(decided),
    });
  });
}

export function countUp(el, to, { duration = 1100, decimals = 0 } = {}) {
  const target = Number(to);
  if (!Number.isFinite(target)) {
    el.textContent = to ?? "–";
    return;
  }
  if (el.dataset.counted === "1") {
    el.textContent = target.toFixed(decimals);
    return;
  }
  el.dataset.counted = "1";
  const start = performance.now();
  const step = (now) => {
    const t = Math.min(1, (now - start) / duration);
    const eased = 1 - Math.pow(1 - t, 3);
    el.textContent = (target * eased).toFixed(decimals);
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

export function selectEl(options, value, attrs = {}) {
  const el = h("select", { class: "select", ...attrs });
  for (const opt of options) {
    const o = typeof opt === "string" ? { value: opt, label: opt } : opt;
    const option = h("option", { value: o.value }, o.label);
    if (String(o.value) === String(value ?? "")) option.selected = true;
    el.append(option);
  }
  return el;
}

export function toggle(checked, onChange, { disabled = false, label = "" } = {}) {
  const input = h("input", { type: "checkbox", "aria-label": label });
  input.checked = !!checked;
  input.disabled = !!disabled;
  input.addEventListener("change", () => onChange(input.checked, input));
  return h("label", { class: "toggle" }, input, h("span"));
}

export function pill(text, kind = "") {
  return h("span", { class: `pill ${kind}` }, h("span", { class: "dot" }), text);
}

export function card(title, ...children) {
  return h("section", { class: "card" }, title ? h("div", { class: "card-title" }, title) : null, children);
}

export function pageHead(lead, right = null) {
  return h("div", { class: "page-head" }, h("p", { class: "lead" }, lead), right);
}

export function timeAgo(ts) {
  if (!ts) return "–";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 45) return "gerade eben";
  if (s < 3600) return `vor ${Math.round(s / 60)} Min.`;
  if (s < 86400) return `vor ${Math.round(s / 3600)} Std.`;
  return new Date(ts * 1000).toLocaleDateString("de-DE");
}

export function debounce(fn, ms = 500) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

export async function busy(button, fn) {
  const old = button.textContent;
  button.disabled = true;
  try {
    return await fn();
  } finally {
    button.disabled = false;
    button.textContent = old;
  }
}
