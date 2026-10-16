"""Encryption for the hosted dashboard.

- Dashboard data (data.enc): AES-256-GCM, key from your password via PBKDF2-SHA256.
  The browser decrypts it with WebCrypto, so the password never leaves your device.
- Sync state (state.enc: database + Garmin tokens + settings): AES-256-GCM with a random
  32-byte key that only exists in data/state_key.txt and the STATE_KEY GitHub secret.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PBKDF2_ITERATIONS = 600_000  # OWASP 2023 recommendation for PBKDF2-SHA256


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def encrypt_for_browser(payload: dict, password: str, salt: bytes | None = None) -> dict:
    """salt: pass a stable per-deployment salt so a key remembered in the browser survives rebuilds."""
    salt, iv = salt or os.urandom(16), os.urandom(12)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS, dklen=32)
    plain = gzip.compress(json.dumps(payload, separators=(",", ":")).encode())
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iter": PBKDF2_ITERATIONS, "salt": _b64(salt),
            "iv": _b64(iv), "ct": _b64(AESGCM(key).encrypt(iv, plain, None))}


def decrypt_from_browser_format(blob: dict, password: str) -> dict:
    """Inverse of encrypt_for_browser (used to test the round trip)."""
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(blob["salt"]), blob["iter"], dklen=32)
    plain = AESGCM(key).decrypt(base64.b64decode(blob["iv"]), base64.b64decode(blob["ct"]), None)
    return json.loads(gzip.decompress(plain))


def deployment_salt(state_key: str) -> bytes:
    return hashlib.sha256(b"garmin-dashboard/salt/" + base64.b64decode(state_key)).digest()[:16]


def new_state_key() -> str:
    return _b64(os.urandom(32))


def encrypt_state(data: bytes, state_key: str) -> bytes:
    iv = os.urandom(12)
    return b"GDS1" + iv + AESGCM(base64.b64decode(state_key)).encrypt(iv, data, None)


def decrypt_state(blob: bytes, state_key: str) -> bytes:
    if blob[:4] != b"GDS1":
        raise ValueError("not a garmin-dashboard state file")
    return AESGCM(base64.b64decode(state_key)).decrypt(blob[4:16], blob[16:], None)
