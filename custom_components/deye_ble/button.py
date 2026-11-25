"""Buttons for Deye BLE."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import DeyeBleCoordinator, DeyeConfigEntry
from .entity import DeyeControlEntity

PARALLEL_UPDATES = 1


class DeyePollNowButton(DeyeControlEntity, ButtonEntity):
    """Request an immediate Deye poll."""

    _attr_translation_key = "poll_now"
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator: DeyeBleCoordinator) -> None:
        super().__init__(coordinator, "poll_now")

    @property
    def available(self) -> bool:
        """Poll Now needs the master BLE Connection switch to be ON."""
        return self.coordinator.connection_enabled

    async def async_press(self) -> None:
        """Run one immediate poll of all register blocks."""
        await self.coordinator.async_poll_now()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DeyeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Deye BLE buttons."""
    async_add_entities([DeyePollNowButton(entry.runtime_data)])
