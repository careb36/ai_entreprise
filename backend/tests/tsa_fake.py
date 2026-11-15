"""A timestamping authority made at test time, for the RFC 3161 tests (issue #542).

The key and certificate are generated per test run; nothing reaches the
network. ``response`` builds a ``TimeStampResp`` the way an authority does
(CMS SignedData over a TSTInfo, signed attributes with ESS signing-certificate
v2), with the encoder of ``services/platform/rfc3161.py``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from services.platform import rfc3161 as r

POLICY = "1.3.6.1.4.1.32473.1"  # documentation arc (RFC 5612)


@dataclass
class Tsa:
    key: object
    cert: x509.Certificate

    @property
    def cert_pem(self) -> str:
        return self.cert.public_bytes(Encoding.PEM).decode()

    @property
    def cert_der(self) -> bytes:
        return self.cert.public_bytes(Encoding.DER)


def make_tsa(kind: str = "rsa", *, timestamping: bool = True, days: int = 30) -> Tsa:
    key = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        if kind == "rsa"
        else ec.generate_private_key(ec.SECP256R1())
    )
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test TSA")])
    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=days))
    )
    usage = (
        ExtendedKeyUsageOID.TIME_STAMPING
        if timestamping
        else ExtendedKeyUsageOID.CODE_SIGNING
    )
    builder = builder.add_extension(x509.ExtendedKeyUsage([usage]), critical=True)
    return Tsa(key, builder.sign(key, hashes.SHA256()))


def _algorithm(digest_oid: str = r.OID_SHA256) -> bytes:
    return r.sequence(r.oid(digest_oid), r.null())


def token(
    tsa: Tsa,
    digest: bytes,
    *,
    nonce: int | None = None,
    gen_time: datetime | None = None,
    serial: int = 7,
    ess_cert: bytes | None = None,
    tamper_tst: bool = False,
) -> bytes:
    moment = gen_time or datetime.now(UTC)

    def tst_info(number: int) -> bytes:
        fields = [
            r.integer(1),
            r.oid(POLICY),
            r.sequence(_algorithm(), r.octets(digest)),
            r.integer(number),
            r.generalized_time(moment),
        ]
        if nonce is not None:
            fields.append(r.integer(nonce))
        return r.sequence(*fields)

    tst = tst_info(serial)
    attributes = r.set_of(
        r.sequence(r.oid(r.OID_CONTENT_TYPE), r.set_of(r.oid(r.OID_TST_INFO))),
        r.sequence(
            r.oid(r.OID_MESSAGE_DIGEST),
            r.set_of(r.octets(hashlib.sha256(tst).digest())),
        ),
        r.sequence(
            r.oid(r.OID_SIGNING_CERT_V2),
            r.set_of(
                r.sequence(
                    r.sequence(
                        r.sequence(
                            r.octets(ess_cert or hashlib.sha256(tsa.cert_der).digest())
                        )
                    )
                )
            ),
        ),
    )
    if isinstance(tsa.key, rsa.RSAPrivateKey):
        signature = tsa.key.sign(attributes, padding.PKCS1v15(), hashes.SHA256())
        signature_algorithm = r.sequence(r.oid("1.2.840.113549.1.1.11"), r.null())
    else:
        signature = tsa.key.sign(attributes, ec.ECDSA(hashes.SHA256()))
        signature_algorithm = r.sequence(r.oid("1.2.840.10045.4.3.2"))
    if tamper_tst:  # the TSTInfo changed after it was signed
        tst = tst_info(serial + 1)
    signer = r.sequence(
        r.integer(1),
        r.sequence(tsa.cert.issuer.public_bytes(), r.integer(tsa.cert.serial_number)),
        _algorithm(),
        bytes([0xA0]) + attributes[1:],
        signature_algorithm,
        r.octets(signature),
    )
    signed_data = r.sequence(
        r.integer(3),
        r.set_of(_algorithm()),
        r.sequence(r.oid(r.OID_TST_INFO), r.context(0, r.octets(tst))),
        r.context(0, tsa.cert_der),
        r.set_of(signer),
    )
    return r.sequence(r.oid(r.OID_SIGNED_DATA), r.context(0, signed_data))


def response(tsa: Tsa, digest: bytes, *, status: int = 0, **options) -> bytes:
    if status not in (0, 1):
        return r.sequence(r.sequence(r.integer(status)))
    return r.sequence(r.sequence(r.integer(status)), token(tsa, digest, **options))


def nonce_of(request: bytes) -> int:
    """The nonce of a ``TimeStampReq`` built by ``rfc3161.build_request``."""
    return r.decode_int(r.parse(request).children()[2])


def digest_of(request: bytes) -> bytes:
    return r.parse(request).children()[1].children()[1].content
