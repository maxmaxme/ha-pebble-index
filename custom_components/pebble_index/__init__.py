"""The Pebble Index integration.

Exposes the Pebble Index shopping list as a Home Assistant todo entity.
Reads and writes go straight to the Firestore documents the Pebble mobile app
syncs with, so an item added here shows up on the ring and vice versa.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import PebbleIndexApi, PebbleIndexAuthError, PebbleIndexError
from .const import (
    CONF_ENCRYPTION_KEY,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    UPDATE_INTERVAL_SEC,
)
from .crypto import EncryptionKeyError
from .firestore import Item, ListDefinition

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.TODO]

type PebbleIndexConfigEntry = ConfigEntry[PebbleIndexCoordinator]


class PebbleIndexCoordinator(DataUpdateCoordinator[dict[str, list[Item]]]):
    """Polls every known list; Firestore stays the source of truth."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: PebbleIndexConfigEntry,
        api: PebbleIndexApi,
        lists: list[ListDefinition],
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SEC),
        )
        self.api = api
        # Read once at setup: lists change far more rarely than their contents,
        # and entities are built from them. A list created or renamed on the
        # phone shows up after reloading the entry.
        self.lists = lists

    async def _async_update_data(self) -> dict[str, list[Item]]:
        try:
            return await self.api.async_items([entry.id for entry in self.lists])
        except PebbleIndexAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except PebbleIndexError as err:
            raise UpdateFailed(str(err)) from err


async def async_setup_entry(hass: HomeAssistant, entry: PebbleIndexConfigEntry) -> bool:
    """Set up Pebble Index from a config entry."""
    # The key is editable after setup, so options win over the original data.
    key = entry.options.get(CONF_ENCRYPTION_KEY) or entry.data.get(CONF_ENCRYPTION_KEY)
    try:
        api = PebbleIndexApi(
            async_get_clientsession(hass), entry.data[CONF_REFRESH_TOKEN], key
        )
        await api.async_authenticate()
        lists = await api.async_lists()
    except PebbleIndexAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except (PebbleIndexError, EncryptionKeyError) as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = PebbleIndexCoordinator(hass, entry, api, lists)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Entering the encryption key later has to take effect without a restart.
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload(hass: HomeAssistant, entry: PebbleIndexConfigEntry) -> None:
    """Reload after the options changed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: PebbleIndexConfigEntry) -> bool:
    """Unload a Pebble Index config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
