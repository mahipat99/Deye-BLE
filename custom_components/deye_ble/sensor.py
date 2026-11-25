"""Sensors for the Deye BLE integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import UNAVAILABLE_WHEN_OFFLINE
from .coordinator import DeyeBleCoordinator, DeyeConfigEntry
from .entity import DeyeEntity
from .faults import FAULTS
from .protocol import INVERTER_STATE_OPTIONS

PARALLEL_UPDATES = 0

_MEASUREMENT = SensorStateClass.MEASUREMENT
_DIAGNOSTIC = EntityCategory.DIAGNOSTIC


def _sensor(
    key: str,
    unit: str | None = None,
    device_class: SensorDeviceClass | None = None,
    state_class: SensorStateClass | None = _MEASUREMENT,
    precision: int | None = None,
    **kwargs: Any,
) -> SensorEntityDescription:
    return SensorEntityDescription(
        key=key,
        translation_key=key,
        native_unit_of_measurement=unit,
        device_class=device_class,
        state_class=state_class,
        suggested_display_precision=precision,
        **kwargs,
    )


def _voltage(key: str) -> SensorEntityDescription:
    return _sensor(
        key, UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE, precision=1
    )


def _current(key: str) -> SensorEntityDescription:
    return _sensor(
        key, UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT, precision=1
    )


def _power(key: str, precision: int = 0, **kwargs: Any) -> SensorEntityDescription:
    return _sensor(
        key, UnitOfPower.WATT, SensorDeviceClass.POWER, precision=precision, **kwargs
    )


def _energy(key: str, precision: int, **kwargs: Any) -> SensorEntityDescription:
    return _sensor(
        key,
        UnitOfEnergy.KILO_WATT_HOUR,
        SensorDeviceClass.ENERGY,
        SensorStateClass.TOTAL_INCREASING,
        precision,
        **kwargs,
    )


def _text(key: str, **kwargs: Any) -> SensorEntityDescription:
    return SensorEntityDescription(
        key=key, translation_key=key, entity_category=_DIAGNOSTIC, **kwargs
    )


def _register(key: str) -> SensorEntityDescription:
    return SensorEntityDescription(
        key=key, translation_key=key, entity_category=_DIAGNOSTIC
    )


# Meter readings only have values when a Deye meter is connected, so they are
# disabled by default.
_OPTIONAL = {"entity_registry_enabled_default": False}

SENSORS: tuple[SensorEntityDescription, ...] = (
    _voltage("pv1_voltage"),
    _current("pv1_current"),
    _power("pv1_power"),
    _voltage("pv2_voltage"),
    _current("pv2_current"),
    _power("pv2_power"),
    _power("pv_power"),
    _voltage("grid_voltage_a"),
    _current("grid_current_a"),
    _sensor(
        "grid_frequency",
        UnitOfFrequency.HERTZ,
        SensorDeviceClass.FREQUENCY,
        precision=2,
    ),
    _power("grid_output_power", precision=1),
    _sensor("power_factor", None, SensorDeviceClass.POWER_FACTOR, precision=3),
    _energy("daily_generation", 1),
    _energy("total_generation", 1),
    _sensor(
        "dc_temperature",
        UnitOfTemperature.CELSIUS,
        SensorDeviceClass.TEMPERATURE,
        precision=1,
    ),
    _power("load_power", **_OPTIONAL),
    _power("meter_power", **_OPTIONAL),
    _energy("daily_load_energy", 2, **_OPTIONAL),
    _energy("total_load_energy", 1, **_OPTIONAL),
    _energy("daily_export_energy", 2, **_OPTIONAL),
    _energy("total_export_energy", 1, **_OPTIONAL),
    _energy("daily_import_energy", 2, **_OPTIONAL),
    _energy("total_import_energy", 1, **_OPTIONAL),
    SensorEntityDescription(
        key="inverter_state",
        translation_key="inverter_state",
        device_class=SensorDeviceClass.ENUM,
        options=list(INVERTER_STATE_OPTIONS),
    ),
    _text("fault_codes"),
    _text("warning_codes"),
    _text("meter_type"),
    _text("control_firmware"),
    _text("communication_firmware"),
    _text("serial_number", entity_registry_enabled_default=False),
    _text("protocol_version", entity_registry_enabled_default=False),
    _text("safety_type", entity_registry_enabled_default=False),
    _text("mppt_phases", entity_registry_enabled_default=False),
    _text("inverter_clock", entity_registry_enabled_default=False),
    _sensor(
        "rated_power",
        UnitOfPower.WATT,
        SensorDeviceClass.POWER,
        state_class=None,
        precision=0,
        entity_category=_DIAGNOSTIC,
    ),
    _register("warning_register_1"),
    _register("warning_register_2"),
    _register("fault_register_1"),
    _register("fault_register_2"),
    _register("fault_register_3"),
    _register("fault_register_4"),
    _sensor(
        "rssi",
        SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        SensorDeviceClass.SIGNAL_STRENGTH,
        precision=0,
        entity_category=_DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="last_successful_update",
        translation_key="last_successful_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=_DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)


class DeyeSensor(DeyeEntity, SensorEntity):
    """A Deye BLE sensor."""

    def __init__(
        self,
        coordinator: DeyeBleCoordinator,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Grid and temperature readings are unknown while the inverter is offline."""
        if (
            self.entity_description.key in UNAVAILABLE_WHEN_OFFLINE
            and not self.coordinator.inverter_online
        ):
            return False
        return super().available

    @property
    def native_value(self) -> Any:
        """Return the latest sensor value."""
        return (self.coordinator.data or {}).get(self.entity_description.key)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """List the active faults with their description and advice."""
        if self.entity_description.key != "fault_codes":
            return None
        raw = (self.coordinator.data or {}).get("fault_codes", "OK")
        codes = [] if raw in (None, "", "OK") else [c.strip() for c in str(raw).split(",")]
        details: dict[str, dict[str, str]] = {}
        for code in codes:
            if code in FAULTS:
                description, advice = FAULTS[code]
                details[code] = {"description": description}
                if advice:
                    details[code]["advice"] = advice
        return {
            "active_faults": codes,
            "details": details,
            "note": "Fault descriptions are unverified for this inverter model.",
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DeyeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Deye BLE sensors."""
    coordinator = entry.runtime_data
    async_add_entities(DeyeSensor(coordinator, description) for description in SENSORS)
