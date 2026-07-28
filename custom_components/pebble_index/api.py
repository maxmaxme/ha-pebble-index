"""Firestore REST client for the Pebble Index feed.

Firestore is the single source of truth: the integration keeps no local copy of
the lists, it only reads and writes the cloud documents. That is what makes
two-way sync free of conflict resolution — the Pebble mobile app already runs a
bidirectional Room<->Firestore sync that resolves by `updatedAt`, so a write
carrying a fresh `updatedAt` is all the phone needs to pick the change up.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime

import aiohttp

from .const import FIRESTORE_URL, REQUEST_TIMEOUT_SEC, TOKEN_URL
from .firestore import (
    DELETE_FIELD_PATHS,
    EDIT_FIELD_PATHS,
    EncryptedItemError,
    Item,
    ListDefinition,
    decode_item,
    decode_list,
    delete_item_fields,
    edit_item_fields,
    new_item_fields,
)

_LOGGER = logging.getLogger(__name__)

_AUTH_ERRORS = {"TOKEN_EXPIRED", "INVALID_REFRESH_TOKEN", "USER_DISABLED", "USER_NOT_FOUND"}

# Firestore caps the values of an array-contains-any filter.
_MAX_LISTS_PER_QUERY = 30


class PebbleIndexError(Exception):
    """The Index backend could not be reached or refused a request."""


class PebbleIndexAuthError(PebbleIndexError):
    """The stored refresh token is no longer usable."""


class PebbleIndexApi:
    """Minimal read/write access to one account's lists and items."""

    def __init__(self, session: aiohttp.ClientSession, refresh_token: str) -> None:
        self._session = session
        self._refresh_token = refresh_token
        self._id_token: str | None = None
        self._id_token_expiry = 0.0
        self._uid: str | None = None

    @property
    def uid(self) -> str | None:
        """Firebase account id, known once authenticated."""
        return self._uid

    async def async_authenticate(self) -> str:
        """Exchange the refresh token for an ID token, returning the account id."""
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
        }
        try:
            async with self._session.post(
                TOKEN_URL,
                data=payload,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SEC),
            ) as response:
                body = await response.json(content_type=None)
                if response.status != 200:
                    message = str(body.get("error", {}).get("message", response.status))
                    if any(code in message for code in _AUTH_ERRORS):
                        raise PebbleIndexAuthError(message)
                    raise PebbleIndexError(message)
        except aiohttp.ClientError as err:
            raise PebbleIndexError(f"Token request failed: {err}") from err
        except TimeoutError as err:
            raise PebbleIndexError("Token request timed out") from err

        self._id_token = body["id_token"]
        # Firebase hands back a refresh token on every exchange; keep the latest
        # in case they ever start rotating it mid-session.
        self._refresh_token = body.get("refresh_token", self._refresh_token)
        self._id_token_expiry = time.monotonic() + int(body.get("expires_in", 3600)) - 60
        self._uid = body["user_id"]
        return self._uid

    async def async_lists(self) -> list[ListDefinition]:
        """Every list in the feed, seeded and user-created alike."""
        body = await self._request("GET", f"/lists/{await self._account()}/lists")
        return [
            definition
            for document in body.get("documents", [])
            if (definition := decode_list(document)) is not None
        ]

    async def async_items(self, list_ids: list[str]) -> dict[str, list[Item]]:
        """Live entries of the given lists, grouped by list id."""
        grouped: dict[str, list[Item]] = {list_id: [] for list_id in list_ids}
        if not list_ids:
            return grouped

        wanted = list_ids[:_MAX_LISTS_PER_QUERY]
        if len(list_ids) > len(wanted):
            _LOGGER.warning(
                "Only the first %s lists are synced; Firestore limits the filter",
                _MAX_LISTS_PER_QUERY,
            )

        query = {
            "structuredQuery": {
                "from": [{"collectionId": "items"}],
                # A single filter on purpose: adding an equality on `deleted`
                # would need a composite index, and the Firestore project
                # belongs to Core Devices — we cannot create one there.
                # Tombstones are dropped client-side instead.
                "where": {
                    "fieldFilter": {
                        "field": {"fieldPath": "parentListIds"},
                        "op": "ARRAY_CONTAINS_ANY",
                        "value": {
                            "arrayValue": {
                                "values": [
                                    {"stringValue": list_id} for list_id in wanted
                                ]
                            }
                        },
                    }
                },
            }
        }
        result = await self._request(
            "POST", f"/items/{await self._account()}:runQuery", query
        )

        try:
            for row in result:
                document = row.get("document")
                if document is None:
                    continue
                if (item := decode_item(document)) is None:
                    continue
                for list_id in item.list_ids:
                    if list_id in grouped:
                        grouped[list_id].append(item)
        except EncryptedItemError as err:
            raise PebbleIndexError(
                "This Index feed is end-to-end encrypted; the integration reads "
                "plaintext items only. Turn encryption off in the Pebble app to sync."
            ) from err
        return grouped

    async def async_add(
        self,
        list_id: str,
        list_kind: str,
        summary: str,
        due: datetime | None = None,
    ) -> None:
        """Append an entry to a list."""
        item_id = uuid.uuid4().hex[:12]
        await self._request(
            "POST",
            f"/items/{await self._account()}/items?documentId={item_id}",
            {"fields": new_item_fields(summary, _now(), list_id, list_kind, due)},
        )

    async def async_edit(
        self,
        item_id: str,
        summary: str,
        done: bool,
        due: datetime | None = None,
    ) -> None:
        """Rename an entry, tick it off, or move its deadline."""
        await self._request(
            "PATCH",
            f"/items/{await self._account()}/items/{item_id}"
            f"?{_update_mask(EDIT_FIELD_PATHS)}",
            {"fields": edit_item_fields(summary, done, _now(), due)},
        )

    async def async_delete(self, item_id: str) -> None:
        """Soft-delete an entry, the way the mobile app does."""
        await self._request(
            "PATCH",
            f"/items/{await self._account()}/items/{item_id}"
            f"?{_update_mask(DELETE_FIELD_PATHS)}",
            {"fields": delete_item_fields(_now())},
        )

    async def _account(self) -> str:
        if self._uid is None:
            await self.async_authenticate()
        return self._uid  # type: ignore[return-value]

    async def _token(self) -> str:
        if self._id_token is None or time.monotonic() >= self._id_token_expiry:
            await self.async_authenticate()
        return self._id_token  # type: ignore[return-value]

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        retry: bool = True,
    ):
        """Call Firestore, refreshing the ID token once on a rejected one."""
        try:
            async with self._session.request(
                method,
                f"{FIRESTORE_URL}{path}",
                json=payload,
                headers={"Authorization": f"Bearer {await self._token()}"},
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SEC),
            ) as response:
                if response.status in (401, 403) and retry:
                    self._id_token = None
                    return await self._request(method, path, payload, retry=False)
                body = await response.json(content_type=None)
                if response.status != 200:
                    raise PebbleIndexError(
                        f"Firestore {method} {path.split('?')[0]} returned "
                        f"{response.status}: {body}"
                    )
                return body
        except aiohttp.ClientError as err:
            raise PebbleIndexError(f"Firestore request failed: {err}") from err
        except TimeoutError as err:
            raise PebbleIndexError("Firestore request timed out") from err


def _update_mask(paths: tuple[str, ...]) -> str:
    return "&".join(f"updateMask.fieldPaths={path}" for path in paths)


def _now() -> datetime:
    return datetime.now(UTC)
