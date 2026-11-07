"""Self-signed gateway TLS identity; clients pin the SHA-256 fingerprint from the QR code."""

from __future__ import annotations

import datetime
import hashlib
import ipaddress
import os
import ssl
from pathlib import Path


def ensure_certificate(
    state_dir: str | Path, addresses: list[str]
) -> tuple[Path, Path, str]:
    """Create (once) an EC P-256 certificate; returns (cert, key, fingerprint hex)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    directory = Path(state_dir) / "tls"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    cert_path, key_path = directory / "gateway.pem", directory / "gateway.key"
    if not (cert_path.exists() and key_path.exists()):
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "kenpi-gateway")])
        now = datetime.datetime.now(datetime.timezone.utc)
        sans = [x509.IPAddress(ipaddress.ip_address(a)) for a in addresses]
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .sign(key, hashes.SHA256())
        )
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path, certificate_fingerprint(cert_path)


def certificate_fingerprint(cert_path: Path) -> str:
    der = ssl.PEM_cert_to_DER_cert(cert_path.read_text())
    return hashlib.sha256(der).hexdigest()


def server_context(cert_path: Path, key_path: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert_path, key_path)
    return context
