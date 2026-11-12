"""Process configuration from the environment.

Only bootstrap values live here. Everything the household configures is stored
in the database and edited in the web interface.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TRUSTED = (
    "127.0.0.0/8",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "::1/128",
    "fc00::/7",
    "fe80::/10",
)


def _networks(raw: str | None) -> tuple[ipaddress._BaseNetwork, ...]:
    items = [p.strip() for p in (raw or "").split(",") if p.strip()]
    if not items:
        items = list(DEFAULT_TRUSTED)
    return tuple(ipaddress.ip_network(p, strict=False) for p in items)


@dataclass(frozen=True)
class Config:
    secret_key: str
    data_dir: Path = Path("/data")
    initial_password: str | None = None
    piper_url: str = "http://piper:5000"
    http_port: int = 8000
    device_port: int = 8765
    models_dir: Path = Path("/models")
    insecure_cookies: bool = False
    trusted_networks: tuple = field(default_factory=lambda: _networks(None))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jarvis.sqlite3"

    @classmethod
    def from_env(cls) -> "Config":
        secret = os.environ.get("JARVIS_SECRET_KEY", "")
        if len(secret) < 24:
            raise SystemExit(
                "JARVIS_SECRET_KEY fehlt oder ist kürzer als 24 Zeichen. "
                "In Coolify als Secret setzen, z. B. mit `openssl rand -base64 48`."
            )
        return cls(
            secret_key=secret,
            data_dir=Path(os.environ.get("JARVIS_DATA_DIR", "/data")),
            initial_password=os.environ.get("JARVIS_INITIAL_PASSWORD") or None,
            piper_url=os.environ.get("JARVIS_PIPER_URL", "http://piper:5000").rstrip("/"),
            http_port=int(os.environ.get("JARVIS_HTTP_PORT", "8000")),
            device_port=int(os.environ.get("JARVIS_DEVICE_PORT", "8765")),
            models_dir=Path(os.environ.get("JARVIS_MODELS_DIR", "/models")),
            insecure_cookies=os.environ.get("JARVIS_INSECURE_COOKIES", "") == "1",
            trusted_networks=_networks(os.environ.get("JARVIS_TRUSTED_NETWORKS")),
        )
