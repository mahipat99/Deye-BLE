"""Binary sensors for Deye BLE."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import DeyeBleCoordinator, DeyeConfigEntry
from .entity import DeyeControlEntity

PARALLEL_UPDATES = 0


class DeyeConnectedBinarySensor(DeyeControlEntity, BinarySensorEntity):
    """Report whether the integration currently has a BLE connection."""

    _attr_translation_key = "ble_connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DeyeBleCoordinator) -> None:
        super().__init__(coordinator, "ble_connected")

    @property
    def is_on(self) -> bool:
        return self.coordinator.connected


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DeyeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Deye BLE binary sensors."""
    async_add_entities([DeyeConnectedBinarySensor(entry.runtime_data)])
