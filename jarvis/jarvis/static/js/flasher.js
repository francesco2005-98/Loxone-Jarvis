// Pair: flash the Jarvis firmware over USB, hand over WiFi with Improv, return the board's address.
// Runs entirely in the browser; the container never touches USB.

import { ESPLoader, Transport } from "../vendor/esptool.js";
import { ImprovSerial, STATE } from "./improv.js";

const ESPRESSIF_VID = 0x303a;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function pairSupported() {
  if (!("serial" in navigator)) return "Pair braucht Chrome oder Edge am Rechner, an dem das Board steckt.";
  if (!window.isSecureContext) return "Pair braucht die HTTPS-Adresse von Coolify. Über http://IP lässt der Browser nicht an USB.";
  return null;
}

async function reacquire(oldPort) {
  for (let i = 0; i < 25; i++) {
    const ports = await navigator.serial.getPorts();
    const match = ports.find((p) => p.getInfo().usbVendorId === ESPRESSIF_VID);
    if (match) return match;
    await sleep(200);
  }
  return oldPort;
}

async function openImprov(port) {
  for (let attempt = 0; attempt < 8; attempt++) {
    const candidate = attempt < 3 ? port : await reacquire(port);
    const improv = new ImprovSerial(candidate);
    try {
      await improv.open();
      return improv;
    } catch {
      await sleep(600);
    }
  }
  throw new Error("Der USB-Anschluss ist nach dem Neustart nicht mehr erreichbar. Kabel kurz ziehen und Pair erneut starten.");
}

export async function pair({ firmwareUrl, wifi, onStep, onProgress, log }) {
  const problem = pairSupported();
  if (problem) throw new Error(problem);
  if (!wifi.ssid) throw new Error("Erst das WLAN für die Mikrofone eintragen.");

  onStep("select");
  const port = await navigator.serial.requestPort({ filters: [{ usbVendorId: ESPRESSIF_VID }] });

  onStep("download");
  const response = await fetch(firmwareUrl, { cache: "no-store" });
  if (!response.ok) throw new Error("Firmware-Abbild fehlt im Container.");
  const firmware = new Uint8Array(await response.arrayBuffer());

  onStep("flash");
  const transport = new Transport(port, false);
  const terminal = { clean() {}, writeLine: (t) => log(t), write: (t) => log(t) };
  const loader = new ESPLoader({ transport, baudrate: 921600, romBaudrate: 115200, terminal, debugLogging: false });
  let chip;
  try {
    chip = await loader.main();
  } catch (err) {
    await transport.disconnect().catch(() => {});
    throw new Error("Board nicht im Flash-Modus. BOOT gedrückt halten, kurz RESET drücken, dann erneut Pair. " + (err.message || ""));
  }
  if (!/ESP32-S3/i.test(chip)) {
    await transport.disconnect().catch(() => {});
    throw new Error(`Das ist kein XIAO ESP32-S3 (${chip}).`);
  }
  await loader.writeFlash({
    fileArray: [{ data: firmware, address: 0x0 }],
    flashSize: "keep",
    flashMode: "keep",
    flashFreq: "keep",
    eraseAll: false,
    compress: true,
    reportProgress: (_i, written, total) => onProgress(written / total),
  });

  onStep("reset");
  await transport.setRTS(true);
  await sleep(100);
  await loader.after("hard_reset");
  await transport.disconnect();
  await sleep(1800);

  onStep("wifi");
  const improv = await openImprov(port);
  try {
    const state = await improv.waitReady(25000);
    if (state !== STATE.READY && state !== STATE.PROVISIONED) throw new Error("Das Board ist nicht bereit für WLAN.");
    const urls = await improv.provision(wifi.ssid, wifi.password || "");
    const url = new URL(urls[0]);
    const name = url.searchParams.get("name");
    if (!name) throw new Error("Das Board hat keinen Namen gemeldet.");
    return { ip: url.hostname, name };
  } finally {
    await improv.close();
  }
}
