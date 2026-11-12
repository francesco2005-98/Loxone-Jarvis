import { busy, card, clear, confirmDialog, h, modal, pageHead, pill, selectEl, toast } from "../dom.js";
import { pair, pairSupported } from "../flasher.js";

const NOISE = [
  { value: 0, label: "Aus" },
  { value: 1, label: "Leicht" },
  { value: 2, label: "Mittel" },
  { value: 3, label: "Stark" },
  { value: 4, label: "Sehr stark" },
];
const LANGS = [
  { value: "auto", label: "Automatisch (Deutsch / Englisch)" },
  { value: "de", label: "Deutsch" },
  { value: "en", label: "English" },
];
const STT = [
  { value: "small", label: "small · genauer" },
  { value: "base", label: "base · schneller" },
];
const BARS = 96;

export default function micsPage(view, ctx) {
  const root = h("div", { class: "page" });
  view.append(root);
  root.append(pageHead("Mikrofone, Wakeword und Bereiche zentral verwalten."));

  let mics = [];
  let areas = [];
  let zones = { zones: [], rooms: [], lights: [] };
  let firmware = null;
  let settings = {};
  let secrets = {};
  let source = "";
  const history = new Array(BARS).fill(0);
  const micHistory = {};
  let lastLevelAt = 0;
  let dirty = true;

  // ------------------------------------------------------- recommendation
  const reco = h("section", { class: "card" });
  root.append(reco);

  // ------------------------------------------------------------ waveform
  const wave = h("canvas", { class: "wave" });
  const sourceSelect = selectEl([], "");
  const signal = h("span", {}, pill("Kein Signal", "dim"));
  const waveCard = h(
    "section",
    { class: "card" },
    h("div", { class: "card-title" }, h("span", {}, "Live Audio"), sourceSelect),
    wave,
    h("div", { class: "row between", style: { "margin-top": "10px" } }, h("span", { class: "label" }, "16 kHz / Mono · Pegel, keine Aufnahme"), signal),
  );
  sourceSelect.style.setProperty("width", "auto");
  sourceSelect.addEventListener("change", () => {
    source = sourceSelect.value;
    history.fill(0);
    dirty = true;
  });

  // -------------------------------------------------------- voice config
  const lang = selectEl(LANGS, "auto");
  const noise = selectEl(NOISE, 2);
  const stt = selectEl(STT, "small");
  const sens = h("input", { type: "range", min: "0", max: "100", step: "1" });
  const sensLabel = h("span", { class: "gold-text" }, "");
  const vol = h("input", { type: "range", min: "0", max: "100", step: "1" });
  const volLabel = h("span", { class: "gold-text" }, "");
  const silence = h("input", { type: "range", min: "0", max: "2000", step: "100" });
  const silenceLabel = h("span", { class: "gold-text" }, "");
  const testArea = selectEl([], "", { style: { flex: "1 1 140px", width: "auto", "min-width": "0" } });
  const testBtn = h("button", { class: "btn", type: "button" }, "Testansage");
  const saveVoice = h("button", { class: "btn primary", type: "button" }, "Speichern");
  const bind = (input, label, fmt) => {
    const update = () => (label.textContent = fmt(Number(input.value)));
    input.addEventListener("input", update);
    return update;
  };
  const updSens = bind(sens, sensLabel, (v) => `${v} %`);
  const updVol = bind(vol, volLabel, (v) => (v === 0 ? "Zonenlautstärke" : `${v} %`));
  const updSil = bind(silence, silenceLabel, (v) => `${v} ms`);
  const config = card(
    "Voice-Konfiguration",
    h(
      "div",
      { class: "kv" },
      h("div", { class: "label" }, "Wake Word"),
      h("div", { class: "gold-text big" }, "Jarvis"),
      h("div", { class: "label" }, "Sprache"),
      lang,
      h("div", { class: "label" }, "Rauschfilter"),
      noise,
      h("div", { class: "label" }, "Empfindlichkeit"),
      h("div", { class: "row" }, sens, sensLabel),
      h("div", { class: "label" }, "Ansage-Lautstärke"),
      h("div", { class: "row" }, vol, volLabel),
      h("div", { class: "label" }, "Stille vor Ansage"),
      h("div", { class: "row" }, silence, silenceLabel),
      h("div", { class: "label" }, "Erkennung"),
      stt,
    ),
    h("div", { class: "row wrap", style: { "margin-top": "16px" } }, testArea, testBtn, saveVoice),
  );
  root.append(h("div", { class: "grid mics-top", style: { "margin-top": "clamp(14px, 1.2vw, 24px)" } }, waveCard, config));

  // ----------------------------------------------------------------- pair
  const ssid = h("input", { class: "input", maxlength: 32, placeholder: "WLAN-Name (2,4 GHz)" });
  const wifiPw = h("input", { class: "input", type: "password", autocomplete: "off", placeholder: "WLAN-Passwort neu setzen" });
  const wifiState = h("span", { class: "hint" }, "");
  const address = h("input", { class: "input", maxlength: 120, placeholder: "z. B. 192.168.1.10" });
  const savePair = h("button", { class: "btn", type: "button" }, "Speichern");
  const pairBtn = h("button", { class: "btn primary", type: "button" }, "Pair");
  const support = pairSupported();
  const pairCard = card(
    "Neues Mikrofon koppeln",
    h(
      "div",
      { class: "kv" },
      h("div", { class: "label" }, "WLAN der Mikrofone"),
      h("div", { class: "stack" }, ssid, h("div", { class: "row" }, wifiPw, wifiState)),
      h("div", { class: "label" }, "Jarvis im Hausnetz"),
      h("div", { class: "stack" }, address, h("div", { class: "hint" }, "Adresse des Mini-Servers. Die Boards melden sich dort auf Port 8765 und laden Updates.")),
    ),
    h("p", { class: "hint" }, "Board per USB an diesen Rechner stecken, Pair klicken. Danach an ein USB-C-Netzteil im Raum. Das Board bekommt WLAN und einen eigenen Schlüssel, nie Loxone- oder Gemini-Zugänge."),
    support ? h("div", { class: "notice" }, support) : null,
    h("div", { class: "row", style: { "margin-top": "12px" } }, h("span", { class: "spacer" }), savePair, pairBtn),
  );
  pairBtn.disabled = !!support;

  // -------------------------------------------------------------- areas
  const areaList = h("div", { class: "areas-list" });
  const newArea = h("input", { class: "input", maxlength: 40, placeholder: "Neuer Bereich, z. B. Wohnbereich" });
  const addArea = h("button", { class: "btn", type: "button" }, "Bereich anlegen");
  const areaCard = card(
    "Bereiche",
    h("p", { class: "hint" }, "Ein Bereich ist eine Hör- und Antwortgruppe: eine Audioserver-Zone, beliebig viele Mikrofone. Hören mehrere Boards denselben Satz, gewinnt das deutlichste."),
    areaList,
    h("div", { class: "row", style: { "margin-top": "14px" } }, newArea, addArea),
  );
  root.append(h("div", { class: "grid mics-top", style: { "margin-top": "clamp(14px, 1.2vw, 24px)" } }, areaCard, pairCard));

  // --------------------------------------------------------------- mics
  const micGrid = h("div", { class: "grid mic-grid" });
  root.append(micGrid);

  // -------------------------------------------------------------- render
  function renderReco(r) {
    clear(reco);
    reco.append(
      h("div", { class: "card-title" }, "Empfohlenes Mikrofon"),
      h(
        "div",
        { class: "reco" },
        h("div", { class: "badge-letter" }, "M"),
        h(
          "div",
          {},
          h("div", { class: "board" }, r.board),
          h("div", { class: "muted small" }, r.reichelt.map((x) => `Reichelt ${x.article} · ${x.variant} (${x.note})`).join("  ·  ")),
          h("ul", {}, r.hints.map((t) => h("li", {}, t))),
        ),
        h("div", { class: "notice", style: { "max-width": "320px" } }, r.warning),
      ),
    );
  }

  function areaOptions(withEmpty = true) {
    const opts = areas.map((a) => ({ value: a.id, label: a.name }));
    return withEmpty ? [{ value: "", label: "Kein Bereich" }, ...opts] : opts;
  }

  function renderSources() {
    const opts = [{ value: "", label: "Alle Bereiche" }, ...areas.map((a) => ({ value: `a:${a.id}`, label: a.name })), ...mics.map((m) => ({ value: `m:${m.id}`, label: m.name }))];
    sourceSelect.replaceChildren(...opts.map((o) => {
      const el = h("option", { value: o.value }, o.label);
      if (o.value === source) el.selected = true;
      return el;
    }));
    testArea.replaceChildren(...areaOptions(false).map((o) => h("option", { value: o.value }, o.label)));
    testBtn.disabled = !areas.length;
  }

  function renderAreas() {
    clear(areaList);
    if (!areas.length) areaList.append(h("p", { class: "muted small" }, "Noch kein Bereich. Lege zuerst einen an, dann ordne Mikrofone zu."));
    for (const area of areas) {
      const name = h("input", { class: "input", value: area.name, maxlength: 40 });
      const zone = selectEl([{ value: "", label: "Keine Zone" }, ...zones.zones.map((z) => ({ value: z.uuid, label: `${z.name}${z.room ? ` · ${z.room}` : ""}` }))], area.zone_uuid || "");
      const light = selectEl([{ value: "", label: "Kein Hauptlicht" }, ...zones.lights.map((l) => ({ value: l.id, label: `${l.name}${l.room ? ` · ${l.room}` : ""}` }))], area.main_light || "");
      const save = (fields) =>
        ctx.api(`/areas/${area.id}`, { method: "PUT", body: fields }).then((res) => {
          Object.assign(area, res);
          renderSources();
        }).catch((e) => toast(e.message, "bad"));
      name.addEventListener("change", () => save({ name: name.value }));
      zone.addEventListener("change", () => save({ zone_uuid: zone.value || null }));
      light.addEventListener("change", () => save({ main_light: light.value || null }));
      const rooms = h("div", { class: "chips rooms" }, h("span", { class: "label", style: { "margin-right": "6px" } }, "Loxone-Räume"));
      for (const room of zones.rooms) {
        const chip = h("span", { class: `chip pick ${area.rooms.includes(room) ? "on" : ""}`, role: "button", tabindex: "0" }, room);
        const flip = () => {
          const set = new Set(area.rooms);
          if (set.has(room)) set.delete(room);
          else set.add(room);
          chip.classList.toggle("on");
          save({ rooms: [...set] });
        };
        chip.addEventListener("click", flip);
        chip.addEventListener("keydown", (e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), flip()));
        rooms.append(chip);
      }
      if (!zones.rooms.length) rooms.append(h("span", { class: "hint" }, "erscheinen, sobald Loxone verbunden ist"));
      const del = h("button", { class: "btn small danger", type: "button" }, "Löschen");
      del.addEventListener("click", async () => {
        if (!(await confirmDialog("Bereich löschen", `${area.name} wird gelöscht. Die Mikrofone bleiben gekoppelt, aber ohne Bereich.`, "Löschen"))) return;
        await ctx.api(`/areas/${area.id}`, { method: "DELETE" });
        load();
      });
      const count = mics.filter((m) => m.area_id === area.id).length;
      areaList.append(
        h(
          "div",
          { class: "area-row" },
          h("div", { class: "stack" }, h("div", { class: "label" }, `Bereich · ${count} Mikrofon${count === 1 ? "" : "e"}`), name),
          h("div", { class: "stack" }, h("div", { class: "label" }, "Audioserver-Zone"), zone),
          h("div", { class: "stack" }, h("div", { class: "label" }, "Hauptlicht"), light),
          del,
          rooms,
        ),
      );
    }
  }

  function renderMics() {
    clear(micGrid);
    mics.forEach((mic, index) => {
      const bars = h("div", { class: `mini-bars ${mic.online ? "live" : ""}` });
      for (let i = 0; i < 26; i++) bars.append(h("i"));
      const name = h("input", { class: "input", value: mic.name, maxlength: 40 });
      name.addEventListener("change", () => ctx.api(`/mics/${mic.id}`, { method: "PUT", body: { name: name.value } }).then(() => load()).catch((e) => toast(e.message, "bad")));
      const area = selectEl(areaOptions(), mic.area_id || "");
      area.addEventListener("change", () => ctx.api(`/mics/${mic.id}`, { method: "PUT", body: { area_id: area.value || null } }).then(() => load()).catch((e) => toast(e.message, "bad")));
      const outdated = firmware && mic.firmware && mic.firmware !== firmware.version;
      const update = h("button", { class: "btn small", type: "button" }, `Update ${firmware ? firmware.version : ""}`);
      update.addEventListener("click", () => busy(update, async () => {
        try {
          await ctx.api(`/mics/${mic.id}/update`, { method: "POST" });
          toast("Update läuft, das Board startet gleich neu.");
        } catch (e) {
          toast(e.message, "bad");
        }
      }));
      const remove = h("button", { class: "btn small danger", type: "button" }, "Entfernen");
      remove.addEventListener("click", async () => {
        if (!(await confirmDialog("Mikrofon entfernen", "Das Board vergisst WLAN und Schlüssel und muss neu gekoppelt werden.", "Entfernen"))) return;
        await ctx.api(`/mics/${mic.id}`, { method: "DELETE" });
        load();
      });
      micGrid.append(
        h(
          "section",
          { class: `card mic-card ${mic.online ? "" : "offline"}`, dataset: { mic: mic.id } },
          h("div", { class: "label" }, `${String(index + 1).padStart(2, "0")} / Audio Input`),
          name,
          h("div", { class: "muted small", style: { "margin-top": "6px" } }, `XIAO ESP32-S3 Sense · ${mic.device}`),
          bars,
          h("div", { class: "stack" }, area),
          h(
            "div",
            { class: "row between", style: { "margin-top": "12px" } },
            h("span", { class: mic.online ? "status-on" : "status-off" }, mic.online ? "ONLINE" : "OFFLINE"),
            h("span", { class: "label" }, mic.area_id ? "Bereich zugewiesen" : "Ohne Bereich"),
          ),
          h("div", { class: "row wrap", style: { "margin-top": "12px" } }, h("span", { class: "hint" }, `Firmware ${mic.firmware || "–"}`), h("span", { class: "spacer" }), outdated ? update : null, remove),
        ),
      );
    });
    if (!mics.length) micGrid.append(h("section", { class: "card" }, h("p", { class: "muted" }, "Noch kein Mikrofon gekoppelt. Board per USB anstecken und oben auf Pair klicken.")));
  }

  // ----------------------------------------------------------- waveform
  function drawWave() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = wave.clientWidth;
    const hgt = wave.clientHeight;
    if (wave.width !== Math.round(w * dpr)) {
      wave.width = Math.round(w * dpr);
      wave.height = Math.round(hgt * dpr);
    }
    const g = wave.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, hgt);
    const gap = 3;
    const bw = Math.max(2, (w - gap * (BARS - 1)) / BARS);
    const mid = hgt / 2;
    for (let i = 0; i < BARS; i++) {
      const v = history[i];
      const bh = Math.max(2, v * (hgt * 0.92));
      const recent = i / (BARS - 1);
      g.fillStyle = `rgba(242, 194, 27, ${0.25 + 0.75 * Math.pow(recent, 0.6)})`;
      g.fillRect(i * (bw + gap), mid - bh / 2, bw, bh);
    }
  }

  let raf = 0;
  const tick = () => {
    raf = requestAnimationFrame(tick);
    const now = performance.now();
    if (now - lastLevelAt > 250 && history[BARS - 1] !== 0) {
      history.push(0);
      history.shift();
      dirty = true;
      lastLevelAt = now;
      signal.replaceChildren(pill("Kein Signal", "dim"));
    }
    if (dirty) {
      drawWave();
      dirty = false;
    }
  };
  raf = requestAnimationFrame(tick);

  function onLevel(msg) {
    const list = micHistory[msg.mic] || (micHistory[msg.mic] = new Array(26).fill(0));
    list.push(msg.level);
    list.shift();
    const card = micGrid.querySelector(`[data-mic="${msg.mic}"] .mini-bars`);
    if (card) [...card.children].forEach((bar, i) => bar.style.setProperty("height", `${Math.max(3, list[i] * 30)}px`));
    const matches = !source || (source.startsWith("m:") && source.slice(2) === msg.mic) || (source.startsWith("a:") && source.slice(2) === msg.area);
    if (!matches) return;
    history.push(msg.level);
    history.shift();
    lastLevelAt = performance.now();
    dirty = true;
    signal.replaceChildren(pill("Signal aktiv", "live"));
  }

  // ------------------------------------------------------------- actions
  saveVoice.addEventListener("click", () => busy(saveVoice, async () => {
    try {
      await ctx.api("/settings", {
        method: "PUT",
        body: {
          values: {
            "stt.language": lang.value,
            "mic.noise_filter": Number(noise.value),
            "mic.sensitivity": Number(sens.value),
            "announce.volume": Number(vol.value),
            "announce.silence_ms": Number(silence.value),
            "stt.model": stt.value,
          },
        },
      });
      toast("Gespeichert");
    } catch (e) {
      toast(e.message, "bad");
    }
  }));

  testBtn.addEventListener("click", () => busy(testBtn, async () => {
    const res = await ctx.api("/announce/test", { method: "POST", body: { area_id: testArea.value } });
    toast(res.ok ? "Testansage gesendet" : `Ansage fehlgeschlagen: ${res.error}`, res.ok ? "info" : "bad");
  }));

  savePair.addEventListener("click", () => busy(savePair, async () => {
    try {
      await ctx.api("/settings", { method: "PUT", body: { values: { "mics.wifi_ssid": ssid.value, "mics.jarvis_address": address.value } } });
      if (wifiPw.value) {
        await ctx.api("/secrets", { method: "PUT", body: { key: "mics.wifi_password", value: wifiPw.value } });
        wifiPw.value = "";
      }
      toast("Gespeichert");
      load();
    } catch (e) {
      toast(e.message, "bad");
    }
  }));

  addArea.addEventListener("click", async () => {
    try {
      await ctx.api("/areas", { method: "POST", body: { name: newArea.value } });
      newArea.value = "";
      load();
    } catch (e) {
      toast(e.message, "bad");
    }
  });

  pairBtn.addEventListener("click", () => startPair());

  function startLocalPair() {
    const known = new Set(mics.map((mic) => mic.id));
    const command = h("code", {}, "python3 jarvis-pair.py");
    const status = h("p", { class: "hint" }, "Prüfe die Angaben …");
    const done = h("div", { class: "stack" });
    let timer = 0;
    modal({
      title: "Mikrofon koppeln",
      body: h(
        "div",
        { class: "stack" },
        h("p", { class: "muted" }, "Der Browser gibt USB an einer http-Adresse nicht frei. Das Pair-Programm öffnet die Kopplung lokal, ohne Domain und ohne HTTPS."),
        done,
        status,
        command,
        h("p", { class: "hint" }, "Windows: py -3 jarvis-pair.py. Das Board steckt an diesem Rechner. Es öffnet sich eine Seite in Chrome oder Edge."),
      ),
      actions: [{ label: "Schließen", onClick: (close) => close() }],
      onClose: () => window.clearInterval(timer),
    });
    command.style.setProperty("display", "none");
    (async () => {
      try {
        const info = await ctx.api("/mics/pair");
        if (!info.firmware) throw new Error("Im Container liegt kein Firmware-Abbild.");
        if (!info.device_url) throw new Error("Erst die Adresse von Jarvis im Hausnetz eintragen.");
        if (!info.wifi_ssid) throw new Error("Erst das WLAN für die Mikrofone eintragen.");
        done.append(
          h("a", { class: "btn primary", href: "/api/mics/pair/helper", download: "jarvis-pair.py", style: { "text-decoration": "none" } }, "jarvis-pair.py laden"),
        );
        status.textContent = "Datei laden, im Download-Ordner starten, dann hier warten.";
        command.style.removeProperty("display");
        timer = window.setInterval(async () => {
          try {
            const data = await ctx.api("/mics");
            const fresh = (data.mics || []).find((mic) => !known.has(mic.id));
            if (!fresh) return;
            window.clearInterval(timer);
            status.textContent = `${fresh.name} ist gekoppelt.`;
            const areaSelect = selectEl(areaOptions(), "");
            areaSelect.addEventListener("change", () =>
              ctx.api(`/mics/${fresh.id}`, { method: "PUT", body: { area_id: areaSelect.value || null } }).then(load),
            );
            done.append(h("div", { class: "label" }, "Bereich"), areaSelect);
            load();
          } catch {
            /* keep waiting while the local program runs */
          }
        }, 2000);
      } catch (e) {
        status.textContent = "";
        done.append(h("div", { class: "error-text" }, e.message || String(e)));
      }
    })();
  }

  function startPair() {
    if (!window.isSecureContext) {
      startLocalPair();
      return;
    }
    const steps = [
      ["select", "Board auswählen"],
      ["download", "Firmware laden"],
      ["flash", "Firmware schreiben"],
      ["reset", "Neustart"],
      ["wifi", "WLAN übergeben"],
      ["key", "Schlüssel setzen"],
    ];
    const items = steps.map(([id, label], i) => h("div", { class: "step", dataset: { step: id } }, h("span", { class: "n" }, String(i + 1)), label));
    const bar = h("div", { class: "bar" }, h("i"));
    const logLine = h("div", { class: "hint" }, "");
    const done = h("div", { class: "stack" });
    let current = -1;
    const setStep = (id) => {
      current = steps.findIndex(([s]) => s === id);
      items.forEach((el, i) => {
        el.classList.toggle("active", i === current);
        el.classList.toggle("done", i < current);
      });
    };
    const m = modal({
      title: "Mikrofon koppeln",
      body: h("div", { class: "stack" }, h("div", { class: "steps" }, items), bar, logLine, done),
      actions: [{ label: "Schließen", onClick: (close) => close() }],
    });
    (async () => {
      try {
        const info = await ctx.api("/mics/pair");
        if (!info.firmware) throw new Error("Im Container liegt kein Firmware-Abbild.");
        if (!info.device_url) throw new Error("Erst die Adresse von Jarvis im Hausnetz eintragen.");
        const result = await pair({
          firmwareUrl: `/static/firmware/${info.firmware.factory}`,
          wifi: { ssid: info.wifi_ssid, password: info.wifi_password },
          onStep: setStep,
          onProgress: (p) => bar.firstChild.style.setProperty("width", `${Math.round(p * 100)}%`),
          log: (t) => (logLine.textContent = String(t).slice(0, 120)),
        });
        setStep("key");
        logLine.textContent = `Board ${result.name} unter ${result.ip}`;
        const mic = await ctx.api("/mics/pair", { method: "POST", body: result });
        items.forEach((el) => el.classList.add("done"));
        const areaSelect = selectEl(areaOptions(), "");
        areaSelect.addEventListener("change", () => ctx.api(`/mics/${mic.id}`, { method: "PUT", body: { area_id: areaSelect.value || null } }).then(load));
        done.append(
          h("div", { class: "big gold-text" }, "Gekoppelt."),
          h("p", { class: "muted" }, "USB abziehen, Board an ein USB-C-Netzteil im Raum. Es meldet sich über WLAN."),
          h("div", { class: "label" }, "Bereich"),
          areaSelect,
        );
        load();
      } catch (e) {
        if (e && e.name === "NotFoundError") {
          m.close();
          return;
        }
        done.append(h("div", { class: "error-text" }, e.message || String(e)));
      }
    })();
  }

  // ---------------------------------------------------------------- load
  async function load() {
    try {
      const [micRes, areaRes, zoneRes, settingsRes] = await Promise.all([ctx.api("/mics"), ctx.api("/areas"), ctx.api("/loxone/zones"), ctx.api("/settings")]);
      mics = micRes.mics;
      firmware = micRes.firmware;
      areas = areaRes.areas;
      zones = zoneRes;
      settings = settingsRes.values;
      secrets = settingsRes.secrets;
      renderReco(micRes.recommendation);
      lang.value = settings["stt.language"];
      noise.value = settings["mic.noise_filter"];
      stt.value = settings["stt.model"];
      sens.value = settings["mic.sensitivity"];
      vol.value = settings["announce.volume"];
      silence.value = settings["announce.silence_ms"];
      updSens();
      updVol();
      updSil();
      ssid.value = settings["mics.wifi_ssid"];
      address.value = settings["mics.jarvis_address"];
      wifiState.textContent = secrets["mics.wifi_password"] ? "Passwort gesetzt" : "Kein Passwort gesetzt";
      renderSources();
      renderAreas();
      renderMics();
      dirty = true;
    } catch (e) {
      toast(e.message, "bad");
    }
  }

  load();
  return {
    relayout: () => (dirty = true),
    destroy: () => cancelAnimationFrame(raf),
    onLive(msg) {
      if (msg.type === "level") onLevel(msg);
      if (msg.type === "mic") {
        const i = mics.findIndex((m) => m.id === msg.mic.id);
        if (i >= 0) mics[i] = msg.mic;
        else mics.push(msg.mic);
        renderMics();
      }
    },
  };
}
