"""Config flow for the Pebble Index integration.

One field: the Firebase refresh token of an account that is already signed in
to index.repebble.com. Core Devices own the Google/GitHub/Apple OAuth clients
and their redirect URIs point at their own domain, so a proper OAuth flow from
Home Assistant is not possible — the token is copied once and then refreshes
itself indefinitely.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PebbleIndexApi, PebbleIndexAuthError, PebbleIndexError
from .const import CONF_REFRESH_TOKEN, DOMAIN, FIREBASE_API_KEY, INDEX_WEB_URL

STEP_SCHEMA = vol.Schema({vol.Required(CONF_REFRESH_TOKEN): str})

# Translation strings may not contain URLs (hassfest rejects them), so the link
# and the console one-liner are supplied at runtime — which also keeps the
# snippet out of all twelve translation files.
PLACEHOLDERS = {
    "index_link": f"[index.repebble.com]({INDEX_WEB_URL})",
    "snippet": (
        f"JSON.parse(localStorage.getItem('firebase:authUser:"
        f"{FIREBASE_API_KEY}:[DEFAULT]')).stsTokenManager.refreshToken"
    ),
}


class PebbleIndexConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for a refresh token and verify it against Firebase."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the token, then exchange it before saving."""
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_REFRESH_TOKEN].strip()
            uid, errors = await self._async_verify(token)
            if uid is not None:
                await self.async_set_unique_id(uid)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Pebble Index",
                    data={CONF_REFRESH_TOKEN: token},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_SCHEMA,
            errors=errors,
            description_placeholders=PLACEHOLDERS,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start over when the stored token stops working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Replace the token on the existing entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_REFRESH_TOKEN].strip()
            uid, errors = await self._async_verify(token)
            if uid is not None:
                entry = self._get_reauth_entry()
                # A token for a different account would silently repoint the
                # entity at someone else's list.
                if entry.unique_id != uid:
                    errors["base"] = "wrong_account"
                else:
                    return self.async_update_reload_and_abort(
                        entry, data_updates={CONF_REFRESH_TOKEN: token}
                    )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_SCHEMA,
            errors=errors,
            description_placeholders=PLACEHOLDERS,
        )

    async def _async_verify(self, token: str) -> tuple[str | None, dict[str, str]]:
        """Return the account id the token belongs to, or a form error."""
        api = PebbleIndexApi(async_get_clientsession(self.hass), token)
        try:
            return await api.async_authenticate(), {}
        except PebbleIndexAuthError:
            return None, {"base": "invalid_auth"}
        except PebbleIndexError:
            return None, {"base": "cannot_connect"}
