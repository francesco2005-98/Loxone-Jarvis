"""Login, sessions, LAN-only access and response hardening."""

from __future__ import annotations

import hashlib
import ipaddress
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.types import ASGIApp

from .crypto import new_token
from .db import Database

SESSION_COOKIE = "jarvis_session"
SESSION_TTL = 7 * 24 * 3600
PASSWORD_KEY = "ui.password_hash"
MIN_PASSWORD = 10

_hasher = PasswordHasher()

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
    "media-src 'self' blob:; font-src 'self'; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored: str | None, password: str) -> bool:
    if not stored:
        return False
    try:
        return _hasher.verify(stored, password)
    except (VerificationError, InvalidHashError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Auth:
    def __init__(self, db: Database):
        self._db = db

    def password_hash(self) -> str | None:
        row = self._db.one("SELECT value FROM settings WHERE key = ?", (PASSWORD_KEY,))
        if not row:
            return None
        import json

        return json.loads(row["value"])

    def has_password(self) -> bool:
        return self.password_hash() is not None

    def set_password(self, password: str) -> None:
        if len(password) < MIN_PASSWORD:
            raise ValueError(f"Mindestens {MIN_PASSWORD} Zeichen")
        import json

        self._db.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (PASSWORD_KEY, json.dumps(hash_password(password))),
        )
        self._db.execute("DELETE FROM sessions")

    def check_password(self, password: str) -> bool:
        return verify_password(self.password_hash(), password)

    def create_session(self, ip: str) -> str:
        token = new_token()
        now = time.time()
        self._db.execute(
            "INSERT INTO sessions(token_hash, created_at, expires_at, ip) VALUES(?,?,?,?)",
            (_token_hash(token), now, now + SESSION_TTL, ip),
        )
        return token

    def session_valid(self, token: str | None) -> bool:
        if not token:
            return False
        row = self._db.one(
            "SELECT expires_at FROM sessions WHERE token_hash = ?", (_token_hash(token),)
        )
        return bool(row and row["expires_at"] > time.time())

    def end_session(self, token: str | None) -> None:
        if token:
            self._db.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))

    def prune(self) -> None:
        self._db.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))


class LoginLimiter:
    """At most five failed logins per address in five minutes."""

    def __init__(self, attempts: int = 5, window: float = 300.0):
        self.attempts = attempts
        self.window = window
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def blocked(self, ip: str) -> bool:
        queue = self._failures[ip]
        now = time.monotonic()
        while queue and now - queue[0] > self.window:
            queue.popleft()
        return len(queue) >= self.attempts

    def fail(self, ip: str) -> None:
        self._failures[ip].append(time.monotonic())

    def reset(self, ip: str) -> None:
        self._failures.pop(ip, None)


def client_ip(scope_client: tuple[str, int] | None, forwarded_for: str | None, trusted) -> str:
    """Return the address of the human behind the request.

    The peer is trusted as a proxy only when it is itself inside the trusted
    networks (Coolify's Traefik on the Docker network). Then the last
    X-Forwarded-For entry is the one that proxy appended.
    """
    peer = scope_client[0] if scope_client else ""
    if forwarded_for and _in_networks(peer, trusted):
        last = forwarded_for.split(",")[-1].strip()
        if last:
            return last
    return peer


def _in_networks(ip: str, networks) -> bool:
    try:
        address = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    return any(address in net for net in networks)


def is_trusted(ip: str, networks) -> bool:
    return _in_networks(ip, networks)


class LanOnlyMiddleware:
    """Pure ASGI so it also guards websockets, which BaseHTTPMiddleware skips."""

    def __init__(self, app: ASGIApp, networks):
        self.app = app
        self.networks = networks

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        ip = client_ip(scope.get("client"), headers.get("x-forwarded-for"), self.networks)
        if not is_trusted(ip, self.networks):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            response = PlainTextResponse("Nur im Hausnetz erreichbar.", status_code=403)
            await response(scope, receive, send)
            return
        scope.setdefault("state", {})["client_ip"] = ip
        await self.app(scope, receive, send)


class HardeningMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/"):
            if request.headers.get("x-jarvis") != "1":
                return JSONResponse({"error": "Anfrage abgelehnt"}, status_code=403)
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("Content-Security-Policy", CSP)
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("Referrer-Policy", "no-referrer")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        headers.setdefault("Permissions-Policy", "serial=(self), camera=(), microphone=(self), geolocation=(self)")
        if request.url.path.startswith("/api/"):
            headers.setdefault("Cache-Control", "no-store")
        elif request.url.path.startswith("/static/"):
            headers.setdefault("Cache-Control", "no-cache")
        return response
