import { busy, card, h, pageHead, pill, selectEl, toast } from "../dom.js";

export default function ai(view, ctx) {
  const root = h("div", { class: "page" });
  view.append(root);
  root.append(pageHead("Gemini deutet jeden Satz. Dein Haus bleibt bei dir."));

  // --------------------------------------------------------------- gemini
  const statePill = h("span", {}, pill("Nicht geprüft", "dim"));
  const keyState = h("span", { class: "mask" }, "–");
  const keyInput = h("input", { class: "input", type: "password", autocomplete: "off", placeholder: "Neuen Schlüssel aus Google AI Studio einfügen" });
  const keySave = h("button", { class: "btn", type: "button" }, "Setzen");
  const modelSelect = selectEl([], "");
  const prompt = h("textarea", { class: "input", rows: 7, maxlength: 4000 });
  const testBtn = h("button", { class: "btn primary", type: "button" }, "Verbindung prüfen");
  const saveBtn = h("button", { class: "btn", type: "button" }, "Speichern");
  const testResult = h("div", { class: "hint" }, "");

  const gemini = h(
    "section",
    { class: "card gold" },
    h(
      "div",
      { class: "provider" },
      h("div", { class: "badge-letter" }, "G"),
      h("div", { style: { flex: "1" } }, h("div", { class: "row between" }, h("div", { class: "huge" }, "Gemini"), statePill), h("div", { class: "muted" }, "Multimodale Intelligenz · Google"), h("div", { class: "label", style: { "margin-top": "6px" } }, "Einziger Anbieter. Keine weiteren Konten.")),
    ),
    h("hr", { class: "sep" }),
    h(
      "div",
      { class: "kv" },
      h("div", { class: "label" }, "API-Schlüssel"),
      h("div", { class: "stack" }, keyState, h("div", { class: "row" }, keyInput, keySave)),
      h("div", { class: "label" }, "Modell"),
      h("div", { class: "stack" }, modelSelect, h("div", { class: "hint" }, "Voreinstellung gemini-3.8-flash. Die Liste kommt direkt von Google.")),
      h("div", { class: "label" }, "System-Prompt"),
      h("div", { class: "stack" }, prompt, h("div", { class: "hint" }, "Persönlichkeit und Ton. Die Sicherheitsregeln hängt Jarvis immer selbst an.")),
    ),
    h("div", { class: "row", style: { "margin-top": "18px" } }, testResult, h("span", { class: "spacer" }), saveBtn, testBtn),
  );

  // --------------------------------------------------------------- voices
  const voiceDe = selectEl([], "");
  const voiceEn = selectEl([], "");
  const speed = h("input", { type: "range", min: "0.7", max: "1.4", step: "0.05" });
  const speedLabel = h("span", { class: "gold-text" }, "");
  const playDe = h("button", { class: "btn small", type: "button" }, "Probehören");
  const playEn = h("button", { class: "btn small", type: "button" }, "Probehören");
  const voiceSave = h("button", { class: "btn", type: "button" }, "Stimmen speichern");
  const voiceHint = h("div", { class: "hint" }, "");
  const voices = card(
    "Stimmen · Piper lokal",
    h(
      "div",
      { class: "kv" },
      h("div", { class: "label" }, "Deutsch"),
      h("div", { class: "row" }, voiceDe, playDe),
      h("div", { class: "label" }, "Englisch"),
      h("div", { class: "row" }, voiceEn, playEn),
      h("div", { class: "label" }, "Tempo"),
      h("div", { class: "row" }, speed, speedLabel),
    ),
    h("p", { class: "hint" }, "Jarvis antwortet mit der Stimme der Sprache, in der du ihn ansprichst. Die Stimmen liegen im Container, nichts geht ins Netz."),
    voiceHint,
    h("div", { class: "row" }, h("span", { class: "spacer" }), voiceSave),
  );

  root.append(h("div", { class: "grid ai-grid" }, gemini, voices));

  const audio = new Audio();

  async function preview(lang, select, button) {
    await busy(button, async () => {
      try {
        const res = await ctx.api("/tts/preview", { method: "POST", body: { lang, voice: select.value }, raw: true });
        const blob = await res.blob();
        if (audio.src) URL.revokeObjectURL(audio.src);
        audio.src = URL.createObjectURL(blob);
        await audio.play();
      } catch (e) {
        toast(`Piper: ${e.message}`, "bad");
      }
    });
  }
  playDe.addEventListener("click", () => preview("de", voiceDe, playDe));
  playEn.addEventListener("click", () => preview("en", voiceEn, playEn));
  speed.addEventListener("input", () => {
    speedLabel.textContent = `${Number(speed.value).toFixed(2)}×`;
  });

  keySave.addEventListener("click", () =>
    busy(keySave, async () => {
      try {
        await ctx.api("/secrets", { method: "PUT", body: { key: "gemini.api_key", value: keyInput.value } });
        keyInput.value = "";
        toast("Schlüssel gespeichert");
        await loadModels();
        await load();
      } catch (e) {
        toast(e.message, "bad");
      }
    }),
  );

  saveBtn.addEventListener("click", () =>
    busy(saveBtn, async () => {
      try {
        await ctx.api("/settings", { method: "PUT", body: { values: { "gemini.model": modelSelect.value, "gemini.system_prompt": prompt.value } } });
        toast("Gespeichert");
      } catch (e) {
        toast(e.message, "bad");
      }
    }),
  );

  testBtn.addEventListener("click", () =>
    busy(testBtn, async () => {
      testResult.textContent = "Prüfe …";
      const res = await ctx.api("/ai/test", { method: "POST" });
      statePill.replaceChildren(res.ok ? pill("Verbunden", "live") : pill("Fehler", "bad"));
      testResult.textContent = res.ok ? `${res.model} antwortet in ${res.ms} ms.` : res.error;
    }),
  );

  voiceSave.addEventListener("click", () =>
    busy(voiceSave, async () => {
      try {
        await ctx.api("/settings", { method: "PUT", body: { values: { "voice.de": voiceDe.value, "voice.en": voiceEn.value, "voice.speed": Number(speed.value) } } });
        toast("Stimmen gespeichert");
      } catch (e) {
        toast(e.message, "bad");
      }
    }),
  );

  function fill(select, items, value) {
    select.replaceChildren(...items.map((it) => {
      const o = h("option", { value: it.value }, it.label);
      if (it.value === value) o.selected = true;
      return o;
    }));
  }

  async function loadModels() {
    const res = await ctx.api("/ai/models");
    fill(modelSelect, res.models.map((m) => ({ value: m, label: m })), res.current);
    if (res.error) testResult.textContent = res.error;
  }

  async function load() {
    try {
      const settings = await ctx.api("/settings");
      const v = settings.values;
      keyState.textContent = settings.secrets["gemini.api_key"] ? "•••• •••• •••• ••••" : "Noch kein Schlüssel";
      prompt.value = v["gemini.system_prompt"];
      speed.value = v["voice.speed"];
      speedLabel.textContent = `${Number(v["voice.speed"]).toFixed(2)}×`;
      fill(modelSelect, [{ value: v["gemini.model"], label: v["gemini.model"] }], v["gemini.model"]);
      const res = await ctx.api("/voices");
      const label = (voice) => `${voice.name} · ${voice.quality} (${voice.locale})`;
      const de = res.voices.filter((x) => x.lang === "de").map((x) => ({ value: x.id, label: label(x) }));
      const en = res.voices.filter((x) => x.lang === "en").map((x) => ({ value: x.id, label: label(x) }));
      fill(voiceDe, de.length ? de : [{ value: v["voice.de"], label: v["voice.de"] }], v["voice.de"]);
      fill(voiceEn, en.length ? en : [{ value: v["voice.en"], label: v["voice.en"] }], v["voice.en"]);
      voiceHint.textContent = res.error ? `Piper nicht erreichbar: ${res.error}` : "";
      if (settings.secrets["gemini.api_key"]) loadModels().catch(() => {});
    } catch (e) {
      toast(e.message, "bad");
    }
  }

  load();
  return {
    destroy() {
      audio.pause();
      if (audio.src) URL.revokeObjectURL(audio.src);
    },
  };
}
