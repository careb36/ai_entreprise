"""RFC 3161 time-stamp tokens: build the request, read the answer, check the token.

Pure functions over bytes (issue #542, ``docs/archive/design/AUDIT_ANCHORS.md``): no
network here. ``services/integrations/tsa_client.py`` posts the request; this
module builds it and checks what comes back.

The backend has no ASN.1 library in its lock, and none is added for this: the
handful of structures involved (``TimeStampReq``, ``TimeStampResp``, CMS
``SignedData`` and ``TSTInfo``) are read with the small DER reader below,
which refuses indefinite lengths, high tag numbers and trailing bytes. Every
cryptographic step (digests, signature checks, certificate parsing) is
``cryptography``'s, already a dependency.

What ``verify_token`` proves: the token was signed by the private key of the
certificate given (the one the Owner pinned for the company's timestamping
authority), over a ``TSTInfo`` whose message imprint is the digest given, at
the ``genTime`` it reports, and the certificate is a time-stamping one valid at
that time. What it does not do is build a chain from that certificate to a
root: the certificate is the trust anchor, chosen by the Owner, and an auditor
checks it against the authority's published one (``openssl ts -verify``).
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import ExtendedKeyUsageOID

MAX_TOKEN_BYTES = 64 * 1024

OID_SIGNED_DATA = "1.2.840.113549.1.7.2"
OID_TST_INFO = "1.2.840.113549.1.9.16.1.4"
OID_CONTENT_TYPE = "1.2.840.113549.1.9.3"
OID_MESSAGE_DIGEST = "1.2.840.113549.1.9.4"
OID_SIGNING_CERT = "1.2.840.113549.1.9.16.2.12"
OID_SIGNING_CERT_V2 = "1.2.840.113549.1.9.16.2.47"
OID_SHA256 = "2.16.840.1.101.3.4.2.1"
OID_SHA384 = "2.16.840.1.101.3.4.2.2"
OID_SHA512 = "2.16.840.1.101.3.4.2.3"

_DIGESTS = {OID_SHA256: "sha256", OID_SHA384: "sha384", OID_SHA512: "sha512"}
_HASHES = {"sha256": hashes.SHA256, "sha384": hashes.SHA384, "sha512": hashes.SHA512}
# Signature algorithms: (key type, digest or None when the digest algorithm says).
_SIGNATURES = {
    "1.2.840.113549.1.1.1": ("rsa", None),
    "1.2.840.113549.1.1.11": ("rsa", "sha256"),
    "1.2.840.113549.1.1.12": ("rsa", "sha384"),
    "1.2.840.113549.1.1.13": ("rsa", "sha512"),
    "1.2.840.10045.4.3.2": ("ec", "sha256"),
    "1.2.840.10045.4.3.3": ("ec", "sha384"),
    "1.2.840.10045.4.3.4": ("ec", "sha512"),
    "1.3.101.112": ("ed25519", None),
}

T_BOOLEAN, T_INTEGER, T_OCTETS, T_NULL, T_OID = 0x01, 0x02, 0x04, 0x05, 0x06
T_GENTIME, T_SEQUENCE, T_SET = 0x18, 0x30, 0x31


class TimestampError(ValueError):
    """The request, the answer or the token is not acceptable; the message says why."""


# ---------------------------------------------------------------------------
# DER encoding (the request; the tests also build tokens with it)
# ---------------------------------------------------------------------------


def _length(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def tlv(tag: int, content: bytes) -> bytes:
    return bytes([tag]) + _length(len(content)) + content


def sequence(*items: bytes) -> bytes:
    return tlv(T_SEQUENCE, b"".join(items))


def set_of(*items: bytes) -> bytes:
    return tlv(T_SET, b"".join(sorted(items)))  # DER sorts a SET OF


def integer(n: int) -> bytes:
    length = max(1, (n.bit_length() + 8) // 8)  # room for the sign bit
    return tlv(T_INTEGER, n.to_bytes(length, "big", signed=True))


def octets(data: bytes) -> bytes:
    return tlv(T_OCTETS, data)


def null() -> bytes:
    return tlv(T_NULL, b"")


def boolean(value: bool) -> bytes:
    return tlv(T_BOOLEAN, b"\xff" if value else b"\x00")


def oid(dotted: str) -> bytes:
    parts = [int(p) for p in dotted.split(".")]
    body = bytearray([40 * parts[0] + parts[1]])
    for part in parts[2:]:
        chunk = [part & 0x7F]
        part >>= 7
        while part:
            chunk.append(0x80 | (part & 0x7F))
            part >>= 7
        body.extend(reversed(chunk))
    return tlv(T_OID, bytes(body))


def generalized_time(moment: datetime) -> bytes:
    return tlv(T_GENTIME, moment.astimezone(UTC).strftime("%Y%m%d%H%M%SZ").encode())


def context(number: int, content: bytes) -> bytes:
    """A constructed context-specific tag ``[number]`` around *content*."""
    return tlv(0xA0 | number, content)


# ---------------------------------------------------------------------------
# DER decoding
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Node:
    tag: int
    content: bytes
    raw: bytes

    def children(self) -> list[Node]:
        if not self.tag & 0x20:
            raise TimestampError("expected a constructed value")
        nodes, pos = [], 0
        while pos < len(self.content):
            node, pos = _read(self.content, pos)
            nodes.append(node)
        return nodes


def _read(data: bytes, pos: int) -> tuple[Node, int]:
    if pos + 2 > len(data):
        raise TimestampError("truncated DER")
    tag = data[pos]
    if tag & 0x1F == 0x1F:
        raise TimestampError("high tag numbers are not used here")
    first = data[pos + 1]
    head = pos + 2
    if first < 0x80:
        length = first
    else:
        count = first & 0x7F
        if count == 0 or count > 4:
            raise TimestampError("indefinite or oversized DER length")
        if head + count > len(data):
            raise TimestampError("truncated DER")
        length = int.from_bytes(data[head : head + count], "big")
        head += count
    end = head + length
    if end > len(data):
        raise TimestampError("truncated DER")
    return Node(tag, data[head:end], data[pos:end]), end


def parse(data: bytes) -> Node:
    """One DER value that is exactly *data*."""
    if len(data) > MAX_TOKEN_BYTES:
        raise TimestampError("value too large")
    node, end = _read(data, 0)
    if end != len(data):
        raise TimestampError("trailing bytes after the DER value")
    return node


def _expect(node: Node, tag: int, what: str) -> Node:
    if node.tag != tag:
        raise TimestampError(f"{what}: unexpected tag 0x{node.tag:02x}")
    return node


def decode_int(node: Node) -> int:
    return int.from_bytes(
        _expect(node, T_INTEGER, "integer").content, "big", signed=True
    )


def decode_oid(node: Node) -> str:
    body = _expect(node, T_OID, "object identifier").content
    if not body:
        raise TimestampError("empty object identifier")
    first = body[0]
    parts = [min(first // 40, 2), first - 40 * min(first // 40, 2)]
    value = 0
    for byte in body[1:]:
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            parts.append(value)
            value = 0
    return ".".join(str(p) for p in parts)


def decode_time(node: Node) -> datetime:
    text = _expect(node, T_GENTIME, "genTime").content.decode("ascii", "replace")
    if not text.endswith("Z"):
        raise TimestampError("genTime must be in UTC")
    whole = text[:-1].split(".")[0]
    try:
        return datetime.strptime(whole, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError as exc:
        raise TimestampError("genTime cannot be read") from exc


# ---------------------------------------------------------------------------
# The request and the answer
# ---------------------------------------------------------------------------


def build_request(digest: bytes, nonce: int) -> bytes:
    """A ``TimeStampReq`` for a SHA-256 *digest*, asking for the TSA's certificate."""
    if len(digest) != 32:
        raise TimestampError("the message imprint must be a SHA-256 digest")
    imprint = sequence(sequence(oid(OID_SHA256), null()), octets(digest))
    return sequence(integer(1), imprint, integer(nonce), boolean(True))


