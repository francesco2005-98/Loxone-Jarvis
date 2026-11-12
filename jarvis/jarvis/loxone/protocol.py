"""Loxone Miniserver wire protocol: key exchange, hashes, encryption, binary frames.

Follows "Communicating with the Miniserver" (Loxone, v16/v17) and the
behaviour of Loxone's own lxcommunicator.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import struct
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

LOX_EPOCH = 1230768000
HASHES = {"SHA1": hashlib.sha1, "SHA256": hashlib.sha256}

IDENT_TEXT = 0
IDENT_FILE = 1
IDENT_VALUES = 2
IDENT_TEXTS = 3
IDENT_DAYTIMER = 4
IDENT_OUT_OF_SERVICE = 5
IDENT_KEEPALIVE = 6
IDENT_WEATHER = 7


def quote(value: object) -> str:
    return urllib.parse.quote(str(value), safe="")


def load_public_key(value: str):
    body = re.sub(r"-----(?:BEGIN|END) [A-Z ]+-----|\s+", "", value)
    return serialization.load_der_public_key(base64.b64decode(body))


def lox_uuid(raw: bytes) -> str:
    d1, d2, d3 = struct.unpack_from("<IHH", raw)
    return f"{d1:08x}-{d2:04x}-{d3:04x}-{raw[8:16].hex()}"


def new_client_uuid() -> str:
    h = uuid.uuid4().hex
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:]}"


def password_hash(user: str, password: str, key_hex: str, salt: str, alg: str) -> str:
    digest = HASHES.get(alg, hashlib.sha1)
    pw = digest(f"{password}:{salt}".encode("utf-8")).hexdigest().upper()
    return hmac.new(bytes.fromhex(key_hex), f"{user}:{pw}".encode("utf-8"), digest).hexdigest()


def token_hash(token: str, key_hex: str, alg: str) -> str:
    digest = HASHES.get(alg, hashlib.sha1)
    return hmac.new(bytes.fromhex(key_hex), token.encode("utf-8"), digest).hexdigest()


@dataclass
class Session:
    """AES session for one websocket connection."""

    public_key: object
    key: bytes = field(default_factory=lambda: os.urandom(32))
    iv: bytes = field(default_factory=lambda: os.urandom(16))
    salt: str = field(default_factory=lambda: os.urandom(16).hex())
    salt_time: float = field(default_factory=time.monotonic)
    salt_uses: int = 0

    def keyexchange_command(self) -> str:
        plain = f"{self.key.hex()}:{self.iv.hex()}".encode("ascii")
        cipher = self.public_key.encrypt(plain, padding.PKCS1v15())
        return "jdev/sys/keyexchange/" + base64.b64encode(cipher).decode("ascii")

    def encrypt_command(self, cmd: str) -> str:
        self.salt_uses += 1
        if self.salt_uses > 30 or time.monotonic() - self.salt_time > 3600:
            new_salt = os.urandom(16).hex()
            plain = f"nextSalt/{self.salt}/{new_salt}/{cmd}"
            self.salt, self.salt_time, self.salt_uses = new_salt, time.monotonic(), 0
        else:
            plain = f"salt/{self.salt}/{cmd}"
        data = plain.encode("utf-8") + b"\x00"
        data += b"\x00" * (-len(data) % 16)
        enc = Cipher(algorithms.AES(self.key), modes.CBC(self.iv)).encryptor()
        blob = base64.b64encode(enc.update(data) + enc.finalize()).decode("ascii")
        return "jdev/sys/enc/" + quote(blob)

    def decrypt(self, b64: str) -> str:
        raw = base64.b64decode(urllib.parse.unquote(b64))
        raw += b"\x00" * (-len(raw) % 16)
        dec = Cipher(algorithms.AES(self.key), modes.CBC(self.iv)).decryptor()
        return (dec.update(raw) + dec.finalize()).split(b"\x00", 1)[0].decode("utf-8", "replace")


@dataclass(frozen=True)
class Header:
    identifier: int
    estimated: bool
    length: int


def parse_header(frame: bytes) -> Header | None:
    if len(frame) != 8 or frame[0] != 0x03:
        return None
    _, ident, info, _, length = struct.unpack("<BBBBI", frame)
    return Header(ident, bool(info & 0x80), length)


def parse_value_states(payload: bytes) -> dict[str, float]:
    usable = len(payload) - len(payload) % 24
    return {
        lox_uuid(payload[o : o + 16]): struct.unpack_from("<d", payload, o + 16)[0]
        for o in range(0, usable, 24)
    }


def parse_text_states(payload: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    offset = 0
    while offset + 36 <= len(payload):
        (length,) = struct.unpack_from("<I", payload, offset + 32)
        text = payload[offset + 36 : offset + 36 + length].decode("utf-8", "replace")
        out[lox_uuid(payload[offset : offset + 16])] = text.rstrip("\x00")
        offset += 36 + length + (-length % 4)
    return out


@dataclass
class Reply:
    control: str
    value: object
    code: int

    @property
    def ok(self) -> bool:
        return self.code == 200


def parse_reply(text: str) -> Reply | None:
    try:
        data = json.loads(text)
    except ValueError:
        return None
    ll = data.get("LL") if isinstance(data, dict) else None
    if not isinstance(ll, dict):
        return None
    code = ll.get("Code", ll.get("code", 0))
    try:
        code = int(code)
    except (TypeError, ValueError):
        code = 0
    value = ll.get("value")
    if isinstance(value, str) and value[:1] in "{[":
        try:
            value = json.loads(value)
        except ValueError:
            pass
    return Reply(str(ll.get("control", "")), value, code)


def lox_time_to_epoch(seconds: float) -> float:
    return LOX_EPOCH + float(seconds)
