"""Process entry: the command centre on JARVIS_HTTP_PORT, the board listener on JARVIS_DEVICE_PORT."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import build_router
from .config import Config
from .device_api import create_device_app
from .house import STATIC_DIR, House
from .security import HardeningMiddleware, LanOnlyMiddleware


def create_app(house: House) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(HardeningMiddleware)
    app.add_middleware(LanOnlyMiddleware, networks=house.config.trusted_networks)
    app.include_router(build_router(house))
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/health", include_in_schema=False)
    async def health() -> dict:
        return {"ok": True}

    return app


class _SecondaryServer(uvicorn.Server):
    """The device listener stops with the main server and leaves signals to it."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield

    def install_signal_handlers(self) -> None:  # older uvicorn
        return


async def serve() -> None:
    logging.basicConfig(
        level=os.environ.get("JARVIS_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for noisy in ("uvicorn.access", "httpx", "google_genai", "aioesphomeapi"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    config = Config.from_env()
    house = House(config)
    await house.start()
    main = uvicorn.Server(
        uvicorn.Config(
            create_app(house),
            host="0.0.0.0",
            port=config.http_port,
            proxy_headers=False,
            access_log=False,
            ws_max_size=1 << 20,
        )
    )
    devices = _SecondaryServer(
        uvicorn.Config(create_device_app(house), host="0.0.0.0", port=config.device_port, access_log=False)
    )
    device_task = asyncio.create_task(devices.serve())
    try:
        await main.serve()
    finally:
        devices.should_exit = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(device_task, timeout=5)
        await house.stop()


def run() -> None:
    asyncio.run(serve())
