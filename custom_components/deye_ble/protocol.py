"""Deye SUN-6K-G05P1 BLE AT/Modbus protocol helpers.

Transport: service FEE7 / characteristic FED4. Request ``AT+INVDATA=8,<hex>\\n``,
reply notification ``+ok=<modbus frame hex>0000``.
"""

from __future__ import annotations

METER_TYPES = {0: "Unconnected", 1: "Chint", 2: "Eastron"}

# Values published by the meter block (3); cleared when no meter is connected.
METER_KEYS = (
    "load_power",
    "daily_load_energy",
    "total_load_energy",
    "meter_power",
    "daily_export_energy",
    "total_export_energy",
    "daily_import_energy",
    "total_import_energy",
)

INVERTER_STATES = (
    "Standby",
    "Self-check",
    "Normal",
    "Alarm",
    "Failure",
    "Turn Off",
)
# "Unknown" is an unrecognised state code; "Offline" is set by the coordinator
# when the inverter does not answer.
INVERTER_STATE_OPTIONS = (*INVERTER_STATES, "Unknown", "Offline")


class DeyeProtocolError(ValueError):
    """Raised when a Deye BLE response is malformed, stale or invalid."""


class DeyeRejectedError(Exception):
    """Raised when the stick or inverter explicitly rejects a request.

    Unlike DeyeProtocolError this is not a stale or garbled frame, so the caller
    should fail the block immediately instead of waiting for another reply.
    """


def modbus_crc(data: bytes) -> int:
    """Return Modbus RTU CRC16."""
    crc = 0xFFFF
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def build_modbus_read(start: int, count: int) -> bytes:
    """Build slave 1 / function 3 read request."""
    frame = bytes(
        (
            0x01,
            0x03,
            (start >> 8) & 0xFF,
            start & 0xFF,
            (count >> 8) & 0xFF,
            count & 0xFF,
        )
    )
    crc = modbus_crc(frame)
    return frame + bytes((crc & 0xFF, (crc >> 8) & 0xFF))


def build_at_command(start: int, count: int) -> bytes:
    """Wrap a Modbus read request in the Deye AT command."""
    return b"AT+INVDATA=8," + build_modbus_read(start, count).hex().upper().encode() + b"\n"


def expected_reply_length(count: int) -> int:
    """Return the ASCII length of a complete ``+ok=`` reply for ``count`` registers.

    ``+ok=`` (4) + hex of the Modbus frame (3 header + 2n data + 2 CRC bytes).
    The trailing ``0000`` padding is optional and not counted.
    """
    return 4 + 2 * (5 + 2 * count)


def reply_ready(buffer: bytes, count: int) -> bool:
    """Return True once the buffered notification text can be parsed.

    Replies may be fragmented over several notifications when the negotiated MTU
    is small, so the caller accumulates data until this returns True.
    """
    text = buffer.decode("ascii", errors="ignore").strip()
    if text.startswith("+err"):
        return True
    if text.startswith("+ok=0183"):  # Modbus exception frame is short
        return True
    return text.startswith("+ok=") and len(text) >= expected_reply_length(count)


