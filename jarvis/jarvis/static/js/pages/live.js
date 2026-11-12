import { h } from "../dom.js";

// Live: this device is microphone and speaker. The microphone is only open while this page is.
const AREA_KEY = "jarvis.live.area";
const MENU_MS = 6000;

export default function live(view, ctx) {
  const root = h("div", { class: "page live-page" });
  view.append(root);
  document.body.classList.add("live-mode");

  const clockTime = h("div", { class: "t" });
  const clockDate = h("div", { class: "d" });
  const notice = h("div", { class: "live-notice" });
  const areaSelect = h("select", { class: "select" }, h("option", { value: "" }, "Kein Raum"));
  const panel = h("div", { class: "live-panel" }, h("span", { class: "label" }, "Raum für Sätze ohne Raumangabe"), areaSelect);
  const version = document.querySelector("#foot-left");
  root.append(
    h("div", { class: "live-version" }, version ? version.textContent : "JARVIS OS"),
    h("div", { class: "live-clock" }, clockTime, clockDate),
    notice,
    panel,
  );

  let stopped = false;
  let started = false;
  let stream = null;
  let audio = null;
  let ws = null;
  let retry = 0;
  let muted = false;
  let phase = "connecting";
  let menuTimer = 0;
  let wakeLock = null;
  let playing = Promise.resolve();
  let fullscreen = false;

  // ------------------------------------------------------------ clock
  const tick = () => {
    const now = new Date();
    clockTime.textContent = now.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    clockDate.textContent = now.toLocaleDateString("de-DE", { weekday: "long", day: "2-digit", month: "long" });
  };
  tick();
  const clockTimer = setInterval(tick, 1000);

  // ------------------------------------------------------------- globe
  const place = (instant = false) => {
    const w = window.innerWidth;
    const hh = window.innerHeight;
    ctx.placeGlobe(w / 2, hh * 0.44, Math.min(w, hh) * 0.3, 1, instant);
  };
  place();

  function setPhase(next) {
    phase = next;
    root.dataset.phase = next;
    ctx.livePhase(next === "connecting" ? "idle" : next);
  }

  function showNotice(content) {
    notice.replaceChildren(...(Array.isArray(content) ? content : [content]));
    notice.classList.toggle("show", !!content);
  }

  // -------------------------------------------------------------- menu
  function toggleMenu(force) {
    const open = force ?? !document.body.classList.contains("live-menu");
    document.body.classList.toggle("live-menu", open);
    clearTimeout(menuTimer);
    if (open) menuTimer = setTimeout(() => document.body.classList.remove("live-menu"), MENU_MS);
  }

  root.addEventListener("click", () => {
    if (!started) {
      start(true);
      return;
    }
    if (audio && audio.state !== "running") audio.resume();
    toggleMenu();
  });
  panel.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleMenu(true);
  });

  ctx.api("/areas").then((res) => {
    for (const area of res.areas) areaSelect.append(h("option", { value: area.id }, area.name));
    areaSelect.value = localStorage.getItem(AREA_KEY) || "";
  }).catch(() => {});
  areaSelect.addEventListener("change", () => {
    localStorage.setItem(AREA_KEY, areaSelect.value);
    if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "area", area: areaSelect.value }));
  });

  // ---------------------------------------------------------- microphone
  function httpsHint() {
    const port = ctx.httpsPort;
    const url = port ? `https://${location.hostname}:${port}/#live` : null;
    showNotice([
      h("div", { class: "big" }, "Mikrofon braucht HTTPS"),
      h("p", {}, url ? "Öffne Live über die sichere Adresse und bestätige das Zertifikat einmal. Danach hört Jarvis hier auf sein Wakeword." : "Der Browser gibt das Mikrofon nur über HTTPS frei."),
      url ? h("a", { class: "btn primary", href: url, style: { "text-transform": "none" } }, url.replace("/#live", "")) : null,
    ]);
  }

  async function start(fromTap) {
    if (stopped) return;
    if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      httpsHint();
      return;
    }
    started = true;
    showNotice(null);
    if (fromTap && document.documentElement.requestFullscreen && !document.fullscreenElement) {
      document.documentElement.requestFullscreen().then(() => (fullscreen = true)).catch(() => {});
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
      });
    } catch (err) {
      started = false;
      showNotice([h("div", { class: "big" }, "Kein Zugriff auf das Mikrofon"), h("p", {}, `${err.message || err.name}. Tippen, um es erneut zu versuchen.`)]);
      return;
    }
    if (stopped) {
      stream.getTracks().forEach((t) => t.stop());
      return;
    }
    audio = new AudioContext();
    await audio.audioWorklet.addModule("/static/js/live-worklet.js");
    const source = audio.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(audio, "jarvis-mic");
    const sink = audio.createGain();
    sink.gain.value = 0;
    source.connect(node);
    node.connect(sink);
    sink.connect(audio.destination);
    node.port.onmessage = (event) => {
      const { pcm, level } = event.data;
      if (phase === "listening" || phase === "followup") ctx.liveLevel(level * 5);
      if (!muted && ws && ws.readyState === 1 && phase !== "thinking" && phase !== "speaking") ws.send(pcm);
    };
    await audio.resume().catch(() => {});
    if (audio.state !== "running") {
      showNotice([h("div", { class: "big" }, "Tippen, um Jarvis zu starten"), h("p", {}, "Der Browser verlangt einmal eine Berührung, bevor er Ton abspielt.")]);
      audio.onstatechange = () => audio.state === "running" && showNotice(null);
    }
    keepAwake();
    connect();
  }

  function connect() {
    if (stopped) return;
    setPhase("connecting");
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/api/live/voice`);
    ws.binaryType = "arraybuffer";
    ws.onopen = () => ws.send(JSON.stringify({ type: "hello", area: localStorage.getItem(AREA_KEY) || "" }));
    ws.onmessage = (event) => {
      let msg;
      try {
        msg = JSON.parse(event.data);
      } catch {
        return;
      }
      onMessage(msg);
    };
    ws.onclose = () => {
      if (stopped) return;
      setPhase("connecting");
      retry = setTimeout(connect, 2000);
    };
  }

  function onMessage(msg) {
    if (msg.type === "state") {
      setPhase(msg.phase);
    } else if (msg.type === "reply") {
      if (msg.audio) enqueue(msg.audio, true);
      else ws.send(JSON.stringify({ type: "played" }));
    } else if (msg.type === "announce") {
      if (msg.audio) enqueue(msg.audio, false);
    }
  }

  // ----------------------------------------------------------- speaker
  function enqueue(b64, ack) {
    playing = playing.then(() => play(b64)).catch(() => {}).then(() => {
      if (ack && ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "played" }));
    });
  }

  async function play(b64) {
    if (!audio || stopped) return;
    muted = true;
    try {
      const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
      const buffer = await audio.decodeAudioData(bytes.buffer);
      const src = audio.createBufferSource();
      const analyser = audio.createAnalyser();
      analyser.fftSize = 512;
      src.buffer = buffer;
      src.connect(analyser);
      analyser.connect(audio.destination);
      const data = new Float32Array(analyser.fftSize);
      let raf = 0;
      const meter = () => {
        analyser.getFloatTimeDomainData(data);
        let sum = 0;
        for (const v of data) sum += v * v;
        ctx.liveLevel(Math.sqrt(sum / data.length) * 4);
        raf = requestAnimationFrame(meter);
      };
      await new Promise((resolve) => {
        src.onended = resolve;
        src.start();
        meter();
      });
      cancelAnimationFrame(raf);
    } finally {
      await new Promise((r) => setTimeout(r, 350));
      muted = false;
    }
  }

  // ------------------------------------------------------------- awake
  async function keepAwake() {
    try {
      if ("wakeLock" in navigator && document.visibilityState === "visible") wakeLock = await navigator.wakeLock.request("screen");
    } catch {
      wakeLock = null;
    }
  }
  const onVisible = () => {
    if (document.visibilityState === "visible" && started) keepAwake();
  };
  document.addEventListener("visibilitychange", onVisible);

  setPhase("connecting");
  start(false);

  return {
    relayout: () => place(true),
    destroy() {
      stopped = true;
      clearInterval(clockTimer);
      clearTimeout(retry);
      clearTimeout(menuTimer);
      document.removeEventListener("visibilitychange", onVisible);
      if (ws) ws.close();
      if (stream) stream.getTracks().forEach((t) => t.stop());
      if (audio) audio.close().catch(() => {});
      if (wakeLock) wakeLock.release().catch(() => {});
      if (fullscreen && document.fullscreenElement) document.exitFullscreen().catch(() => {});
      document.body.classList.remove("live-mode", "live-menu");
      ctx.livePhase(null);
    },
  };
}
