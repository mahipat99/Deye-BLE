"""Base entities for Deye BLE."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEVICE_NAME, DOMAIN, MANUFACTURER
from .coordinator import DeyeBleCoordinator


class DeyeEntity(CoordinatorEntity[DeyeBleCoordinator]):
    """Base Deye BLE entity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DeyeBleCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_{key}"
        # The serial number is added to the device after the first read.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            manufacturer=MANUFACTURER,
            model=coordinator.model_profile.model,
            name=DEVICE_NAME,
        )


class DeyeControlEntity(DeyeEntity):
    """Switches, buttons and connection state.

    These stay available while the inverter is asleep, so BLE can still be
    toggled and Poll Now pressed at night.
    """

    @property
    def available(self) -> bool:
        return True
