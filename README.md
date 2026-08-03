# Pebble Index for Home Assistant

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=maxmaxme&repository=ha-pebble-index&category=integration)
[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=pebble_index)

[![Validate](https://img.shields.io/github/actions/workflow/status/maxmaxme/ha-pebble-index/validate.yml?branch=main&style=for-the-badge&label=validate)](https://github.com/maxmaxme/ha-pebble-index/actions/workflows/validate.yml)
[![HACS: custom](https://img.shields.io/badge/HACS-custom-orange.svg?style=for-the-badge)](https://hacs.xyz)
[![Release](https://img.shields.io/github/v/release/maxmaxme/ha-pebble-index?style=for-the-badge)](https://github.com/maxmaxme/ha-pebble-index/releases)
[![Minimum HA version](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fgithub.com%2Fmaxmaxme%2Fha-pebble-index%2Fraw%2Fmain%2Fhacs.json&query=%24.homeassistant&style=for-the-badge&logo=homeassistant&logoColor=%2311BDF2&label=Minimum%20HA)](https://github.com/maxmaxme/ha-pebble-index/blob/main/hacs.json)
[![License](https://img.shields.io/github/license/maxmaxme/ha-pebble-index?style=for-the-badge)](LICENSE)

Two-way sync between the lists of a [Pebble Index](https://index.repebble.com)
account and Home Assistant `todo` entities. Dictate a note to the ring and it
appears in Home Assistant; add an item in Home Assistant and it appears on the
ring. Every list in the account is exposed, not just the shopping list.

## What it syncs

| | |
| --- | --- |
| **Lists** | One `todo` entity per Index list — the three seeded ones (Shopping list, Reminders, Notes to self) plus any list you created yourself. Titles, and an icon per seeded list, come from the account. |
| **Items** | Create, rename, tick off, un-tick, delete. Both directions. |
| **Deadlines** | An item's `dueAt` maps to the entity's due date. A reminder dictated to the ring arrives with its date; a due date set in Home Assistant is written back and the item becomes a reminder in the native UI (`repeat: one_time`, `notification: push`). |
| **Deletes** | Soft, the way the app does it (`deleted: true`), so the tombstone replicates to your other devices instead of the item quietly reappearing. |
| **Item kind** | Entries in a checklist-kind list get tickable-checkbox metadata, entries with a deadline get reminder metadata, everything else is a plain note — matching what the app writes itself. |
| **Encryption** | End-to-end encrypted accounts are supported. Paste the key from the Pebble app (Index settings → Encryption → Copy Key) and items are decrypted on read and encrypted on write, so writing from Home Assistant does not put your text back in the clear. |
| **Accounts** | Several Pebble accounts can be added; each is its own config entry with its own entities. |
| **UI** | Config dialog and errors in en, ru, de, fr, es, it, pt-BR, nl, pl, sv, zh-Hans, ja. Brand icon included. |

Not supported, deliberately:

- **Ordering, descriptions.** `ItemDocument` has no ordering field, and item
  bodies were not needed for lists.
- **Recordings and transcripts.** Reachable in the same Firestore, deliberately
  left out: it would drag the text of everything you dictate into Home
  Assistant's state machine and recorder database.
- **Ring battery.** Not available — the cloud document for a ring holds only its
  MAC, serial and firmware version. Battery never leaves the phone.

## How it works

The Pebble mobile app keeps the Index feed in Firestore and already runs a
bidirectional sync of its own, resolving conflicts by each document's
`updatedAt`. This integration therefore keeps **no local copy** — an entity is a
view over the Firestore documents, and every write stamps a fresh `updatedAt` so
the phone picks it up. No merge logic, nothing to diverge.

Reads poll every 30 seconds, in a single query per account. Lists themselves are
read once at setup: a list created or renamed on the phone shows up after
reloading the config entry.

## Install

1. Click the **Open in HACS** badge above — it opens this repository as a custom
   repository in your own Home Assistant. Download it there, then restart Home
   Assistant. (Manually: HACS → ⋮ → Custom repositories → this URL, category
   Integration.)
2. Sign in at [index.repebble.com](https://index.repebble.com) with the same
   account as the Pebble mobile app.
3. In the browser console on that tab:

   ```javascript
   JSON.parse(localStorage.getItem('firebase:authUser:AIzaSyBZ9Cdvwwv9At2lPmc8TxyyEqSXGXejGvc:[DEFAULT]')).stsTokenManager.refreshToken
   ```

4. Add the **Pebble Index** integration and paste the token.

A bookmarklet, if you would rather not open DevTools next time:

```
javascript:navigator.clipboard.writeText(JSON.parse(localStorage.getItem('firebase:authUser:AIzaSyBZ9Cdvwwv9At2lPmc8TxyyEqSXGXejGvc:[DEFAULT]')).stsTokenManager.refreshToken).then(()=>alert('Token copied'))
```

### Why paste a token instead of signing in

Core Devices own the Google/GitHub/Apple OAuth clients behind that login page,
and their redirect URIs point at their own domain. A Home Assistant OAuth flow
would need a client registered by them, so there is no way around the manual
step. The refresh token renews itself, so it is a one-time copy — and if it ever
stops working, Home Assistant raises its normal re-authentication prompt.

Treat the token as a full credential for the account: it reads notes and
transcripts too, not only the lists this integration exposes. Keep it in
`secrets.yaml`.

## Encryption

If the account has end-to-end encryption turned on, the item text lives in an
envelope (AES-256-CBC + HMAC-SHA256) and the cleartext fields are blank. Paste
the key into the integration's options — Settings → Devices & services → Pebble
Index → Configure — and it decrypts on read and encrypts on write. The key is
`Copy Key` in the Pebble app under Index settings → Encryption.

Without the key the lists cannot be read at all, and the entities say so. With a
key from a *different* key set, the error names that specifically: the app
stores a fingerprint of the key alongside each envelope.

## Using it as your only shopping list

The entity is not a mirror of Home Assistant's built-in `shopping_list` — it is
the cloud list itself. To have one list everywhere, rename the Shopping list
entity to whatever you call it, delete the built-in **Shopping list**
integration, and expose the entity to Assist so voice commands land in it.

## Brand assets

`custom_components/pebble_index/brand/` holds `icon.png` (256×256, transparent),
`icon@2x.png` and `logo.png`, built from the wordmark shipped in the Pebble
mobile app and recoloured to its brand coral `#FA4A36`.

The directory name matters: Home Assistant's `brands` component serves a custom
integration's own files from `<integration>/brand/` before falling back to the
[brands CDN](https://brands.home-assistant.io), and it only looks there when a
directory called exactly `brand` sits next to `manifest.json`. Adding or renaming
it needs a Home Assistant restart — the check is a cached property on the loaded
integration.

## Caveats

- This talks to a private, undocumented backend. If Core Devices change their
  schema, sync breaks; `tests/test_mapping.py` is what tells you where.
- A reminder created from Home Assistant has no `localReminderId`, so the phone
  may not raise a local notification for it — the item and its date are correct,
  the alarm is the app's own scheduling.
- Home Assistant only loads `translations/<lang>.json` for custom integrations.
  `strings.json` alone leaves the config dialog showing raw field names.

## Tests

```bash
python3 tests/test_mapping.py   # stdlib only
python3 tests/test_crypto.py    # needs cryptography (ships with Home Assistant)
```

## License

MIT — see [LICENSE](LICENSE). Applies to the code in this repository.

Not covered by it: the files under `custom_components/pebble_index/brand/`. They
are recolours of `pebble-logo.png` from Core Devices' GPLv3-licensed
[mobile app](https://github.com/coredevices/mobileapp), and the wordmark is their
trademark — used here referentially, to identify which service this integration
talks to. Nothing else in this repository derives from their code: the document
schema was read from it, but a data format is not copyrightable and no code was
copied.
