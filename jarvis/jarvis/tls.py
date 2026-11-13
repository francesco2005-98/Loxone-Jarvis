"""Certificates for the HTTPS port.

Browsers only hand out the microphone, and only install an app, on a trusted
secure origin. Jarvis keeps its own small certificate authority in the data
volume and signs the server certificate with it. Installing that CA once on a
tablet or phone makes https://<server>:8501 trusted without a domain. The
server certificate covers every address the interface was opened with and is
re-signed when a new one shows up.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
import logging
import os
import re
import socket
import threading
from pathlib import Path
from typing import Callable

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

log = logging.getLogger(__name__)

CA_DAYS = 3650
VALID_DAYS = 397
RENEW_DAYS = 30
MAX_HOSTS = 24
HOSTNAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62})(\.[a-z0-9]([a-z0-9-]{0,62}))*$")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _write_private(path: Path, key: ec.EllipticCurvePrivateKey) -> None:
    data = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def _local_addresses() -> set[str]:
    found = {"127.0.0.1"}
    try:
        found.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    return found


def normalize_host(raw: str | None) -> str | None:
    """Host header -> bare IP or hostname, or None when it is not something to put in a certificate."""
    host = (raw or "").strip().lower()
    if not host:
        return None
    if host.startswith("["):
        host = host[1:].split("]", 1)[0]
    elif host.count(":") == 1:
        host = host.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    return host if len(host) <= 253 and HOSTNAME.match(host) else None


class Certificates:
    def __init__(self, directory: Path):
        self.directory = directory
        self.ca_cert = directory / "jarvis-ca.crt"
        self.ca_key = directory / "jarvis-ca.key"
        self.cert = directory / "jarvis.crt"
        self.key = directory / "jarvis.key"
        self._hosts_file = directory / "hosts.json"
        self._lock = threading.Lock()
        self._listeners: list[Callable[[], None]] = []
        self.hosts: set[str] = set()
        self.ready = False

    def on_change(self, listener: Callable[[], None]) -> None:
        self._listeners.append(listener)

    # ------------------------------------------------------------ setup
    def ensure(self) -> tuple[Path, Path]:
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            try:
                self.hosts = set(json.loads(self._hosts_file.read_text("utf-8")))
            except (OSError, ValueError):
                self.hosts = set()
            self.hosts |= _local_addresses() | {"localhost", "jarvis.local"}
            ca = self._load_or_create_ca()
            if self._leaf_outdated(ca):
                self._sign_leaf(ca)
            self.ready = True
        return self.cert, self.key

    def observe(self, raw_host: str | None) -> bool:
        """Remember an address the interface was opened with; True when the certificate was re-signed."""
        host = normalize_host(raw_host)
        if not self.ready or host is None or host in self.hosts or len(self.hosts) >= MAX_HOSTS:
            return False
        with self._lock:
            if host in self.hosts:
                return False
            self.hosts.add(host)
            self._hosts_file.write_text(json.dumps(sorted(self.hosts)), "utf-8")
            self._sign_leaf(self._load_or_create_ca())
        log.info("Zertifikat um %s erweitert", host)
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:  # noqa: BLE001
                log.exception("certificate reload failed")
        return True

    # ------------------------------------------------------------ inside
    def _load_or_create_ca(self) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
        if self.ca_cert.exists() and self.ca_key.exists():
            cert = x509.load_pem_x509_certificate(self.ca_cert.read_bytes())
            key = serialization.load_pem_private_key(self.ca_key.read_bytes(), password=None)
            if cert.not_valid_after_utc - _now() > dt.timedelta(days=VALID_DAYS + RENEW_DAYS):
                return cert, key  # type: ignore[return-value]
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "Jarvis Hausnetz"), x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Jarvis")]
        )
        ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(_now() - dt.timedelta(minutes=5))
            .not_valid_after(_now() + dt.timedelta(days=CA_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                    key_encipherment=False, data_encipherment=False, key_agreement=False,
                    encipher_only=False, decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(ski, critical=False)
            .sign(key, hashes.SHA256())
        )
        _write_private(self.ca_key, key)
        self.ca_cert.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        if self.cert.exists():
            self.cert.unlink()
        return cert, key

    def _leaf_outdated(self, ca: tuple[x509.Certificate, ec.EllipticCurvePrivateKey]) -> bool:
        if not (self.cert.exists() and self.key.exists()):
            return True
        try:
            cert = x509.load_pem_x509_certificate(self.cert.read_bytes())
        except ValueError:
            return True
        if cert.issuer != ca[0].subject or cert.not_valid_after_utc - _now() < dt.timedelta(days=RENEW_DAYS):
            return True
        try:
            names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        except x509.ExtensionNotFound:
            return True
        covered = {str(ip) for ip in names.get_values_for_type(x509.IPAddress)} | set(names.get_values_for_type(x509.DNSName))
        return not self.hosts <= covered

    def _sign_leaf(self, ca: tuple[x509.Certificate, ec.EllipticCurvePrivateKey]) -> None:
        ca_cert, ca_key = ca
        key = ec.generate_private_key(ec.SECP256R1())
        san: list[x509.GeneralName] = []
        for host in sorted(self.hosts):
            try:
                san.append(x509.IPAddress(ipaddress.ip_address(host)))
            except ValueError:
                san.append(x509.DNSName(host))
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Jarvis")]))
            .issuer_name(ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(_now() - dt.timedelta(minutes=5))
            .not_valid_after(_now() + dt.timedelta(days=VALID_DAYS))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
            )
            .sign(ca_key, hashes.SHA256())
        )
        _write_private(self.key, key)
        self.cert.write_bytes(
            cert.public_bytes(serialization.Encoding.PEM) + ca_cert.public_bytes(serialization.Encoding.PEM)
        )


def ensure_certificate(directory: Path) -> tuple[Path, Path]:
    return Certificates(directory).ensure()
