"""BLE transport and polling coordinator for Deye SUN-6K-G05P1."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
import time
from typing import Any

from bleak import BleakClient
from bleak.exc import BleakError
from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CHARACTERISTIC_UUID,
    CLOUD_BREAK_SECONDS,
    CONF_CLOUD_BREAK,
    CONF_CONNECTION_MODE,
    CONF_DEVICE_NAME,
    CONF_POLL_INTERVAL,
    DEFAULT_CLOUD_BREAK,
    DEFAULT_LOGGER_NAME,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    IDLE_POLL_INTERVAL,
    IDLE_STATES,
    MODE_ALWAYS_CONNECTED,
    MODE_ON_DEMAND,
    OFFLINE_AFTER_FAILED_CYCLES,
    PERSISTED_KEYS,
    REQUEST_GAP,
    RESPONSE_TIMEOUT,
    SERVICE_UUID,
    SLOW_BLOCK_INTERVAL,
    STORAGE_VERSION,
    ZERO_WHEN_OFFLINE,
)
from .models import DEFAULT_MODEL_PROFILE
from .protocol import (
    METER_KEYS,
    DeyeProtocolError,
    DeyeRejectedError,
    build_at_command,
    decode_block,
    parse_notification,
    reply_ready,
)

_LOGGER = logging.getLogger(__name__)

type DeyeConfigEntry = ConfigEntry[DeyeBleCoordinator]


class DeyeBleCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manage a Deye BLE connection and poll its Modbus registers.

    Two user switches control behaviour:

    * BLE Connection (master): OFF disconnects at once, forces Automatic Polling
      OFF and blocks all BLE traffic (including Poll Now). ON only permits BLE;
      it never turns polling back on.
    * Automatic Polling: OFF stops scheduled reads. Poll Now still works while
      BLE Connection is ON. In Always Connected mode the link stays up.

    When the inverter stops answering (asleep at night, out of range) the
    coordinator keeps the last known energy values, reports production as zero,
    marks grid readings unavailable and slows polling down until it returns.
    """

    def __init__(self, hass: HomeAssistant, entry: DeyeConfigEntry) -> None:
        self.entry = entry
        self.model_profile = DEFAULT_MODEL_PROFILE
        self.address: str = entry.data[CONF_ADDRESS]
        self.device_name: str = entry.data.get(CONF_DEVICE_NAME, DEFAULT_LOGGER_NAME)
        self.connection_mode: str = entry.options.get(
            CONF_CONNECTION_MODE, MODE_ON_DEMAND
        )
        self.poll_interval = int(
            entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        )
        self.cloud_break = int(entry.options.get(CONF_CLOUD_BREAK, DEFAULT_CLOUD_BREAK))

        self.client: BleakClient | None = None
        self.characteristic: Any | None = None
        self._notification_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._lock = asyncio.Lock()
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._persisted: dict[str, Any] = {}
        self._polling_enabled = True
        self._connection_enabled = True
        self._manual_refresh = False
        self._closing = False
        self._connected = False
        self._offline = False
        self._failed_cycles = 0
        self._release_until: float | None = None
        self._cancel_restore: CALLBACK_TYPE | None = None
        self._last_success: datetime | None = None
        self._successful_blocks = 0
        self._block_last_ok: dict[int, float] = {}
        self._failed_blocks: dict[str, str] = {}
        self._last_block_status: dict[str, str] = {}
        self._last_error: str | None = None
        self._connection_attempts = 0
        self._registered_serial: str | None = None

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{self.address}",
            update_interval=timedelta(seconds=self.poll_interval),
        )

    @property
    def polling_enabled(self) -> bool:
        """Return whether scheduled polling is enabled."""
        return self._polling_enabled

    @property
    def connection_enabled(self) -> bool:
        """Return whether this integration may use BLE at all."""
        return self._connection_enabled

    @property
    def connected(self) -> bool:
        """Return whether a BLE connection is currently active."""
        return bool(self.client and self.client.is_connected and self._connected)

    @property
    def inverter_online(self) -> bool:
        """Return False once the inverter has stopped answering."""
        return not self._offline

    async def async_load_state(self) -> None:
        """Restore switch states and last known values after a restart."""
        stored = await self._store.async_load() or {}
        self._connection_enabled = bool(stored.get("connection_enabled", True))
        # Polling can never be ON while the master switch is OFF.
        self._polling_enabled = (
            bool(stored.get("polling_enabled", True)) and self._connection_enabled
        )
        restored = stored.get("data")
        if isinstance(restored, dict):
            self._persisted = {k: v for k, v in restored.items() if k in PERSISTED_KEYS}
            self.data = dict(self._persisted)

    def _state_to_save(self) -> dict[str, Any]:
        return {
            "connection_enabled": self._connection_enabled,
            "polling_enabled": self._polling_enabled,
            "data": self._persisted,
        }

    def _save_state(self) -> None:
        self._store.async_delay_save(self._state_to_save, 1.0)

    def _remember(self, data: dict[str, Any]) -> None:
        """Persist the slow-changing values, only when they changed."""
        subset = {key: data[key] for key in PERSISTED_KEYS if key in data}
        if subset != self._persisted:
            self._persisted = subset
            self._store.async_delay_save(self._state_to_save, 30.0)

    async def async_initial_refresh(self) -> None:
        """First poll, run in the background so setup never blocks on BLE."""
        await self.async_refresh()
        if self._should_idle_connect():
            await self._async_connect_idle()

    async def async_set_polling_enabled(self, enabled: bool) -> None:
        """Enable or disable scheduled polling; Poll Now keeps working."""
        if enabled and not self._connection_enabled:
            raise ServiceValidationError(
                "Turn BLE Connection on before enabling Automatic Polling"
            )
        self._polling_enabled = enabled
        self._save_state()
        self.async_update_listeners()
        if enabled:
            await self.async_request_refresh()

    async def async_set_connection_enabled(self, enabled: bool) -> None:
        """Master switch. OFF disconnects and also turns polling OFF."""
        self._connection_enabled = enabled
        if not enabled:
            self._polling_enabled = False
        self._save_state()
        self.async_update_listeners()
        if not enabled:
            await self._async_disconnect()
        elif self._should_idle_connect():
            await self._async_connect_idle()

    async def async_poll_now(self) -> None:
        """Run one poll immediately (all blocks), if BLE Connection is ON."""
        if not self._connection_enabled:
            raise ServiceValidationError(
                "BLE Connection is turned off; turn it on to poll"
            )
        remaining = self._release_remaining()
        if remaining > 0:
            raise ServiceValidationError(
                f"Cloud upload break in progress; try again in {remaining:.0f} seconds"
            )
        self._manual_refresh = True
        try:
            # async_refresh bypasses the request debouncer, so the flag is still
            # set while the update runs.
            await self.async_refresh()
        finally:
            self._manual_refresh = False

    def _should_idle_connect(self) -> bool:
        """Always Connected + polling OFF: still hold the link open."""
        return (
            self.connection_mode == MODE_ALWAYS_CONNECTED
            and self._connection_enabled
            and not self._polling_enabled
        )

    async def _async_connect_idle(self) -> None:
        async with self._lock:
            if not self._connection_enabled or self._closing or self.connected:
                return
            try:
                await self._async_connect()
            except (UpdateFailed, HomeAssistantError, BleakError, OSError) as err:
                _LOGGER.warning(
                    "Could not open Deye BLE connection: %s", self._clean(err)
                )

    async def async_shutdown(self) -> None:
        """Stop timers and release the BLE connection when the entry unloads."""
        self._closing = True
        if self._cancel_restore is not None:
            self._cancel_restore()
            self._cancel_restore = None
        await super().async_shutdown()
        await self._async_disconnect()

    def _clean(self, err: object) -> str:
        """Return an error text without the device address (for logs and diagnostics)."""
        text = str(err)
        for form in (
            self.address,
            self.address.lower(),
            self.address.replace(":", "_"),
        ):
            text = text.replace(form, "<address>")
        return text

    def _notification_handler(self, _sender: Any, data: bytearray) -> None:
        """Queue a GATT notification."""
        self._notification_queue.put_nowait(bytes(data))

    def _disconnected_callback(self, _client: BleakClient) -> None:
        """Handle a connection lost outside our control."""
        if self.client is None:
            return  # deliberate disconnect, already handled
        self._connected = False
        self.async_update_listeners()
        _LOGGER.warning("Connection to the Deye logger was lost")

    def _release_remaining(self) -> float:
        """Return the seconds left in a cloud upload break (0 when none)."""
        if self._release_until is None:
            return 0.0
        remaining = self._release_until - time.monotonic()
        if remaining <= 0:
            self._release_until = None
            return 0.0
        return remaining

    def async_setup_cloud_break_timer(self) -> None:
        """Always Connected: periodically free the stick for its cloud upload."""
        if self.connection_mode != MODE_ALWAYS_CONNECTED or self.cloud_break <= 0:
            return
        self.entry.async_on_unload(
            async_track_time_interval(
                self.hass,
                self._async_cloud_break,
                timedelta(minutes=self.cloud_break),
            )
        )

    async def _async_cloud_break(self, _now: datetime | None = None) -> None:
        """Drop the link for CLOUD_BREAK_SECONDS so the stick can upload."""
        if (
            self._closing
            or not self._connection_enabled
            or self.connection_mode != MODE_ALWAYS_CONNECTED
        ):
            return
        async with self._lock:  # never cut a poll in half
            if not self.connected:
                return
            _LOGGER.debug("Releasing BLE for %s s for cloud upload", CLOUD_BREAK_SECONDS)
            self._release_until = time.monotonic() + CLOUD_BREAK_SECONDS
            await self._async_disconnect()
        if self._should_idle_connect():
            # With polling ON the next poll after the break reconnects; with
            # polling OFF nothing would, so restore the link here.
            self._cancel_restore = async_call_later(
                self.hass, CLOUD_BREAK_SECONDS, self._async_restore_link
            )

    async def _async_restore_link(self, _now: datetime) -> None:
        self._cancel_restore = None
        self._release_until = None
        await self._async_connect_idle()

    async def _async_connect(self) -> None:
        """Connect and subscribe to the Deye notification characteristic."""
        if not self._connection_enabled:
            raise UpdateFailed("BLE Connection is disabled")
        if self._release_remaining() > 0:
            raise UpdateFailed("Cloud upload break in progress")
        if self.connected:
            return

        await self._async_disconnect()

        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            raise UpdateFailed("Bluetooth device is not currently reachable")

        try:
            self.client = await establish_connection(
                BleakClient,
                ble_device,
                self.device_name,
                disconnected_callback=self._disconnected_callback,
                max_attempts=3,
                timeout=20,
            )
            service = self.client.services.get_service(SERVICE_UUID)
            if service is None:
                raise HomeAssistantError(
                    f"Deye GATT service {SERVICE_UUID} was not found"
                )
            self.characteristic = service.get_characteristic(CHARACTERISTIC_UUID)
            if self.characteristic is None:
                raise HomeAssistantError(
                    f"Deye GATT characteristic {CHARACTERISTIC_UUID} was not found"
                )

            properties = set(self.characteristic.properties)
            if "notify" not in properties:
                raise HomeAssistantError("Deye characteristic does not support notify")
            if not ({"write", "write-without-response"} & properties):
                raise HomeAssistantError("Deye characteristic is not writable")

            self._notification_queue = asyncio.Queue()
            await self.client.start_notify(
                self.characteristic, self._notification_handler
            )
            if self._closing:
                raise UpdateFailed("Integration is shutting down")
            self._connected = True
            self.async_update_listeners()
            _LOGGER.debug("Connected to the Deye logger")

        except Exception:
            await self._async_disconnect()
            raise

    async def _async_disconnect(self) -> None:
        """Stop notifications and disconnect safely."""
        client = self.client
        self.client = None
        self.characteristic = None
        self._connected = False
        self.async_update_listeners()

        if client is None:
            return
        try:
            if client.is_connected:
                try:
                    await client.stop_notify(CHARACTERISTIC_UUID)
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("Unable to stop Deye notifications", exc_info=True)
                await client.disconnect()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Error disconnecting from Deye BLE", exc_info=True)

    async def _async_read_block(
        self, block: int, start: int, count: int
    ) -> tuple[int, ...]:
        """Send one read command and wait for its matching reply."""
        if (
            not self.client
            or not self.client.is_connected
            or self.characteristic is None
        ):
            raise UpdateFailed("Deye BLE is not connected")

        # Discard notifications left over from a previous request.
        while not self._notification_queue.empty():
            self._notification_queue.get_nowait()

        command = build_at_command(start, count)
        _LOGGER.debug(
            "Deye BLE TX block=%s command=%s",
            block,
            command.decode("ascii", errors="replace").strip(),
        )
        properties = set(self.characteristic.properties)
        # FED4 is write-without-response; only use acknowledged writes if offered.
        await self.client.write_gatt_char(
            self.characteristic, command, response="write" in properties
        )

        deadline = time.monotonic() + RESPONSE_TIMEOUT
        last_error: Exception | None = None
        buffer = b""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise UpdateFailed(
                    f"Timed out waiting for Deye response to block {block}"
                ) from last_error
            try:
                payload = await asyncio.wait_for(
                    self._notification_queue.get(), timeout=remaining
                )
            except TimeoutError as err:
                raise UpdateFailed(
                    f"Timed out waiting for Deye response to block {block}"
                ) from err

            _LOGGER.debug("Deye BLE RX block=%s payload=%s", block, payload.hex(" "))
            # A reply can arrive split over several notifications on a small MTU.
            if payload.startswith((b"+ok=", b"+err")):
                buffer = payload
            else:
                buffer += payload
            if not reply_ready(buffer, count):
                continue
            try:
                return parse_notification(buffer, count)
            except DeyeRejectedError as err:
                raise UpdateFailed(f"Block {block} rejected: {err}") from err
            except DeyeProtocolError as err:
                last_error = err
                buffer = b""
                _LOGGER.debug("Ignoring Deye notification for block %s: %s", block, err)

    def _block_due(self, block: int, now: float, data: dict[str, Any]) -> bool:
        """Decide whether a block is read this cycle.

        Meter blocks are skipped while the meter-detect register reports no
        meter (code 0); if detection has not succeeded yet they are read anyway.
        Slow blocks run on the first poll and then every SLOW_BLOCK_INTERVAL.
        """
        if block in self.model_profile.meter_blocks and data.get("meter_code") == 0:
            return False
        if self._manual_refresh or block not in self.model_profile.slow_blocks:
            return True
        last = self._block_last_ok.get(block)
        return last is None or now - last >= SLOW_BLOCK_INTERVAL

    async def _async_update_data(self) -> dict[str, Any]:
        """Read all supported registers."""
        cached = dict(self.data or {})
        if not self._connection_enabled:
            return cached
        if not self._polling_enabled and not self._manual_refresh:
            return cached
        if self._release_remaining() > 0:
            return cached  # cloud upload break; the next scheduled poll resumes

        async with self._lock:
            if not self._connection_enabled or self._closing:
                return cached
            data = dict(cached)
            success_count = 0
            previously_failed = self._failed_blocks
            self._failed_blocks = {}
            self._last_block_status = {}
            try:
                await self._async_connect()
                self._connection_attempts += 1

                for block, start, count in self.model_profile.read_blocks:
                    if not self._connection_enabled or self._closing:
                        break
                    if not self._block_due(block, time.monotonic(), data):
                        self._last_block_status[str(block)] = (
                            "skipped (no meter)"
                            if block in self.model_profile.meter_blocks
                            else "cached"
                        )
                        continue
                    try:
                        registers = await self._async_read_block(block, start, count)
                    except (UpdateFailed, HomeAssistantError, OSError) as err:
                        self._record_block_failure(block, err, previously_failed)
                        if success_count == 0:
                            break  # nothing answers; do not wait for every block
                        await asyncio.sleep(REQUEST_GAP)
                        continue
                    except BleakError as err:
                        # Link dropped mid-cycle: keep what was read, stop here.
                        self._record_block_failure(block, err, previously_failed)
                        break

                    data.update(decode_block(block, registers))
                    if block == 8:
                        profile = self.model_profile
                        data["mppt_phases"] = (
                            f"{profile.mppt_count} MPPT / {profile.phases} Phase"
                        )
                    success_count += 1
                    self._block_last_ok[block] = time.monotonic()
                    self._last_block_status[str(block)] = "success"
                    self._last_success = dt_util.utcnow()
                    data["last_successful_update"] = self._last_success
                    if block == 7:
                        self._sync_device_serial(data.get("serial_number"))
                    elif block == 9 and data.get("meter_code") == 0:
                        # No meter: drop old values instead of showing stale ones.
                        for key in METER_KEYS:
                            data.pop(key, None)
                    await asyncio.sleep(REQUEST_GAP)

            except UpdateFailed as err:
                self._last_error = self._clean(err)
                _LOGGER.debug("Deye poll failed: %s", self._last_error)
            except (HomeAssistantError, BleakError, OSError) as err:
                self._last_error = self._clean(err)
                _LOGGER.debug("Unable to read Deye inverter: %s", self._last_error)
            finally:
                if self.connection_mode != MODE_ALWAYS_CONNECTED:
                    await self._async_disconnect()

            return self._finish_cycle(data, success_count)

    def _finish_cycle(self, data: dict[str, Any], success_count: int) -> dict[str, Any]:
        """Update the online/offline state after a poll cycle and return the data."""
        if not self._connection_enabled or self._closing:
            return dict(self.data or {})

        if success_count:
            self._failed_cycles = 0
            if self._offline:
                self._offline = False
                _LOGGER.info("Deye inverter is responding again")
            if data.get("inverter_state") == "Offline":
                data.pop("inverter_state")
            self._successful_blocks = success_count
            if not self._failed_blocks:
                self._last_error = None
            self._remember(data)
        else:
            self._failed_cycles += 1
            self._last_error = self._last_error or "No valid reply from the inverter"
            threshold = 1 if self._last_success is None else OFFLINE_AFTER_FAILED_CYCLES
            if not self._offline and self._failed_cycles >= threshold:
                self._offline = True
                _LOGGER.warning(
                    "Deye inverter is not responding (asleep or out of range); "
                    "last known energy values are kept and polling slows to every "
                    "%s s until it answers again",
                    max(self.poll_interval, IDLE_POLL_INTERVAL),
                )

        if self._offline:
            for key in ZERO_WHEN_OFFLINE:
                data[key] = 0
            data["inverter_state"] = "Offline"

        data["rssi"] = self._get_rssi()
        self._update_poll_interval(data)
        return data

    def _update_poll_interval(self, data: dict[str, Any]) -> None:
        """Poll slowly while the inverter is offline, in standby or turned off."""
        idle = self._offline or data.get("inverter_state") in IDLE_STATES
        target = max(self.poll_interval, IDLE_POLL_INTERVAL) if idle else self.poll_interval
        if self.update_interval != timedelta(seconds=target):
            self.update_interval = timedelta(seconds=target)

    def _record_block_failure(
        self, block: int, err: Exception, previously_failed: dict[str, str]
    ) -> None:
        text = self._clean(err)
        self._failed_blocks[str(block)] = text
        self._last_block_status[str(block)] = "failed"
        self._last_error = text
        # Warn once per block; repeats stay at debug level.
        level = (
            logging.DEBUG
            if str(block) in previously_failed or self._offline
            else logging.WARNING
        )
        _LOGGER.log(level, "Deye block %s read failed: %s", block, text)

    def _get_rssi(self) -> int | None:
        """Return the latest RSSI reported by Home Assistant Bluetooth."""
        service_info = bluetooth.async_last_service_info(
            self.hass, self.address, connectable=True
        )
        return service_info.rssi if service_info else None

    def diagnostics_data(self) -> dict[str, Any]:
        """Return connection and polling status for the diagnostics download."""
        return {
            "connected": self.connected,
            "connection_enabled": self._connection_enabled,
            "polling_enabled": self._polling_enabled,
            "inverter_online": self.inverter_online,
            "connection_mode": self.connection_mode,
            "poll_interval_seconds": self.poll_interval,
            "current_interval_seconds": (
                self.update_interval.total_seconds() if self.update_interval else None
            ),
            "cloud_break_minutes": self.cloud_break,
            "connection_attempts": self._connection_attempts,
            "successful_blocks_last_cycle": self._successful_blocks,
            "failed_cycles_in_a_row": self._failed_cycles,
            "last_successful_update": self._last_success,
            "last_block_status": dict(self._last_block_status),
            "failed_blocks": dict(self._failed_blocks),
            "last_error": self._last_error,
        }

    def async_sync_device_serial(self) -> None:
        """Put the inverter serial on the device once it is known.

        The serial is read after the entities exist, so it cannot be part of the
        static DeviceInfo. Called after each serial read and once from setup.
        """
        self._sync_device_serial((self.data or {}).get("serial_number"))

    def _sync_device_serial(self, serial: str | None) -> None:
        if not serial or serial == self._registered_serial:
            return
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(identifiers={(DOMAIN, self.address)})
        if device is None:
            return
        if device.serial_number != serial:
            registry.async_update_device(device.id, serial_number=serial)
        self._registered_serial = serial
