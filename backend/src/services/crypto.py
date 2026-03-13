"""Fernet-based token encryption for storing GitHub tokens at rest."""

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from loguru import logger

from src.config.settings import settings


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    raw_key = settings.encryption_key or settings.secret_key
    key = hashlib.sha256(raw_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_token(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str | None:
    """Decrypt a token. Returns None if decryption fails (e.g. SECRET_KEY changed)."""
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        logger.warning("Failed to decrypt token — SECRET_KEY may have changed")
        return None
