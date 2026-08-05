"""Self-check for the Firestore document mapping.

Runnable on its own (`python3 tests/test_mapping.py`) or under pytest — it
imports nothing but the module under test, so it fails loudly if Core Devices
change the document schema and the mapping is adjusted wrongly.
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "pebble_index"

sys.path.insert(0, str(COMPONENT))

from firestore import (  # noqa: E402
    KIND_CHECKLIST,
    SEED_SHOPPING,
    EncryptedItemError,
    decode_instant,
    decode_item,
    decode_list,
    delete_item_fields,
    edit_field_paths,
    edit_item_fields,
    encode_instant,
    item_metadata,
    new_item_fields,
)

NOW = datetime(2026, 7, 28, 12, 0, 0, tzinfo=UTC)
DUE = datetime(2026, 7, 31, 9, 30, 0, tzinfo=UTC)
PREFIX = "projects/coreapp-ce061/databases/(default)/documents"


def _item_doc(fields: dict, item_id: str = "abc123") -> dict:
    return {"name": f"{PREFIX}/items/uid42/items/{item_id}", "fields": fields}


def _shopping(summary: str = "Milk", due=None) -> dict:
    return new_item_fields(summary, NOW, "list_shopping", KIND_CHECKLIST, due)


def test_round_trip():
    item = decode_item(_item_doc(_shopping()))
    assert item is not None
    assert item.uid == "abc123"
    assert item.summary == "Milk"
    assert item.done is False
    assert item.due is None
    assert item.list_ids == ("list_shopping",)


def test_checklist_list_gets_tickable_metadata():
    fields = _shopping()
    # Checklist metadata is what makes the native Pebble UI draw a checkbox.
    assert fields["metadata"]["mapValue"]["fields"]["type"]["stringValue"] == "checklist"


def test_plain_list_gets_note_metadata():
    assert item_metadata("note", None) == {"type": "note"}


def test_item_with_deadline_becomes_a_reminder():
    # The app's own reminder tool writes exactly these values.
    assert item_metadata("note", DUE) == {
        "type": "reminder",
        "repeat": "one_time",
        "notification": "push",
    }


def test_checklist_wins_over_a_deadline():
    # The app's inline list row ticks `checklist` and nothing else, so a
    # shopping entry typed as `reminder` cannot be checked off. `dueAt` still
    # carries the deadline.
    assert item_metadata("checklist", DUE) == {"type": "checklist"}


def test_due_date_round_trip():
    item = decode_item(_item_doc(_shopping("Cake", DUE)))
    assert item is not None and item.due == DUE


def test_due_date_can_be_cleared():
    fields = _shopping("Cake", DUE) | edit_item_fields("Cake", False, NOW, None)
    item = decode_item(_item_doc(fields))
    assert item is not None and item.due is None


def test_instant_is_a_component_map_not_a_timestamp():
    encoded = encode_instant(NOW)["mapValue"]["fields"]
    assert encoded["epochSeconds"] == {"integerValue": str(int(NOW.timestamp()))}
    assert encoded["nanosecondsOfSecond"] == {"integerValue": "0"}


def test_plain_firestore_timestamps_are_also_read():
    # Not what the app writes, but the web client and old records may.
    assert decode_instant({"timestampValue": "2026-07-31T09:30:00Z"}) == DUE


def test_completed_item_decodes_as_done():
    fields = _shopping("Eggs") | edit_item_fields("Eggs", True, NOW)
    item = decode_item(_item_doc(fields))
    assert item is not None and item.done is True


def test_tombstones_are_ignored():
    fields = _shopping("Butter") | delete_item_fields(NOW)
    assert decode_item(_item_doc(fields)) is None


def test_encrypted_item_is_reported():
    fields = _shopping("secret")
    fields["encrypted"] = {"mapValue": {"fields": {"ciphertext": {"stringValue": "…"}}}}
    try:
        decode_item(_item_doc(fields))
    except EncryptedItemError:
        return
    raise AssertionError("encrypted items must not be decoded silently")


def test_explicit_null_encrypted_field_is_fine():
    fields = _shopping("Cheese")
    # The app serializes its defaults, so the field is present and null.
    fields["encrypted"] = {"nullValue": None}
    assert decode_item(_item_doc(fields)) is not None


def test_list_decodes_with_seed_and_kind():
    document = {
        "name": f"{PREFIX}/lists/uid42/lists/list_shopping",
        "fields": {
            "title": {"stringValue": "Shopping list"},
            "listKind": {"stringValue": "checklist"},
            "seed": {"stringValue": "shopping"},
            "icon": {"stringValue": "🛒"},
        },
    }
    definition = decode_list(document)
    assert definition is not None
    assert definition.id == "list_shopping"
    assert definition.title == "Shopping list"
    assert definition.kind == KIND_CHECKLIST
    assert definition.seed == SEED_SHOPPING


def test_deleted_list_is_ignored():
    document = {
        "name": f"{PREFIX}/lists/uid42/lists/list_old",
        "fields": {
            "title": {"stringValue": "Old"},
            "deleted": {"booleanValue": True},
        },
    }
    assert decode_list(document) is None


def test_untitled_list_falls_back_to_its_id():
    document = {"name": f"{PREFIX}/lists/uid42/lists/list_x", "fields": {}}
    definition = decode_list(document)
    assert definition is not None and definition.title == "list_x"


def _fake_encrypt(plaintext: bytes) -> dict:
    """Stand-in for the real envelope; keeps this file stdlib-only."""
    return {
        "version": 1,
        "iv": "",
        "ciphertext": plaintext.decode(),
        "key_fingerprint": "fake",
    }


def _fake_decrypt(envelope: dict) -> bytes:
    return envelope["ciphertext"].encode()


def test_encrypted_write_blanks_the_cleartext_copies():
    fields = new_item_fields(
        "Milk", NOW, "list_shopping", KIND_CHECKLIST, DUE, _fake_encrypt
    )
    assert fields["title"] == {"stringValue": ""}
    assert fields["dueAt"] == {"nullValue": None}
    # `done` and the parents must stay readable — the phone's own sync uses them.
    assert fields["done"] == {"booleanValue": False}
    payload = json.loads(
        fields["encrypted"]["mapValue"]["fields"]["ciphertext"]["stringValue"]
    )
    assert payload["title"] == "Milk"
    assert payload["dueAt"] == {"epochSeconds": int(DUE.timestamp()), "nanosecondsOfSecond": 0}


def test_encrypted_item_round_trips_with_a_key():
    fields = new_item_fields(
        "Milk", NOW, "list_shopping", KIND_CHECKLIST, DUE, _fake_encrypt
    )
    item = decode_item(_item_doc(fields), _fake_decrypt)
    assert item is not None
    assert item.summary == "Milk"
    assert item.due == DUE


def test_encrypted_list_title_round_trips():
    document = {
        "name": f"{PREFIX}/lists/uid42/lists/list_shopping",
        "fields": {
            "title": {"stringValue": ""},
            "listKind": {"stringValue": "checklist"},
            # The envelope is a Firestore map, so the fake is wrapped like one.
            "encrypted": {
                "mapValue": {
                    "fields": {
                        "ciphertext": {
                            "stringValue": json.dumps({"title": "Shopping list"})
                        }
                    }
                }
            },
        },
    }
    definition = decode_list(document, _fake_decrypt)
    assert definition is not None and definition.title == "Shopping list"


def test_metadata_is_only_overwritten_when_encrypting():
    # In cleartext the app's own metadata is left alone, so editing an item from
    # Home Assistant cannot flatten a recurring reminder set up on the ring.
    assert "metadata" not in edit_field_paths(False)
    assert "metadata" in edit_field_paths(True)
    assert "metadata" not in edit_item_fields("Eggs", True, NOW)


def test_manifest_keys_are_sorted():
    """hassfest wants domain, name, then alphabetical order."""
    keys = list(json.loads((COMPONENT / "manifest.json").read_text()))
    assert keys[:2] == ["domain", "name"], keys
    assert keys[2:] == sorted(keys[2:]), keys


def test_translations_carry_no_urls():
    """hassfest rejects URLs in translation strings.

    They belong in `description_placeholders`, supplied by the config flow.
    """

    def strings(node):
        if isinstance(node, dict):
            for value in node.values():
                yield from strings(value)
        elif isinstance(node, str):
            yield node

    files = [COMPONENT / "strings.json", *(COMPONENT / "translations").glob("*.json")]
    assert len(files) == 13, files
    for path in files:
        for text in strings(json.loads(path.read_text(encoding="utf-8"))):
            assert "http" not in text, (path.name, text)


if __name__ == "__main__":
    for name, case in sorted(globals().items()):
        if name.startswith("test_"):
            case()
            print(f"ok  {name}")
    print("all good")
