"""Self-check for the encryption envelope.

Needs `cryptography` (a Home Assistant core dependency), unlike
test_mapping.py which is stdlib-only.

The reference vector below was produced by the scheme documented in the Pebble
app's `AesCbcHmacCrypto`: key = base64 of 32 bytes, HMAC key = SHA-256("hmac" ||
key), wire = IV(16) || HMAC(32) || AES-256-CBC(PKCS7). It is checked
independently of this module's own encrypt so a change to either side is caught.
"""

import base64
import hashlib
import hmac
import json
import sys
from pathlib import Path

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "pebble_index"
sys.path.insert(0, str(COMPONENT))

from cryptography.hazmat.primitives import padding  # noqa: E402
from cryptography.hazmat.primitives.ciphers import (  # noqa: E402
    Cipher,
    algorithms,
    modes,
)

from crypto import (  # noqa: E402
    EncryptionKeyError,
    TamperedError,
    decode_key,
    decrypt_envelope,
    encrypt_envelope,
    key_fingerprint,
)

KEY = bytes(range(32))
KEY_B64 = base64.b64encode(KEY).decode()
OTHER_KEY = bytes(range(1, 33))


def _reference_envelope(plaintext: bytes, key: bytes, iv: bytes) -> dict:
    """Build an envelope the way the mobile app does, without using crypto.py."""
    padder = padding.PKCS7(128).padder()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padder.update(plaintext) + padder.finalize())
    ciphertext += encryptor.finalize()
    mac = hmac.new(
        hashlib.sha256(b"hmac" + key).digest(), iv + ciphertext, hashlib.sha256
    ).digest()
    return {
        "version": 1,
        "iv": base64.b64encode(iv).decode(),
        "ciphertext": base64.b64encode(iv + mac + ciphertext).decode(),
        "key_fingerprint": hashlib.sha256(key).hexdigest(),
    }


def test_decrypts_the_reference_wire_format():
    payload = json.dumps({"title": "Milk", "body": "", "dueAt": None}).encode()
    envelope = _reference_envelope(payload, KEY, b"\x00" * 16)
    assert json.loads(decrypt_envelope(envelope, KEY))["title"] == "Milk"


def test_round_trip():
    envelope = encrypt_envelope(b"cheese", KEY)
    assert decrypt_envelope(envelope, KEY) == b"cheese"


def test_iv_is_random_per_write():
    first = encrypt_envelope(b"same", KEY)
    second = encrypt_envelope(b"same", KEY)
    assert first["ciphertext"] != second["ciphertext"]


def test_envelope_carries_the_iv_twice():
    # The app stores the IV both separately and as the head of the blob.
    envelope = encrypt_envelope(b"x", KEY)
    blob = base64.b64decode(envelope["ciphertext"])
    assert blob[:16] == base64.b64decode(envelope["iv"])


def test_fingerprint_matches_the_apps_definition():
    assert key_fingerprint(KEY) == hashlib.sha256(KEY).hexdigest()


def test_wrong_key_is_reported_as_such():
    envelope = encrypt_envelope(b"secret", KEY)
    try:
        decrypt_envelope(envelope, OTHER_KEY)
    except EncryptionKeyError:
        return
    raise AssertionError("a mismatched key must not read as corruption")


def test_tampering_is_detected():
    envelope = encrypt_envelope(b"secret", KEY)
    blob = bytearray(base64.b64decode(envelope["ciphertext"]))
    blob[-1] ^= 0xFF
    envelope["ciphertext"] = base64.b64encode(bytes(blob)).decode()
    try:
        decrypt_envelope(envelope, KEY)
    except TamperedError:
        return
    raise AssertionError("a modified ciphertext must not decrypt")


def test_key_must_be_32_bytes_of_base64():
    assert decode_key(KEY_B64) == KEY
    for bad in ("not base64!", base64.b64encode(b"short").decode(), ""):
        try:
            decode_key(bad)
        except EncryptionKeyError:
            continue
        raise AssertionError(f"accepted a bad key: {bad!r}")


if __name__ == "__main__":
    for name, case in sorted(globals().items()):
        if name.startswith("test_"):
            case()
            print(f"ok  {name}")
    print("all good")
