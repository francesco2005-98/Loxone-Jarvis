// Improv Wi-Fi over Web Serial (https://www.improv-wifi.com/serial/).
// The board's log output shares the port, so packets are searched in the byte stream.

const HEADER = [0x49, 0x4d, 0x50, 0x52, 0x4f, 0x56]; // "IMPROV"
const TYPE_STATE = 0x01;
const TYPE_ERROR = 0x02;
const TYPE_RPC = 0x03;
const TYPE_RESULT = 0x04;
const RPC_WIFI = 0x01;
const RPC_STATE = 0x02;
export const STATE = { READY: 0x02, PROVISIONING: 0x03, PROVISIONED: 0x04 };
const ERRORS = {
  0x01: "Ungültiger Befehl",
  0x02: "Unbekannter Befehl",
  0x03: "WLAN-Verbindung fehlgeschlagen. SSID und Passwort prüfen, nur 2,4 GHz.",
  0x05: "Ungültiger Name",
  0xff: "Unbekannter Fehler",
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export class ImprovSerial {
  constructor(port) {
    this.port = port;
    this.buffer = [];
    this.waiters = [];
    this.state = null;
    this.error = 0;
    this.closed = false;
  }

  async open() {
    await this.port.open({ baudRate: 115200, bufferSize: 8192 });
    this.reader = this.port.readable.getReader();
    this.writer = this.port.writable.getWriter();
    this.loop = this.readLoop();
  }

  async readLoop() {
    try {
      while (!this.closed) {
        const { value, done } = await this.reader.read();
        if (done) break;
        if (value) {
          for (const byte of value) this.buffer.push(byte);
          this.parse();
        }
      }
    } catch {
      // port closed
    }
  }

  parse() {
    for (;;) {
      const start = this.findHeader();
      if (start < 0) {
        if (this.buffer.length > 4096) this.buffer.splice(0, this.buffer.length - 16);
        return;
      }
      if (start > 0) this.buffer.splice(0, start);
      if (this.buffer.length < 9) return;
      const length = this.buffer[8];
      const total = 9 + length + 1;
      if (this.buffer.length < total) return;
      const packet = this.buffer.splice(0, total);
      const sum = packet.slice(0, total - 1).reduce((a, b) => a + b, 0) & 0xff;
      if (sum !== packet[total - 1]) continue;
      this.dispatch(packet[7], packet.slice(9, 9 + length));
    }
  }

  findHeader() {
    outer: for (let i = 0; i + HEADER.length <= this.buffer.length; i++) {
      for (let j = 0; j < HEADER.length; j++) if (this.buffer[i + j] !== HEADER[j]) continue outer;
      return i;
    }
    return -1;
  }

  dispatch(type, data) {
    if (type === TYPE_STATE) this.state = data[0];
    if (type === TYPE_ERROR) this.error = data[0];
    for (const waiter of [...this.waiters]) waiter(type, data);
  }

  async send(type, data) {
    const packet = [...HEADER, 0x01, type, data.length, ...data];
    packet.push(packet.reduce((a, b) => a + b, 0) & 0xff, 0x0a);
    await this.writer.write(new Uint8Array(packet));
  }

  wait(predicate, timeoutMs, timeoutMessage) {
    return new Promise((resolve, reject) => {
      const waiter = (type, data) => {
        let result;
        try {
          result = predicate(type, data);
        } catch (err) {
          cleanup();
          reject(err);
          return;
        }
        if (result !== undefined) {
          cleanup();
          resolve(result);
        }
      };
      const timer = setTimeout(() => {
        cleanup();
        reject(new Error(timeoutMessage));
      }, timeoutMs);
      const cleanup = () => {
        clearTimeout(timer);
        this.waiters = this.waiters.filter((w) => w !== waiter);
      };
      this.waiters.push(waiter);
    });
  }

  async waitReady(timeoutMs = 20000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      await this.send(TYPE_RPC, [RPC_STATE, 0]);
      try {
        return await this.wait((type, data) => (type === TYPE_STATE ? data[0] : undefined), 1500, "timeout");
      } catch {
        await sleep(300);
      }
    }
    throw new Error("Das Board antwortet nicht auf Improv. Läuft die Jarvis-Firmware?");
  }

  async provision(ssid, password, timeoutMs = 45000) {
    const enc = new TextEncoder();
    const s = [...enc.encode(ssid)];
    const p = [...enc.encode(password)];
    const payload = [s.length, ...s, p.length, ...p];
    const result = this.wait(
      (type, data) => {
        if (type === TYPE_ERROR && data[0] !== 0) throw new Error(ERRORS[data[0]] || "Fehler");
        if (type === TYPE_RESULT && data[0] === RPC_WIFI) return parseStrings(data.slice(2));
        return undefined;
      },
      timeoutMs,
      "Das Board kommt nicht ins WLAN.",
    );
    await this.send(TYPE_RPC, [RPC_WIFI, payload.length, ...payload]);
    return result;
  }

  async close() {
    this.closed = true;
    try {
      await this.reader.cancel();
    } catch {}
    try {
      this.reader.releaseLock();
      this.writer.releaseLock();
    } catch {}
    try {
      await this.port.close();
    } catch {}
  }
}

function parseStrings(bytes) {
  const out = [];
  const dec = new TextDecoder();
  let i = 0;
  while (i < bytes.length) {
    const len = bytes[i];
    out.push(dec.decode(new Uint8Array(bytes.slice(i + 1, i + 1 + len))));
    i += 1 + len;
  }
  return out;
}
