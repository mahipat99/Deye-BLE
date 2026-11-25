"""Model profiles for Deye BLE inverters.

Only add a profile once its register map has been confirmed on real hardware.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DeyeModelProfile:
    """Register map and hardware capabilities for one inverter family."""

    model: str
    phases: int
    mppt_count: int
    # (block id, first register, register count)
    read_blocks: tuple[tuple[int, int, int], ...]
    # Blocks holding static or slowly changing data.
    slow_blocks: frozenset[int] = frozenset()
    # Blocks only read while a meter is connected.
    meter_blocks: frozenset[int] = frozenset()


SUN_6K_G05P1_EU_CM2 = DeyeModelProfile(
    model="SUN-6K-G05P1-EU-CM2",
    phases=1,
    mppt_count=2,
    read_blocks=(
        (7, 0x0000, 8),  # serial, protocol version
        (1, 0x006D, 4),  # PV1 / PV2 voltage and current
        (2, 0x0049, 21),  # grid
        (8, 0x000C, 13),  # firmware, rated power, clock
        (4, 0x003B, 6),  # state and generation energy
        (6, 0x005A, 1),  # single-phase radiator temperature
        (5, 0x0065, 6),  # warning and fault bitfields
        (9, 0x00E4, 1),  # meter type; must precede block 3
        (3, 0x00C6, 13),  # meter
    ),
    slow_blocks=frozenset({7, 8, 9}),
    meter_blocks=frozenset({3}),
)

DEFAULT_MODEL_PROFILE = SUN_6K_G05P1_EU_CM2
