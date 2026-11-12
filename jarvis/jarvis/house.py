"""Wires every service together. One instance per process."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Coroutine

from .areas import Area, Areas
from .audio.audioserver import AudioserverClient
from .audio.speech import Speech
from .audio.tts import Piper
from .brain.config_agent import ConfigAgent
from .brain.gemini import Brain
from .config import Config
from .crypto import SecretStore
from .db import Database
from .events import EventBus
from .loxone.catalog import Catalog
from .loxone.client import MiniserverClient
from .loxone.structure import LoxControl
from .mcp_client import ConfigMcp
from .people import People
from .security import Auth
from .settings import Settings
from .timers import TimerService
from .usage import Usage
from .voice.coordinator import Coordinator
from .voice.mics import MicManager
from .voice.speaker import SpeakerRecognizer
from .voice.stt import Recognizer

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
FIRMWARE_DIR = STATIC_DIR / "firmware"


class House:
    def __init__(self, config: Config):
        self.config = config
        self.db = Database(config.db_path)
        self.secrets = SecretStore(self.db, config.secret_key, config.data_dir)
        self.settings = Settings(self.db)
        self.events = EventBus(self.db)
        self.auth = Auth(self.db)
        self.usage = Usage(self.db, self.settings)
        self.areas = Areas(self.db)
        self.people = People(self.db)
        self.timers = TimerService(self.db, self.settings)
        self.catalog = Catalog(self.db)
        self.miniserver = MiniserverClient(self.settings, self.secrets, self.catalog, self.events)
        self.audioserver = AudioserverClient(self.settings, self.miniserver, self.events)
        self.piper = Piper(config.piper_url, self.settings)
        self.stt = Recognizer(config.models_dir)
        self.speaker = SpeakerRecognizer(config.models_dir / "speaker" / "wespeaker_en_voxceleb_resnet34_LM.onnx")
        self.mcp = ConfigMcp(self.settings)
        self.speech = Speech(self)
        self.mics = MicManager(self)
        self.coordinator = Coordinator(self)
        self.brain = Brain(self)
        self.config_agent = ConfigAgent(self, self.brain)
        self.brain.runner.config_task = self.config_agent.run
        self._tasks: set[asyncio.Task] = set()
        self._favorites: dict[int, tuple[float, list[dict]]] = {}
        self.started_at = time.time()
        self.settings.on_change(self._settings_changed)

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self.config.initial_password and not self.auth.has_password():
            self.auth.set_password(self.config.initial_password)
        self.miniserver.start()
        self.audioserver.start()
        self.timers.start(self._timer_due)
        await self.mics.start()
        self.background(self._housekeeping(), "housekeeping")
        self.background(asyncio.to_thread(self.stt.preload, self.settings.get("stt.model")), "stt-preload")

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await self.mics.stop()
        await self.timers.stop()
        await self.audioserver.stop()
        await self.miniserver.stop()
        self.db.close()

    def background(self, coro: Coroutine, name: str) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)

        def done(t: asyncio.Task) -> None:
            self._tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                log.error("background task %s failed", name, exc_info=t.exception())

        task.add_done_callback(done)
        return task

    async def _housekeeping(self) -> None:
        while True:
            self.events.prune(int(self.settings.get("log.retention_days")))
            self.auth.prune()
            await asyncio.sleep(3600)

    def _settings_changed(self, keys: set[str]) -> None:
        if keys & {"loxone.host", "loxone.user", "loxone.tls"}:
            self.miniserver.forget_token()
            self.miniserver.reconnect()
        if keys & {"audioserver.host"}:
            self.audioserver.reconnect()
        if "mic.sensitivity" in keys:
            self.mics.apply_sensitivity()
        if "stt.model" in keys:
            self.background(asyncio.to_thread(self.stt.preload, self.settings.get("stt.model")), "stt-preload")

    def secret_changed(self, key: str) -> None:
        if key == "loxone.password":
            self.miniserver.forget_token()
            self.miniserver.reconnect()

    # --------------------------------------------------------------- audio
    def zone_of(self, area: Area) -> LoxControl | None:
        if not area.zone_uuid:
            return None
        zone = self.miniserver.structure.controls.get(area.zone_uuid)
        return zone if zone and zone.type == "AudioZoneV2" else None

    def cached_favorites(self, area: Area) -> list[str]:
        zone = self.zone_of(area)
        if zone is None or zone.details.get("playerid") is None:
            return []
        player = int(zone.details["playerid"])
        cached = self._favorites.get(player)
        if cached is None or time.monotonic() - cached[0] > 600:
            if self.audioserver.connected:
                self.background(self._load_favorites(player), "favorites")
        return [f"{f['slot']}: {f['name']}" for f in (cached[1] if cached else [])]

    async def _load_favorites(self, player: int) -> None:
        try:
            items = await self.audioserver.favorites(player)
            self._favorites[player] = (time.monotonic(), items)
        except Exception:  # noqa: BLE001
            self._favorites[player] = (time.monotonic(), [])

    async def play_favorite(self, zone: LoxControl, value: Any) -> None:
        from .loxone.catalog import ActionError

        player = zone.details.get("playerid")
        slot: int | None = None
        text = str(value or "").strip()
        if text.isdigit():
            slot = int(text)
        elif player is not None:
            if int(player) not in self._favorites:
                await self._load_favorites(int(player))
            for fav in self._favorites.get(int(player), (0, []))[1]:
                if text.lower() in fav["name"].lower() or fav["name"].lower() in text.lower():
                    slot = fav["slot"]
                    break
        if slot is None:
            slot = 1 if not text else None
        if slot is None:
            raise ActionError("Favorit nicht gefunden")
        return await self._play_slot(zone, slot)

    async def _play_slot(self, zone: LoxControl, slot: int) -> None:
        from .loxone.catalog import ActionError

        player = zone.details.get("playerid")
        if player is not None and self.audioserver.connected:
            await self.audioserver.play_favorite(int(player), slot)
        else:
            reply = await self.miniserver.command(zone.uuid, f"playZoneFav/{slot}")
            if not reply.ok:
                raise ActionError(f"Miniserver meldet Code {reply.code}")

    async def play_query(self, zone: LoxControl, query: str) -> str:
        """Play a room favourite or the first matching Spotify playlist."""
        from .loxone.catalog import ActionError

        text = str(query or "").strip()
        if len(text) < 2:
            raise ActionError("Suchbegriff fehlt")
        player = zone.details.get("playerid")
        if player is None:
            raise ActionError("Diese Zone hat keinen Player")
        player_id = int(player)
        if not self.audioserver.connected:
            raise ActionError("Audioserver nicht verbunden")
        if player_id not in self._favorites:
            await self._load_favorites(player_id)
        favourite = _match_favourite(text, self._favorites.get(player_id, (0, []))[1])
        if favourite is not None:
            await self.play_favorite(zone, favourite["name"])
            return favourite["name"]
        accounts = await self.audioserver.services()
        spotify = next((account for account in accounts if account["service"] == "spotify"), None)
        if spotify is None:
            raise ActionError("Spotify ist am Audioserver nicht angemeldet")
        items = await self.audioserver.search_service("spotify", spotify["user"], text)
        if not items:
            raise ActionError(f"Keine Spotify-Playlist zu „{text}“")
        item = items[0]
        await self.audioserver.play_service(player_id, "spotify", item["user"] or spotify["user"], item["path"])
        self.events.log("audio", f"Spotify: {item['name']}", text)
        return item["name"]

    # --------------------------------------------------------------- timers
    async def _timer_due(self, timer: dict) -> None:
        lang = timer.get("lang") or "de"
        label = timer.get("label") or ""
        if timer["kind"] == "reminder":
            text = timer.get("text") or label
            message = f"Erinnerung: {text}" if lang == "de" else f"Reminder: {text}"
        else:
            message = f"Der Timer {label} ist abgelaufen." if lang == "de" else f"Your {label} timer is done."
        area = self.areas.get(timer.get("bereich_id"))
        areas = [area] if area else [a for a in self.areas.list() if a.zone_uuid]
        self.events.log("timer", message)
        await self.speech.say(message, lang, areas)

    # -------------------------------------------------------------- devices
    def device_url(self) -> str | None:
        address = self.settings.get("mics.jarvis_address")
        if not address:
            return None
        if ":" not in address.split("]")[-1]:
            address = f"{address}:{self.config.device_port}"
        return f"http://{address}"

    def firmware_info(self) -> dict | None:
        manifest = FIRMWARE_DIR / "manifest.json"
        if not manifest.exists():
            return None
        try:
            return json.loads(manifest.read_text("utf-8"))
        except ValueError:
            return None


def _match_favourite(query: str, favourites: list[dict]) -> dict | None:
    wanted = query.strip().lower()
    exact = [item for item in favourites if str(item.get("name") or "").lower() == wanted]
    if exact:
        return exact[0]
    contained = [
        item
        for item in favourites
        if len(str(item.get("name") or "")) >= 3
        and (str(item["name"]).lower() in wanted or wanted in str(item["name"]).lower())
    ]
    if not contained:
        return None
    contained.sort(key=lambda item: len(str(item["name"])), reverse=True)
    return contained[0]
