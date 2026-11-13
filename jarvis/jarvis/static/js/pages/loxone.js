import { busy, card, clear, countUp, h, pageHead, pill, svg, timeAgo, toast, toggle } from "../dom.js";

const GROUPS = [
  { id: "light", label: "Licht", unit: "Kreise", count: (c) => c.light },
  { id: "climate", label: "Klima", unit: "Zonen", count: (c) => c.climate },
  { id: "shading", label: "Beschattung", unit: "Bausteine", count: (c) => c.shading },
  { id: "scenes", label: "Szenen", unit: "verfügbar", count: (c) => c.scenes },
];
const EXTRA = [
  { id: "gate", label: "Tore" },
  { id: "alarm", label: "Alarm" },
  { id: "other", label: "Sonstiges" },
];

function rightsText(rights) {
  if (!rights) return "–";
  const parts = [];
  if (rights & 4) parts.push("App");
  if (rights & 2) parts.push("Web");
  if (rights & 1) parts.push("Admin!");
  return `Lesen & Steuern (${parts.join(", ") || rights})`;
}

export default function loxonePage(view, ctx) {
  const root = h("div", { class: "page" });
  view.append(root);
  root.append(pageHead("Dein Miniserver. Deine Räume. Eine nahtlose Verbindung."));

  let controls = [];
  let selected = null;
  let data = null;

  // --------------------------------------------------------------- left
  const statusPill = h("span", {}, pill("Verbinde", "dim"));
  const msName = h("div", { class: "muted" }, "Miniserver");
  const host = h("input", { class: "input", maxlength: 120, placeholder: "192.168.1.20" });
  const user = h("input", { class: "input", maxlength: 64, placeholder: "jarvis" });
  const password = h("input", { class: "input", type: "password", autocomplete: "off", placeholder: "Passwort neu setzen" });
  const pwState = h("span", { class: "hint" }, "");
  let tlsValue = false;
  const tls = toggle(false, (v) => (tlsValue = v), { label: "TLS" });
  const connect = h("button", { class: "btn primary", type: "button" }, "Verbinden");
  const info = h("div", { class: "stack", style: { "margin-top": "16px" } });
  const left = h(
    "section",
    { class: "card" },
    h("div", { class: "card-title" }, "Miniserver / Gateway"),
    statusPill,
    h("div", { class: "huge", style: { "margin-top": "16px" } }, "Loxone"),
    msName,
    h("hr", { class: "sep" }),
    h("div", { class: "stack" }, h("div", { class: "label" }, "Adresse"), host, h("div", { class: "label" }, "Benutzer ohne Admin-Recht"), user, h("div", { class: "label" }, "Passwort"), password, pwState, h("div", { class: "row" }, tls, h("span", { class: "small muted" }, "HTTPS/WSS (Gen 2, Zertifikat wird nicht geprüft)"))),
    h("div", { class: "row", style: { "margin-top": "14px" } }, h("span", { class: "spacer" }), connect),
    info,
  );

  // --------------------------------------------------------- topology
  const topo = h("div", { class: "topology" });
  const nodes = {};
  const right = h("section", { class: "card" }, h("div", { class: "card-title" }, h("span", {}, "Live Topologie / Smart Home"), h("span", { class: "chips" }, EXTRA.map((g) => h("button", { class: "btn small", type: "button", on: { click: () => select(g.id) } }, g.label)))), topo);
  root.append(h("div", { class: "grid lox-grid" }, left, right));

  // --------------------------------------------------------- utterance
  const utter = h("div", { class: "utterance" }, "Noch kein Satz verstanden.");
  const utterResult = h("span", {}, "");
  const utterMeta = h("div", { class: "hint" }, "");
  root.append(h("div", { class: "grid", style: { "margin-top": "clamp(14px, 1.2vw, 24px)" } }, h("section", { class: "card" }, h("div", { class: "card-title" }, h("span", {}, "Voice → Intent → Action"), utterResult), utter, utterMeta)));

  // ------------------------------------------------------------ devices
  const devicesBox = h("div", {});
  const devicesTitle = h("span", {}, "Bausteine");
  const bulkOn = h("button", { class: "btn small", type: "button" }, "Alle freigeben");
  const bulkOff = h("button", { class: "btn small", type: "button" }, "Alle sperren");
  const devicesCard = h(
    "section",
    { class: "card", style: { "margin-top": "clamp(14px, 1.2vw, 24px)" } },
    h("div", { class: "card-title" }, devicesTitle, h("span", { class: "row" }, bulkOn, bulkOff)),
    h("p", { class: "hint" }, "Nur freigegebene Bausteine sieht Gemini, und nur diese schaltet Jarvis. Sprechweisen sind weitere Namen, z. B. „indirekte Beleuchtung am Fernseher“. Tore, Alarm und Schlösser verlangen ein gesprochenes Ja."),
    devicesBox,
  );
  root.append(devicesCard);

  // ------------------------------------------------- audioserver & mcp
  const asHost = h("input", { class: "input", maxlength: 400, placeholder: "automatisch aus dem Miniserver" });
  const asState = h("span", {}, pill("–", "dim"));
  const asList = h("div", { class: "stack" });
  const asSave = h("button", { class: "btn", type: "button" }, "Speichern & prüfen");
  const mcpUrl = h("input", { class: "input", maxlength: 300, placeholder: "http://192.168.1.30:PORT/mcp" });
  const mcpState = h("div", { class: "hint" }, "");
  const mcpSave = h("button", { class: "btn", type: "button" }, "Speichern & prüfen");
  root.append(
    h(
      "div",
      { class: "grid lower" },
      card(
        "Audioserver",
        h("div", { class: "stack" }, h("div", { class: "row between" }, h("span", { class: "label" }, "Audioserver"), asState), asList, h("span", { class: "label" }, "Adressen (optional)"), asHost, h("p", { class: "hint" }, "Jarvis übernimmt alle Audioserver aus dem Miniserver und spricht jede Zone über ihren eigenen Server an. Leer lassen, außer die Adressen aus Loxone Config sind von hier nicht erreichbar. Dann alle Adressen mit Komma getrennt eintragen. Jarvis meldet sich mit dem Token des Miniserver-Benutzers an und spielt Antworten als Durchsage. Musik läuft danach weiter."), h("div", { class: "row" }, h("span", { class: "spacer" }), asSave)),
      ),
      card(
        "Loxone Config · MCP-Server",
        h("div", { class: "stack" }, mcpUrl, mcpState, h("p", { class: "hint" }, "In Loxone Config in der Titelleiste den MCP-Server einschalten und die angezeigte Adresse hier eintragen. Config bleibt auf einem Rechner im Haus offen. Jede Änderung sagt Jarvis vorher an und wartet auf dein Ja."), h("div", { class: "row" }, h("span", { class: "spacer" }), mcpSave)),
      ),
    ),
  );

  // ------------------------------------------------------------ render
  function renderTopology(counts) {
    clear(topo);
    const w = topo.clientWidth || 800;
    const hh = topo.clientHeight || 360;
    const cx = w / 2;
    const cy = hh / 2;
    const nodeW = Math.min(230, Math.max(150, w * 0.2));
    const positions = [
      { x: w * 0.06, y: hh * 0.12 },
      { x: w * 0.94 - nodeW, y: hh * 0.12 },
      { x: w * 0.06, y: hh * 0.62 },
      { x: w * 0.94 - nodeW, y: hh * 0.62 },
    ];
    const canvas = svg("svg", { viewBox: `0 0 ${w} ${hh}` });
    const r = Math.min(w, hh) * 0.16;
    canvas.append(
      svg("circle", { class: "ring", cx, cy, r: r * 1.25, "stroke-opacity": 0.18 }),
      svg("circle", { class: "ring", cx, cy, r, "stroke-opacity": 0.7, "stroke-width": 1.5 }),
      svg("circle", { class: "ring", cx, cy, r: r * 0.8, "stroke-opacity": 0.25 }),
    );
    GROUPS.forEach((g, i) => {
      const p = positions[i];
      const nx = i % 2 === 0 ? p.x + nodeW : p.x;
      const ny = p.y + 34;
      const ang = Math.atan2(ny - cy, nx - cx);
      const link = svg("path", { class: "link", d: `M ${cx + Math.cos(ang) * r} ${cy + Math.sin(ang) * r} L ${nx} ${ny}` });
      link.style.setProperty("animation-delay", `${0.15 * i}s`);
      canvas.append(link);
    });
    topo.append(canvas, h("div", { class: "hub" }, h("div", { class: "w" }, "JARVIS"), h("div", { class: "label" }, "Gateway")));
    GROUPS.forEach((g, i) => {
      const p = positions[i];
      const value = h("span", {}, "0");
      const node = h(
        "button",
        { class: `node ${selected === g.id ? "active" : ""}`, type: "button", style: { left: `${p.x}px`, top: `${p.y}px`, width: `${nodeW}px`, "animation-delay": `${0.1 * i}s` } },
        h("div", { class: "label" }, g.label),
        h("div", { class: "v" }, value, ` ${g.unit}`),
      );
      node.addEventListener("click", () => select(g.id === "scenes" ? "light" : g.id));
      topo.append(node);
      nodes[g.id] = node;
      countUp(value, g.count(counts));
    });
  }

  function renderInfo(d) {
    clear(info);
    const row = (label, value) => h("div", {}, h("div", { class: "label" }, label), h("div", { style: { "margin-top": "4px" } }, value));
    info.append(
      row("Verbindung", `Lokal / ${d.tls ? "WSS" : "WebSocket"}`),
      row("Datenpunkte", `${d.datapoints} synchronisiert`),
      row("Letzter Sync", timeAgo(d.last_sync)),
      row("Berechtigungen", rightsText(d.rights)),
    );
    if (d.rights & 1) info.append(h("div", { class: "notice" }, "Dieser Benutzer hat Admin-Recht. Für Jarvis einen eigenen Benutzer ohne Admin anlegen."));
    if (d.error) info.append(h("div", { class: "error-text" }, d.error));
  }

  function renderStatus(d) {
    const s = d.status;
    statusPill.replaceChildren(s === "verbunden" ? pill("Sicher verbunden", "live") : s === "fehler" ? pill("Fehler", "bad") : pill(s, "dim"));
    msName.textContent = d.name ? `Miniserver · ${d.name}` : "Miniserver · Zuhause";
    const as = d.audioserver;
    asState.replaceChildren(as.status === "verbunden" ? pill("Angemeldet", "live") : pill(as.status, as.error ? "bad" : "dim"));
    const servers = as.servers || [];
    asList.replaceChildren(
      ...(servers.length
        ? servers.map((sv) =>
            h(
              "div",
              { class: "row between" },
              h("div", {}, h("div", {}, sv.name), h("div", { class: "hint" }, sv.host), sv.error && sv.status !== "verbunden" ? h("div", { class: "error-text" }, sv.error) : null),
              sv.status === "verbunden" ? pill("Angemeldet", "live") : pill(sv.status, sv.error ? "bad" : "dim"),
            ),
          )
        : [h("p", { class: "muted small" }, "Noch kein Audioserver gefunden. Erscheint, sobald der Miniserver verbunden ist.")]),
    );
    if (!asHost.value) asHost.placeholder = as.host ? `automatisch: ${as.host}` : "automatisch aus dem Miniserver";
    mcpState.textContent = d.mcp.error ? `Letzter Fehler: ${d.mcp.error}` : "";
  }

  function renderLast(last) {
    if (!last) return;
    utter.textContent = `„${last.text}“`;
    utterResult.replaceChildren(pill(last.actions && last.actions.length ? "Ausgeführt" : "Beantwortet", "live"));
    const t = last.timing || {};
    const sec = (ms) => `${(ms / 1000).toLocaleString("de-DE", { maximumFractionDigits: 1, minimumFractionDigits: 1 })} s`;
    const parts = [["Erkennung", t.stt], ["KI", t.brain], ["Stimme", t.voice]].filter(([, ms]) => typeof ms === "number").map(([label, ms]) => `${label} ${sec(ms)}`);
    utterMeta.textContent = `${last.area} · ${last.actions && last.actions.length ? last.actions.join(", ") : last.reply} · ${parts.length ? parts.join(" · ") : `${last.ms} ms`}`;
  }

  function select(group) {
    selected = group;
    for (const [id, node] of Object.entries(nodes)) node.classList.toggle("active", id === group);
    renderDevices();
    devicesCard.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderDevices() {
    clear(devicesBox);
    const label = [...GROUPS, ...EXTRA].find((g) => g.id === selected);
    devicesTitle.textContent = selected ? `Bausteine · ${label ? label.label : ""}` : "Bausteine";
    const list = controls.filter((c) => !selected || c.group === selected);
    bulkOn.disabled = bulkOff.disabled = !selected;
    if (!list.length) {
      devicesBox.append(h("p", { class: "muted small" }, data && data.status === "verbunden" ? "Keine Bausteine in dieser Gruppe." : "Erscheinen, sobald der Miniserver verbunden ist."));
      return;
    }
    const table = h("table", { class: "controls-table" });
    table.append(h("thead", {}, h("tr", {}, ["Kennung", "Name", "Raum", "Art", "Zustand", "Freigabe", "Ja nötig", "Sprechweisen"].map((t) => h("th", {}, t)))));
    const body = h("tbody");
    for (const c of list) {
      const aliases = h("div", { class: "chips" });
      const renderAliases = () => {
        clear(aliases);
        for (const alias of c.aliases) {
          aliases.append(h("span", { class: "chip" }, alias, h("button", { type: "button", "aria-label": "entfernen", on: { click: () => saveControl(c, { aliases: c.aliases.filter((a) => a !== alias) }).then(renderAliases) } }, "×")));
        }
        const input = h("input", { class: "input alias-input", placeholder: "+ Sprechweise", maxlength: 60 });
        input.addEventListener("keydown", (e) => {
          if (e.key === "Enter" && input.value.trim()) saveControl(c, { aliases: [...c.aliases, input.value.trim()] }).then(renderAliases);
        });
        aliases.append(input);
      };
      renderAliases();
      body.append(
        h(
          "tr",
          {},
          h("td", { class: "id" }, c.id),
          h("td", {}, c.child ? h("span", { class: "muted" }, "↳ ") : null, c.name),
          h("td", { class: "muted" }, c.room || "–"),
          h("td", { class: "muted small" }, c.type),
          h("td", { class: "small" }, c.state || "–"),
          h("td", {}, c.secured ? h("span", { class: "hint" }, "Visu-Passwort") : toggle(c.allowed, (v) => saveControl(c, { allowed: v }), { label: "Freigabe" })),
          h("td", {}, toggle(c.critical, (v) => saveControl(c, { critical: v }), { label: "Bestätigung", disabled: c.group === "gate" || c.group === "alarm" })),
          h("td", {}, aliases),
        ),
      );
    }
    table.append(body);
    devicesBox.append(table);
  }

  async function saveControl(c, fields) {
    try {
      const res = await ctx.api(`/loxone/controls/${c.id}`, { method: "PUT", body: fields });
      Object.assign(c, res);
    } catch (e) {
      toast(e.message, "bad");
      renderDevices();
    }
  }

  // ------------------------------------------------------------ actions
  connect.addEventListener("click", () => busy(connect, async () => {
    try {
      await ctx.api("/settings", { method: "PUT", body: { values: { "loxone.host": host.value, "loxone.user": user.value, "loxone.tls": tlsValue } } });
      if (password.value) {
        await ctx.api("/secrets", { method: "PUT", body: { key: "loxone.password", value: password.value } });
        password.value = "";
      }
      await ctx.api("/loxone/reconnect", { method: "POST" });
      toast("Verbinde mit dem Miniserver …");
      setTimeout(load, 2500);
    } catch (e) {
      toast(e.message, "bad");
    }
  }));

  const bulk = (allowed) => async () => {
    if (!selected) return;
    await ctx.api("/loxone/controls/bulk", { method: "POST", body: { group: selected, allowed } });
    await loadControls();
  };
  bulkOn.addEventListener("click", bulk(true));
  bulkOff.addEventListener("click", bulk(false));

  asSave.addEventListener("click", () => busy(asSave, async () => {
    await ctx.api("/settings", { method: "PUT", body: { values: { "audioserver.host": asHost.value } } }).catch((e) => toast(e.message, "bad"));
    const res = await ctx.api("/audioserver/test", { method: "POST" });
    const count = (res.servers || []).length;
    toast(res.ok ? `${count === 1 ? "Audioserver" : `${count} Audioserver`} angemeldet: ${res.host}` : `Audioserver: ${res.error}`, res.ok ? "info" : "bad");
    load();
  }));

  mcpSave.addEventListener("click", () => busy(mcpSave, async () => {
    try {
      await ctx.api("/settings", { method: "PUT", body: { values: { "mcp.url": mcpUrl.value } } });
    } catch (e) {
      toast(e.message, "bad");
      return;
    }
    const res = await ctx.api("/mcp/test", { method: "POST" });
    mcpState.textContent = res.ok
      ? `${res.tools} Werkzeuge, davon ${res.read_only} lesend. ${res.save_tool ? `Speichern auf den Miniserver: ${res.save_tool}.` : "Kein Werkzeug zum Speichern auf den Miniserver: Änderungen bleiben im offenen Projekt."}`
      : `Nicht erreichbar: ${res.error}`;
  }));

  // --------------------------------------------------------------- load
  async function loadControls() {
    const res = await ctx.api("/loxone/controls");
    controls = res.controls;
    renderDevices();
  }

  async function load() {
    try {
      data = await ctx.api("/loxone");
      host.value = data.host || "";
      user.value = data.user || "jarvis";
      tlsValue = !!data.tls;
      tls.querySelector("input").checked = tlsValue;
      const settings = await ctx.api("/settings");
      pwState.textContent = settings.secrets["loxone.password"] ? "Passwort gesetzt" : "Kein Passwort gesetzt";
      asHost.value = settings.values["audioserver.host"] || "";
      mcpUrl.value = settings.values["mcp.url"] || "";
      renderStatus(data);
      renderInfo(data);
      renderTopology(data.counts);
      renderLast(data.last);
      await loadControls();
    } catch (e) {
      toast(e.message, "bad");
    }
  }

  load();
  return {
    relayout: () => data && renderTopology(data.counts),
    onLive(msg) {
      if (msg.type === "utterance") renderLast(msg.utterance);
      if (msg.type === "event" && msg.event.kind === "loxone") load();
    },
  };
}
