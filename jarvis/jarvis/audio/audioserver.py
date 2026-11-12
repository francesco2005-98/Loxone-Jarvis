"""Announcements and music on the Miniserver-paired Loxone Audioservers.

A paired server only accepts commands after `secure/authenticate` with the
Miniserver JWT. The reply is uploaded as WAV and played with
`playuploadedfile`, which runs as an announcement: music pauses and resumes.
A house can have several Audioservers; every zone talks to the one the
Miniserver assigns it.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
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
from ..loxone.structure import strip_port
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
    """One connection to one Audioserver."""

    def __init__(self, address: str, miniserver: MiniserverClient, events: EventBus, name: str = ""):
        self.address = address
        self.name = name or address
        self.miniserver = miniserver
        self.events = events
        self.status = "wartet auf Miniserver"
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
    def host(self) -> str:
        return self.address

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"audioserver {self.address}")

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
            host = self.address
            token = self.miniserver.current_token()
            if not token:
                self.status = "wartet auf Miniserver"
                await self._sleep(5)
                continue
            try:
                self.status = "verbinde"
                await self._session(host, *token)
                backoff = 2.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("Audioserver %s: %s", host, exc)
                if self.status == "verbunden":
                    self.events.log("audio", f"Audioserver {self.name} getrennt", str(exc)[:200])
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
                    self.events.log("audio", f"Audioserver {self.name} angemeldet", host)
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

    def public(self) -> dict:
        return {"name": self.name, "host": self.address, "status": self.status, "error": self.error}


def parse_hosts(value: str | None) -> list[str]:
    """Manual addresses, separated by comma, semicolon or whitespace."""
    out: list[str] = []
    for part in re.split(r"[\s,;]+", value or ""):
        host = strip_port(part)
        if host and host not in out:
            out.append(host)
    return out


class Audioservers:
    """All Audioservers of the house, one connection each.

    Without manual addresses Jarvis connects to every Audioserver in the
    Miniserver structure. A zone is routed to the server the structure names
    for it, else to the server whose events carried its player id, else to the
    only server there is.
    """

    SYNC_INTERVAL = 5.0

    def __init__(self, settings: Settings, miniserver: MiniserverClient, events: EventBus):
        self.settings = settings
        self.miniserver = miniserver
        self.events = events
        self.clients: dict[str, AudioserverClient] = {}
        self._seen: dict[int, str] = {}
        self._listeners: list[Callable[[int, bool], None]] = []
        self._started = False
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------ lifecycle
    def configured(self) -> list[dict]:
        found = self.miniserver.structure.audioservers()
        manual = parse_hosts(self.settings.get("audioserver.host"))
        if not manual:
            return found
        by_host = {server["host"]: server for server in found}
        return [{"uuid": by_host.get(h, {}).get("uuid"), "name": by_host.get(h, {}).get("name") or h, "host": h} for h in manual]

    def sync(self) -> None:
        wanted = {server["host"]: server for server in self.configured()}
        for host in [h for h in self.clients if h not in wanted]:
            client = self.clients.pop(host)
            asyncio.create_task(client.stop())
            self._seen = {p: h for p, h in self._seen.items() if h != host}
        for host, server in wanted.items():
            client = self.clients.get(host)
            if client is None:
                client = AudioserverClient(host, self.miniserver, self.events, name=server["name"])
                client.on_zone_event(lambda player, active, h=host: self._zone_event(h, player, active))
                self.clients[host] = client
                if self._started:
                    client.start()
            else:
                client.name = server["name"]

    def start(self) -> None:
        self._started = True
        self.sync()
        for client in self.clients.values():
            client.start()
        self._task = asyncio.create_task(self._watch(), name="audioservers")

    async def stop(self) -> None:
        self._started = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await asyncio.gather(*(client.stop() for client in self.clients.values()), return_exceptions=True)

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(self.SYNC_INTERVAL)
            try:
                self.sync()
            except Exception:  # noqa: BLE001
                log.exception("audioserver sync failed")

    def reconnect(self) -> None:
        self.sync()
        for client in self.clients.values():
            client.reconnect()

    def on_zone_event(self, listener: Callable[[int, bool], None]) -> None:
        self._listeners.append(listener)

    def _zone_event(self, host: str, player: int, active: bool) -> None:
        self._seen[player] = host
        for listener in list(self._listeners):
            try:
                listener(player, active)
            except Exception:  # noqa: BLE001
                log.exception("zone listener failed")

    # --------------------------------------------------------------- state
    @property
    def connected(self) -> bool:
        """At least one Audioserver is signed in."""
        return any(client.connected for client in self.clients.values())

    @property
    def connected_count(self) -> int:
        return sum(1 for client in self.clients.values() if client.connected)

    @property
    def status(self) -> str:
        clients = list(self.clients.values())
        if not clients:
            return "nicht eingerichtet" if self.miniserver.connected else "wartet auf Miniserver"
        ok = self.connected_count
        if ok == len(clients):
            return "verbunden"
        if ok:
            return f"teilweise verbunden ({ok}/{len(clients)})"
        states = {client.status for client in clients}
        return states.pop() if len(states) == 1 else "getrennt"

    @property
    def error(self) -> str | None:
        for client in self.clients.values():
            if client.error and not client.connected:
                return client.error if len(self.clients) == 1 else f"{client.name}: {client.error}"
        return None

    def hosts(self) -> list[str]:
        return list(self.clients)

    def servers(self) -> list[dict]:
        return [client.public() for client in self.clients.values()]

    def client_for(self, player: int) -> AudioserverClient | None:
        zone = self.miniserver.structure.zone_for_player(player)
        host = self.miniserver.structure.zone_server_host(zone) if zone else None
        if host in self.clients:
            return self.clients[host]
        if self._seen.get(player) in self.clients:
            return self.clients[self._seen[player]]
        if len(self.clients) == 1:
            return next(iter(self.clients.values()))
        return None

    def ready_for(self, player: int) -> bool:
        client = self.client_for(player)
        return client is not None and client.connected

    def _require(self, player: int) -> AudioserverClient:
        client = self.client_for(player)
        if client is None:
            raise AudioserverError(f"Kein Audioserver für Player {player} gefunden")
        return client

    def event_active(self, player: int) -> bool:
        client = self.client_for(player)
        return client is not None and client.event_active(player)

    # ------------------------------------------------------------- commands
    async def test(self, timeout: float = 6.0) -> list[dict]:
        self.sync()

        async def one(client: AudioserverClient) -> dict:
            try:
                await client.ensure_ready(timeout=timeout)
                return {**client.public(), "ok": True}
            except Exception as exc:  # noqa: BLE001
                reason = "wartet auf die Anmeldung am Miniserver" if client.status == "wartet auf Miniserver" else str(exc)
                return {**client.public(), "ok": False, "error": reason[:200]}

        return list(await asyncio.gather(*(one(c) for c in self.clients.values())))

    async def announce(self, targets: list[tuple[int, int]], wav: bytes) -> Announcement:
        """Play the WAV on every target, each through the Audioserver of its zone."""
        groups: dict[str, list[tuple[int, int]]] = defaultdict(list)
        missing: list[int] = []
        for player, volume in targets:
            client = self.client_for(player)
            if client is None:
                missing.append(player)
            else:
                groups[client.address].append((player, volume))
        if missing:
            self.events.log("audio", "Kein Audioserver für Zone", ", ".join(f"Player {p}" for p in missing))
        if not groups:
            raise AudioserverError("Kein Audioserver für diese Zonen verbunden")
        results = await asyncio.gather(
            *(self.clients[host].announce(group, wav) for host, group in groups.items()), return_exceptions=True
        )
        parts = [r for r in results if isinstance(r, Announcement)]
        failures = [(host, r) for host, r in zip(groups, results) if isinstance(r, BaseException)]
        for host, exc in failures:
            self.events.log("audio", f"Ansage über {self.clients[host].name} fehlgeschlagen", str(exc)[:200])
        if not parts:
            raise failures[0][1]
        if len(parts) == 1:
            return parts[0]
        combined = Announcement("", set().union(*(part.players for part in parts)))
        asyncio.create_task(_join(parts, combined))
        return combined

    async def favorites(self, player: int) -> list[dict]:
        return await self._require(player).favorites(player)

    async def play_favorite(self, player: int, slot: int) -> None:
        await self._require(player).play_favorite(player, slot)

    async def services(self, player: int) -> list[dict]:
        return await self._require(player).services()

    async def search_service(
        self, player: int, service: str, user: str, query: str, *, kind: str = "playlist", limit: int = 8
    ) -> list[dict]:
        return await self._require(player).search_service(service, user, query, kind=kind, limit=limit)

    async def play_service(self, player: int, service: str, user: str, audiopath: str) -> None:
        await self._require(player).play_service(player, service, user, audiopath)


async def _join(parts: list[Announcement], combined: Announcement, limit: float = 180.0) -> None:
    """Began when the first server starts, finished when the last one ends."""

    async def run() -> None:
        began = [asyncio.create_task(part.began.wait()) for part in parts]
        try:
            await asyncio.wait(began, return_when=asyncio.FIRST_COMPLETED)
            combined.began.set()
            await asyncio.gather(*(part.finished.wait() for part in parts))
        finally:
            for task in began:
                task.cancel()

    try:
        await asyncio.wait_for(run(), timeout=limit)
    except asyncio.TimeoutError:
        pass
    finally:
        combined.finished.set()


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
