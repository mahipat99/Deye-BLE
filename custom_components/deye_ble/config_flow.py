"""Config flow for Deye BLE."""

from __future__ import annotations

import logging
from typing import Any

from bleak import BleakClient
from bleak.exc import BleakError
from bleak_retry_connector import establish_connection
import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback

from .const import (
    CHARACTERISTIC_UUID,
    CONF_CLOUD_BREAK,
    CONF_CONNECTION_MODE,
    CONF_DEVICE_NAME,
    CONF_POLL_INTERVAL,
    DEFAULT_CLOUD_BREAK,
    DEFAULT_LOGGER_NAME,
    DEFAULT_POLL_INTERVAL,
    DEVICE_NAME,
    DOMAIN,
    MAX_CLOUD_BREAK,
    MAX_POLL_INTERVAL,
    MIN_CLOUD_BREAK,
    MIN_POLL_INTERVAL,
    MODE_ALWAYS_CONNECTED,
    MODE_ON_DEMAND,
    SERVICE_UUID,
)

_LOGGER = logging.getLogger(__name__)


async def _async_validate_device(hass: HomeAssistant, address: str, name: str) -> None:
    """Connect once and verify the Deye GATT service and characteristic."""
    ble_device = bluetooth.async_ble_device_from_address(
        hass, address, connectable=True
    )
    if ble_device is None:
        raise OSError("Bluetooth device is not reachable")

    client = await establish_connection(
        BleakClient, ble_device, name, max_attempts=2, timeout=20
    )
    try:
        service = client.services.get_service(SERVICE_UUID)
        if service is None:
            raise OSError("Deye GATT service not found")
        characteristic = service.get_characteristic(CHARACTERISTIC_UUID)
        if characteristic is None:
            raise OSError("Deye GATT characteristic not found")
        properties = set(characteristic.properties)
        if "notify" not in properties or not (
            {"write", "write-without-response"} & properties
        ):
            raise OSError("Deye characteristic must support notify and write")
    finally:
        await client.disconnect()


class DeyeBleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the supported Deye inverter."""

    VERSION = 1

    def __init__(self) -> None:
        self._address = ""
        self._device_name = DEFAULT_LOGGER_NAME

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a logger stick found by Home Assistant Bluetooth discovery."""
        self._address = discovery_info.address.upper()
        await self.async_set_unique_id(self._address)
        self._abort_if_unique_id_configured()
        self._device_name = discovery_info.name or DEFAULT_LOGGER_NAME
        self.context["title_placeholders"] = {"name": self._device_name}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm adding the discovered logger."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await _async_validate_device(
                    self.hass, self._address, self._device_name
                )
            except (BleakError, OSError):
                _LOGGER.debug("Device is not a usable Deye logger", exc_info=True)
                errors["base"] = "cannot_connect"
            else:
                return self._create_entry()

        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": self._device_name},
            errors=errors,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a discovered BLE device or enter its address."""
        errors: dict[str, str] = {}
        infos = {
            info.address.upper(): info
            for info in bluetooth.async_discovered_service_info(
                self.hass, connectable=True
            )
        }

        if user_input is not None:
            self._address = user_input[CONF_ADDRESS].upper()
            await self.async_set_unique_id(self._address)
            self._abort_if_unique_id_configured()

            info = infos.get(self._address)
            self._device_name = (
                (info.name or info.device.name or DEFAULT_LOGGER_NAME)
                if info
                else DEFAULT_LOGGER_NAME
            )
            try:
                await _async_validate_device(
                    self.hass, self._address, self._device_name
                )
            except (BleakError, OSError):
                _LOGGER.debug("Device is not a usable Deye logger", exc_info=True)
                errors["base"] = "cannot_connect"
            else:
                return self._create_entry()

        # Logger sticks advertise as AP_<serial>; list them first.
        def sort_key(info: BluetoothServiceInfoBleak) -> bool:
            return not (info.name or info.device.name or "").startswith("AP_")

        discovered: dict[str, str] = {}
        for info in sorted(infos.values(), key=sort_key):
            name = info.name or info.device.name or "Unknown BLE device"
            prefix = "★ " if name.startswith("AP_") else ""
            discovered[info.address.upper()] = (
                f"{prefix}{name} · {info.address} · {info.rssi} dBm"
            )

        address_schema: Any = vol.In(discovered) if discovered else str
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): address_schema}),
            errors=errors,
        )

    def _create_entry(self) -> ConfigFlowResult:
        return self.async_create_entry(
            title=DEVICE_NAME,
            data={CONF_ADDRESS: self._address, CONF_DEVICE_NAME: self._device_name},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> DeyeBleOptionsFlow:
        """Return the options flow."""
        return DeyeBleOptionsFlow()


class DeyeBleOptionsFlow(OptionsFlow):
    """Configure the connection mode, polling interval and cloud upload break."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the settings form."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_CONNECTION_MODE,
                    default=current.get(CONF_CONNECTION_MODE, MODE_ON_DEMAND),
                ): vol.In(
                    {
                        MODE_ON_DEMAND: "Connect on Demand",
                        MODE_ALWAYS_CONNECTED: "Always Connected",
                    }
                ),
                vol.Required(
                    CONF_POLL_INTERVAL,
                    default=current.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_POLL_INTERVAL, max=MAX_POLL_INTERVAL),
                ),
                vol.Required(
                    CONF_CLOUD_BREAK,
                    default=current.get(CONF_CLOUD_BREAK, DEFAULT_CLOUD_BREAK),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_CLOUD_BREAK, max=MAX_CLOUD_BREAK),
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
