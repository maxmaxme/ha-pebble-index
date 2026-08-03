"""The Index end-to-end encryption envelope.

Reimplements `AesCbcHmacCrypto` from the Pebble mobile app, byte for byte:

    key         base64 of 32 raw bytes, shown by the app as "Copy Key"
    hmac key    SHA-256("hmac" || key)
    wire        IV(16) || HMAC-SHA256(32) || AES-256-CBC ciphertext (PKCS7)
    hmac scope  IV + ciphertext, verified before decrypting (Encrypt-then-MAC)

`cryptography` is a Home Assistant core dependency, so it is not declared in
the manifest — it is always present.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_IV_LEN = 16
_MAC_LEN = 32
_KEY_LEN = 32


class EncryptionKeyError(Exception):
    """The configured key is malformed, or belongs to a different key set."""


class TamperedError(Exception):
    """The envelope failed its integrity check."""


def decode_key(key_base64: str) -> bytes:
    """Validate and decode a key as the app prints it."""
    try:
        key = base64.b64decode(key_base64.strip(), validate=True)
    except (ValueError, TypeError) as err:
        raise EncryptionKeyError("The key is not valid base64") from err
    if len(key) != _KEY_LEN:
        raise EncryptionKeyError(
            f"An AES-256 key must be {_KEY_LEN} bytes, this one is {len(key)}"
        )
    return key


def key_fingerprint(key: bytes) -> str:
    """SHA-256 hex of the raw key bytes, as stored in the envelope."""
    return hashlib.sha256(key).hexdigest()


def decrypt_envelope(envelope: dict[str, str], key: bytes) -> bytes:
    """Return the plaintext of an envelope.

    Raises `EncryptionKeyError` when the envelope was written with a different
    key — the app records a fingerprint precisely so this is distinguishable
    from corruption.
    """
    stored = envelope.get("key_fingerprint")
    if stored and stored != key_fingerprint(key):
        raise EncryptionKeyError(
            "This item was encrypted with a different key than the one configured"
        )

    blob = base64.b64decode(envelope["ciphertext"])
    if len(blob) <= _IV_LEN + _MAC_LEN:
        raise TamperedError("Envelope is too short to hold IV, HMAC and ciphertext")

    iv = blob[:_IV_LEN]
    mac = blob[_IV_LEN : _IV_LEN + _MAC_LEN]
    ciphertext = blob[_IV_LEN + _MAC_LEN :]

    if not hmac.compare_digest(mac, _mac(key, iv, ciphertext)):
        raise TamperedError("Envelope failed its integrity check")

    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    return unpadder.update(padded) + unpadder.finalize()


def encrypt_envelope(plaintext: bytes, key: bytes) -> dict[str, str | int]:
    """Wrap plaintext the way the app does, ready to store in Firestore."""
    iv = os.urandom(_IV_LEN)
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padder.update(plaintext) + padder.finalize())
    ciphertext += encryptor.finalize()

    blob = iv + _mac(key, iv, ciphertext) + ciphertext
    return {
        "version": 1,
        # The app stores the IV twice: on its own and as the head of the blob.
        "iv": base64.b64encode(iv).decode(),
        "ciphertext": base64.b64encode(blob).decode(),
        "key_fingerprint": key_fingerprint(key),
    }


def _mac(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    return hmac.new(
        hashlib.sha256(b"hmac" + key).digest(), iv + ciphertext, hashlib.sha256
    ).digest()
