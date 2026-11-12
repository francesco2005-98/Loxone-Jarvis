export class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

export async function api(path, { method = "GET", body, raw = false } = {}) {
  const options = { method, headers: {}, credentials: "same-origin", cache: "no-store" };
  if (method !== "GET") options.headers["X-Jarvis"] = "1";
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const res = await fetch(`/api${path}`, options);
  if (res.status === 401 && !["/login", "/password"].includes(path)) {
    window.dispatchEvent(new CustomEvent("jarvis:logout"));
  }
  if (raw) {
    if (!res.ok) throw new ApiError(`Fehler ${res.status}`, res.status);
    return res;
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    let message = data.detail || data.error || `Fehler ${res.status}`;
    if (Array.isArray(message)) message = "Eingabe ungültig";
    if (data.fields) message = Object.values(data.fields).join(", ");
    throw new ApiError(message, res.status, data);
  }
  return data;
}

// Live channel with automatic reconnect. Messages are dispatched by type.
export class Live {
  constructor() {
    this.handlers = new Set();
    this.ws = null;
    this.delay = 1000;
    this.closed = false;
  }

  on(handler) {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  connect() {
    this.closed = false;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/api/live`);
    this.ws = ws;
    ws.onmessage = (event) => {
      let msg;
      try {
        msg = JSON.parse(event.data);
      } catch {
        return;
      }
      for (const handler of this.handlers) {
        try {
          handler(msg);
        } catch (err) {
          console.error(err);
        }
      }
    };
    ws.onopen = () => {
      this.delay = 1000;
    };
    ws.onclose = () => {
      if (this.closed) return;
      setTimeout(() => this.connect(), this.delay);
      this.delay = Math.min(this.delay * 2, 15000);
    };
  }

  close() {
    this.closed = true;
    if (this.ws) this.ws.close();
  }
}
