"""The local certificate authority that lets the proxy read ChatGPT/Perplexity traffic.

HTTPS hides request bodies from anything between the browser and the site. To
inspect a prompt or upload before it leaves, the proxy presents its own
certificate for chatgpt.com / perplexity.ai, signed by this local CA. The browser
accepts it only if the user trusts the CA (``system.install_certificate``) or a
dedicated browser window is told to (``browser.open_protected_chrome``).

* Created on first run, unique to this computer, never leaves it.
* The private key is stored in the user's own profile folder.
* It is only ever used for the hosts the proxy decrypts (see sites.py).

The file layout is what mitmproxy expects in its "confdir".
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import os
from pathlib import Path

from upload_guard.config import app_dir

COMMON_NAME = "Upload Guard local inspection CA"


def ca_dir() -> Path:
    path = app_dir() / "ca"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_ca() -> Path:
    """Create the CA (key + certificate) once. Returns the folder mitmproxy should use."""
    folder = ca_dir()
    combined = folder / "mitmproxy-ca.pem"
    if combined.exists():
        return folder
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, COMMON_NAME),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Upload Guard (this computer only)")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=825))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                         content_commitment=False, key_encipherment=False, data_encipherment=False,
                                         key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                serialization.NoEncryption())
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    _write_private(combined, key_pem + cert_pem)
    (folder / "mitmproxy-ca-cert.pem").write_bytes(cert_pem)
    (folder / "mitmproxy-ca-cert.cer").write_bytes(cert.public_bytes(serialization.Encoding.DER))
    return folder


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def certificate_der() -> bytes:
    return (ensure_ca() / "mitmproxy-ca-cert.cer").read_bytes()


def thumbprint() -> str:
    """SHA-1 thumbprint, as Windows shows it (used to find it in the certificate store)."""
    return hashlib.sha1(certificate_der()).hexdigest().upper()


def spki_hash() -> str:
    """Base64 SHA-256 of the public key, for Chrome's --ignore-certificate-errors-spki-list."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    cert = x509.load_der_x509_certificate(certificate_der())
    spki = cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(hashlib.sha256(spki).digest()).decode()
