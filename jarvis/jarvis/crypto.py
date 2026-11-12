"""Encryption of stored secrets (API key, Miniserver password, WiFi, mic keys).

The key is derived from JARVIS_SECRET_KEY, which lives only in Coolify and
never on the data volume. A per-install salt sits next to the database.
"""

from __future__ import annotations

import os
import secrets as pysecrets
import time
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .db import Database


class SecretError(RuntimeError):
    pass


def _load_salt(data_dir: Path) -> bytes:
    path = data_dir / "secret.salt"
    if path.exists():
        return path.read_bytes()
    data_dir.mkdir(parents=True, exist_ok=True)
    salt = os.urandom(16)
    path.write_bytes(salt)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return salt


def derive_key(secret_key: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1)
    return kdf.derive(secret_key.encode("utf-8"))


class SecretStore:
    """Write-only from the UI's perspective: values are never sent back."""

    def __init__(self, db: Database, secret_key: str, data_dir: Path):
        self._db = db
        self._aead = AESGCM(derive_key(secret_key, _load_salt(data_dir)))
        self._check_key()

    def _check_key(self) -> None:
        marker = self._db.one("SELECT key FROM secrets WHERE key = '_check'")
        if marker is None:
            self.set("_check", "ok")
            return
        try:
            self.get("_check")
        except SecretError as exc:
            raise SystemExit(
                "JARVIS_SECRET_KEY passt nicht zu den gespeicherten Schlüsseln. "
                "Den alten Wert in Coolify wiederherstellen."
            ) from exc

    def set(self, key: str, value: str | bytes | None) -> None:
        if value is None or value == "" or value == b"":
            self.delete(key)
            return
        raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        nonce = os.urandom(12)
        ciphertext = self._aead.encrypt(nonce, raw, key.encode("utf-8"))
        self._db.execute(
            "INSERT INTO secrets(key, nonce, ciphertext, updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET nonce=excluded.nonce, "
            "ciphertext=excluded.ciphertext, updated_at=excluded.updated_at",
            (key, nonce, ciphertext, time.time()),
        )

    def get_bytes(self, key: str) -> bytes | None:
        row = self._db.one("SELECT nonce, ciphertext FROM secrets WHERE key = ?", (key,))
        if row is None:
            return None
        try:
            return self._aead.decrypt(row["nonce"], row["ciphertext"], key.encode("utf-8"))
        except InvalidTag as exc:
            raise SecretError(f"secret {key} cannot be decrypted") from exc

    def get(self, key: str) -> str | None:
        raw = self.get_bytes(key)
        return raw.decode("utf-8") if raw is not None else None

    def has(self, key: str) -> bool:
        return self._db.one("SELECT 1 AS x FROM secrets WHERE key = ?", (key,)) is not None

    def delete(self, key: str) -> None:
        self._db.execute("DELETE FROM secrets WHERE key = ?", (key,))


def new_token(nbytes: int = 32) -> str:
    return pysecrets.token_urlsafe(nbytes)