def parse_notification(payload: bytes, count: int) -> tuple[int, ...]:
    """Decode ``+ok=HEX`` and validate the Modbus response for ``count`` registers."""
    try:
        text = payload.decode("ascii").strip()
    except UnicodeDecodeError as err:
        raise DeyeProtocolError("Notification is not ASCII") from err

    if text.startswith("+err"):
        raise DeyeRejectedError(f"Stick returned an error: {text}")
    if not text.startswith("+ok="):
        raise DeyeProtocolError("Notification does not start with +ok=")

    hex_payload = text[4:]
    if len(hex_payload) % 2:
        raise DeyeProtocolError("Odd number of hexadecimal characters")
    try:
        frame = bytes.fromhex(hex_payload)
    except ValueError as err:
        raise DeyeProtocolError("Invalid hexadecimal response") from err

    if len(frame) >= 3 and frame[0] == 0x01 and frame[1] == 0x83:
        raise DeyeRejectedError(f"Modbus exception code 0x{frame[2]:02X}")

    expected = count * 2
    if len(frame) < 5 or frame[0] != 0x01 or frame[1] != 0x03:
        raise DeyeProtocolError("Unexpected Modbus slave/function")
    byte_count = frame[2]
    if byte_count != expected or len(frame) < 5 + byte_count:
        raise DeyeProtocolError(
            f"Unexpected response length: {byte_count} bytes, expected {expected}"
        )

    crc_offset = 3 + byte_count
    received_crc = frame[crc_offset] | (frame[crc_offset + 1] << 8)
    if modbus_crc(frame[:crc_offset]) != received_crc:
        raise DeyeProtocolError("Modbus CRC mismatch")

    return tuple(
        (frame[3 + index * 2] << 8) | frame[4 + index * 2]
        for index in range(byte_count // 2)
    )


def u32(registers: tuple[int, ...] | list[int], index: int) -> int:
    """Decode a Deye low-word-first unsigned 32-bit value."""
    return ((registers[index + 1] & 0xFFFF) << 16) | (registers[index] & 0xFFFF)


def s32(registers: tuple[int, ...] | list[int], index: int) -> int:
    """Decode a Deye low-word-first signed 32-bit value."""
    value = u32(registers, index)
    return value - 0x100000000 if value & 0x80000000 else value


def decode_block(block: int, r: tuple[int, ...]) -> dict[str, object]:
    """Convert a validated register block to Home Assistant values."""
    values: dict[str, object] = {}

    if block == 1:
        values.update(
            pv1_voltage=r[0] / 10,
            pv1_current=r[1] / 10,
            pv2_voltage=r[2] / 10,
            pv2_current=r[3] / 10,
            # PV power is not a register; the Deye app computes voltage x current.
            pv1_power=round(r[0] * r[1] / 100, 1),
            pv2_power=round(r[2] * r[3] / 100, 1),
            pv_power=round((r[0] * r[1] + r[2] * r[3]) / 100, 1),
        )

    elif block == 2:
        values.update(
            grid_voltage_a=r[0] / 10,
            grid_current_a=r[3] / 10,
            grid_frequency=r[6] / 100,
            grid_output_power=u32(r, 7) / 10,
            power_factor=r[20] / 1000,
        )

    elif block == 3:
        values.update(
            load_power=u32(r, 0),
            daily_load_energy=r[2] / 100,
            total_load_energy=u32(r, 3) / 10,
            meter_power=s32(r, 5),
            daily_export_energy=r[7] / 100,
            total_export_energy=u32(r, 8) / 10,
            daily_import_energy=r[10] / 100,
            total_import_energy=u32(r, 11) / 10,
        )

    elif block == 4:
        state = r[0]
        values.update(
            inverter_state=(
                INVERTER_STATES[state] if state < len(INVERTER_STATES) else "Unknown"
            ),
            daily_generation=r[1] / 10,
            total_generation=u32(r, 4) / 10,
        )

    elif block == 5:
        warning_registers = r[0:2]
        fault_registers = r[2:6]
        warnings = [
            f"W{bit + 1:02d}"
            for bit in range(32)
            if warning_registers[bit // 16] & (1 << (bit % 16))
        ]
        faults = [
            f"F{bit + 1:02d}"
            for bit in range(64)
            if fault_registers[bit // 16] & (1 << (bit % 16))
        ]
        values.update(
            warning_register_1=r[0],
            warning_register_2=r[1],
            fault_register_1=r[2],
            fault_register_2=r[3],
            fault_register_3=r[4],
            fault_register_4=r[5],
            warning_codes=", ".join(warnings) if warnings else "OK",
            fault_codes=", ".join(faults) if faults else "OK",
        )

    elif block == 6:
        # Register 0x005A, single-phase IGBT/radiator temperature: (raw - 1000) x 0.1.
        if r[0]:
            values["dc_temperature"] = (r[0] - 1000) / 10

    elif block == 7:
        serial = "".join(
            chr(byte)
            for register in r[3:8]
            for byte in (register >> 8, register & 0xFF)
            if byte
        )
        values.update(
            serial_number=serial,
            protocol_version=f"{r[2]:04X}",
        )

    elif block == 9:
        values.update(
            meter_code=r[0],
            meter_type=METER_TYPES.get(r[0], f"Unknown ({r[0]})"),
        )

    elif block == 8:
        values.update(
            control_firmware=f"{r[0]:04X}-{r[1]:04X}",
            communication_firmware=f"{r[2]:04X}",
            safety_type=f"{r[3]:03d}",
            rated_power=u32(r, 4) / 10,
            inverter_clock=(
                f"{2000 + (r[10] >> 8):04d}-{r[10] & 0xFF:02d}-"
                f"{r[11] >> 8:02d} {r[11] & 0xFF:02d}:"
                f"{r[12] >> 8:02d}:{r[12] & 0xFF:02d}"
            ),
        )

    return values
