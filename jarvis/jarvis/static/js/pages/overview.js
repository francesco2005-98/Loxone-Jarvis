import { card, clear, confirmDialog, countUp, h, modal, pageHead, pill, selectEl, timeAgo, toast } from "../dom.js";

const ANREDE = [
  { value: "name", label: "Eigener Name" },
  { value: "sir", label: "Sir" },
  { value: "mam", label: "Mam" },
];

export default function overview(view, ctx) {
  const root = h("div", { class: "page" });
  view.append(root);
  let areas = [];
  let mics = [];
  let sentences = [];
  let enrollBox = null;

  const head = pageHead("Verbinde die Systeme, dann hört Jarvis zu.", pill("Live connection", "live"));
  const leadEl = head.querySelector(".lead");
  root.append(head);

  const sysAi = sysItem("KI-Engine");
  const sysVoice = sysItem("Voice Network");
  const sysLox = sysItem("Loxone Link");
  const left = card("Systemübersicht", sysAi.el, sysVoice.el, sysLox.el);

  const coreLabel = h("div", { class: "core-label idle" }, "Verbinde …");
  const slot = h(
    "div",
    { class: "core-slot" },
    h("div", { class: "core-center" }, h("div", { class: "w" }, "JARVIS"), h("div", { class: "s" }, "NEURAL CORE")),
    coreLabel,
  );
  const activity = h("div", {});
  const right = card("Live-Aktivität", activity);
  root.append(h("div", { class: "grid ov-grid" }, left, slot, right));

  const stat = (label, unit) => {
    const value = h("span", { class: "huge" }, "–");
    const unitEl = h("span", { class: "unit" }, unit);
    const el = h("section", { class: "card stat" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value, unitEl));
    return { el, value, unitEl };
  };
  const stResponse = stat("Antwortzeit", "ms");
  const stRooms = stat("Vernetzte Räume", "Räume");
  const stActions = stat("Freigegebene Aktionen", "aktiv");
  const stOnline = stat("Systemstatus", "% online");
  root.append(h("div", { class: "grid stats" }, stResponse.el, stRooms.el, stActions.el, stOnline.el));

  const usageBox = h("div", { class: "usage-line" });
  root.append(
    h("div", { class: "grid", style: { "margin-top": "clamp(14px, 1.2vw, 24px)" } }, card("Gemini · dieser Monat", usageBox)),
  );

  const peopleList = h("div", {});
  const peopleCard = card(
    h("span", {}, "Personen"),
    h("p", { class: "hint" }, "Erkennt Jarvis die Stimme sicher, benutzt er die Anrede und die Notizen dieser Person. Sonst bleibt er neutral und fragt nicht nach."),
    peopleList,
    h("div", { class: "row", style: { "margin-top": "14px" } }, h("button", { class: "btn", type: "button", on: { click: () => editPerson() } }, "Person anlegen")),
  );
  const notes = h("textarea", { class: "input", rows: 6, placeholder: "z. B. Wir essen meistens um 19 Uhr. Die Katze heißt Mila." });
  const tzSelect = selectEl(timezones(), "Europe/Berlin");
  const saveHouse = h("button", { class: "btn primary", type: "button" }, "Speichern");
  saveHouse.addEventListener("click", async () => {
    try {
      await ctx.api("/settings", { method: "PUT", body: { values: { "house.notes": notes.value, "house.timezone": tzSelect.value } } });
      toast("Haushalt gespeichert");
    } catch (e) {
      toast(e.message, "bad");
    }
  });
  const houseCard = card(
    "Haushalt",
    h("div", { class: "stack" }, h("div", { class: "label" }, "Notizen des Haushalts"), notes, h("div", { class: "label" }, "Zeitzone für Timer und Erinnerungen"), tzSelect, h("div", { class: "row" }, h("span", { class: "spacer" }), saveHouse)),
  );
  root.append(h("div", { class: "grid lower" }, peopleCard, houseCard));

  // ------------------------------------------------------------------ data
  function sysItem(label) {
    const big = h("div", { class: "big" }, "–");
    const sub = h("div", { class: "muted small" }, "");
    const el = h("div", { class: "sys-item off" }, h("div", { class: "label" }, label), big, sub);
    return { el, big, sub };
  }

  function renderStatus(status) {
    if (!status) return;
    const g = status.gemini;
    sysAi.big.textContent = "Gemini";
    sysAi.sub.textContent = !g.configured ? "Schlüssel fehlt" : g.error ? `Fehler: ${g.error}` : `${g.model} · bereit`;
    sysAi.el.classList.toggle("off", !g.configured || !!g.error);
    sysVoice.big.textContent = `${String(status.mics.online).padStart(2, "0")} / ${String(status.mics.total).padStart(2, "0")}`;
    sysVoice.sub.textContent = status.mics.total ? "Mikrofone verfügbar" : "Noch kein Mikrofon gekoppelt";
    sysVoice.el.classList.toggle("off", status.mics.online === 0);
    const connected = status.loxone.status === "verbunden";
    sysLox.big.textContent = connected ? "Verbunden" : status.loxone.status.charAt(0).toUpperCase() + status.loxone.status.slice(1);
    sysLox.sub.textContent = connected ? `Miniserver · ${status.loxone.name || "Lokal"}` : status.loxone.error || "Miniserver";
    sysLox.el.classList.toggle("off", !connected);
    const allOk = connected && g.configured && !g.error && status.mics.online > 0;
    leadEl.textContent = allOk
      ? "Alle Systeme verbunden. Bereit für deinen nächsten Gedanken."
      : "Noch nicht alle Systeme verbunden. Die Seiten KI, Mikrofone und Loxone führen dich durch.";
    countUp(stResponse.value, status.response_ms ?? "–");
    countUp(stRooms.value, status.counts.rooms);
    countUp(stActions.value, status.counts.allowed);
    countUp(stOnline.value, status.reachable_pct);
    renderPhase();
  }

  function renderPhase() {
    const status = ctx.status;
    if (!status) return;
    const active = Object.entries(ctx.phases).find(([, p]) => p !== "idle");
    const areaName = (id) => (areas.find((a) => a.id === id) || {}).name || "";
    let text = status.listening ? "Bereit zuzuhören" : "Kein Mikrofon verbunden";
    let idle = !status.listening;
    if (active) {
      const [area, phase] = active;
      idle = false;
      text = {
        collecting: `Hört zu · ${areaName(area)}`,
        listening: `Hört zu · ${areaName(area)}`,
        thinking: "Denkt nach",
        speaking: `Spricht · ${areaName(area)}`,
        followup: `Wartet auf Antwort · ${areaName(area)}`,
      }[phase] || text;
    }
    coreLabel.textContent = text;
    coreLabel.classList.toggle("idle", idle);
  }

  function renderEvents(events) {
    clear(activity);
    if (!events.length) activity.append(h("p", { class: "muted small" }, "Noch keine Ereignisse."));
    for (const ev of events.slice(0, 7)) activity.append(eventRow(ev, false));
  }

  function eventRow(ev, fresh) {
    return h(
      "div",
      { class: `activity-item ${fresh ? "fresh" : ""}` },
      h("div", {}, h("div", { class: "t" }, ev.title), h("div", { class: "d" }, [ev.detail, timeAgo(ev.ts)].filter(Boolean).join(" · "))),
      h("span", { class: "dot" }),
    );
  }

  function renderUsage(u) {
    clear(usageBox);
    const item = (label, value, extra) =>
      h("div", {}, h("div", { class: "label" }, label), h("div", {}, h("span", { class: "n" }, Number(value).toLocaleString("de-DE")), extra ? h("span", { class: "muted small" }, ` ${extra}`) : null));
    usageBox.append(
      item("Anfragen", u.requests),
      item("Tokens ein", u.input_tokens),
      item("Tokens aus", u.output_tokens),
      item("Suchanfragen", u.search_queries, `/ ${Number(u.free_searches).toLocaleString("de-DE")} frei`),
      h("div", { class: "hint", style: { "align-self": "center" } }, "Wird nur angezeigt. Ein volles Kontingent schaltet nichts ab."),
    );
  }

  function renderPeople(people) {
    clear(peopleList);
    if (!people.length) peopleList.append(h("p", { class: "muted small" }, "Noch niemand angelegt."));
    for (const person of people) {
      const anrede = (ANREDE.find((a) => a.value === person.anrede) || {}).label;
      peopleList.append(
        h(
          "div",
          { class: "person" },
          h("div", {}, h("div", { class: "name" }, person.name), h("div", { class: "muted small" }, `Anrede: ${anrede === "Eigener Name" ? person.name : anrede}`), person.notes ? h("div", { class: "small", style: { "margin-top": "4px", color: "var(--text-2)" } }, person.notes) : null),
          h(
            "div",
            { class: "row wrap", style: { "justify-content": "flex-end", "align-self": "start" } },
            person.has_voice ? pill("Stimmprofil", "") : pill("Ohne Stimme", "dim"),
            h("button", { class: "btn small", type: "button", on: { click: () => enrollPerson(person) } }, person.has_voice ? "Neu aufnehmen" : "Stimme aufnehmen"),
            h("button", { class: "btn small", type: "button", on: { click: () => editPerson(person) } }, "Bearbeiten"),
            h("button", { class: "btn small danger", type: "button", on: { click: () => removePerson(person) } }, "Löschen"),
          ),
        ),
      );
    }
  }

  function editPerson(person = null) {
    const name = h("input", { class: "input", value: person ? person.name : "", maxlength: 60, placeholder: "Name" });
    const anrede = selectEl(ANREDE, person ? person.anrede : "name");
    const notesField = h("textarea", { class: "input", rows: 5, maxlength: 4000, placeholder: "Vorlieben, z. B. mag es abends gedimmt, hört gern Jazz." });
    notesField.value = person ? person.notes : "";
    modal({
      title: person ? "Person bearbeiten" : "Person anlegen",
      body: h("div", { class: "stack" }, h("div", { class: "label" }, "Name"), name, h("div", { class: "label" }, "Anrede"), anrede, h("div", { class: "label" }, "Notizen"), notesField),
      actions: [
        { label: "Abbrechen", onClick: (close) => close() },
        {
          label: "Speichern",
          kind: "primary",
          onClick: async (close) => {
            try {
              const body = { name: name.value, anrede: anrede.value, notes: notesField.value };
              await ctx.api(person ? `/people/${person.id}` : "/people", { method: person ? "PUT" : "POST", body });
              close();
              loadPeople();
            } catch (e) {
              toast(e.message, "bad");
            }
          },
        },
      ],
    });
  }

  async function removePerson(person) {
    if (!(await confirmDialog("Person löschen", `${person.name} und das Stimmprofil werden gelöscht.`, "Löschen"))) return;
    await ctx.api(`/people/${person.id}`, { method: "DELETE" });
    loadPeople();
  }

  function enrollPerson(person) {
    const online = mics.filter((m) => m.online);
    if (!online.length) {
      toast("Erst ein Mikrofon koppeln und verbinden.", "bad");
      return;
    }
    const micSelect = selectEl(online.map((m) => ({ value: m.id, label: m.name })), online[0].id);
    const sentence = h("div", { class: "utterance" }, "");
    const state = h("div", { class: "muted small" }, "Stell dich nah an das Board und sprich in normaler Lautstärke.");
    const bar = h("div", { class: "bar" }, h("i"));
    let started = false;
    const m = modal({
      title: `Stimmprofil für ${person.name}`,
      body: h("div", { class: "stack" }, h("div", { class: "label" }, "Mikrofon"), micSelect, h("p", { class: "hint" }, "Drei kurze Sätze. Die Aufnahme bleibt im Haus, gespeichert wird nur ein Stimmabdruck."), sentence, bar, state),
      actions: [
        { label: "Schließen", onClick: (close) => close() },
        {
          label: "Start",
          kind: "primary",
          onClick: async () => {
            if (started) return;
            try {
              const res = await ctx.api(`/people/${person.id}/enroll`, { method: "POST", body: { mic_id: micSelect.value } });
              sentences = res.sentences;
              started = true;
              micSelect.disabled = true;
            } catch (e) {
              toast(e.message, "bad");
            }
          },
        },
      ],
      onClose: () => {
        if (started) ctx.api("/people/enroll/cancel", { method: "POST", body: { mic_id: micSelect.value } }).catch(() => {});
        enrollBox = null;
        loadPeople();
      },
    });
    enrollBox = { person, sentence, state, bar, close: m.close };
  }

  function onEnroll(msg) {
    if (!enrollBox || msg.person !== enrollBox.person.id) return;
    enrollBox.bar.firstChild.style.setProperty("width", `${(msg.step / msg.total) * 100}%`);
    if (msg.state === "listening") {
      enrollBox.sentence.textContent = `„${msg.sentence}“`;
      enrollBox.state.textContent = `Satz ${msg.step + 1} von ${msg.total}: jetzt vorlesen.`;
    } else if (msg.state === "retry") {
      enrollBox.state.textContent = "Nicht verstanden, bitte den Satz noch einmal.";
    } else if (msg.state === "done") {
      enrollBox.sentence.textContent = "Fertig.";
      enrollBox.state.textContent = "Stimmprofil gespeichert.";
    } else if (msg.state === "failed") {
      enrollBox.state.textContent = "Abgebrochen: zu leise oder zu kurz. Näher ans Board und erneut starten.";
    }
  }

  async function loadPeople() {
    const res = await ctx.api("/people");
    sentences = res.sentences;
    renderPeople(res.people);
  }

  async function load() {
    try {
      const [data, areaRes, micRes] = await Promise.all([ctx.api("/overview"), ctx.api("/areas"), ctx.api("/mics")]);
      areas = areaRes.areas;
      mics = micRes.mics;
      ctx.status = data.status;
      renderStatus(data.status);
      renderEvents(data.events);
      renderUsage(data.usage);
      notes.value = data.notes || "";
      tzSelect.value = data.timezone;
      loadPeople();
    } catch (e) {
      toast(e.message, "bad");
    }
  }

  // ----------------------------------------------------------------- globe
  let raf = 0;
  function place(instant = false) {
    const r = slot.getBoundingClientRect();
    const usable = r.height - 34;
    ctx.placeGlobe(r.left + r.width / 2, r.top + usable / 2, Math.max(110, Math.min(r.width, usable) * 0.46), 1, instant);
  }
  const onScroll = () => {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      place(true);
    });
  };
  window.addEventListener("scroll", onScroll, { passive: true });
  requestAnimationFrame(() => place(false));
  load();

  return {
    relayout: () => place(true),
    destroy: () => window.removeEventListener("scroll", onScroll),
    onLive(msg) {
      if (msg.type === "hello" || msg.type === "status") renderStatus(msg.status);
      if (msg.type === "phase") renderPhase();
      if (msg.type === "event") {
        if (activity.firstChild && activity.firstChild.tagName === "P") clear(activity);
        activity.prepend(eventRow(msg.event, true));
        while (activity.children.length > 7) activity.lastChild.remove();
      }
      if (msg.type === "enroll") onEnroll(msg);
      if (msg.type === "mic") {
        const i = mics.findIndex((m) => m.id === msg.mic.id);
        if (i >= 0) mics[i] = msg.mic;
        else mics.push(msg.mic);
      }
    },
  };
}

function timezones() {
  try {
    return Intl.supportedValuesOf("timeZone");
  } catch {
    return ["Europe/Berlin", "Europe/Vienna", "Europe/Zurich", "Europe/London", "UTC"];
  }
}
