"""HTTP API for the command centre. Everything except login requires a session."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from . import __version__, location
from .brain.gemini import BrainUnavailable
from .house import House
from .loxone.catalog import ActionError
from .people import ENROLL_SENTENCES
from .crypto import new_token
from .security import SESSION_COOKIE, SESSION_TTL, LoginLimiter
from .settings import SECRET_KEYS, SettingsError
from .voice.live import LiveSession
from .voice.mics import PairError

RECOMMENDATION = {
    "board": "Seeed XIAO ESP32-S3 Sense",
    "reichelt": [
        {"article": "406873", "variant": "mit Stiftleiste", "note": "bevorzugt, solange 358353 fehlt"},
        {"article": "358353", "variant": "ohne Stiftleiste", "note": "dasselbe Board"},
    ],
    "warning": "Artikel 358354 und die Header-Variante ohne Kamera haben kein Mikrofon.",
    "hints": [
        "Stabantenne auf den U.FL-Stecker setzen, das Board hat keine eingebaute Antenne.",
        "D11 und D12 sind die Mikrofonleitungen, daran nichts anschließen.",
        "Strom über USB-C. Bei PoE nur über einen Splitter auf 5 V.",
        "Pair in Chrome oder Edge, das Board kann nur 2,4-GHz-WLAN.",
    ],
}


class PasswordBody(BaseModel):
    password: str = Field(max_length=200)


class ChangePasswordBody(BaseModel):
    current: str = Field(max_length=200)
    password: str = Field(max_length=200)


class SettingsBody(BaseModel):
    values: dict[str, Any]


class SecretBody(BaseModel):
    key: str
    value: str = Field(default="", max_length=500)


_PRIVATE_KEYS = {"currentuser", "user", "username", "password", "token", "remoteurl", "localurl", "serialnr", "macaddress", "ip"}


def sanitize_structure(data: Any) -> Any:
    """LoxAPP3 without user, address and token fields, for support."""
    if isinstance(data, dict):
        return {k: sanitize_structure(v) for k, v in data.items() if str(k).lower() not in _PRIVATE_KEYS}
    if isinstance(data, list):
        return [sanitize_structure(v) for v in data]
    return data


class LocationQuery(BaseModel):
    query: str = Field(min_length=2, max_length=200)


class Coordinates(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class PreviewBody(BaseModel):
    lang: str = "de"
    voice: str | None = None
    text: str | None = Field(default=None, max_length=300)


class PersonBody(BaseModel):
    name: str = Field(max_length=60)
    anrede: str = "name"
    notes: str = Field(default="", max_length=4000)


class EnrollBody(BaseModel):
    mic_id: str


class AreaBody(BaseModel):
    name: str | None = Field(default=None, max_length=40)
    zone_uuid: str | None = None
    main_light: str | None = None
    rooms: list[str] | None = None


class AnnounceBody(BaseModel):
    area_id: str
    text: str | None = Field(default=None, max_length=300)
    lang: str = "de"


class MicBody(BaseModel):
    name: str | None = Field(default=None, max_length=40)
    area_id: str | None = None


class PairBody(BaseModel):
    ip: str = Field(max_length=64)
    name: str = Field(max_length=64)


class ControlBody(BaseModel):
    allowed: bool | None = None
    critical: bool | None = None
    aliases: list[str] | None = None
    light_kind: str | None = None


class BulkBody(BaseModel):
    group: str
    allowed: bool


def build_router(house: House) -> APIRouter:
    router = APIRouter(prefix="/api")
    limiter = LoginLimiter()

    def require(request: Request) -> None:
        if not house.auth.session_valid(request.cookies.get(SESSION_COOKIE)):
            raise HTTPException(status_code=401, detail="Anmeldung erforderlich")

    def ip_of(request: Request) -> str:
        return getattr(request.state, "client_ip", None) or (request.client.host if request.client else "")

    def set_cookie(response: Response, token: str) -> None:
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=SESSION_TTL,
            httponly=True,
            secure=not house.config.insecure_cookies,
            samesite="strict",
            path="/",
        )

    auth = [Depends(require)]

    # ---------------------------------------------------------------- session
    @router.get("/session")
    async def session(request: Request) -> dict:
        return {
            "authenticated": house.auth.session_valid(request.cookies.get(SESSION_COOKIE)),
            "setup_required": not house.auth.has_password(),
            "version": __version__,
            "https_port": house.config.https_port or None,
        }

    @router.post("/setup")
    async def setup(body: PasswordBody, request: Request) -> Response:
        if house.auth.has_password():
            raise HTTPException(409, "Passwort ist schon gesetzt")
        try:
            house.auth.set_password(body.password)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        response = JSONResponse({"ok": True})
        set_cookie(response, house.auth.create_session(ip_of(request)))
        house.events.log("security", "Oberflächen-Passwort gesetzt")
        return response

    @router.post("/login")
    async def login(body: PasswordBody, request: Request) -> Response:
        ip = ip_of(request)
        if limiter.blocked(ip):
            raise HTTPException(429, "Zu viele Versuche. Bitte fünf Minuten warten.")
        if not house.auth.check_password(body.password):
            limiter.fail(ip)
            await asyncio.sleep(0.8)
            house.events.log("security", "Fehlgeschlagene Anmeldung", ip)
            raise HTTPException(401, "Passwort falsch")
        limiter.reset(ip)
        response = JSONResponse({"ok": True})
        set_cookie(response, house.auth.create_session(ip))
        return response

    @router.post("/logout")
    async def logout(request: Request) -> Response:
        house.auth.end_session(request.cookies.get(SESSION_COOKIE))
        response = JSONResponse({"ok": True})
        response.delete_cookie(
            SESSION_COOKIE,
            path="/",
            httponly=True,
            secure=not house.config.insecure_cookies,
            samesite="strict",
        )
        return response

    @router.post("/password", dependencies=auth)
    async def change_password(body: ChangePasswordBody, request: Request) -> Response:
        if not house.auth.check_password(body.current):
            raise HTTPException(401, "Aktuelles Passwort falsch")
        try:
            house.auth.set_password(body.password)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        response = JSONResponse({"ok": True})
        set_cookie(response, house.auth.create_session(ip_of(request)))
        return response

    # --------------------------------------------------------------- overview
    def status() -> dict:
        mics = list(house.mics.mics.values())
        online = sum(1 for m in mics if m.connected)
        counts = house.catalog.counts(house.miniserver.state)
        audio_total = max(1, len(house.audioserver.clients))
        reachable_total = len(mics) + 1 + audio_total
        reachable = online + int(house.miniserver.connected) + house.audioserver.connected_count
        return {
            "gemini": {
                "configured": house.secrets.has("gemini.api_key"),
                "model": house.settings.get("gemini.model"),
                "error": house.brain.last_error,
                "latency_ms": house.brain.last_latency_ms,
            },
            "mics": {"online": online, "total": len(mics)},
            "loxone": {
                "status": house.miniserver.status,
                "error": house.miniserver.error,
                "name": house.miniserver.structure.ms_name,
            },
            "audioserver": {
                "status": house.audioserver.status,
                "error": house.audioserver.error,
                "servers": house.audioserver.servers(),
            },
            "counts": counts,
            "reachable_pct": int(round(100 * reachable / reachable_total)) if reachable_total else 0,
            "listening": online > 0,
            "response_ms": house.coordinator.average_latency,
            "phases": {area_id: s.phase.value for area_id, s in house.coordinator.sessions.items()},
        }

    @router.get("/overview", dependencies=auth)
    async def overview() -> dict:
        return {
            "status": status(),
            "usage": house.usage.current(),
            "events": house.events.recent(25),
            "last": house.coordinator.last,
            "timezone": house.settings.get("house.timezone"),
            "notes": house.settings.get("house.notes"),
            "default_anrede": house.settings.get("house.default_anrede"),
            "location": {
                "label": house.settings.get("house.location"),
                "lat": house.settings.get("house.lat"),
                "lon": house.settings.get("house.lon"),
                "suggestion": house.miniserver.structure.location,
            },
        }

    @router.post("/location/search", dependencies=auth)
    async def location_search(body: LocationQuery) -> dict:
        try:
            return {"results": await location.search(body.query)}
        except location.LocationError as exc:
            raise HTTPException(502, str(exc)) from exc

    @router.post("/location/reverse", dependencies=auth)
    async def location_reverse(body: Coordinates) -> dict:
        try:
            label = await location.reverse(body.lat, body.lon)
        except location.LocationError:
            label = f"{body.lat:.5f}, {body.lon:.5f}"
        return {"label": label, "lat": round(body.lat, 5), "lon": round(body.lon, 5)}

    # --------------------------------------------------------------- settings
    @router.get("/settings", dependencies=auth)
    async def get_settings() -> dict:
        return {
            "values": house.settings.all(),
            "secrets": {key: house.secrets.has(key) for key in sorted(SECRET_KEYS)},
        }

    @router.put("/settings", dependencies=auth)
    async def put_settings(body: SettingsBody) -> dict:
        try:
            changed = house.settings.update(body.values)
        except SettingsError as exc:
            return JSONResponse({"error": "Ungültige Eingabe", "fields": exc.errors}, status_code=400)
        return {"ok": True, "values": changed}

    @router.put("/secrets", dependencies=auth)
    async def put_secret(body: SecretBody) -> dict:
        if body.key not in SECRET_KEYS:
            raise HTTPException(400, "unbekannter Schlüssel")
        house.secrets.set(body.key, body.value.strip())
        house.secret_changed(body.key)
        house.events.log("security", f"Zugang {body.key} {'gesetzt' if body.value else 'gelöscht'}")
        return {"ok": True, "set": house.secrets.has(body.key)}

    # --------------------------------------------------------------------- ai
    @router.get("/ai/models", dependencies=auth)
    async def models() -> dict:
        try:
            return {"models": await house.brain.models(), "current": house.settings.get("gemini.model")}
        except BrainUnavailable as exc:
            return {"models": [house.settings.get("gemini.model")], "current": house.settings.get("gemini.model"), "error": str(exc)}

    @router.post("/ai/test", dependencies=auth)
    async def ai_test() -> dict:
        try:
            return await house.brain.test()
        except BrainUnavailable as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/voices", dependencies=auth)
    async def voices() -> dict:
        try:
            items = await house.piper.voices()
        except Exception as exc:  # noqa: BLE001
            return {"voices": [], "error": str(exc)}
        return {"voices": items}


    @router.get("/phrases", dependencies=auth)
    async def phrases() -> dict:
        book = house.phrases
        persona = house.settings.get("gemini.system_prompt")
        return {
            "examples": book.examples("de"),
            "generated_at": book.generated_at,
            "current": book.current_for(persona),
        }

    @router.post("/phrases/refresh", dependencies=auth)
    async def phrases_refresh() -> dict:
        if not house.secrets.has("gemini.api_key"):
            raise HTTPException(400, "Erst einen Gemini-Schlüssel setzen")
        if not await house.refresh_phrases(force=True):
            raise HTTPException(502, house.brain.last_error or "Gemini hat keine brauchbaren Sätze geliefert")
        return await phrases()

    @router.post("/tts/preview", dependencies=auth)
    async def preview(body: PreviewBody) -> Response:
        lang = "en" if body.lang == "en" else "de"
        text = body.text or (
            "Guten Abend. Alle Systeme sind bereit." if lang == "de" else "Good evening. All systems are ready."
        )
        try:
            audio = await house.piper.synthesize(text, lang, voice=body.voice)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, str(exc)) from exc
        return Response(audio, media_type="audio/wav")

    # ----------------------------------------------------------------- people
    @router.get("/people", dependencies=auth)
    async def people() -> dict:
        return {"people": house.people.list(), "sentences": list(ENROLL_SENTENCES)}

    @router.post("/people", dependencies=auth)
    async def create_person(body: PersonBody) -> dict:
        try:
            return house.people.create(body.name, body.anrede, body.notes)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.put("/people/{person_id}", dependencies=auth)
    async def update_person(person_id: str, body: PersonBody) -> dict:
        try:
            person = house.people.update(person_id, body.name, body.anrede, body.notes)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if person is None:
            raise HTTPException(404, "unbekannt")
        return person

    @router.delete("/people/{person_id}", dependencies=auth)
    async def delete_person(person_id: str) -> dict:
        house.people.delete(person_id)
        return {"ok": True}

    @router.delete("/people/{person_id}/voice", dependencies=auth)
    async def delete_voice(person_id: str) -> dict:
        house.people.set_voice(person_id, None)
        return {"ok": True}

    @router.post("/people/{person_id}/enroll", dependencies=auth)
    async def enroll(person_id: str, body: EnrollBody) -> dict:
        try:
            await house.coordinator.start_enroll(person_id, body.mic_id)
        except KeyError as exc:
            raise HTTPException(404, "Person unbekannt") from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True, "sentences": list(ENROLL_SENTENCES)}

    @router.post("/people/enroll/cancel", dependencies=auth)
    async def enroll_cancel(body: EnrollBody) -> dict:
        house.coordinator.cancel_enroll(body.mic_id)
        return {"ok": True}

    # ------------------------------------------------------------------ areas
    @router.get("/areas", dependencies=auth)
    async def areas() -> dict:
        return {"areas": [a.public() for a in house.areas.list()]}

    @router.post("/areas", dependencies=auth)
    async def create_area(body: AreaBody) -> dict:
        try:
            return house.areas.create(body.name or "").public()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.put("/areas/{area_id}", dependencies=auth)
    async def update_area(area_id: str, body: AreaBody) -> dict:
        fields = body.model_dump(exclude_unset=True)
        if fields.get("main_light"):
            entry = house.catalog.get(fields["main_light"])
            if entry is None or entry.group != "light":
                raise HTTPException(400, "Hauptlicht muss ein Lichtbaustein sein")
        if fields.get("zone_uuid") and fields["zone_uuid"] not in {z["uuid"] for z in house.catalog.zones()}:
            raise HTTPException(400, "Audio-Zone unbekannt")
        try:
            return house.areas.update(area_id, **fields).public()
        except KeyError as exc:
            raise HTTPException(404, "Bereich unbekannt") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.delete("/areas/{area_id}", dependencies=auth)
    async def delete_area(area_id: str) -> dict:
        house.areas.delete(area_id)
        for mic in house.mics.mics.values():
            if mic.area_id == area_id:
                mic.area_id = None
        return {"ok": True}


    @router.post("/announce/test", dependencies=auth)
    async def announce_test(body: AnnounceBody) -> dict:
        area = house.areas.get(body.area_id)
        if area is None:
            raise HTTPException(404, "Bereich unbekannt")
        lang = "en" if body.lang == "en" else "de"
        text = body.text or ("Testansage. Hier spricht Jarvis." if lang == "de" else "Test announcement. Jarvis here.")
        ok = await house.speech.say(text, lang, [area])
        return {"ok": ok, "error": None if ok else (house.audioserver.error or "siehe Aktivität")}

    # ------------------------------------------------------------------- mics
    @router.get("/mics", dependencies=auth)
    async def mics() -> dict:
        firmware = house.firmware_info()
        return {
            "mics": [m.public() for m in house.mics.mics.values()],
            "recommendation": RECOMMENDATION,
            "firmware": firmware,
            "device_url": house.device_url(),
        }

    @router.put("/mics/{mic_id}", dependencies=auth)
    async def update_mic(mic_id: str, body: MicBody) -> dict:
        if mic_id not in house.mics.mics:
            raise HTTPException(404, "Mikrofon unbekannt")
        fields = body.model_dump(exclude_unset=True)
        try:
            mic = house.mics.update(
                mic_id,
                label=fields.get("name"),
                area_id=fields["area_id"] if "area_id" in fields else False,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return mic.public()

    @router.delete("/mics/{mic_id}", dependencies=auth)
    async def delete_mic(mic_id: str) -> dict:
        await house.mics.remove(mic_id)
        return {"ok": True}

    @router.post("/mics/{mic_id}/update", dependencies=auth)
    async def update_firmware(mic_id: str) -> dict:
        try:
            await house.mics.update_firmware(mic_id)
        except (KeyError, RuntimeError) as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True}

    def pair_payload() -> dict:
        """WiFi for Improv. Only handed to a logged-in browser or a fresh pair ticket."""
        return {
            "wifi_ssid": house.settings.get("mics.wifi_ssid"),
            "wifi_password": house.secrets.get("mics.wifi_password") or "",
            "device_url": house.device_url(),
            "firmware": house.firmware_info(),
        }

    async def pair_board(body: PairBody) -> dict:
        from .security import is_trusted

        if not is_trusted(body.ip, house.config.trusted_networks):
            raise HTTPException(400, "Das Board muss eine Adresse im Hausnetz haben")
        try:
            mic = await house.mics.pair(body.ip, body.name)
        except PairError as exc:
            raise HTTPException(409, str(exc)) from exc
        return mic.public()

    tickets: dict[str, float] = {}

    def issue_ticket() -> str:
        now = time.monotonic()
        for key, expires in list(tickets.items()):
            if expires < now:
                tickets.pop(key, None)
        token = new_token(18)
        tickets[token] = now + 600
        return token

    def ticket_ok(token: str) -> None:
        if tickets.get(token, 0) < time.monotonic():
            tickets.pop(token, None)
            raise HTTPException(401, "Pair-Programm abgelaufen. In der Oberfläche erneut laden.")

    @router.get("/mics/pair", dependencies=auth)
    async def pair_info() -> dict:
        return pair_payload()

    @router.post("/mics/pair", dependencies=auth)
    async def pair(body: PairBody) -> dict:
        return await pair_board(body)

    @router.get("/mics/pair/helper", dependencies=auth)
    async def pair_helper(request: Request) -> Response:
        """Script that opens localhost so USB pairing works on http://<IP>."""
        token = issue_ticket()
        origin = f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"
        template = (Path(__file__).parent / "static" / "pair" / "jarvis-pair.py").read_text(encoding="utf-8")
        script = template.replace("__JARVIS_URL__", origin).replace("__JARVIS_TOKEN__", token)
        return Response(
            script,
            media_type="text/x-python; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="jarvis-pair.py"'},
        )

    @router.get("/mics/pair/helper/{token}")
    async def pair_helper_info(token: str) -> dict:
        ticket_ok(token)
        return pair_payload()

    @router.post("/mics/pair/helper/{token}")
    async def pair_helper_finish(token: str, body: PairBody) -> dict:
        ticket_ok(token)
        mic = await pair_board(body)
        tickets.pop(token, None)
        return mic

    # ----------------------------------------------------------------- loxone
    @router.get("/loxone", dependencies=auth)
    async def loxone() -> dict:
        ms = house.miniserver
        mcp = house.mcp
        return {
            "status": ms.status,
            "error": ms.error,
            "host": house.settings.get("loxone.host"),
            "user": house.settings.get("loxone.user"),
            "tls": house.settings.get("loxone.tls"),
            "name": ms.structure.ms_name,
            "datapoints": len(ms.values) + len(ms.texts),
            "last_sync": ms.last_sync,
            "rights": ms.token_rights,
            "counts": house.catalog.counts(ms.state),
            "audioserver": {
                "status": house.audioserver.status,
                "error": house.audioserver.error,
                "host": ", ".join(house.audioserver.hosts()) or None,
                "servers": house.audioserver.servers(),
            },
            "mcp": {"url": house.settings.get("mcp.url"), "error": mcp.last_error},
            "last": house.coordinator.last,
        }

    @router.get("/loxone/structure", dependencies=auth)
    async def loxone_structure() -> Response:
        raw = house.miniserver.raw_structure
        if raw is None:
            raise HTTPException(404, "Noch keine Struktur vom Miniserver geladen")
        return Response(
            json.dumps(sanitize_structure(raw), ensure_ascii=False, indent=1),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="jarvis-loxone-struktur.json"'},
        )

    @router.post("/loxone/reconnect", dependencies=auth)
    async def loxone_reconnect() -> dict:
        house.miniserver.forget_token()
        house.miniserver.reconnect()
        house.audioserver.reconnect()
        return {"ok": True}

    @router.get("/loxone/controls", dependencies=auth)
    async def controls() -> dict:
        state = house.miniserver.state
        items = []
        for entry in house.catalog.entries():
            data = entry.public()
            data["state"] = house.catalog.describe_state(entry, state)
            items.append(data)
        return {"controls": items}

    @router.put("/loxone/controls/{short_id}", dependencies=auth)
    async def update_control(short_id: str, body: ControlBody) -> dict:
        try:
            entry = house.catalog.update(short_id, **body.model_dump(exclude_unset=True))
        except KeyError as exc:
            raise HTTPException(404, "Baustein unbekannt") from exc
        except ActionError as exc:
            raise HTTPException(400, str(exc)) from exc
        return entry.public()

    @router.post("/loxone/controls/bulk", dependencies=auth)
    async def bulk(body: BulkBody) -> dict:
        changed = 0
        for entry in house.catalog.entries():
            if entry.group == body.group and not entry.secured and entry.allowed != body.allowed:
                house.catalog.update(entry.short_id, allowed=body.allowed)
                changed += 1
        return {"changed": changed}

    @router.get("/loxone/zones", dependencies=auth)
    async def zones() -> dict:
        return {
            "zones": house.catalog.zones(),
            "rooms": house.catalog.rooms(),
            "lights": [
                {"id": e.short_id, "name": e.name, "room": e.room}
                for e in house.catalog.entries()
                if e.group == "light"
            ],
        }

    @router.post("/mcp/test", dependencies=auth)
    async def mcp_test() -> dict:
        if not house.mcp.configured:
            return {"ok": False, "error": "Keine Adresse eingetragen"}
        try:
            return await house.mcp.test()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}

    @router.post("/audioserver/test", dependencies=auth)
    async def audioserver_test() -> dict:
        servers = await house.audioserver.test(timeout=6)
        if not servers:
            return {"ok": False, "servers": [], "error": "Kein Audioserver gefunden"}
        failed = [s for s in servers if not s["ok"]]
        return {
            "ok": not failed,
            "servers": servers,
            "host": ", ".join(s["host"] for s in servers),
            "error": "; ".join(f"{s['name']}: {s['error']}" for s in failed) or None,
        }

    # ------------------------------------------------------------------- live
    def ws_allowed(ws: WebSocket) -> bool:
        origin = ws.headers.get("origin", "")
        host = ws.headers.get("host", "")
        return house.auth.session_valid(ws.cookies.get(SESSION_COOKIE)) and not (
            origin and origin.split("://", 1)[-1] != host
        )

    @router.websocket("/live/voice")
    async def live_voice(ws: WebSocket) -> None:
        if not ws_allowed(ws):
            await ws.close(code=1008)
            return
        await ws.accept()
        session = LiveSession(house, ws.send_json)
        house.live_sessions.add(session)
        worker = asyncio.create_task(session.run(), name="live-voice")
        try:
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("bytes"):
                    session.feed(message["bytes"])
                elif message.get("text"):
                    data = json.loads(message["text"])
                    if isinstance(data, dict):
                        await session.command(data)
        except (WebSocketDisconnect, RuntimeError, ValueError):
            pass
        finally:
            worker.cancel()
            house.live_sessions.discard(session)

    @router.websocket("/live")
    async def live(ws: WebSocket) -> None:
        if not ws_allowed(ws):
            await ws.close(code=1008)
            return
        await ws.accept()
        queue = house.events.subscribe()
        await ws.send_json({"type": "hello", "status": status(), "ts": time.time()})
        try:
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=20)
                except asyncio.TimeoutError:
                    message = {"type": "status", "status": status()}
                await ws.send_json(message)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            house.events.unsubscribe(queue)

    return router
