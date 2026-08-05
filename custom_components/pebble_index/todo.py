"""Todo platform exposing the Index lists."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import PebbleIndexConfigEntry, PebbleIndexCoordinator
from .api import PebbleIndexError
from .firestore import SEED_NOTES, SEED_REMINDERS, SEED_SHOPPING, ListDefinition

ICONS = {
    SEED_SHOPPING: "mdi:cart",
    SEED_REMINDERS: "mdi:bell-ring",
    SEED_NOTES: "mdi:note-text",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PebbleIndexConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one entity per list in the account's feed."""
    coordinator = entry.runtime_data
    async_add_entities(
        PebbleIndexList(coordinator, definition) for definition in coordinator.lists
    )


class PebbleIndexList(CoordinatorEntity[PebbleIndexCoordinator], TodoListEntity):
    """A view over one Firestore list, not a second copy of it."""

    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.SET_DUE_DATETIME_ON_ITEM
        # The frontend sends `due_date` (not `due_datetime`) whenever the time
        # half of the dialog is left empty, and HA validates on key presence.
        | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
    )

    def __init__(
        self, coordinator: PebbleIndexCoordinator, definition: ListDefinition
    ) -> None:
        super().__init__(coordinator)
        self._list = definition
        self._attr_name = definition.title
        self._attr_icon = ICONS.get(definition.seed or "", "mdi:format-list-bulleted")
        self._attr_unique_id = f"{coordinator.api.uid}_{definition.id}"

    @property
    def todo_items(self) -> list[TodoItem]:
        """Current entries, straight from the last poll."""
        return [
            TodoItem(
                uid=item.uid,
                summary=item.summary,
                status=(
                    TodoItemStatus.COMPLETED
                    if item.done
                    else TodoItemStatus.NEEDS_ACTION
                ),
                due=dt_util.as_local(item.due) if item.due else None,
            )
            for item in self.coordinator.data.get(self._list.id, [])
        ]

    async def async_create_todo_item(self, item: TodoItem) -> None:
        """Add an entry."""
        await self._write(
            self.coordinator.api.async_add(
                self._list.id,
                self._list.kind,
                item.summary or "",
                _as_utc(item),
            )
        )

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Rename an entry, tick it off, or move its deadline."""
        await self._write(
            self.coordinator.api.async_edit(
                item.uid or "",
                item.summary or "",
                item.status == TodoItemStatus.COMPLETED,
                _as_utc(item),
                self._list.kind,
            )
        )

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Remove entries."""
        for uid in uids:
            await self._write(self.coordinator.api.async_delete(uid), refresh=False)
        await self.coordinator.async_request_refresh()

    async def _write(self, call, refresh: bool = True) -> None:
        """Run a write and re-read, so the UI shows what the cloud now holds."""
        try:
            await call
        except PebbleIndexError as err:
            raise HomeAssistantError(str(err)) from err
        if refresh:
            await self.coordinator.async_request_refresh()


def _as_utc(item: TodoItem) -> datetime | None:
    """Normalise a due value to UTC.

    Home Assistant hands over either a datetime or a bare date, depending on
    which due-date feature the caller used; a bare date means midnight local.
    """
    if item.due is None:
        return None
    if isinstance(item.due, datetime):
        return dt_util.as_utc(item.due)
    return dt_util.as_utc(dt_util.start_of_local_day(item.due))
