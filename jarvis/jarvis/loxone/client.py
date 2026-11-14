"""Live connection to the Miniserver with a token for the non-admin user `jarvis`."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import unicodedata
from collections import deque
from typing import Any, Callable

import aiohttp

from ..crypto import SecretStore
from ..events import EventBus
from ..settings import Settings
from . import protocol as p
from .catalog import Catalog
from .structure import Structure, parse

log = logging.getLogger(__name__)

TOKEN_KEY = "loxone.token"
REQUEST_TIMEOUT = 15.0
KEEPALIVE_SECONDS = 30.0


class AuthError(RuntimeError):
    pass


class NotConnected(RuntimeError):
    pass


class MiniserverClient:
    def __init__(self, settings: Settings, secrets: SecretStore, catalog: Catalog, events: EventBus):
        self.settings = settings
        self.secrets = secrets
        self.catalog = catalog
        self.events = events
        self.status = "nicht eingerichtet"
        self.error: str | None = None
        self.values: dict[str, float] = {}
        self.texts: dict[str, str] = {}
        self.structure = Structure()
        self.raw_structure: dict | None = None
        self.last_sync: float | None = None
        self.token_rights = 0
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: p.Session | None = None
        self._pending: deque[asyncio.Future] = deque()
        self._send_lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._kick = asyncio.Event()
        self._listeners: list[Callable[[set[str]], None]] = []

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="miniserver")

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

    def on_states(self, listener: Callable[[set[str]], None]) -> None:
        self._listeners.append(listener)

    @property
    def connected(self) -> bool:
        return self.status == "verbunden"

    def state(self, control_uuid: str, name: str) -> Any:
        control = self.structure.controls.get(control_uuid)
        if control is None:
            return None
        state_uuid = control.states.get(name)
        if state_uuid is None:
            return None
        if state_uuid in self.texts:
            return self.texts[state_uuid]
        return self.values.get(state_uuid)

    async def command(self, uuid: str, cmd: str) -> p.Reply:
        if not self.connected or self._ws is None:
            raise NotConnected("Miniserver nicht verbunden")
        text = await self._request(f"jdev/sps/io/{uuid}/{cmd}")
        reply = p.parse_reply(text)
        if reply is None:
            raise RuntimeError("unerwartete Antwort vom Miniserver")
        return reply

    # ---------------------------------------------------------------- loop
    async def _run(self) -> None:
        backoff = 2.0
        while True:
            self._kick.clear()
            host = self.settings.get("loxone.host")
            user = self.settings.get("loxone.user")
            password = self.secrets.get("loxone.password")
            if not host or not user or not (password or self.secrets.has(TOKEN_KEY)):
                self.status = "nicht eingerichtet"
                await self._kick.wait()
                continue
            try:
                self.status = "verbinde"
                self.error = None
                await self._connect(host, user, password)
                backoff = 2.0
            except asyncio.CancelledError:
                raise
            except AuthError as exc:
                self.status = "fehler"
                self.error = str(exc)
                self.events.log("loxone", "Miniserver-Anmeldung abgelehnt", str(exc))
                await self._wait(900)
                continue
            except Exception as exc:  # noqa: BLE001
                log.warning("Miniserver connection: %s", exc)
                if self.status == "verbunden":
                    self.events.log("loxone", "Verbindung zum Miniserver getrennt", str(exc)[:200])
                self.status = "getrennt"
                self.error = str(exc)[:200]
            finally:
                self._fail_pending(NotConnected("Verbindung getrennt"))
                self._ws = None
            await self._wait(backoff)
            backoff = min(backoff * 2, 60.0)

    async def _wait(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._kick.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    def _base(self, host: str, ws: bool) -> str:
        tls = self.settings.get("loxone.tls")
        scheme = ("wss" if tls else "ws") if ws else ("https" if tls else "http")
        return f"{scheme}://{host}"

    async def _connect(self, host: str, user: str, password: str | None) -> None:
        ssl = False if self.settings.get("loxone.tls") else None
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.get(f"{self._base(host, False)}/jdev/sys/getPublicKey", ssl=ssl) as resp:
                body = await resp.text()
            reply = p.parse_reply(body)
            if reply is None or not reply.ok:
                raise RuntimeError("Miniserver liefert keinen öffentlichen Schlüssel")
            self._session = p.Session(p.load_public_key(str(reply.value)))
            ws = await http.ws_connect(
                f"{self._base(host, True)}/ws/rfc6455",
                protocols=("remotecontrol",),
                heartbeat=None,
                autoping=True,
                max_msg_size=0,
                ssl=ssl,
            )
            self._ws = ws
            reader = asyncio.create_task(self._reader(ws), name="miniserver-reader")
            try:
                await self._authenticate(user, password)
                await self._load_structure()
                await self._request("jdev/sps/enablebinstatusupdate")
                self.status = "verbunden"
                self.events.log(
                    "loxone",
                    "Loxone synchronisiert",
                    f"{len(self.catalog.entries())} Bausteine, {self.structure.ms_name}",
                )
                keepalive = asyncio.create_task(self._keepalive(ws))
                refresher = asyncio.create_task(self._refresher(user))
                try:
                    await reader
                finally:
                    keepalive.cancel()
                    refresher.cancel()
            finally:
                reader.cancel()
                if not ws.closed:
                    await ws.close()
            code = ws.close_code
            if code == 4003:
                raise AuthError("Benutzer nach zu vielen Fehlversuchen gesperrt")
            if code in (4004, 4005, 4006):
                self.secrets.delete(TOKEN_KEY)
                raise AuthError("Benutzer geändert oder deaktiviert")
            raise ConnectionError(f"Websocket geschlossen ({code})")

    async def _authenticate(self, user: str, password: str | None) -> None:
        assert self._session is not None
        reply = p.parse_reply(await self._request(self._session.keyexchange_command()))
        if reply is None or not reply.ok:
            raise RuntimeError("Schlüsseltausch fehlgeschlagen")

        stored = self._load_token(user)
        if stored:
            key, salt, alg = await self._getkey2(user)
            hashed = p.token_hash(stored["token"], key, alg)
            reply = p.parse_reply(await self._encrypted(f"authwithtoken/{hashed}/{p.quote(user)}"))
            if reply and reply.ok:
                value = reply.value if isinstance(reply.value, dict) else {}
                self.token_rights = int(value.get("tokenRights", 0) or 0)
                stored["valid_until"] = value.get("validUntil", stored.get("valid_until"))
                self._save_token(stored)
                return
            self.secrets.delete(TOKEN_KEY)

        if not password:
            raise AuthError("Kein Passwort für den Miniserver-Benutzer hinterlegt")
        key, salt, alg = await self._getkey2(user)
        hashed = p.password_hash(user, password, key, salt, alg)
        client_uuid = self._client_uuid()
        reply = p.parse_reply(
            await self._encrypted(
                f"jdev/sys/getjwt/{hashed}/{p.quote(user)}/4/{client_uuid}/{p.quote('Jarvis')}"
            )
        )
        if reply is None or not reply.ok or not isinstance(reply.value, dict):
            code = reply.code if reply else 0
            raise AuthError(f"Anmeldung abgelehnt (Code {code}). Benutzer, Passwort und App-Recht prüfen.")
        value = reply.value
        self.token_rights = int(value.get("tokenRights", 0) or 0)
        self._save_token(
            {
                "token": value["token"],
                "valid_until": value.get("validUntil"),
                "alg": alg,
                "user": user,
                "host": self.settings.get("loxone.host"),
            }
        )

    async def _getkey2(self, user: str) -> tuple[str, str, str]:
        reply = p.parse_reply(await self._encrypted(f"jdev/sys/getkey2/{p.quote(user)}"))
        if reply is None or not reply.ok or not isinstance(reply.value, dict):
            raise AuthError("Benutzer unbekannt oder Schlüsselabfrage abgelehnt")
        value = reply.value
        return str(value["key"]), str(value.get("salt", "")), str(value.get("hashAlg", "SHA1")).upper()

    async def _load_structure(self) -> None:
        text = await self._request("data/LoxAPP3.json", timeout=90.0)
        # Names typed on some systems arrive with decomposed umlauts; matching and speech expect "ü".
        data = json.loads(unicodedata.normalize("NFC", text))
        self.raw_structure = data
        self.structure = parse(data)
        self.catalog.sync(self.structure)
        self.last_sync = time.time()

    async def _keepalive(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        while not ws.closed:
            await asyncio.sleep(KEEPALIVE_SECONDS)
            async with self._send_lock:
                await ws.send_str("keepalive")

    async def _refresher(self, user: str) -> None:
        while True:
            await asyncio.sleep(3600)
            stored = self._load_token(user)
            if not stored or not stored.get("valid_until"):
                continue
            remaining = p.lox_time_to_epoch(float(stored["valid_until"])) - time.time()
            if remaining > 3 * 86400:
                continue
            try:
                key, _salt, alg = await self._getkey2(user)
                hashed = p.token_hash(stored["token"], key, alg)
                reply = p.parse_reply(await self._encrypted(f"jdev/sys/refreshjwt/{hashed}/{p.quote(user)}"))
                if reply and reply.ok and isinstance(reply.value, dict):
                    stored["token"] = reply.value.get("token", stored["token"])
                    stored["valid_until"] = reply.value.get("validUntil", stored["valid_until"])
                    self._save_token(stored)
            except Exception:  # noqa: BLE001
                log.warning("token refresh failed", exc_info=True)

    # ---------------------------------------------------------- transport
    async def _encrypted(self, cmd: str) -> str:
        assert self._session is not None
        return await self._request(self._session.encrypt_command(cmd))

    async def _request(self, cmd: str, timeout: float = REQUEST_TIMEOUT) -> str:
        ws = self._ws
        if ws is None or ws.closed:
            raise NotConnected("Miniserver nicht verbunden")
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        async with self._send_lock:
            self._pending.append(future)
            await ws.send_str(cmd)
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except asyncio.TimeoutError:
            # Keep FIFO order intact: the reply may still come and must be consumed.
            future.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
            raise TimeoutError(f"Keine Antwort auf {cmd.split('/')[0:3]}") from None

    def _fail_pending(self, exc: Exception) -> None:
        while self._pending:
            future = self._pending.popleft()
            if not future.done():
                future.set_exception(exc)
                future.exception()

    async def _reader(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        expect: p.Header | None = None
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.BINARY:
                data: bytes = msg.data
                if expect is None:
                    header = p.parse_header(data)
                    if header is None or header.estimated:
                        continue
                    if header.identifier == p.IDENT_OUT_OF_SERVICE:
                        self.events.log("loxone", "Miniserver geht außer Betrieb")
                        await ws.close()
                        return
                    if header.identifier == p.IDENT_KEEPALIVE or header.length == 0:
                        continue
                    expect = header
                    continue
                ident, expect = expect.identifier, None
                if ident == p.IDENT_VALUES:
                    self._apply(values=p.parse_value_states(data))
                elif ident == p.IDENT_TEXTS:
                    self._apply(texts=p.parse_text_states(data))
                elif ident in (p.IDENT_TEXT, p.IDENT_FILE):
                    self._resolve(data.decode("utf-8", "replace"))
            elif msg.type == aiohttp.WSMsgType.TEXT:
                expect = None
                self._resolve(msg.data)
            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                return

    def _resolve(self, text: str) -> None:
        while self._pending:
            future = self._pending.popleft()
            if not future.done():
                future.set_result(text)
                return

    def _apply(self, values: dict[str, float] | None = None, texts: dict[str, str] | None = None) -> None:
        changed: set[str] = set()
        if values:
            self.values.update(values)
            changed.update(values)
        if texts:
            self.texts.update(texts)
            changed.update(texts)
        for listener in list(self._listeners):
            try:
                listener(changed)
            except Exception:  # noqa: BLE001
                log.exception("state listener failed")

    # -------------------------------------------------------------- token
    def _load_token(self, user: str) -> dict | None:
        raw = self.secrets.get(TOKEN_KEY)
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        if data.get("user") != user or data.get("host") != self.settings.get("loxone.host"):
            return None
        if data.get("valid_until") and p.lox_time_to_epoch(float(data["valid_until"])) < time.time():
            return None
        return data

    def _save_token(self, data: dict) -> None:
        self.secrets.set(TOKEN_KEY, json.dumps(data))

    def _client_uuid(self) -> str:
        existing = self.secrets.get("loxone.client_uuid")
        if existing:
            return existing
        value = p.new_client_uuid()
        self.secrets.set("loxone.client_uuid", value)
        return value

    def forget_token(self) -> None:
        self.secrets.delete(TOKEN_KEY)

    def current_token(self) -> tuple[str, str] | None:
        """(user, JWT) for the Audioserver login, which accepts the Miniserver token."""
        user = self.settings.get("loxone.user")
        stored = self._load_token(user)
        if not stored or not self.connected:
            return None
        return user, stored["token"]
