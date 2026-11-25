"""Deye BLE Home Assistant integration."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_VERSION
from .coordinator import DeyeBleCoordinator, DeyeConfigEntry

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: DeyeConfigEntry) -> bool:
    """Set up Deye BLE from a config entry."""
    coordinator = DeyeBleCoordinator(hass, entry)
    await coordinator.async_load_state()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    entry.async_on_unload(coordinator.async_shutdown)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_sync_device_serial()
    coordinator.async_setup_cloud_break_timer()

    # The inverter sleeps at night. A failed first poll must not block setup or
    # hide the control switches, so poll in the background instead of raising
    # ConfigEntryNotReady.
    entry.async_create_background_task(
        hass, coordinator.async_initial_refresh(), f"{DOMAIN}_initial_refresh"
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DeyeConfigEntry) -> bool:
    """Unload Deye BLE."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: DeyeConfigEntry) -> None:
    """Delete stored state when the entry is removed."""
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()


async def _async_update_listener(hass: HomeAssistant, entry: DeyeConfigEntry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