def _malformed(function):
    """Name every other parse failure (an index past a short value, bad text) as
    ``TimestampError``: a malformed token is a refused token, never a crash."""

    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except TimestampError:
            raise
        except (IndexError, TypeError, ValueError, UnicodeError, OverflowError) as exc:
            raise TimestampError(f"malformed value ({type(exc).__name__})") from exc

    wrapped.__doc__ = function.__doc__
    wrapped.__name__ = function.__name__
    return wrapped


@_malformed
def token_from_response(response: bytes) -> bytes:
    """The ``TimeStampToken`` of a granted ``TimeStampResp``, or ``TimestampError``."""
    items = _expect(parse(response), T_SEQUENCE, "TimeStampResp").children()
    if not items:
        raise TimestampError("empty TimeStampResp")
    status_info = _expect(items[0], T_SEQUENCE, "PKIStatusInfo").children()
    status = decode_int(status_info[0]) if status_info else -1
    if status not in (0, 1):  # granted, grantedWithMods
        raise TimestampError(f"the authority refused the request (status {status})")
    if len(items) < 2:
        raise TimestampError("granted without a token")
    return items[1].raw


# ---------------------------------------------------------------------------
# Checking a token
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenFacts:
    gen_time: datetime
    serial: int
    policy: str
    tsa_cert_sha256: str


