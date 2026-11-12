"""XIAO ESP32-S3 Sense boards running the Jarvis ESPHome firmware.

Jarvis is the API client. Each board has its own Noise key, set once during
Pair over an all-zeros-key connection and stored encrypted. Unknown boards
are never connected.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..house import House

log = logging.getLogger(__name__)

API_PORT = 6053
KEY_PREFIX = "mic.key."
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{1,62}$")


class PairError(RuntimeError):
    pass


@dataclass
class Mic:
    id: str
    name: str
    label: str
    mac: str
    host: str | None
    area_id: str | None
    firmware: str | None
    last_seen: float | None
    client: Any = None
    reconnect: Any = None
    connected: bool = False
    switch_key: int | None = None
    number_key: int | None = None
    button_key: int | None = None
    services: dict[str, Any] = field(default_factory=dict)
    unsubscribe: Any = None
    level: float = 0.0

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.label,
            "device": self.name,
            "mac": self.mac,
            "host": self.host,
            "area_id": self.area_id,
            "firmware": self.firmware,
            "online": self.connected,
            "last_seen": self.last_seen,
        }


class MicManager:
    def __init__(self, house: "House"):
        self.house = house
        self.mics: dict[str, Mic] = {}

    # ------------------------------------------------------------ storage
    def load(self) -> None:
        for row in self.house.db.all("SELECT * FROM mics ORDER BY created_at"):
            mic = Mic(
                id=row["id"],
                name=row["name"],
                label=row["label"] or row["name"],
                mac=row["mac"],
                host=row["host"],
                area_id=row["bereich_id"],
                firmware=row["firmware"],
                last_seen=row["last_seen"],
            )
            self.mics[mic.id] = mic

    def _save(self, mic: Mic) -> None:
        self.house.db.execute(
            "UPDATE mics SET host=?, bereich_id=?, firmware=?, last_seen=? WHERE id=?",
            (mic.host, mic.area_id, mic.firmware, mic.last_seen, mic.id),
        )

    def in_area(self, area_id: str) -> list[Mic]:
        return [m for m in self.mics.values() if m.area_id == area_id]

    def update(self, mic_id: str, *, label: str | None = None, area_id: str | None | bool = False) -> Mic:
        mic = self.mics[mic_id]
        if label is not None:
            label = label.strip()
            if not 1 <= len(label) <= 40:
                raise ValueError("Name mit 1 bis 40 Zeichen")
            mic.label = label
            self.house.db.execute("UPDATE mics SET label=? WHERE id=?", (label, mic.id))
        if area_id is not False:
            if area_id and self.house.areas.get(area_id) is None:
                raise ValueError("Bereich unbekannt")
            mic.area_id = area_id or None
            self._save(mic)
        return mic

    # --------------------------------------------------------- connection
    async def start(self) -> None:
        self.load()
        for mic in self.mics.values():
            await self._connect(mic)

    async def stop(self) -> None:
        for mic in self.mics.values():
            await self._disconnect(mic)

    async def _connect(self, mic: Mic) -> None:
        from aioesphomeapi import APIClient, ReconnectLogic

        key = self.house.secrets.get(KEY_PREFIX + mic.id)
        if not mic.host or not key:
            return
        await self._disconnect(mic)
        client = APIClient(
            mic.host,
            API_PORT,
            None,
            client_info="loxone-jarvis",
            noise_psk=key,
            expected_name=mic.name,
            expected_mac=re.sub(r"[^0-9a-f]", "", mic.mac.lower()),
        )
        mic.client = client

        async def on_connect() -> None:
            await self._on_connect(mic)

        async def on_disconnect(expected: bool) -> None:
            mic.connected = False
            mic.unsubscribe = None
            self.house.events.publish({"type": "mic", "mic": mic.public()})
            if not expected:
                self.house.events.log("mic", f"{mic.label} getrennt")

        mic.reconnect = ReconnectLogic(
            client=client, on_connect=on_connect, on_disconnect=on_disconnect, name=mic.name
        )
        await mic.reconnect.start()

    async def _disconnect(self, mic: Mic) -> None:
        if mic.reconnect is not None:
            try:
                await mic.reconnect.stop()
            except Exception:  # noqa: BLE001
                pass
        if mic.client is not None:
            try:
                await mic.client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        mic.client = None
        mic.reconnect = None
        mic.connected = False

    async def _on_connect(self, mic: Mic) -> None:
        client = mic.client
        info, entities, services = await client.device_info_and_list_entities()
        mic.firmware = info.project_version or info.esphome_version
        mic.last_seen = time.time()
        mic.switch_key = mic.number_key = mic.button_key = None
        for entity in entities:
            ident = getattr(entity, "object_id", "") or getattr(entity, "name", "").lower().replace(" ", "_")
            kind = type(entity).__name__
            if kind == "SwitchInfo" and ident == "wake_word_enabled":
                mic.switch_key = entity.key
            elif kind == "NumberInfo" and ident == "wake_word_sensitivity":
                mic.number_key = entity.key
            elif kind == "ButtonInfo" and ident == "factory_reset":
                mic.button_key = entity.key
        mic.services = {s.name: s for s in services}
        coordinator = self.house.coordinator

        async def handle_start(conversation_id: str, flags: int, audio_settings: Any, wake_word: str | None):
            return await coordinator.on_start(mic, conversation_id, wake_word)

        async def handle_stop(abort: bool) -> None:
            await coordinator.on_stop(mic, abort)

        async def handle_audio(data: bytes, data2: bytes | None = None) -> None:
            coordinator.on_audio(mic, data)

        mic.unsubscribe = client.subscribe_voice_assistant(
            handle_start=handle_start, handle_stop=handle_stop, handle_audio=handle_audio
        )
        mic.connected = True
        self._save(mic)
        self.apply_sensitivity(mic)
        address = self.house.device_url()
        if address and "set_server_url" in mic.services:
            try:
                await client.execute_service(mic.services["set_server_url"], {"server_url": address})
            except Exception:  # noqa: BLE001
                log.info("set_server_url failed for %s", mic.name, exc_info=True)
        self.house.events.publish({"type": "mic", "mic": mic.public()})
        self.house.events.log("mic", f"{mic.label} verbunden", mic.host or "")

    # --------------------------------------------------------------- pair
    async def pair(self, host: str, device_name: str) -> Mic:
        from aioesphomeapi import ZERO_NOISE_PSK, APIClient

        device_name = device_name.strip().lower()
        if not NAME_RE.match(device_name):
            raise PairError("Gerätename ungültig")
        client = APIClient(host, API_PORT, None, client_info="loxone-jarvis", noise_psk=ZERO_NOISE_PSK)
        try:
            await asyncio.wait_for(client.connect(login=False), timeout=15)
            info = await client.device_info()
            if info.name != device_name:
                raise PairError(f"Unter {host} meldet sich {info.name}, nicht {device_name}")
            if "jarvis" not in (info.project_name or ""):
                raise PairError("Auf dem Board läuft nicht die Jarvis-Firmware")
            if not info.api_encryption_provisionable:
                raise PairError("Das Board hat schon einen Schlüssel. Erst „Werkseinstellungen“ oder neu flashen.")
            key = base64.b64encode(secrets.token_bytes(32))
            if not await client.noise_encryption_set_key(key):
                raise PairError("Das Board hat den Schlüssel abgelehnt")
            mac = info.mac_address.lower()
        except PairError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise PairError(f"Board unter {host} nicht erreichbar: {exc}") from exc
        finally:
            try:
                await client.disconnect(force=True)
            except Exception:  # noqa: BLE001
                pass

        existing = next((m for m in self.mics.values() if m.mac == mac), None)
        if existing:
            await self._disconnect(existing)
            mic = existing
            mic.host = host
            mic.name = device_name
            self.house.db.execute("UPDATE mics SET name=?, host=? WHERE id=?", (device_name, host, mic.id))
        else:
            mic = Mic(
                id=uuid.uuid4().hex[:10],
                name=device_name,
                label=f"Mikrofon {len(self.mics) + 1}",
                mac=mac,
                host=host,
                area_id=None,
                firmware=info.project_version,
                last_seen=time.time(),
            )
            self.house.db.execute(
                "INSERT INTO mics(id, name, label, mac, host, bereich_id, firmware, last_seen, created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (mic.id, mic.name, mic.label, mic.mac, mic.host, None, mic.firmware, mic.last_seen, time.time()),
            )
            self.mics[mic.id] = mic
        self.house.secrets.set(KEY_PREFIX + mic.id, key.decode("ascii"))
        self.house.events.log("mic", f"{mic.label} gekoppelt", device_name)
        await asyncio.sleep(0.5)
        await self._connect(mic)
        return mic

    async def hello(self, device_name: str, host: str, version: str | None) -> bool:
        """A board reports its address after boot. Only known boards are considered."""
        mic = next((m for m in self.mics.values() if m.name == device_name), None)
        if mic is None:
            return False
        mic.last_seen = time.time()
        if mic.host != host:
            mic.host = host
            self._save(mic)
            await self._connect(mic)
        elif not mic.connected and mic.reconnect is None:
            await self._connect(mic)
        return True

    async def remove(self, mic_id: str) -> None:
        mic = self.mics.get(mic_id)
        if mic is None:
            return
        if mic.connected and mic.button_key is not None:
            try:
                mic.client.button_command(mic.button_key)
                await asyncio.sleep(0.3)
            except Exception:  # noqa: BLE001
                pass
        await self._disconnect(mic)
        self.house.secrets.delete(KEY_PREFIX + mic.id)
        self.house.db.execute("DELETE FROM mics WHERE id = ?", (mic.id,))
        self.mics.pop(mic.id, None)

    # ------------------------------------------------------------ control
    def send(self, mic: Mic, event: str, data: dict[str, str] | None = None) -> None:
        from aioesphomeapi import VoiceAssistantEventType

        if not mic.connected or mic.client is None:
            return
        try:
            mic.client.send_voice_assistant_event(getattr(VoiceAssistantEventType, f"VOICE_ASSISTANT_{event}"), data)
        except Exception:  # noqa: BLE001
            log.debug("send %s to %s failed", event, mic.name, exc_info=True)

    def mute(self, mic: Mic, muted: bool) -> None:
        if mic.connected and mic.switch_key is not None:
            try:
                mic.client.switch_command(mic.switch_key, not muted)
            except Exception:  # noqa: BLE001
                log.debug("mute failed", exc_info=True)

    def mute_area(self, area_id: str, muted: bool) -> None:
        for mic in self.in_area(area_id):
            self.mute(mic, muted)

    async def listen(self, mic: Mic) -> bool:
        service = mic.services.get("listen")
        if not mic.connected or service is None:
            return False
        try:
            await mic.client.execute_service(service, {})
            return True
        except Exception:  # noqa: BLE001
            log.info("listen failed on %s", mic.name, exc_info=True)
            return False

    def apply_sensitivity(self, mic: Mic | None = None) -> None:
        value = float(self.house.settings.get("mic.sensitivity"))
        for target in [mic] if mic else list(self.mics.values()):
            if target.connected and target.number_key is not None:
                try:
                    target.client.number_command(target.number_key, value)
                except Exception:  # noqa: BLE001
                    log.debug("sensitivity failed", exc_info=True)

    async def update_firmware(self, mic_id: str) -> None:
        mic = self.mics[mic_id]
        firmware = self.house.firmware_info()
        if not firmware:
            raise RuntimeError("Kein Firmware-Abbild im Container")
        url = self.house.device_url()
        if not url:
            raise RuntimeError("Jarvis-Adresse im Hausnetz fehlt")
        service = mic.services.get("ota_update")
        if not mic.connected or service is None:
            raise RuntimeError("Mikrofon nicht verbunden")
        await mic.client.execute_service(
            service, {"url": f"{url}/device/firmware/ota.bin", "md5sum": firmware["ota_md5"]}
        )
        self.house.events.log("mic", f"{mic.label} lädt Firmware {firmware['version']}")
