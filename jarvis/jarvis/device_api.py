"""Second listener on the house network for the boards only: hello and firmware download."""

from __future__ import annotations

import ipaddress
import time
from collections import defaultdict, deque

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .house import FIRMWARE_DIR, House
from .security import LanOnlyMiddleware


class Hello(BaseModel):
    name: str = Field(max_length=64)
    ip: str = Field(default="", max_length=64)
    esphome_version: str | None = Field(default=None, max_length=40)


def create_device_app(house: House) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(LanOnlyMiddleware, networks=house.config.trusted_networks)
    calls: dict[str, deque[float]] = defaultdict(deque)

    def limited(ip: str) -> bool:
        queue = calls[ip]
        now = time.monotonic()
        while queue and now - queue[0] > 60:
            queue.popleft()
        queue.append(now)
        return len(queue) > 20

    @app.post("/device/hello")
    async def hello(body: Hello, request: Request) -> dict:
        source = request.client.host if request.client else ""
        if limited(source):
            raise HTTPException(429)
        host = body.ip or source
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise HTTPException(400) from exc
        if not address.is_private:
            raise HTTPException(400)
        known = await house.mics.hello(body.name.strip().lower(), str(address), body.esphome_version)
        return {"known": known}

    @app.get("/device/firmware/ota.bin")
    async def firmware() -> FileResponse:
        path = FIRMWARE_DIR / "jarvis-mic.ota.bin"
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(path, media_type="application/octet-stream")

    return app
