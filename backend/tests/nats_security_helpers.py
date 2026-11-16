"""What the tests of the bus's credentials and TLS share (issue #327).

The generator of the broker's configuration (``scripts/nats_config.py``), the
variables both components read and a fixture that clears them, a placeholder
password, and certificates for the TLS cases (a CA, a server and a client
certificate). The tests are ``test_nats_security_*.py``.
"""

import datetime
import importlib.util
from pathlib import Path

import nats_auth
import nats_connect
import pytest

REPO = Path(__file__).resolve().parents[2]
SECRET = "s3cret-pass-word"

_spec = importlib.util.spec_from_file_location(
    "nats_config", REPO / "scripts" / "nats_config.py"
)
nats_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nats_config)

# Both components read the same variables the same way.
MODULES = pytest.mark.parametrize(
    "module", [nats_auth, nats_connect], ids=["backend", "daemon"]
)
ALL_VARS = (
    "NATS_USER",
    "NATS_PASSWORD",
    "NATS_CREDS_FILE",
    "NATS_NKEY_SEED",
    "NATS_TLS",
    "NATS_TLS_CA",
    "NATS_TLS_CERT",
    "NATS_TLS_KEY",
    "NATS_BIND",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ALL_VARS:
        monkeypatch.delenv(name, raising=False)


def _write_certs(folder: Path) -> dict:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = datetime.datetime.now(datetime.UTC)

    def key():
        return ec.generate_private_key(ec.SECP256R1())

    def name(cn):
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])

    def write_key(k, path):
        path.write_bytes(
            k.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )

    ca_key = key()
    ca = (
        x509.CertificateBuilder()
        .subject_name(name("fabrique-test-ca"))
        .issuer_name(name("fabrique-test-ca"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    paths = {"ca": folder / "ca.crt"}
    paths["ca"].write_bytes(ca.public_bytes(serialization.Encoding.PEM))

    import ipaddress

    for role, usage in (
        ("server", x509.ExtendedKeyUsageOID.SERVER_AUTH),
        ("client", x509.ExtendedKeyUsageOID.CLIENT_AUTH),
    ):
        k = key()
        cert = (
            x509.CertificateBuilder()
            .subject_name(name(f"fabrique-{role}"))
            .issuer_name(ca.subject)
            .public_key(k.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName("localhost"),
                        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    ]
                ),
                critical=False,
            )
            .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        paths[role] = folder / f"{role}.crt"
        paths[f"{role}_key"] = folder / f"{role}.key"
        paths[role].write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        write_key(k, paths[f"{role}_key"])
    return paths


@pytest.fixture(scope="module")
def certs(tmp_path_factory):
    return _write_certs(tmp_path_factory.mktemp("certs"))
