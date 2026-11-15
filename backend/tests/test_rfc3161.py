"""RFC 3161 tokens, read and checked without the network (issue #542).

The authority is made at test time (``tsa_fake.py``); one test also has
``openssl ts`` issue a token, when the binary is there, so the reader is
checked against an implementation other than its own encoder.
"""

import hashlib
import shutil
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
import tsa_fake
from services.platform import rfc3161

DIGEST = hashlib.sha256(b"an anchor statement").digest()


@pytest.fixture(scope="module")
def tsa():
    return tsa_fake.make_tsa()


def test_a_token_from_the_pinned_authority_verifies(tsa):
    request = rfc3161.build_request(DIGEST, 4242)
    assert tsa_fake.digest_of(request) == DIGEST
    assert tsa_fake.nonce_of(request) == 4242
    answer = tsa_fake.response(tsa, DIGEST, nonce=4242, serial=99)
    facts = rfc3161.verify_token(
        rfc3161.token_from_response(answer), DIGEST, tsa.cert_pem, nonce=4242
    )
    assert facts.serial == 99
    assert facts.policy == tsa_fake.POLICY
    assert facts.tsa_cert_sha256 == hashlib.sha256(tsa.cert_der).hexdigest()
    assert abs(facts.gen_time - datetime.now(UTC)) < timedelta(minutes=1)


def test_an_ecdsa_authority_verifies_too():
    tsa = tsa_fake.make_tsa("ec")
    facts = rfc3161.verify_token(tsa_fake.token(tsa, DIGEST), DIGEST, tsa.cert_pem)
    assert facts.serial == 7


def test_a_token_over_another_statement_is_refused(tsa):
    token = tsa_fake.token(tsa, hashlib.sha256(b"something else").digest())
    with pytest.raises(rfc3161.TimestampError, match="different statement"):
        rfc3161.verify_token(token, DIGEST, tsa.cert_pem)


def test_a_token_signed_by_another_authority_is_refused(tsa):
    other = tsa_fake.make_tsa()
    token = tsa_fake.token(other, DIGEST)
    with pytest.raises(rfc3161.TimestampError, match="different certificate"):
        rfc3161.verify_token(token, DIGEST, tsa.cert_pem)
    # Even when it names the pinned certificate, the signature gives it away.
    forged = tsa_fake.token(
        other, DIGEST, ess_cert=hashlib.sha256(tsa.cert_der).digest()
    )
    with pytest.raises(rfc3161.TimestampError, match="signature does not verify"):
        rfc3161.verify_token(forged, DIGEST, tsa.cert_pem)


def test_a_tstinfo_changed_after_signing_is_refused(tsa):
    token = tsa_fake.token(tsa, DIGEST, tamper_tst=True)
    with pytest.raises(rfc3161.TimestampError, match="signed digest"):
        rfc3161.verify_token(token, DIGEST, tsa.cert_pem)


def test_the_nonce_must_be_the_requests(tsa):
    token = tsa_fake.token(tsa, DIGEST, nonce=1)
    with pytest.raises(rfc3161.TimestampError, match="nonce"):
        rfc3161.verify_token(token, DIGEST, tsa.cert_pem, nonce=2)


def test_a_time_outside_the_certificate_is_refused(tsa):
    token = tsa_fake.token(tsa, DIGEST, gen_time=datetime.now(UTC) + timedelta(days=90))
    with pytest.raises(rfc3161.TimestampError, match="not valid at genTime"):
        rfc3161.verify_token(token, DIGEST, tsa.cert_pem)


def test_a_certificate_that_is_not_for_time_stamping_is_refused():
    tsa = tsa_fake.make_tsa(timestamping=False)
    with pytest.raises(rfc3161.TimestampError, match="time-stamping"):
        rfc3161.verify_token(tsa_fake.token(tsa, DIGEST), DIGEST, tsa.cert_pem)


