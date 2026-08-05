# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Home Assistant custom integration (Python) that exposes the lists of a
[Pebble Index](https://index.repebble.com) account as `todo` entities,
synced both ways. Integration domain: `pebble_index`. Installed into HA
through HACS as a custom repository (not bind-mounted from disk).

There is no official Pebble API. The integration talks to the same
**Firestore** project the mobile app uses (`coreapp-ce061`) over the REST
API, authenticated by a Firebase refresh token the user copies out of a
signed-in index.repebble.com session. The `apiKey` in `const.py` is not a
secret — it is a public project identifier shipped in every web bundle.

**This is a private, undocumented backend.** Everything the code knows
about the document schema was read from the app's open source
([coredevices/mobileapp](https://github.com/coredevices/mobileapp),
GPLv3) — no code was copied, only the shape of the data. When something
breaks mysteriously, re-read that repo before guessing:
`index-ai/.../entity/ItemDocument.kt`, `ListDocument.kt`, and
`experimental/.../indexfeed/ItemFactory.kt` are the files that matter.

## The one architectural decision

**Firestore is the single source of truth. The integration keeps no local
copy.** An entity is a *view* over the cloud documents; every write
stamps a fresh `updatedAt`.

That is why there is no conflict resolution anywhere in this code, and it
is not an omission: the mobile app already runs a bidirectional
Room↔Firestore sync (`IndexFeedSyncService`) that resolves by `updatedAt`.
A write with a newer `updatedAt` is all the phone needs.

Rejected alternatives — do not re-litigate without new information:

- **Mirroring into HA's built-in `shopping_list`.** Two independent
  copies, and HA todo items carry neither `updatedAt` nor tombstones, so
  deletes and edits become guesswork. Users who want one list rename this
  entity and delete the built-in integration.
- **A config option for "one list or two".** Doubles the code and the
  maintenance for a choice made once; the same outcome is a rename in the
  UI.
- **Merging several accounts into one entity.** Index lists are bound to
  a `uid` with no sharing primitive, so a merge would give a complete
  view only inside HA while each ring still sees its own half.

If a second account/ring ever becomes a real requirement, the design that
does work is the inverse: HA as master, fanning out to N accounts. That
needs a stored watermark of the last poll (without it, a checkbox ticked
on the ring gets reverted) and Firestore documents written under the HA
item id so the mapping stays free. Don't start it before the second ring
exists.

## Layout

```
custom_components/pebble_index/
  __init__.py        # setup_entry, PebbleIndexCoordinator (30 s poll)
  api.py             # token exchange + Firestore REST: lists, items, writes
  firestore.py       # PURE document <-> dataclass mapping; no HTTP, no HA imports
  crypto.py          # the app's AES-256-CBC + HMAC envelope, read and write
  config_flow.py     # refresh token + optional key, reauth, options flow for the key
  const.py           # DOMAIN, public Firebase ids, URLs, intervals
  todo.py            # one TodoListEntity per Index list
  brand/             # icon.png (256x256), icon@2x.png, logo.png
  translations/      # 12 languages; strings.json is a copy of en.json
tests/test_mapping.py  # assert-based, stdlib only, fake crypto injected
tests/test_crypto.py   # envelope vs an independent reference implementation
.github/workflows/validate.yml  # hassfest + hacs/action + the self-check
```

`firestore.py` stays import-free of HTTP and HA on purpose: it is the
part that breaks when Core Devices change their schema, so it has to be
testable without either.

```bash
python3 tests/test_mapping.py    # stdlib only
python3 tests/test_crypto.py     # needs cryptography — absent on macOS hosts
```

`cryptography` is missing from a plain host Python. The working way to run the
crypto check locally is the real runtime:

```bash
rsync -a --delete --exclude __pycache__ --exclude .git ./ pi:/opt/home-infra/ha-data/_pebble_test/
ssh pi 'docker exec home-assistant python3 /config/_pebble_test/tests/test_crypto.py'
ssh pi 'rm -rf /opt/home-infra/ha-data/_pebble_test'
```

## Hard-won facts

Each of these cost a debugging cycle. Don't undo them.

- **Timestamps are a component map, not `timestampValue`.** The app
  annotates its timestamps with kotlinx's `InstantComponentSerializer`,
  which writes `{epochSeconds, nanosecondsOfSecond}`. Write a real
  Firestore timestamp and the phone fails to decode the document. Reads
  tolerate both encodings.
- **Exactly one filter per query.** `deleted` is filtered client-side
  because combining `array-contains-any` with an equality needs a
  composite index — and the Firestore project belongs to Core Devices, so
  we cannot create indexes in it.
- **Deletes are soft** (`deleted: true`), matching the app, so the
  tombstone replicates instead of the item reappearing.
- **Item metadata is derived, not hardcoded**: a checklist-kind list makes
  it `checklist`, a deadline makes it `reminder` (`repeat: one_time`,
  `notification: push` — the values the app's own tool writes), otherwise
  `note`. Mirrors `ItemFactory`. **Checklist wins over the deadline**: the
  app's inline list row ticks `checklist` and nothing else
  (`ObjectListDetail.kt`), so a shopping entry typed as `reminder` renders
  with an empty gutter and cannot be checked off. The deadline survives in
  `dueAt` regardless, and the seeded Reminders list is `listKind: note`, so
  real reminders are unaffected.
- **Lists are read once at setup**, items every 30 s. A list created on
  the phone appears after an entry reload.
- **HA only loads `translations/<lang>.json`** for custom integrations.
  `strings.json` alone leaves the config dialog showing raw field names —
  keep both, and keep `strings.json` identical to `en.json`.
- **Brand assets live in `brand/`** (singular) next to `manifest.json`.
  HA's `brands` component serves a custom integration's own files from
  there before the CDN, gated on a `has_branding` check that is literally
  "is there a directory named `brand`". It is a cached property, so
  adding it needs a restart. HACS also requires this directory.
- **Encryption is `crypto.py`, and it must match the app byte for byte**:
  key = base64 of 32 bytes (the app's "Copy Key"), HMAC key =
  SHA-256("hmac" || key), wire = IV(16) || HMAC(32) || AES-256-CBC(PKCS7),
  HMAC over IV + ciphertext, verified before decrypting. The envelope's
  `ciphertext` holds the whole blob, and `iv` repeats its first 16 bytes.
  `cryptography` is a HA core dependency, so it is not in the manifest.
- **The envelope covers `title`, `body`, `dueAt`, `metadata` only.**
  `done`, `deleted`, `parentListIds` and the timestamps stay in clear —
  the app's own sync needs them. Writes encrypt when a key is configured,
  so Home Assistant never puts the text back in the clear.
- **One unreadable item must not take down the readable lists.** Decoding
  is per-item; failures are counted and logged. Only when *nothing*
  decoded does the poll fail, because then it is a configuration problem
  (no key, or a key from another key set).

## hassfest rules that bite

CI runs `home-assistant/actions/hassfest`, whose image is **amd64-only** —
it cannot be run on the arm64 Pi. Two of its rules are re-implemented in
`tests/test_mapping.py` so they fail locally instead of in CI:

- `manifest.json` keys must be `domain`, `name`, then alphabetical.
- Translation strings may not contain URLs. The site link and the console
  one-liner are `{index_link}` / `{snippet}` placeholders supplied by
  `config_flow.py`, which also keeps the snippet out of all 12 files.

## Versioning & release

A merge to `main` alone does nothing for existing installs:

1. Bump `manifest.json::version` in the same commit as the change.
2. Publish a GitHub Release whose tag matches that version.

No build step — HACS copies `custom_components/` verbatim.

## Deployment for testing

The Pi runs HA from `/opt/home-infra/ha-data` (see the `home-infra`
repo). To test an unreleased change without HACS:

```bash
rsync -a --delete --exclude __pycache__ \
  custom_components/pebble_index/ \
  pi:/opt/home-infra/ha-data/custom_components/pebble_index/
ssh pi 'docker restart home-assistant'
```

A restart is required for code changes — HA loads Python modules once per
process, so a config-entry reload only picks up new *lists*, not new code.
Remove that directory before installing through HACS.

## Licensing note

Code is MIT. The files in `brand/` are not: they are recolours of
`pebble-logo.png` from the GPLv3 mobile app, and the wordmark is Core
Devices' trademark, used referentially. Keep that carve-out in
[README.md](README.md) intact, and don't put "Pebble" in a product name
beyond referential use — their own README asks for that.
