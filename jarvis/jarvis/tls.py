"""Self-signed certificate for the HTTPS port.

Browsers only hand out the microphone on a secure origin, so the Live page on a
tablet needs HTTPS even without a domain. The certificate is created once in
the data volume and renewed before it runs out.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import os
import socket
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

VALID_DAYS = 397
RENEW_DAYS = 30


def _local_addresses() -> list[str]:
    found = {"127.0.0.1"}
    try:
        found.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    return sorted(found)


def _expiring(cert_path: Path) -> bool:
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    except (OSError, ValueError):
        return True
    return cert.not_valid_after_utc - dt.datetime.now(dt.timezone.utc) < dt.timedelta(days=RENEW_DAYS)


def ensure_certificate(directory: Path) -> tuple[Path, Path]:
    cert_path = directory / "jarvis.crt"
    key_path = directory / "jarvis.key"
    if cert_path.exists() and key_path.exists() and not _expiring(cert_path):
        return cert_path, key_path
    directory.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Jarvis")])
    now = dt.datetime.now(dt.timezone.utc)
    san = [x509.DNSName("localhost"), x509.DNSName("jarvis.local")]
    san += [x509.IPAddress(ipaddress.ip_address(addr)) for addr in _local_addresses()]
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=VALID_DAYS))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_bytes = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(key_bytes)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path
