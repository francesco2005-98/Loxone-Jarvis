"""Announcements on a Miniserver-paired Loxone Audioserver.

The paired server only accepts commands after `secure/authenticate` with the
Miniserver JWT. The reply is uploaded as WAV and played with
`playuploadedfile`, which runs as an announcement: music pauses and resumes.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import quote

import aiohttp
from cryptography.hazmat.primitives import padding as sympad
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ..events import EventBus
from ..loxone.client import MiniserverClient
from ..loxone.protocol import load_public_key
from ..settings import Settings

log = logging.getLogger(__name__)

PORT = 7091
UPLOAD_RATE = 24000


class AudioserverError(RuntimeError):
    pass


@dataclass
class Announcement:
    filename: str
    players: set[int]
    started: set[int] = field(default_factory=set)
    ended: set[int] = field(default_factory=set)
    began: asyncio.Event = field(default_factory=asyncio.Event)
    finished: asyncio.Event = field(default_factory=asyncio.Event)

    def mark_end(self, player: int) -> None:
        if player in self.players:
            self.ended.add(player)
            if self.ended >= self.players:
                self.finished.set()


def build_authenticate(user: str, jwt: str, pubkey_pem: str, session_token: str) -> str:
    key, iv = os.urandom(32), os.urandom(16)
    padder = sympad.PKCS7(128).padder()
    data = padder.update(jwt.encode("utf-8")) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    aes_b64 = base64.b64encode(enc.update(data) + enc.finalize()).decode("ascii")
    public = load_public_key(pubkey_pem)
    rsa = public.encrypt(f"{key.hex()}:{iv.hex()}:{session_token}".encode("utf-8"), padding.PKCS1v15())
    rsa_b64 = base64.b64encode(rsa).decode("ascii")
    return "secure/authenticate/" + "/".join(quote(part, safe="") for part in (user, rsa_b64, aes_b64))


class AudioserverClient:
    def __init__(self, settings: Settings, miniserver: MiniserverClient, events: EventBus):
        self.settings = settings
        self.miniserver = miniserver
        self.events = events
        self.status = "nicht eingerichtet"
        self.error: str | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._ready = asyncio.Event()
        self._waiters: dict[str, deque[asyncio.Future]] = defaultdict(deque)
        self._announcements: dict[str, Announcement] = {}
        self._event_active: dict[int, bool] = {}
        self._listeners: list[Callable[[int, bool], None]] = []
        self._task: asyncio.Task | None = None
        self._kick = asyncio.Event()

    # ------------------------------------------------------------ lifecycle
    def host(self) -> str | None:
        return self.settings.get("audioserver.host") or self.miniserver.structure.audioserver_host()

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="audioserver")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def reconnect(self) -> None:
        self._kick.set()
        if self._ws is not None and not self._ws.closed:
            asyncio.create_task(self._ws.close())

    def on_zone_event(self, listener: Callable[[int, bool], None]) -> None:
        self._listeners.append(listener)

    @property
    def connected(self) -> bool:
        return self._ready.is_set()

    async def _run(self) -> None:
        backoff = 2.0
        while True:
            self._kick.clear()
            host = self.host()
            token = self.miniserver.current_token()
            if not host or not token:
                self.status = "wartet auf Miniserver" if host else "nicht eingerichtet"
                await self._sleep(5)
                continue
            try:
                self.status = "verbinde"
                await self._session(host, *token)
                backoff = 2.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("Audioserver: %s", exc)
                if self.status == "verbunden":
                    self.events.log("audio", "Audioserver getrennt", str(exc)[:200])
                self.status = "getrennt"
                self.error = str(exc)[:200]
            finally:
                self._ready.clear()
                self._fail_waiters(AudioserverError("Verbindung getrennt"))
                self._ws = None
            await self._sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._kick.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _session(self, host: str, user: str, jwt: str) -> None:
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=10)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            ws = await http.ws_connect(
                f"ws://{host}:{PORT}/", protocols=("remotecontrol",), heartbeat=None, max_msg_size=0
            )
            self._ws = ws
            try:
                session_token = await asyncio.wait_for(self._greeting(ws), timeout=10)
                reader = asyncio.create_task(self._reader(ws))
                try:
                    keys = await self._command("audio/cfg/getkey/full", "getkey_result")
                    pem = keys[0]["pubkey"] if isinstance(keys, list) else keys["pubkey"]
                    result = await self._command(
                        build_authenticate(user, jwt, pem, session_token), "authenticate_result"
                    )
                    if result != "authentication successful":
                        raise AudioserverError(f"Anmeldung abgelehnt: {result}")
                    self.status = "verbunden"
                    self.error = None
                    self._ready.set()
                    self.events.log("audio", "Audioserver angemeldet", host)
                    await reader
                finally:
                    reader.cancel()
            finally:
                if not ws.closed:
                    await ws.close()

    @staticmethod
    async def _greeting(ws: aiohttp.ClientWebSocketResponse) -> str:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT and "Session-Token:" in msg.data:
                return msg.data.split("Session-Token:", 1)[1].splitlines()[0].strip()
            if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                break
        raise AudioserverError("keine Begrüßung vom Audioserver")

    async def _reader(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                    return
                continue
            try:
                data = json.loads(msg.data)
            except ValueError:
                continue
            if not isinstance(data, dict):
                continue
            if "audio_event" in data:
                self._on_events(data["audio_event"])
            for key, value in data.items():
                if key.endswith("_result"):
                    self._resolve(key, value)
            if "error" in data:
                self._reject_oldest(AudioserverError(str(data["error"])))

    def _resolve(self, key: str, value: object) -> None:
        queue = self._waiters.get(key)
        while queue:
            future = queue.popleft()
            if not future.done():
                future.set_result(value)
                return

    def _reject_oldest(self, exc: Exception) -> None:
        for queue in self._waiters.values():
            while queue:
                future = queue.popleft()
                if not future.done():
                    future.set_exception(exc)
                    return

    def _fail_waiters(self, exc: Exception) -> None:
        for queue in self._waiters.values():
            while queue:
                future = queue.popleft()
                if not future.done():
                    future.set_exception(exc)
                    future.exception()
        for announcement in self._announcements.values():
            announcement.finished.set()
        self._announcements.clear()

    async def _command(self, cmd: str, result_key: str, timeout: float = 8.0) -> object:
        ws = self._ws
        if ws is None or ws.closed:
            raise AudioserverError("Audioserver nicht verbunden")
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiters[result_key].append(future)
        await ws.send_str(cmd)
        return await asyncio.wait_for(future, timeout=timeout)

    def _on_events(self, items: object) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict) or "playerid" not in item:
                continue
            try:
                player = int(item["playerid"])
            except (TypeError, ValueError):
                continue
            eventype = item.get("eventype")
            path = str(item.get("audiopath") or "")
            if eventype and item.get("mode") == "play":
                if not self._event_active.get(player):
                    self._event_active[player] = True
                    self._notify(player, True)
                name = path.rsplit("/", 1)[-1]
                announcement = self._announcements.get(name)
                if announcement and player in announcement.players:
                    announcement.started.add(player)
                    announcement.began.set()
            elif not eventype and self._event_active.get(player):
                self._event_active[player] = False
                self._notify(player, False)
                for announcement in list(self._announcements.values()):
                    if player in announcement.started:
                        announcement.mark_end(player)
                        if announcement.finished.is_set():
                            self._announcements.pop(announcement.filename, None)

    def _notify(self, player: int, active: bool) -> None:
        for listener in list(self._listeners):
            try:
                listener(player, active)
            except Exception:  # noqa: BLE001
                log.exception("zone listener failed")

    # -------------------------------------------------------------- public
    async def ensure_ready(self, timeout: float = 4.0) -> None:
        if self._ready.is_set():
            return
        self._kick.set()
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise AudioserverError(self.error or "Audioserver nicht verbunden") from exc

    async def announce(self, targets: list[tuple[int, int]], wav: bytes) -> Announcement:
        """Play WAV (mono, 24 kHz) as announcement on (player_id, volume) targets."""
        await self.ensure_ready()
        host = self.host()
        filename = f"jarvis-{uuid.uuid4().hex[:16]}.wav"
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.put(
                f"http://{host}:{PORT}/audio/cfg/upload/audioupload/add/{filename}",
                data=wav,
                headers={"Content-Type": "audio/wav", "Accept-Encoding": "identity"},
            ) as resp:
                if resp.status // 100 != 2:
                    raise AudioserverError(f"Upload abgelehnt ({resp.status})")
                await resp.content.read(1024)
        announcement = Announcement(filename, {pid for pid, _ in targets})
        self._announcements[filename] = announcement
        for player, volume in targets:
            volume = max(1, min(100, int(volume)))
            result = await self._command(
                f"audio/grouped/playuploadedfile/{filename}/{player}#{volume}", "playuploadedfile_result"
            )
            if result is not True:
                raise AudioserverError(f"Ansage in Zone {player} nicht bestätigt")
        return announcement

    async def favorites(self, player: int) -> list[dict]:
        await self.ensure_ready()
        result = await self._command(f"audio/cfg/getroomfavs/{player}/0/50", "getroomfavs_result")
        return _favorite_items(result)

    async def play_favorite(self, player: int, slot: int) -> None:
        await self.ensure_ready()
        await self._command(f"audio/{player}/roomfav/play/{slot}", "roomfav_result")

    async def services(self) -> list[dict]:
        await self.ensure_ready()
        result = await self._command("audio/cfg/getservices", "getservices_result")
        return service_accounts(result)

    async def search_service(self, service: str, user: str, query: str, *, kind: str = "playlist", limit: int = 8) -> list[dict]:
        await self.ensure_ready()
        command, key = search_command(service, user, query, kind=kind, limit=limit)
        result = await self._command(command, key, timeout=15)
        return search_items(result)

    async def play_service(self, player: int, service: str, user: str, audiopath: str) -> None:
        await self.ensure_ready()
        command, key = serviceplay_command(player, service, user, audiopath)
        result = await self._command(command, key, timeout=15)
        if result is False:
            raise AudioserverError("Der Audioserver hat die Wiedergabe abgelehnt")

    def event_active(self, player: int) -> bool:
        return bool(self._event_active.get(player))


def search_command(service: str, user: str, query: str, *, kind: str = "playlist", limit: int = 8) -> tuple[str, str]:
    safe_kind = kind if kind in ("playlist", "track", "album", "artist") else "playlist"
    return (
        "audio/cfg/search/{service}/{user}/{kind}/{query}/{limit}/0".format(
            service=quote(service, safe=""),
            user=quote(user, safe=""),
            kind=safe_kind,
            query=quote(query.strip(), safe=""),
            limit=max(1, min(20, int(limit))),
        ),
        "search_result",
    )


def serviceplay_command(player: int, service: str, user: str, audiopath: str) -> tuple[str, str]:
    return (
        f"audio/{int(player)}/serviceplay/{quote(service, safe='')}/{quote(user, safe='')}/{quote(audiopath, safe='')}",
        "serviceplay_result",
    )


def service_accounts(result: object) -> list[dict]:
    """Spotify (and other) accounts configured on the Audioserver."""
    found: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            label = " ".join(str(node.get(key, "")) for key in ("service", "name", "type", "cmd", "id")).lower()
            user = node.get("user") or node.get("uid") or node.get("username") or node.get("account")
            service = "spotify" if "spotify" in label else ""
            if service and user:
                item = (service, str(user))
                if item not in seen:
                    seen.add(item)
                    found.append({"service": service, "user": str(user), "name": str(node.get("name") or "Spotify")})
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(result)
    return found


def search_items(result: object) -> list[dict]:
    """Playable hits, playlists first."""
    found: list[dict] = []
    seen: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            path = node.get("audiopath") or node.get("path") or node.get("uri") or node.get("id")
            name = node.get("name") or node.get("title")
            if isinstance(path, str) and name and ":" in path and path not in seen:
                seen.add(path)
                found.append(
                    {
                        "name": str(name),
                        "path": path,
                        "playlist": _is_playlist(node, path),
                        "user": str(node.get("user") or ""),
                    }
                )
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(result)
    playlists = [item for item in found if item["playlist"]]
    return playlists or found


def _is_playlist(node: dict, path: str) -> bool:
    kind = str(node.get("type") if node.get("type") is not None else node.get("audiotype") or "").lower()
    if "playlist" in kind or "playlist" in path.lower():
        return True
    return kind in {"2", "3", "7", "11", "13"}


def _favorite_items(result: object) -> list[dict]:
    """Find the favourites list in whatever envelope the server returns."""
    stack = [result]
    while stack:
        current = stack.pop()
        if isinstance(current, list):
            if current and all(isinstance(x, dict) and ("name" in x or "title" in x) for x in current):
                out = []
                for index, item in enumerate(current, start=1):
                    slot = item.get("slot")
                    out.append(
                        {
                            "slot": int(slot) if str(slot).isdigit() else index,
                            "name": str(item.get("name") or item.get("title") or ""),
                        }
                    )
                return out
            stack.extend(current)
        elif isinstance(current, dict):
            stack.extend(current.values())
    return []
