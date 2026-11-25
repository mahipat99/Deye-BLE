"""Constants for the Deye BLE integration."""

DOMAIN = "deye_ble"

CONF_DEVICE_NAME = "device_name"
CONF_CONNECTION_MODE = "connection_mode"
CONF_POLL_INTERVAL = "poll_interval"
CONF_CLOUD_BREAK = "cloud_break"

MODE_ON_DEMAND = "on_demand"
MODE_ALWAYS_CONNECTED = "always_connected"

DEFAULT_POLL_INTERVAL = 60
MIN_POLL_INTERVAL = 10
MAX_POLL_INTERVAL = 300

# Always Connected mode: every CLOUD_BREAK minutes (0 = never) the Bluetooth
# link is released for CLOUD_BREAK_SECONDS so the stick can reach the cloud.
DEFAULT_CLOUD_BREAK = 0
MIN_CLOUD_BREAK = 0
MAX_CLOUD_BREAK = 1440
CLOUD_BREAK_SECONDS = 60

# Pause between requests and how long to wait for a reply.
REQUEST_GAP = 0.6
RESPONSE_TIMEOUT = 5.0

# Static or slow blocks (serial, firmware, rated power, clock, meter detection)
# are read on the first poll and then only this often (seconds).
SLOW_BLOCK_INTERVAL = 600

# The inverter is treated as offline (asleep or out of range) after this many
# poll cycles in a row without a single valid reply. If nothing has answered
# since startup, the first failed cycle is enough.
OFFLINE_AFTER_FAILED_CYCLES = 3

# While offline, in Standby or turned off, poll no more often than this (seconds).
IDLE_POLL_INTERVAL = 300
IDLE_STATES = ("Standby", "Turn Off")

# Offline behaviour of the live values. Production values read as zero because
# nothing is being produced. Grid and temperature readings cannot be known and
# become unavailable. Everything else keeps its last known value.
ZERO_WHEN_OFFLINE = frozenset(
    {
        "pv1_voltage",
        "pv1_current",
        "pv1_power",
        "pv2_voltage",
        "pv2_current",
        "pv2_power",
        "pv_power",
        "grid_current_a",
        "grid_output_power",
    }
)
UNAVAILABLE_WHEN_OFFLINE = frozenset(
    {
        "grid_voltage_a",
        "grid_frequency",
        "power_factor",
        "dc_temperature",
        "load_power",
        "meter_power",
    }
)

# Values kept across Home Assistant restarts so they are still shown at night.
PERSISTED_KEYS = frozenset(
    {
        "daily_generation",
        "total_generation",
        "daily_load_energy",
        "total_load_energy",
        "daily_export_energy",
        "total_export_energy",
        "daily_import_energy",
        "total_import_energy",
        "rated_power",
        "serial_number",
        "protocol_version",
        "control_firmware",
        "communication_firmware",
        "safety_type",
        "mppt_phases",
        "meter_code",
        "meter_type",
    }
)

STORAGE_VERSION = 1

# GATT service FEE7, characteristic FED4 (notify + write without response).
SERVICE_UUID = "0000fee7-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_UUID = "0000fed4-0000-1000-8000-00805f9b34fb"

MANUFACTURER = "Deye"
DEVICE_NAME = "Deye SUN-6K-G05P1"
DEFAULT_LOGGER_NAME = "Deye inverter"
