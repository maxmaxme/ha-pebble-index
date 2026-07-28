"""Mapping between Firestore REST documents and Index lists and items.

Free of HTTP and Home Assistant imports on purpose: this is the part that
breaks if Core Devices change their document schema, so it has to be testable
on its own (see tests/test_mapping.py).

Mirrors `ListDocument` at `lists/{uid}/lists/{listId}` and `ItemDocument` at
`items/{uid}/items/{itemId}` in the Pebble mobile app.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

SEED_SHOPPING = "shopping"
SEED_REMINDERS = "todos"
SEED_NOTES = "notes_self"

KIND_CHECKLIST = "checklist"


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
    """A document's text lives in an end-to-end encrypted envelope."""


def encode_instant(when: datetime) -> dict:
    """Encode a timestamp the way the mobile app's serializer does.

    The documents annotate their timestamps with kotlinx's
    `InstantComponentSerializer`, which writes a two-field structure rather
    than a Firestore timestamp — so this must be a map, not `timestampValue`,
    or the phone fails to decode the document.
    """
    seconds, nanos = divmod(int(when.timestamp() * 1_000_000_000), 1_000_000_000)
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
    seconds = int(fields.get("epochSeconds", {}).get("integerValue", 0))
    nanos = int(fields.get("nanosecondsOfSecond", {}).get("integerValue", 0))
    return datetime.fromtimestamp(seconds + nanos / 1_000_000_000, tz=UTC)


def decode_list(document: dict) -> ListDefinition | None:
    """Convert a Firestore document into a list, or None if it is deleted."""
    fields = document.get("fields", {})
    if fields.get("deleted", {}).get("booleanValue"):
        return None
    list_id = document["name"].rsplit("/", 1)[-1]
    return ListDefinition(
        id=list_id,
        title=fields.get("title", {}).get("stringValue", "") or list_id,
        kind=fields.get("listKind", {}).get("stringValue", "note"),
        seed=fields.get("seed", {}).get("stringValue") or None,
    )


def decode_item(document: dict) -> Item | None:
    """Convert a Firestore document into an item, or None if it is deleted."""
    fields = document.get("fields", {})

    # Deletions are soft in this schema — the phone keeps the tombstone so it
    # can replicate the delete to its other devices.
    if fields.get("deleted", {}).get("booleanValue"):
        return None

    envelope = fields.get("encrypted", {})
    if envelope and "nullValue" not in envelope:
        raise EncryptedItemError(document.get("name", ""))

    return Item(
        uid=document["name"].rsplit("/", 1)[-1],
        summary=fields.get("title", {}).get("stringValue", ""),
        done=bool(fields.get("done", {}).get("booleanValue", False)),
        due=decode_instant(fields.get("dueAt")),
        list_ids=tuple(
            value.get("stringValue")
            for value in fields.get("parentListIds", {})
            .get("arrayValue", {})
            .get("values", [])
        ),
    )


def item_metadata(list_kind: str, due: datetime | None) -> dict:
    """Pick the metadata variant the native UI expects for this item.

    Mirrors ItemFactory in the mobile app: entries with a deadline are
    reminders, entries of a checklist-kind list get a tickable box, everything
    else is a plain note.
    """
    if due is not None:
        # `one_time` / `push` are the values the app's own reminder tool writes.
        variant = {
            "type": {"stringValue": "reminder"},
            "repeat": {"stringValue": "one_time"},
            "notification": {"stringValue": "push"},
        }
    elif list_kind == KIND_CHECKLIST:
        variant = {"type": {"stringValue": "checklist"}}
    else:
        variant = {"type": {"stringValue": "note"}}
    return {"mapValue": {"fields": variant}}


def new_item_fields(
    summary: str,
    now: datetime,
    list_id: str,
    list_kind: str,
    due: datetime | None = None,
) -> dict:
    """Fields for a freshly created entry."""
    return {
        "title": {"stringValue": summary},
        "body": {"stringValue": ""},
        "done": {"booleanValue": False},
        "deleted": {"booleanValue": False},
        "parentListIds": {"arrayValue": {"values": [{"stringValue": list_id}]}},
        "metadata": item_metadata(list_kind, due),
        "dueAt": encode_instant(due) if due else {"nullValue": None},
        "createdAt": encode_instant(now),
        "updatedAt": encode_instant(now),
    }


def edit_item_fields(
    summary: str,
    done: bool,
    now: datetime,
    due: datetime | None = None,
) -> dict:
    """Fields for an edit. `updatedAt` must advance — it is what the phone's
    sync compares to decide that the cloud copy won."""
    return {
        "title": {"stringValue": summary},
        "done": {"booleanValue": done},
        "dueAt": encode_instant(due) if due else {"nullValue": None},
        "updatedAt": encode_instant(now),
    }


EDIT_FIELD_PATHS = ("title", "done", "dueAt", "updatedAt")


def delete_item_fields(now: datetime) -> dict:
    """Fields for a soft delete."""
    return {
        "deleted": {"booleanValue": True},
        "updatedAt": encode_instant(now),
    }


DELETE_FIELD_PATHS = ("deleted", "updatedAt")