def _attributes(signed_attrs: Node) -> dict[str, list[Node]]:
    found: dict[str, list[Node]] = {}
    for attribute in signed_attrs.children():
        parts = _expect(attribute, T_SEQUENCE, "attribute").children()
        if len(parts) != 2:
            raise TimestampError("malformed signed attribute")
        found[decode_oid(parts[0])] = _expect(parts[1], T_SET, "values").children()
    return found


def _check_signing_cert(attrs: dict[str, list[Node]], cert_der: bytes) -> None:
    """When the token names its certificate (ESS), it must be the pinned one."""
    if OID_SIGNING_CERT_V2 in attrs:
        name, algorithm = "v2", "sha256"
        certs = attrs[OID_SIGNING_CERT_V2][0].children()[0].children()
    elif OID_SIGNING_CERT in attrs:
        name, algorithm = "v1", "sha1"
        certs = attrs[OID_SIGNING_CERT][0].children()[0].children()
    else:
        return
    if not certs:
        raise TimestampError(f"signing-certificate {name} names no certificate")
    fields = certs[0].children()
    if fields and fields[0].tag == T_SEQUENCE:  # ESSCertIDv2 with a hash algorithm
        algorithm = _DIGESTS.get(decode_oid(fields[0].children()[0]), "")
        fields = fields[1:]
    if not algorithm or not fields:
        raise TimestampError("signing-certificate cannot be read")
    expected = hashlib.new(algorithm, cert_der).digest()
    if not hmac.compare_digest(
        _expect(fields[0], T_OCTETS, "certHash").content, expected
    ):
        raise TimestampError(
            "the token names a different certificate than the pinned one"
        )


def _verify_signature(
    cert: x509.Certificate,
    algorithm: str,
    digest_name: str,
    signature: bytes,
    data: bytes,
) -> None:
    key_type, named = _SIGNATURES.get(algorithm, (None, None))
    chosen = _HASHES[named or digest_name]()
    key = cert.public_key()
    try:
        if key_type == "rsa" and isinstance(key, rsa.RSAPublicKey):
            key.verify(signature, data, padding.PKCS1v15(), chosen)
        elif key_type == "ec" and isinstance(key, ec.EllipticCurvePublicKey):
            key.verify(signature, data, ec.ECDSA(chosen))
        elif key_type == "ed25519" and isinstance(key, ed25519.Ed25519PublicKey):
            key.verify(signature, data)
        else:
            raise TimestampError(f"unsupported signature algorithm {algorithm}")
    except InvalidSignature as exc:
        raise TimestampError("the token's signature does not verify") from exc


def load_certificate(pem: str) -> x509.Certificate:
    try:
        return x509.load_pem_x509_certificate(pem.encode())
    except ValueError as exc:
        raise TimestampError("the TSA certificate is not a PEM certificate") from exc


