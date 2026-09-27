"""Provider API keys, encrypted at rest.

A key is written once, stored as Fernet ciphertext, and never returned by the
API -- only its last four characters, so an administrator can tell which key is
installed. In production the Fernet key comes from `AI_KEY_ENCRYPTION_KEY`
(ideally injected from a KMS or vault); rotating it means re-entering provider
keys, which is deliberate: there is no path that decrypts old keys in bulk.
"""

from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.ai.config import ai_settings
from app.core.config import settings as core

log = logging.getLogger(__name__)


def _fernet() -> Fernet:
    key = ai_settings().AI_KEY_ENCRYPTION_KEY
    if not key:
        log.warning("AI_KEY_ENCRYPTION_KEY unset: deriving a development key from SECRET_KEY")
        key = base64.urlsafe_b64encode(
            hashlib.sha256(b"ftp-ai-keys|" + core.SECRET_KEY.encode()).digest()).decode()
    return Fernet(key.encode())


def encrypt(secret: str) -> bytes:
    return _fernet().encrypt(secret.encode())


def decrypt(blob: bytes | None) -> str | None:
    if not blob:
        return None
    try:
        return _fernet().decrypt(bytes(blob)).decode()
    except InvalidToken as exc:
        raise RuntimeError(
            "stored provider key cannot be decrypted -- was AI_KEY_ENCRYPTION_KEY "
            "changed? Re-enter the key on the AI management page.") from exc


def last4(secret: str) -> str:
    return secret[-4:] if len(secret) >= 8 else "••••"