def test_a_refusal_from_the_authority_is_named(tsa):
    with pytest.raises(rfc3161.TimestampError, match="status 2"):
        rfc3161.token_from_response(tsa_fake.response(tsa, DIGEST, status=2))


@pytest.mark.parametrize(
    "data, reason",
    [
        (b"", "truncated"),
        (b"\x30\x80\x00\x00", "indefinite"),
        (b"\x30\x05\x02\x01\x01", "truncated"),
        (b"\x30\x03\x02\x01\x01\x00", "trailing"),
        (b"\x1f\x01\x00", "high tag"),
        (b"\x30\x84\xff\xff\xff\xff", "truncated"),
    ],
)
def test_malformed_der_is_refused(data, reason):
    with pytest.raises(rfc3161.TimestampError, match=reason):
        rfc3161.parse(data)


def test_garbage_is_refused_as_a_token(tsa):
    with pytest.raises(rfc3161.TimestampError):
        rfc3161.verify_token(b"\x30\x03\x02\x01\x01", DIGEST, tsa.cert_pem)
    with pytest.raises(rfc3161.TimestampError, match="too large"):
        rfc3161.parse(b"\x30" + b"\x00" * rfc3161.MAX_TOKEN_BYTES)
    # Well-formed DER of the wrong shape (an empty SignedData wrapper, an
    # empty status) is refused by name, never an IndexError.
    hollow = rfc3161.sequence(
        rfc3161.oid(rfc3161.OID_SIGNED_DATA), rfc3161.context(0, b"")
    )
    with pytest.raises(rfc3161.TimestampError, match="malformed"):
        rfc3161.verify_token(hollow, DIGEST, tsa.cert_pem)
    with pytest.raises(rfc3161.TimestampError):
        rfc3161.token_from_response(rfc3161.sequence(rfc3161.sequence()))


def test_only_a_sha256_imprint_is_requested():
    with pytest.raises(rfc3161.TimestampError):
        rfc3161.build_request(b"short", 1)


@pytest.mark.skipif(shutil.which("openssl") is None, reason="no openssl binary")
def test_a_token_issued_by_openssl_verifies(tmp_path):
    """Interoperability: a token from ``openssl ts -reply``, keys made here."""
    key, cert = tmp_path / "tsa.key", tmp_path / "tsa.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
         "-out", str(cert), "-days", "2", "-subj", "/CN=test-tsa",
         "-addext", "extendedKeyUsage=critical,timeStamping"],
        check=True, capture_output=True,
    )  # fmt: skip
    (tmp_path / "serial").write_text("01\n")
    config = tmp_path / "ts.cnf"
    config.write_text(
        "[ tsa ]\ndefault_tsa = t\n[ t ]\n"
        f"serial = {tmp_path / 'serial'}\nsigner_cert = {cert}\nsigner_key = {key}\n"
        f"certs = {cert}\nsigner_digest = sha256\ndefault_policy = {tsa_fake.POLICY}\n"
        "digests = sha256\ness_cert_id_alg = sha256\n"
        "ess_cert_id_chain = no\naccuracy = secs:1\nordering = no\ntsa_name = no\n"
        "crypto_device = builtin\n"
    )
    query = tmp_path / "req.tsq"
    query.write_bytes(rfc3161.build_request(DIGEST, 777))
    reply = tmp_path / "reply.tsr"
    done = subprocess.run(
        ["openssl", "ts", "-reply", "-config", str(config), "-queryfile", str(query),
         "-out", str(reply)],
        check=False, capture_output=True,
    )  # fmt: skip
    if done.returncode != 0 or not reply.exists():
        pytest.skip(f"this openssl cannot act as a TSA: {done.stderr[-300:]!r}")
    facts = rfc3161.verify_token(
        rfc3161.token_from_response(reply.read_bytes()),
        DIGEST,
        cert.read_text(),
        nonce=777,
    )
    assert facts.policy == tsa_fake.POLICY
