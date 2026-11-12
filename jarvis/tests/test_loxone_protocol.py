import base64
import hashlib
import hmac
import struct
import urllib.parse
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from jarvis.loxone import protocol as p


def test_password_hash_matches_documented_formula():
    key = "41434633443642453730374531373636"
    salt = "31346231666666362d3439393030"
    expected_pw = hashlib.sha1(b"geheim:" + salt.encode()).hexdigest().upper()
    expected = hmac.new(bytes.fromhex(key), f"jarvis:{expected_pw}".encode(), hashlib.sha1).hexdigest()
    assert p.password_hash("jarvis", "geheim", key, salt, "SHA1") == expected
    expected256 = hmac.new(
        bytes.fromhex(key),
        f"jarvis:{hashlib.sha256(b'geheim:' + salt.encode()).hexdigest().upper()}".encode(),
        hashlib.sha256,
    ).hexdigest()
    assert p.password_hash("jarvis", "geheim", key, salt, "SHA256") == expected256


def test_token_hash_is_lowercase_hmac():
    out = p.token_hash("tok", "0a0b", "SHA256")
    assert out == hmac.new(bytes.fromhex("0a0b"), b"tok", hashlib.sha256).hexdigest()
    assert out == out.lower()


def test_uuid_layout_matches_bytes_le():
    raw = uuid.uuid4().bytes
    text = p.lox_uuid(raw)
    std = str(uuid.UUID(bytes_le=raw))
    assert text == std[:23] + std[24:]
    assert len(text.split("-")) == 4


def _key_pair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    der = private.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    loxone_style = "-----BEGIN CERTIFICATE-----" + base64.b64encode(der).decode() + "-----END CERTIFICATE-----"
    return private, loxone_style


def test_keyexchange_and_command_encryption_roundtrip():
    private, pem = _key_pair()
    session = p.Session(p.load_public_key(pem))
    cmd = session.keyexchange_command()
    assert cmd.startswith("jdev/sys/keyexchange/")
    plain = private.decrypt(base64.b64decode(cmd.split("/", 3)[3]), padding.PKCS1v15()).decode()
    assert plain == f"{session.key.hex()}:{session.iv.hex()}"

    enc = session.encrypt_command("jdev/sys/getkey2/jarvis")
    assert enc.startswith("jdev/sys/enc/")
    decrypted = session.decrypt(urllib.parse.unquote(enc[len("jdev/sys/enc/") :]))
    assert decrypted.startswith("salt/") and decrypted.endswith("/jdev/sys/getkey2/jarvis")


def test_salt_rotates_with_next_salt():
    _, pem = _key_pair()
    session = p.Session(p.load_public_key(pem))
    seen = [session.decrypt(urllib.parse.unquote(session.encrypt_command("x")[13:])) for _ in range(32)]
    assert any(s.startswith("nextSalt/") for s in seen)


def test_header_and_states():
    header = struct.pack("<BBBBI", 3, 2, 0, 0, 48)
    parsed = p.parse_header(header)
    assert parsed.identifier == 2 and parsed.length == 48 and not parsed.estimated
    assert p.parse_header(struct.pack("<BBBBI", 3, 0, 0x80, 0, 0)).estimated
    u1, u2 = uuid.uuid4().bytes, uuid.uuid4().bytes
    payload = u1 + struct.pack("<d", 1.5) + u2 + struct.pack("<d", -2.0)
    values = p.parse_value_states(payload)
    assert values[p.lox_uuid(u1)] == 1.5 and values[p.lox_uuid(u2)] == -2.0

    text = "Szene Abend".encode()
    record = u1 + uuid.uuid4().bytes + struct.pack("<I", len(text)) + text + b"\x00" * (-len(text) % 4)
    record2 = u2 + uuid.uuid4().bytes + struct.pack("<I", 2) + b"ok\x00\x00"
    texts = p.parse_text_states(record + record2)
    assert texts[p.lox_uuid(u1)] == "Szene Abend"
    assert texts[p.lox_uuid(u2)] == "ok"


def test_parse_reply_variants():
    reply = p.parse_reply('{"LL":{"control":"x","value":"{\\"key\\":\\"ab\\"}","Code":"200"}}')
    assert reply.ok and reply.value == {"key": "ab"}
    reply = p.parse_reply('{"LL":{"control":"x","value":"1","code":401}}')
    assert reply.code == 401 and not reply.ok
    assert p.parse_reply("not json") is None
