#!/usr/bin/env python3
"""USB pairing without HTTPS.

Chrome only allows Web Serial on a secure page. This program serves that page
on http://127.0.0.1, which the browser treats as secure, and reports the
board back to Jarvis. Run it on the computer the board is plugged into.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

JARVIS = "__JARVIS_URL__"
TOKEN = "__JARVIS_TOKEN__"

PAGE = """<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>Jarvis Pair</title>
  <style>
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #12110e; color: #f4efe4; font: 16px/1.45 system-ui, sans-serif; }
    main { width: min(440px, calc(100% - 32px)); }
    h1 { font-size: 1.4rem; font-weight: 560; margin: 0 0 8px; }
    p { color: #c8c0b0; }
    button { background: #e2b657; color: #1a160f; border: 0; border-radius: 999px; padding: 12px 18px; font: inherit; cursor: pointer; }
    button:disabled { opacity: 0.55; cursor: default; }
    .bar { height: 6px; background: #2a261f; border-radius: 99px; margin: 16px 0; overflow: hidden; }
    .bar i { display: block; height: 100%; width: 0; background: #e2b657; }
    .err { color: #ff8d7a; }
  </style>
</head>
<body>
  <main>
    <h1>Mikrofon koppeln</h1>
    <p id="lead">Board per USB an diesen Rechner stecken, dann starten. Chrome oder Edge.</p>
    <div class="bar"><i id="bar"></i></div>
    <button id="go" type="button">Board auswählen</button>
    <p id="out"></p>
  </main>
  <script type="module">
    import { pair } from "/js/flasher.js";
    const wifi = __WIFI_JSON__;
    const out = document.querySelector("#out");
    const button = document.querySelector("#go");
    const bar = document.querySelector("#bar");
    if (!("serial" in navigator)) {
      button.disabled = true;
      out.textContent = "Bitte diese Adresse in Chrome oder Edge öffnen: " + location.href;
    }
    button.addEventListener("click", async () => {
      button.disabled = true;
      out.className = "";
      out.textContent = "Firmware wird geschrieben …";
      try {
        const result = await pair({
          firmwareUrl: "/firmware.bin",
          wifi,
          onStep: (step) => { out.textContent = step; },
          onProgress: (p) => { bar.style.width = Math.round(p * 100) + "%"; },
          log: (line) => { out.textContent = String(line).slice(0, 160); },
        });
        const response = await fetch("/complete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(result),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || data.error || "Jarvis hat die Kopplung abgelehnt.");
        bar.style.width = "100%";
        out.textContent = "Gekoppelt. USB abziehen und das Board an ein Netzteil im Raum. Bereich in der Jarvis-Oberfläche zuordnen. Dieses Fenster kann zu.";
      } catch (err) {
        if (err && err.name === "NotFoundError") {
          button.disabled = false;
          out.textContent = "Abgebrochen.";
          return;
        }
        out.className = "err";
        out.textContent = err.message || String(err);
        button.disabled = false;
      }
    });
  </script>
</body>
</html>
"""


def call(method: str, path: str, body: dict | None = None, timeout: float = 60) -> bytes:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(JARVIS + path, data=data, method=method)
    request.add_header("X-Jarvis", "1")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"Jarvis antwortet {exc.code}: {detail[:300]}") from exc
    except URLError as exc:
        raise SystemExit(f"Jarvis unter {JARVIS} nicht erreichbar: {exc.reason}") from exc


def main() -> None:
    if JARVIS.startswith("__") or TOKEN.startswith("__"):
        raise SystemExit("Dieses Programm aus der Jarvis-Oberfläche laden, nicht aus dem Repository.")
    info = json.loads(call("GET", f"/api/mics/pair/helper/{TOKEN}"))
    firmware = (info.get("firmware") or {}).get("factory")
    if not firmware:
        raise SystemExit("Im Container liegt kein Firmware-Abbild.")
    if not info.get("device_url"):
        raise SystemExit("Erst in der Oberfläche die Adresse von Jarvis im Hausnetz eintragen.")
    if not info.get("wifi_ssid"):
        raise SystemExit("Erst in der Oberfläche das WLAN für die Mikrofone eintragen.")
    files = {
        "/js/flasher.js": call("GET", "/static/js/flasher.js"),
        "/js/improv.js": call("GET", "/static/js/improv.js"),
        "/vendor/esptool.js": call("GET", "/static/vendor/esptool.js"),
        "/firmware.bin": call("GET", f"/static/firmware/{firmware}", timeout=180),
    }
    wifi = json.dumps({"ssid": info["wifi_ssid"], "password": info.get("wifi_password") or ""}).replace("<", "\\u003c")
    page = PAGE.replace("__WIFI_JSON__", wifi)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/":
                body = page.encode("utf-8")
                kind = "text/html; charset=utf-8"
            elif path in files:
                body = files[path]
                kind = "text/javascript; charset=utf-8" if path.endswith(".js") else "application/octet-stream"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0] != "/complete":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except ValueError:
                self.send_error(400)
                return
            try:
                result = call(
                    "POST",
                    f"/api/mics/pair/helper/{TOKEN}",
                    {"ip": payload.get("ip", ""), "name": payload.get("name", "")},
                )
            except SystemExit as exc:
                raw = json.dumps({"detail": str(exc)}).encode("utf-8")
                self.send_response(409)
            else:
                raw = result
                self.send_response(200)
                print("Board gekoppelt. Das Fenster kann zu.")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    host, port = server.server_address
    url = f"http://{host}:{port}/"
    print(f"Pair im Browser: {url}")
    print("Falls sich kein Fenster öffnet, die Adresse in Chrome oder Edge öffnen.")
    threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    print("Fertig.")


if __name__ == "__main__":
    main()
