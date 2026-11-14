import { busy, card, clear, h, pageHead, pill, selectEl, timeAgo, toast, toggle } from "../dom.js";

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
      h("div", { style: { flex: "1" } }, h("div", { class: "row between" }, h("div", { class: "huge" }, "Gemini"), statePill), h("div", { class: "muted" }, "Multimodale Intelligenz · Google")),
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

  // ------------------------------------------------------------ tuning
  const slider = (min, max, step, fmt) => {
    const input = h("input", { type: "range", min: String(min), max: String(max), step: String(step) });
    const label = h("span", { class: "gold-text" }, "");
    const update = () => (label.textContent = fmt(Number(input.value)));
    input.addEventListener("input", update);
    return { input, label, update, row: h("div", { class: "row" }, input, label) };
  };
  const endSilence = slider(300, 1500, 50, (v) => `${v} ms`);
  const followup = slider(0, 20, 1, (v) => (v === 0 ? "aus" : `${v} s`));
  const conversation = slider(15, 300, 15, (v) => (v < 60 ? `${v} s` : `${Math.floor(v / 60)} min${v % 60 ? ` ${v % 60} s` : ""}`));
  const declineHours = slider(0, 24, 1, (v) => (v === 0 ? "nicht merken" : `${v} h`));
  const suggestions = selectEl(
    [
      { value: "reserved", label: "Zurückhaltend · nur wenn es klar passt" },
      { value: "active", label: "Aktiv · bietet öfter etwas an" },
      { value: "off", label: "Aus · keine Gegenfragen" },
    ],
    "reserved",
  );
  const speculative = toggle(false, () => {});
  const quickToggle = toggle(false, () => {});
  const earlyWake = toggle(false, () => {});
  const thinking = selectEl(
    [
      { value: "auto", label: "Automatisch (Flash minimal, Pro niedrig)" },
      { value: "minimal", label: "Minimal · am schnellsten" },
      { value: "low", label: "Niedrig" },
      { value: "medium", label: "Mittel" },
      { value: "high", label: "Hoch · am gründlichsten" },
    ],
    "auto",
  );
  const phraseList = h("div", { class: "stack" });
  const phraseState = h("div", { class: "hint" }, "");
  const phraseBtn = h("button", { class: "btn small", type: "button" }, "Neu formulieren");
  const tuneSave = h("button", { class: "btn primary", type: "button" }, "Speichern");
  const row = (label, control, hint) => [h("div", { class: "label" }, label), h("div", { class: "stack", style: { gap: "4px" } }, control, hint ? h("div", { class: "hint" }, hint) : null)];
  const tuning = card(
    "Feinabstimmung · Reaktion",
    h(
      "div",
      { class: "kv" },
      ...row("Satzende nach", endSilence.row, "So lange Stille gilt als Satzende. Kürzer antwortet schneller, länger lässt Zeit für kurze Denkpausen im Satz."),
      ...row("Vorab-Erkennung", speculative, "Startet die Spracherkennung schon in der ersten kurzen Pause. Ist der Satz bis dahin ein vollständiger Sofortbefehl, antwortet Jarvis sofort, ohne das Satzende abzuwarten. Spart rund 0,3 s."),
      ...row("Nachfrage-Fenster", followup.row, "So lange hört Jarvis nach einer Antwort ohne erneutes „Jarvis“ zu."),
      ...row("Gespräch endet nach", conversation.row, "So lange nach dem letzten „Jarvis“ gehen Rückfragen ohne Aktivierungswort. Danach, und sobald ein Satz nicht an Jarvis gerichtet ist (Gespräch im Raum, Fernseher), muss er wieder mit „Jarvis“ geweckt werden. Der Gesprächsverlauf wird dann geleert."),
      ...row("Sofortantworten", quickToggle, "Licht, Lichtkreise, Stimmungen, Dimmen, Rollos, Uhrzeit und Datum ohne Gemini, in Jarvis' Persönlichkeit."),
      ...row("Nachdenken von Gemini", thinking, "Weniger Nachdenken antwortet schneller, mehr hilft bei komplexen Fragen."),
      ...row("Vorschläge & Gegenfragen", suggestions, "Jarvis bietet passende Folgeaktionen an, etwa abends die Rollos zu den Lichtern. Ein einfaches „Ja“ genügt, ausgeführt wird sofort und lokal."),
      ...row("Abgelehnte Vorschläge merken", declineHours.row, "So lange schlägt Jarvis nach einem „Nein“ dasselbe nicht erneut vor."),
      ...row("Live: früh hinhören", earlyWake, "Erkennt „Jarvis“ schon während du sprichst und zeigt sofort, dass er zuhört."),
    ),
    h("hr", { class: "sep" }),
    h("div", { class: "row between" }, h("span", { class: "label" }, "So klingen Sofortantworten"), phraseBtn),
    phraseList,
    phraseState,
    h("div", { class: "row", style: { "margin-top": "12px" } }, h("span", { class: "spacer" }), tuneSave),
  );
  root.append(h("div", { class: "grid", style: { "margin-top": "clamp(14px, 1.2vw, 24px)" } }, tuning));

  function renderPhrases(res) {
    clear(phraseList);
    for (const text of res.examples) phraseList.append(h("div", { class: "notice" }, `„${text}“`));
    phraseState.textContent = res.generated_at
      ? `Von Gemini aus der Persönlichkeit formuliert, ${timeAgo(res.generated_at)}${res.current ? "" : " · Persönlichkeit seitdem geändert, wird neu formuliert"}.`
      : "Eingebaute Butler-Sätze. Mit Gemini-Schlüssel formuliert Jarvis sie aus deiner Persönlichkeit.";
  }

  phraseBtn.addEventListener("click", () =>
    busy(phraseBtn, async () => {
      try {
        renderPhrases(await ctx.api("/phrases/refresh", { method: "POST" }));
        toast("Sofortantworten neu formuliert");
      } catch (e) {
        toast(e.message, "bad");
      }
    }),
  );

  tuneSave.addEventListener("click", () =>
    busy(tuneSave, async () => {
      try {
        await ctx.api("/settings", {
          method: "PUT",
          body: {
            values: {
              "voice.end_silence_ms": Number(endSilence.input.value),
              "voice.followup_s": Number(followup.input.value),
              "voice.conversation_s": Number(conversation.input.value),
              "brain.thinking": thinking.value,
              "brain.suggestions": suggestions.value,
              "brain.decline_hours": Number(declineHours.input.value),
              "voice.speculative": speculative.querySelector("input").checked,
              "brain.quick": quickToggle.querySelector("input").checked,
              "live.early_wake": earlyWake.querySelector("input").checked,
            },
          },
        });
        toast("Feinabstimmung gespeichert");
      } catch (e) {
        toast(e.message, "bad");
      }
    }),
  );

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
        setTimeout(() => ctx.api("/phrases").then(renderPhrases).catch(() => {}), 8000);
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
      endSilence.input.value = v["voice.end_silence_ms"];
      endSilence.update();
      followup.input.value = v["voice.followup_s"];
      followup.update();
      conversation.input.value = v["voice.conversation_s"];
      conversation.update();
      thinking.value = v["brain.thinking"];
      suggestions.value = v["brain.suggestions"];
      declineHours.input.value = v["brain.decline_hours"];
      declineHours.update();
      speculative.querySelector("input").checked = v["voice.speculative"];
      quickToggle.querySelector("input").checked = v["brain.quick"];
      earlyWake.querySelector("input").checked = v["live.early_wake"];
      ctx.api("/phrases").then(renderPhrases).catch(() => {});
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
