"""Downloadable diagnostics for Deye BLE."""

from __future__ import annotations

from typing import Any

from homeassistant.components import bluetooth
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import async_redact_data

from .const import CONF_DEVICE_NAME
from .coordinator import DeyeConfigEntry

# The BLE name AP_<serial> embeds the logger serial, so redact it too.
TO_REDACT = {CONF_ADDRESS, CONF_DEVICE_NAME, "advertised_name", "serial_number"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: DeyeConfigEntry
) -> dict[str, Any]:
    """Return a redacted snapshot of configuration and BLE/protocol status."""
    coordinator = entry.runtime_data
    profile = coordinator.model_profile
    info = bluetooth.async_last_service_info(
        hass, entry.data[CONF_ADDRESS], connectable=True
    )
    snapshot = {
        "entry": {
            CONF_ADDRESS: entry.data.get(CONF_ADDRESS),
            CONF_DEVICE_NAME: entry.data.get(CONF_DEVICE_NAME),
            "options": dict(entry.options),
        },
        "model_profile": {
            "model": profile.model,
            "phases": profile.phases,
            "mppt_count": profile.mppt_count,
            "read_blocks": profile.read_blocks,
        },
        "bluetooth": {
            "advertised_name": (info.name or info.device.name) if info else None,
            "rssi": info.rssi if info else None,
        },
        "polling": coordinator.diagnostics_data(),
        "coordinator_data": dict(coordinator.data or {}),
    }
    return async_redact_data(snapshot, TO_REDACT)