@_malformed
def verify_token(
    token: bytes, digest: bytes, cert_pem: str, nonce: int | None = None
) -> TokenFacts:
    """Check *token* covers the SHA-256 *digest* and is signed by *cert_pem*'s key."""
    cert = load_certificate(cert_pem)
    content_info = _expect(parse(token), T_SEQUENCE, "ContentInfo").children()
    if len(content_info) != 2 or decode_oid(content_info[0]) != OID_SIGNED_DATA:
        raise TimestampError("not a CMS SignedData")
    signed = _expect(content_info[1], 0xA0, "content").children()
    signed = _expect(signed[0], T_SEQUENCE, "SignedData").children()
    if len(signed) < 4:
        raise TimestampError("SignedData is incomplete")
    encap = _expect(signed[2], T_SEQUENCE, "encapContentInfo").children()
    if len(encap) != 2 or decode_oid(encap[0]) != OID_TST_INFO:
        raise TimestampError("the token does not carry a TSTInfo")
    tst_der = _expect(
        _expect(encap[1], 0xA0, "eContent").children()[0], T_OCTETS, "eContent"
    ).content
    signer_infos = _expect(signed[-1], T_SET, "signerInfos").children()
    if len(signer_infos) != 1:
        raise TimestampError("a time-stamp token has exactly one signer")

    tst = _expect(parse(tst_der), T_SEQUENCE, "TSTInfo").children()
    if len(tst) < 5:
        raise TimestampError("TSTInfo is incomplete")
    policy = decode_oid(tst[1])
    imprint = _expect(tst[2], T_SEQUENCE, "messageImprint").children()
    if decode_oid(imprint[0].children()[0]) != OID_SHA256:
        raise TimestampError("the token's imprint is not SHA-256")
    if not hmac.compare_digest(
        _expect(imprint[1], T_OCTETS, "imprint").content, digest
    ):
        raise TimestampError("the token covers a different statement")
    serial = decode_int(tst[3])
    gen_time = decode_time(tst[4])
    token_nonce = next((decode_int(n) for n in tst[5:] if n.tag == T_INTEGER), None)
    if nonce is not None and token_nonce != nonce:
        raise TimestampError("the token's nonce is not the request's")

    info = _expect(signer_infos[0], T_SEQUENCE, "SignerInfo").children()
    if len(info) < 6 or info[3].tag != 0xA0:
        raise TimestampError("the signer has no signed attributes")
    digest_name = _DIGESTS.get(decode_oid(info[2].children()[0]))
    if digest_name is None:
        raise TimestampError("unsupported digest algorithm")
    attrs = _attributes(info[3])
    content_type = attrs.get(OID_CONTENT_TYPE) or []
    if not content_type or decode_oid(content_type[0]) != OID_TST_INFO:
        raise TimestampError("the signed content type is not TSTInfo")
    message_digest = attrs.get(OID_MESSAGE_DIGEST) or []
    if not message_digest or not hmac.compare_digest(
        _expect(message_digest[0], T_OCTETS, "messageDigest").content,
        hashlib.new(digest_name, tst_der).digest(),
    ):
        raise TimestampError("the signed digest does not match the TSTInfo")
    cert_der = cert.public_bytes(Encoding.DER)
    _check_signing_cert(attrs, cert_der)
    signed_bytes = bytes([T_SET]) + info[3].raw[1:]  # signed as a SET OF (RFC 5652 5.4)
    algorithm = decode_oid(info[4].children()[0])
    _verify_signature(
        cert,
        algorithm,
        digest_name,
        _expect(info[5], T_OCTETS, "signature").content,
        signed_bytes,
    )

    if not cert.not_valid_before_utc <= gen_time <= cert.not_valid_after_utc:
        raise TimestampError("the TSA certificate was not valid at genTime")
    try:
        usage = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    except x509.ExtensionNotFound as exc:
        raise TimestampError(
            "the TSA certificate is not a time-stamping certificate"
        ) from exc
    if ExtendedKeyUsageOID.TIME_STAMPING not in usage:
        raise TimestampError("the TSA certificate is not a time-stamping certificate")
    return TokenFacts(gen_time, serial, policy, hashlib.sha256(cert_der).hexdigest())
