"""
Credential encryption utilities using Fernet symmetric encryption.

Fernet guarantees that encrypted data cannot be manipulated or read without
the key.  The key must be a URL-safe base64-encoded 32-byte value, generated
once and stored in the ``FERNET_SECRET_KEY`` environment variable.

Generate a new key::

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

If ``FERNET_SECRET_KEY`` is not configured the module operates in *plain-text
passthrough* mode — values are stored as-is with a ``plaintext:`` prefix so
that the absence of encryption is detectable at read time.  A warning is
emitted on every write so operators are aware.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_PLAINTEXT_PREFIX = "plaintext:"


def _get_fernet():
    """Return a configured ``Fernet`` instance or ``None`` if key is absent."""
    from config import settings  # local import to avoid circular deps at module load
    from cryptography.fernet import Fernet

    key = settings.FERNET_SECRET_KEY
    if not key:
        return None
    try:
        return Fernet(key.encode())
    except Exception as exc:
        logger.error("Invalid FERNET_SECRET_KEY — encryption disabled: %s", exc)
        return None


def encrypt_value(plaintext: str) -> str:
    """
    Encrypt *plaintext* with Fernet.

    Returns a UTF-8 Fernet token string (URL-safe base64).  Falls back to a
    ``plaintext:<value>`` string if no key is configured so that stored values
    remain self-describing and the missing-key condition is detectable.

    Args:
        plaintext: The secret string to protect (e.g. an API token).

    Returns:
        Encrypted Fernet token, or ``"plaintext:<value>"`` when key is absent.
    """
    fernet = _get_fernet()
    if fernet is None:
        logger.warning(
            "FERNET_SECRET_KEY not configured — storing credential as plain text. "
            "Set FERNET_SECRET_KEY in your environment for production deployments."
        )
        return f"{_PLAINTEXT_PREFIX}{plaintext}"
    return fernet.encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> Optional[str]:
    """
    Decrypt a Fernet-encrypted token back to plain text.

    Handles the ``plaintext:`` passthrough prefix for environments where the
    Fernet key was not configured at write time.

    Args:
        ciphertext: A Fernet token string or a ``"plaintext:<value>"`` string.

    Returns:
        The decrypted string, or ``None`` if decryption fails.
    """
    if ciphertext.startswith(_PLAINTEXT_PREFIX):
        return ciphertext[len(_PLAINTEXT_PREFIX):]

    fernet = _get_fernet()
    if fernet is None:
        logger.error(
            "Cannot decrypt credential: FERNET_SECRET_KEY is not configured."
        )
        return None
    try:
        return fernet.decrypt(ciphertext.encode()).decode()
    except Exception as exc:
        logger.error("Fernet decryption failed: %s", exc)
        return None
