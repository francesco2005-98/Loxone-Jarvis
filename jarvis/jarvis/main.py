"""Process entry: the command centre on JARVIS_HTTP_PORT (and HTTPS on JARVIS_HTTPS_PORT), the board listener on JARVIS_DEVICE_PORT."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .api import build_router
from .config import Config
from .device_api import create_device_app
from .house import STATIC_DIR, House
from .security import HardeningMiddleware, LanOnlyMiddleware


log = logging.getLogger("jarvis")


def create_app(house: House) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(HardeningMiddleware)
    app.add_middleware(LanOnlyMiddleware, networks=house.config.trusted_networks)
    app.include_router(build_router(house))
    app.add_middleware(_HostObserver, certs=house.certs)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/manifest.webmanifest", include_in_schema=False)
    async def manifest() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json", headers={"Cache-Control": "no-cache"}
        )

    @app.get("/sw.js", include_in_schema=False)
    async def service_worker() -> FileResponse:
        return FileResponse(STATIC_DIR / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})

    @app.get("/jarvis-ca.crt", include_in_schema=False)
    async def ca_certificate() -> Response:
        if not house.certs.ca_cert.exists():
            return Response("HTTPS ist ausgeschaltet", status_code=404)
        return Response(
            house.certs.ca_cert.read_bytes(),
            media_type="application/x-x509-ca-cert",
            headers={"Content-Disposition": 'attachment; filename="jarvis-ca.crt"', "Cache-Control": "no-cache"},
        )

    @app.get("/health", include_in_schema=False)
    async def health() -> dict:
        return {"ok": True}

    return app


class _HostObserver:
    """Every address the interface is opened with ends up in the HTTPS certificate."""

    def __init__(self, app, certs):
        self.app = app
        self.certs = certs

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] in ("http", "websocket") and self.certs.ready:
            host = dict(scope.get("headers") or []).get(b"host", b"").decode("latin-1")
            if host:
                self.certs.observe(host)
        await self.app(scope, receive, send)


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
    app = create_app(house)
    main = uvicorn.Server(
        uvicorn.Config(
            app,
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
    secondary = [(devices, device_task)]
    log.info("Weboberfläche erreichbar auf Port %s", config.http_port)
    secure = _secure_server(app, config, house)
    if secure is not None:
        secondary.append((secure, asyncio.create_task(secure.serve())))
        log.info(
            "Live-Modus und App über HTTPS auf Port %s. Zertifikat einmal pro Gerät installieren: http://<Server>:%s/jarvis-ca.crt",
            config.https_port,
            config.http_port,
        )
    try:
        await main.serve()
    finally:
        for server, _ in secondary:
            server.should_exit = True
        for _, task in secondary:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(task, timeout=5)
        await house.stop()


def _secure_server(app: FastAPI, config: Config, house: House) -> uvicorn.Server | None:
    if not config.https_port:
        return None
    try:
        cert, key = house.certs.ensure()
    except Exception as exc:  # noqa: BLE001
        log.warning("HTTPS nicht verfügbar, Live-Modus ohne Mikrofon: %s", exc)
        return None
    server = _SecondaryServer(
        uvicorn.Config(
            app,
            host="0.0.0.0",
            port=config.https_port,
            ssl_certfile=str(cert),
            ssl_keyfile=str(key),
            proxy_headers=False,
            access_log=False,
            ws_max_size=1 << 20,
        )
    )

    def reload() -> None:
        # New handshakes pick up the re-signed certificate; open connections keep theirs.
        context = getattr(server.config, "ssl", None)
        if context is not None:
            context.load_cert_chain(str(house.certs.cert), str(house.certs.key))

    house.certs.on_change(reload)
    return server


def run() -> None:
    asyncio.run(serve())
