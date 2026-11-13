// Makes Jarvis installable as an app. Nothing is cached: the interface always comes from the house.
const OFFLINE = `<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Jarvis</title><body style="margin:0;height:100vh;display:grid;place-items:center;background:#06080a;color:#f2c21b;font:600 18px system-ui;letter-spacing:.2em">
JARVIS IST NICHT ERREICHBAR</body></html>`;

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (event) => {
  if (event.request.mode !== "navigate") return;
  event.respondWith(fetch(event.request).catch(() => new Response(OFFLINE, { headers: { "Content-Type": "text/html; charset=utf-8" } })));
});
