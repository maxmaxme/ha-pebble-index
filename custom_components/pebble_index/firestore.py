"""Mapping between Firestore REST documents and Index lists and items.

Free of HTTP, Home Assistant and crypto imports on purpose: this is the part
that breaks if Core Devices change their document schema, so it has to be
testable on its own (see tests/test_mapping.py). Encryption is injected as
`decrypt` / `encrypt` callables by `api.py`.

Mirrors `ListDocument` at `lists/{uid}/lists/{listId}` and `ItemDocument` at
`items/{uid}/items/{itemId}` in the Pebble mobile app.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

SEED_SHOPPING = "shopping"
SEED_REMINDERS = "todos"
SEED_NOTES = "notes_self"

KIND_CHECKLIST = "checklist"

# Envelope in, plaintext out / plaintext in, envelope out.
Decryptor = Callable[[dict], bytes]
Encryptor = Callable[[bytes], dict]


@dataclass(frozen=True)
class ListDefinition:
    """One list in the Index feed."""

    id: str
    title: str
    kind: str
    seed: str | None


@dataclass(frozen=True)
class Item:
    """One live entry of an Index list."""

    uid: str
    summary: str
    done: bool
    due: datetime | None = None
    list_ids: tuple[str, ...] = field(default=())


class EncryptedItemError(Exception):
    """A document is encrypted and no key is configured."""


def encode_instant(when: datetime) -> dict:
    """Encode a timestamp the way the mobile app's serializer does.

    The documents annotate their timestamps with kotlinx's
    `InstantComponentSerializer`, which writes a two-field structure rather
    than a Firestore timestamp — so this must be a map, not `timestampValue`,
    or the phone fails to decode the document.
    """
    seconds, nanos = _split_instant(when)
    return {
        "mapValue": {
            "fields": {
                "epochSeconds": {"integerValue": str(seconds)},
                "nanosecondsOfSecond": {"integerValue": str(nanos)},
            }
        }
    }


def decode_instant(value: dict | None) -> datetime | None:
    """Read a timestamp, tolerating either encoding.

    The app writes the component map, but the web client and older records may
    carry a plain Firestore timestamp.
    """
    if not value or "nullValue" in value:
        return None
    if (stamp := value.get("timestampValue")) is not None:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    fields = value.get("mapValue", {}).get("fields")
    if not fields:
        return None
    return _join_instant(
        int(fields.get("epochSeconds", {}).get("integerValue", 0)),
        int(fields.get("nanosecondsOfSecond", {}).get("integerValue", 0)),
    )


def decode_list(document: dict, decrypt: Decryptor | None = None) -> ListDefinition | None:
    """Convert a Firestore document into a list, or None if it is deleted."""
    fields = document.get("fields", {})
    if fields.get("deleted", {}).get("booleanValue"):
        return None

    list_id = document["name"].rsplit("/", 1)[-1]
    title = fields.get("title", {}).get("stringValue", "")

    if (envelope := _envelope(fields)) is not None:
        if decrypt is None:
            raise EncryptedItemError(document.get("name", ""))
        title = json.loads(decrypt(envelope))["title"]

    return ListDefinition(
        id=list_id,
        title=title or list_id,
        kind=fields.get("listKind", {}).get("stringValue", "note"),
        seed=fields.get("seed", {}).get("stringValue") or None,
    )


def decode_item(document: dict, decrypt: Decryptor | None = None) -> Item | None:
    """Convert a Firestore document into an item, or None if it is deleted."""
    fields = document.get("fields", {})

    # Deletions are soft in this schema — the phone keeps the tombstone so it
    # can replicate the delete to its other devices.
    if fields.get("deleted", {}).get("booleanValue"):
        return None

    summary = fields.get("title", {}).get("stringValue", "")
    due = decode_instant(fields.get("dueAt"))

    if (envelope := _envelope(fields)) is not None:
        if decrypt is None:
            raise EncryptedItemError(document.get("name", ""))
        # The app moves title, body, dueAt and metadata inside the envelope and
        # blanks the cleartext copies; `done` and the parents stay readable so
        # its own sync can still work on them.
        sensitive = json.loads(decrypt(envelope))
        summary = sensitive.get("title", "")
        due = _plain_instant(sensitive.get("dueAt"))

    return Item(
        uid=document["name"].rsplit("/", 1)[-1],
        summary=summary,
        done=bool(fields.get("done", {}).get("booleanValue", False)),
        due=due,
        list_ids=tuple(
            value.get("stringValue")
            for value in fields.get("parentListIds", {})
            .get("arrayValue", {})
            .get("values", [])
        ),
    )


def item_metadata(list_kind: str, due: datetime | None) -> dict:
    """Pick the metadata variant the native UI expects for this item.

    Mirrors ItemFactory in the mobile app: entries of a checklist-kind list get
    a tickable box, entries with a deadline are reminders, everything else is a
    plain note.

    Checklist wins over the deadline on purpose. The app's inline list row ticks
    `checklist` and nothing else, so a shopping-list entry typed as `reminder`
    renders with an empty gutter and cannot be checked off at all. The deadline
    is not lost either way — it lives in `dueAt`, not in the metadata. The
    seeded Reminders list is `listKind: note`, so real reminders are unaffected.
    """
    if list_kind == KIND_CHECKLIST:
        return {"type": "checklist"}
    if due is not None:
        # `one_time` / `push` are the values the app's own reminder tool writes.
        return {
            "type": "reminder",
            "repeat": "one_time",
            "notification": "push",
        }
    return {"type": "note"}


def new_item_fields(
    summary: str,
    now: datetime,
    list_id: str,
    list_kind: str,
    due: datetime | None = None,
    encrypt: Encryptor | None = None,
) -> dict:
    """Fields for a freshly created entry."""
    return {
        **_sensitive_fields(summary, due, item_metadata(list_kind, due), encrypt),
        "done": {"booleanValue": False},
        "deleted": {"booleanValue": False},
        "parentListIds": {"arrayValue": {"values": [{"stringValue": list_id}]}},
        "createdAt": encode_instant(now),
        "updatedAt": encode_instant(now),
    }


def edit_item_fields(
    summary: str,
    done: bool,
    now: datetime,
    due: datetime | None = None,
    list_kind: str = "note",
    encrypt: Encryptor | None = None,
) -> dict:
    """Fields for an edit. `updatedAt` must advance — it is what the phone's
    sync compares to decide that the cloud copy won."""
    metadata = item_metadata(list_kind, due) if encrypt else None
    return {
        **_sensitive_fields(summary, due, metadata, encrypt),
        "done": {"booleanValue": done},
        "updatedAt": encode_instant(now),
    }


def edit_field_paths(encrypted: bool) -> tuple[str, ...]:
    """Which fields an edit overwrites.

    `metadata` is only touched when encrypting, because then it lives inside
    the envelope and has to be rewritten. In cleartext it is left alone, so an
    edit from Home Assistant cannot flatten a recurring reminder the ring set
    up.
    """
    if encrypted:
        return ("title", "body", "dueAt", "metadata", "encrypted", "done", "updatedAt")
    return ("title", "body", "dueAt", "done", "updatedAt")


def delete_item_fields(now: datetime) -> dict:
    """Fields for a soft delete."""
    return {
        "deleted": {"booleanValue": True},
        "updatedAt": encode_instant(now),
    }


DELETE_FIELD_PATHS = ("deleted", "updatedAt")


def _sensitive_fields(
    summary: str,
    due: datetime | None,
    metadata: dict | None,
    encrypt: Encryptor | None,
) -> dict:
    """The four fields the app treats as sensitive, in clear or in an envelope."""
    if encrypt is not None:
        payload = {
            "title": summary,
            "body": "",
            "dueAt": _plain_instant_json(due),
            "metadata": metadata or {"type": "note"},
        }
        return {
            "title": {"stringValue": ""},
            "body": {"stringValue": ""},
            "dueAt": {"nullValue": None},
            "metadata": _encode_plain(metadata or {"type": "note"}),
            "encrypted": _encode_plain(encrypt(json.dumps(payload).encode())),
        }

    fields = {
        "title": {"stringValue": summary},
        "body": {"stringValue": ""},
        "dueAt": encode_instant(due) if due else {"nullValue": None},
    }
    if metadata is not None:
        fields["metadata"] = _encode_plain(metadata)
    return fields


def _envelope(fields: dict) -> dict | None:
    """Read an `EncryptedEnvelope` out of a document, if it carries one."""
    value = fields.get("encrypted")
    if not value or "nullValue" in value:
        return None
    envelope = value.get("mapValue", {}).get("fields", {})
    return {
        key: inner.get("stringValue", inner.get("integerValue"))
        for key, inner in envelope.items()
    }


def _encode_plain(value) -> dict:
    """Encode a plain Python value as a Firestore value."""
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, dict):
        return {
            "mapValue": {
                "fields": {key: _encode_plain(item) for key, item in value.items()}
            }
        }
    raise TypeError(f"Unsupported value: {value!r}")


def _plain_instant_json(when: datetime | None) -> dict | None:
    """Timestamp as it appears inside the encrypted payload — plain JSON, not
    Firestore values, but the same two-component shape."""
    if when is None:
        return None
    seconds, nanos = _split_instant(when)
    return {"epochSeconds": seconds, "nanosecondsOfSecond": nanos}


def _plain_instant(value: dict | None) -> datetime | None:
    if not value:
        return None
    return _join_instant(
        int(value.get("epochSeconds", 0)), int(value.get("nanosecondsOfSecond", 0))
    )


def _split_instant(when: datetime) -> tuple[int, int]:
    return divmod(int(when.timestamp() * 1_000_000_000), 1_000_000_000)


def _join_instant(seconds: int, nanos: int) -> datetime:
    return datetime.fromtimestamp(seconds + nanos / 1_000_000_000, tz=UTC)
