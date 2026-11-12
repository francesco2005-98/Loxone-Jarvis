import { api, Live } from "./api.js";
import { $, clear, toast } from "./dom.js";
import { Scene } from "./scene.js";
import overview from "./pages/overview.js";
import ai from "./pages/ai.js";
import mics from "./pages/mics.js";
import loxone from "./pages/loxone.js";

const PAGES = {
  overview: { module: overview, hash: "uebersicht", footer: "01 / NEURAL COMMAND CENTER" },
  ai: { module: ai, hash: "ki", footer: "02 / INTELLIGENCE ENGINE" },
  mics: { module: mics, hash: "mikrofone", footer: "03 / VOICE IS THE INTERFACE" },
  loxone: { module: loxone, hash: "loxone", footer: "04 / CONNECTED TO YOUR WORLD" },
};
const ORDER = ["overview", "ai", "mics", "loxone"];
const PHASE_RANK = { speaking: 5, listening: 4, collecting: 4, thinking: 3, followup: 2, idle: 0 };

const live = new Live();
const phases = {};
let scene;
let current = null;
let entered = false;

const ctx = {
  api,
  live,
  toast,
  status: null,
  navigate: (id) => {
    location.hash = PAGES[id].hash;
  },
  placeGlobe: (x, y, r, alpha, instant) => scene && scene.setGlobe(x, y, r, alpha, instant),
  phases,
};

function gateGlobe(instant = false) {
  const w = window.innerWidth;
  const hgt = window.innerHeight;
  scene.setGlobe(w / 2, hgt * 0.44, Math.min(w, hgt) * 0.16, 1, instant);
}

function backgroundGlobe() {
  const w = window.innerWidth;
  const hgt = window.innerHeight;
  scene.setGlobe(w * 0.84, hgt * 0.7, Math.min(w, hgt) * 0.2, 0.1);
}

function dominantPhase() {
  let best = "idle";
  for (const phase of Object.values(phases)) if ((PHASE_RANK[phase] || 0) > (PHASE_RANK[best] || 0)) best = phase;
  return best;
}

function setStatus(status) {
  if (!status) return;
  ctx.status = status;
  const ok = status.loxone.status === "verbunden" && status.gemini.configured && !status.gemini.error;
  const pill = $("#system-pill");
  pill.className = `pill ${ok ? "live" : "dim"}`;
  $("#system-text").textContent = ok ? "System online" : "Eingeschränkt";
  for (const [area, phase] of Object.entries(status.phases || {})) phases[area] = phase;
  scene.setPhase(dominantPhase());
}

function onLive(msg) {
  if (msg.type === "hello" || msg.type === "status") setStatus(msg.status);
  if (msg.type === "phase") {
    phases[msg.area] = msg.phase;
    scene.setPhase(dominantPhase());
  }
  if (msg.type === "level") scene.level(msg.level);
  if (current && current.instance.onLive) current.instance.onLive(msg);
}

function moveUnderline(button) {
  const bar = $("#nav-underline");
  if (!button) return;
  bar.style.setProperty("width", `${button.offsetWidth}px`);
  bar.style.setProperty("transform", `translateX(${button.offsetLeft}px)`);
}

function route() {
  const hash = location.hash.replace("#", "") || "uebersicht";
  const id = ORDER.find((key) => PAGES[key].hash === hash) || "overview";
  if (current && current.id === id) return;
  if (current && current.instance.destroy) current.instance.destroy();
  const view = clear($("#view"));
  for (const btn of document.querySelectorAll("#nav button")) btn.classList.toggle("active", btn.dataset.page === id);
  moveUnderline(document.querySelector(`#nav button[data-page="${id}"]`));
  $("#foot-center").textContent = PAGES[id].footer;
  $("#foot-progress").style.setProperty("width", `${((ORDER.indexOf(id) + 1) / ORDER.length) * 100}%`);
  if (id !== "overview") backgroundGlobe();
  const instance = PAGES[id].module(view, ctx) || {};
  current = { id, instance };
  window.scrollTo({ top: 0 });
}

function clock() {
  const now = new Date();
  $("#foot-clock").textContent = `${now.toLocaleDateString("de-DE")} · ${now.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}`;
}

async function enter(short) {
  if (entered) return;
  entered = true;
  const gate = $("#gate");
  $("#gate-form").style.setProperty("display", "none");
  $("#gate-title").textContent = "Dein Zuhause. Ein Bewusstsein.";
  $("#gate-tag").textContent = "INTELLIGENCE, CONNECTED.";
  gate.classList.remove("hidden");
  await new Promise((r) => setTimeout(r, short ? 900 : 1500));
  gate.classList.add("hidden");
  $("#app").classList.add("visible");
  live.on(onLive);
  live.connect();
  route();
  clock();
  setInterval(clock, 30000);
}

function showGate(setup) {
  const gate = $("#gate");
  const form = $("#gate-form");
  const pw = $("#gate-password");
  const pw2 = $("#gate-password2");
  const err = $("#gate-error");
  gate.classList.remove("hidden");
  if (setup) {
    $("#gate-title").textContent = "Willkommen, Jarvis ist bereit.";
    $("#gate-tag").textContent = "ERSTES PASSWORT FESTLEGEN · MINDESTENS 10 ZEICHEN";
    pw.autocomplete = "new-password";
    pw2.style.setProperty("display", "block");
    pw2.required = true;
    $("#gate-submit").textContent = "Passwort festlegen";
  }
  pw.focus();
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    err.textContent = "";
    if (setup && pw.value !== pw2.value) {
      err.textContent = "Die Passwörter stimmen nicht überein.";
      return;
    }
    try {
      await api(setup ? "/setup" : "/login", { method: "POST", body: { password: pw.value } });
      pw.value = "";
      pw2.value = "";
      enter(false);
    } catch (e) {
      err.textContent = e.message;
      pw.select();
    }
  });
}

async function boot() {
  scene = new Scene($("#scene"));
  gateGlobe(true);
  for (const btn of document.querySelectorAll("#nav button")) {
    btn.addEventListener("click", () => ctx.navigate(btn.dataset.page));
  }
  window.addEventListener("hashchange", route);
  window.addEventListener("resize", () => {
    moveUnderline(document.querySelector("#nav button.active"));
    if (!entered) gateGlobe(true);
    if (current && current.instance.relayout) current.instance.relayout();
  });
  window.addEventListener("jarvis:logout", () => {
    if (entered) location.reload();
  });
  $("#logout").addEventListener("click", async () => {
    await api("/logout", { method: "POST" }).catch(() => {});
    location.reload();
  });
  let session = { authenticated: false, setup_required: false, version: "" };
  try {
    session = await api("/session");
  } catch (e) {
    toast(`Jarvis nicht erreichbar: ${e.message}`, "bad");
  }
  $("#gate-version").textContent = `JARVIS OS ${session.version || ""}`;
  $("#foot-left").textContent = `JARVIS OS / ${session.version || ""}`;
  if (session.authenticated) enter(true);
  else showGate(session.setup_required);
}

boot();
