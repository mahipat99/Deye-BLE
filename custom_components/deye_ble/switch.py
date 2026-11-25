"""Control switches for Deye BLE."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import DeyeBleCoordinator, DeyeConfigEntry
from .entity import DeyeControlEntity

PARALLEL_UPDATES = 0


class DeyeConnectionSwitch(DeyeControlEntity, SwitchEntity):
    """Master switch. OFF disconnects, stops polling and blocks all BLE use."""

    _attr_translation_key = "ble_connection"

    def __init__(self, coordinator: DeyeBleCoordinator) -> None:
        super().__init__(coordinator, "ble_connection")

    @property
    def icon(self) -> str:
        return "mdi:bluetooth-connect" if self.is_on else "mdi:bluetooth-off"

    @property
    def is_on(self) -> bool:
        return self.coordinator.connection_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_connection_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_connection_enabled(False)


class DeyePollingSwitch(DeyeControlEntity, SwitchEntity):
    """Scheduled reads. OFF still allows Poll Now while BLE Connection is ON."""

    _attr_translation_key = "ble_polling"

    def __init__(self, coordinator: DeyeBleCoordinator) -> None:
        super().__init__(coordinator, "ble_polling")

    @property
    def icon(self) -> str:
        return "mdi:timer-sync" if self.is_on else "mdi:timer-off"

    @property
    def is_on(self) -> bool:
        return self.coordinator.polling_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_polling_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_polling_enabled(False)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DeyeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Deye BLE switches."""
    coordinator = entry.runtime_data
    async_add_entities(
        [DeyeConnectionSwitch(coordinator), DeyePollingSwitch(coordinator)]
    )
