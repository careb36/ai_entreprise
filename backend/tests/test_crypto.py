"""
Unit tests for backend/app/crypto.py — Fernet symmetric encryption utilities.

Tests cover:
- Round-trip encrypt → decrypt with a valid Fernet key.
- Plain-text passthrough when FERNET_SECRET_KEY is absent.
- decrypt_value handles the ``plaintext:`` prefix correctly.
- decrypt_value returns None on tampered ciphertext.
- Invalid key format falls back gracefully.
"""

import os
import importlib
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reload_crypto(fernet_key: str | None):
    """Reload crypto module with the given key in the environment."""
    if fernet_key is None:
        os.environ.pop("FERNET_SECRET_KEY", None)
    else:
        os.environ["FERNET_SECRET_KEY"] = fernet_key

    import config
    importlib.reload(config)
    # Patch the module-level singleton
    config.settings = config.Settings()

    import crypto
    importlib.reload(crypto)
    return crypto


def _valid_fernet_key() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEncryptDecryptRoundTrip:
    def test_encrypt_decrypt_round_trip(self):
        """Fernet encrypt → decrypt returns the original plaintext."""
        key = _valid_fernet_key()
        crypto = _reload_crypto(key)

        secret = "super-secret-api-token"
        ciphertext = crypto.encrypt_value(secret)

        assert ciphertext != secret
        assert not ciphertext.startswith("plaintext:")

        recovered = crypto.decrypt_value(ciphertext)
        assert recovered == secret

    def test_encrypt_produces_unique_tokens(self):
        """Two encryptions of the same plaintext should produce different tokens (IV)."""
        key = _valid_fernet_key()
        crypto = _reload_crypto(key)

        a = crypto.encrypt_value("same-value")
        b = crypto.encrypt_value("same-value")
        # Fernet includes a timestamp and random IV — tokens must differ.
        assert a != b

    def test_decrypt_tampered_ciphertext_returns_none(self):
        """decrypt_value returns None when the ciphertext has been tampered with."""
        key = _valid_fernet_key()
        crypto = _reload_crypto(key)

        ciphertext = crypto.encrypt_value("original")
        tampered = ciphertext[:-4] + "XXXX"

        assert crypto.decrypt_value(tampered) is None

    def test_decrypt_wrong_key_returns_none(self):
        """decrypt_value returns None when decrypting with the wrong key."""
        key1 = _valid_fernet_key()
        key2 = _valid_fernet_key()

        crypto1 = _reload_crypto(key1)
        ciphertext = crypto1.encrypt_value("value-under-key1")

        crypto2 = _reload_crypto(key2)
        assert crypto2.decrypt_value(ciphertext) is None


class TestPlaintextFallback:
    def test_encrypt_without_key_adds_prefix(self):
        """Without FERNET_SECRET_KEY, encrypt_value uses the plaintext: prefix."""
        crypto = _reload_crypto(None)
        result = crypto.encrypt_value("my-token")
        assert result == "plaintext:my-token"

    def test_decrypt_plaintext_prefix_returns_value(self):
        """decrypt_value strips the plaintext: prefix correctly."""
        crypto = _reload_crypto(None)
        result = crypto.decrypt_value("plaintext:my-token")
        assert result == "my-token"

    def test_decrypt_plaintext_empty_suffix(self):
        """decrypt_value handles plaintext: with an empty value."""
        crypto = _reload_crypto(None)
        result = crypto.decrypt_value("plaintext:")
        assert result == ""

    def test_decrypt_ciphertext_without_key_returns_none(self):
        """decrypt_value returns None for a real ciphertext when no key is configured."""
        # Encrypt with a key first.
        key = _valid_fernet_key()
        crypto_with_key = _reload_crypto(key)
        ciphertext = crypto_with_key.encrypt_value("secret")

        # Now reload without the key.
        crypto_no_key = _reload_crypto(None)
        assert crypto_no_key.decrypt_value(ciphertext) is None


class TestInvalidKey:
    def test_invalid_fernet_key_falls_back_to_plaintext(self):
        """An invalid FERNET_SECRET_KEY is caught and falls back to plaintext mode."""
        crypto = _reload_crypto("not-a-valid-fernet-key")
        result = crypto.encrypt_value("data")
        # Should gracefully fall back to plaintext prefix.
        assert result == "plaintext:data"
