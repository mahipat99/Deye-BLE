"""Protocol unit tests (no Home Assistant needed).

Run from the repository root: python -m unittest discover -s tests
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components" / "deye_ble"))
from faults import FAULTS  # noqa: E402
from protocol import (  # noqa: E402
    INVERTER_STATE_OPTIONS,
    DeyeProtocolError,
    DeyeRejectedError,
    build_at_command,
    build_modbus_read,
    decode_block,
    expected_reply_length,
    modbus_crc,
    parse_notification,
    reply_ready,
)


def make_reply(registers: tuple[int, ...]) -> bytes:
    """Build a '+ok=' notification like the stick sends (with 0000 padding)."""
    body = bytes((0x01, 0x03, len(registers) * 2)) + b"".join(
        value.to_bytes(2, "big") for value in registers
    )
    crc = modbus_crc(body)
    frame = body + bytes((crc & 0xFF, crc >> 8))
    return b"+ok=" + frame.hex().upper().encode() + b"0000"


class ProtocolTests(unittest.TestCase):
    def test_crc_known_vector(self):
        self.assertEqual(modbus_crc(bytes.fromhex("01030000000A")), 0xCDC5)

    def test_command_matches_live_capture(self):
        # Registers 3-7, as sent from nRF Connect / the ESP32.
        self.assertEqual(build_at_command(3, 5), b"AT+INVDATA=8,01030003000575C9\n")
        self.assertEqual(len(build_modbus_read(109, 4)), 8)

    def test_live_serial_reply(self):
        reply = b"+ok=01030A5158374D324B3952345428690000"
        registers = parse_notification(reply, 5)
        serial = bytes(b for r in registers for b in (r >> 8, r & 0xFF)).decode()
        self.assertEqual(serial, "QX7M2K9R4T")

    def test_trailing_padding_is_ignored(self):
        self.assertEqual(parse_notification(make_reply((0x1234, 0x5678)), 2), (0x1234, 0x5678))

    def test_wrong_register_count_is_rejected(self):
        with self.assertRaises(DeyeProtocolError):
            parse_notification(make_reply((1, 2)), 3)

    def test_crc_mismatch(self):
        bad = bytearray(make_reply((1, 2)))
        bad[10] = ord("F")  # corrupt the first data nibble, keep the old CRC
        bad = bytes(bad)
        with self.assertRaises(DeyeProtocolError):
            parse_notification(bad, 2)

    def test_error_replies_fail_fast(self):
        with self.assertRaises(DeyeRejectedError):
            parse_notification(b"+err=1", 2)
        with self.assertRaises(DeyeRejectedError):
            parse_notification(b"+ok=01830240F0", 2)

    def test_fragmented_reply_reassembly(self):
        reply = make_reply(tuple(range(21)))
        self.assertGreaterEqual(len(reply), expected_reply_length(21))
        self.assertFalse(reply_ready(reply[:20], 21))
        self.assertTrue(reply_ready(reply, 21))
        self.assertEqual(parse_notification(reply, 21), tuple(range(21)))

    def test_pv_block_and_computed_power(self):
        values = decode_block(1, (3210, 45, 6540, 67))
        self.assertEqual(values["pv1_voltage"], 321.0)
        self.assertEqual(values["pv1_current"], 4.5)
        self.assertEqual(values["pv1_power"], round(321.0 * 4.5, 1))
        self.assertEqual(values["pv_power"], round(321.0 * 4.5 + 654.0 * 6.7, 1))

    def test_u32_is_low_word_first(self):
        # Total generation: low word 0x0001, high word 0x0002 -> 0x00020001.
        values = decode_block(4, (2, 55, 0, 0, 0x0001, 0x0002))
        self.assertEqual(values["total_generation"], 0x00020001 / 10)

    def test_warning_and_fault_code_numbering(self):
        # Warning registers 0x0065/0x0066 map sequentially to W01-W32;
        # fault registers 0x0067-0x006A map sequentially to F01-F64.
        values = decode_block(5, (0x0001, 0x8000, 0x0001, 0x8000, 0x0001, 0x8000))
        self.assertEqual(values["warning_codes"], "W01, W32")
        self.assertEqual(values["fault_codes"], "F01, F32, F33, F64")

    def test_single_phase_temperature_only(self):
        # The supported single-phase profile reads only register 0x005A.
        self.assertEqual(decode_block(6, (1250,))["dc_temperature"], 25.0)
        self.assertNotIn("ac_temperature", decode_block(6, (1250,)))

    def test_meter_load_energy_is_not_generation_energy(self):
        meter = decode_block(3, (100, 0, 123, 456, 0, 0, 0, 20, 0, 0, 30, 0, 0))
        inverter = decode_block(4, (2, 123, 0, 0, 456, 0))
        self.assertEqual(meter["daily_load_energy"], 1.23)
        self.assertEqual(meter["total_load_energy"], 45.6)
        self.assertEqual(inverter["daily_generation"], 12.3)
        self.assertEqual(inverter["total_generation"], 45.6)
        self.assertNotEqual(meter["daily_load_energy"], inverter["daily_generation"])

    def test_meter_detection(self):
        self.assertEqual(decode_block(9, (0,))["meter_type"], "Unconnected")
        self.assertEqual(decode_block(9, (1,))["meter_code"], 1)
        self.assertEqual(decode_block(9, (2,))["meter_type"], "Eastron")

    def test_mppt_phases_raw_word_is_not_misrepresented_as_capability(self):
        # 0x0012's high byte is not a verified MPPT count. The coordinator
        # now displays the capability from the validated model profile.
        regs = (0,) * 6 + (0x0101,) + (0,) * 6
        self.assertNotIn("mppt_phases", decode_block(8, regs))

    def test_inverter_state_names(self):
        self.assertEqual(decode_block(4, (2, 0, 0, 0, 0, 0))["inverter_state"], "Normal")
        self.assertEqual(decode_block(4, (99, 0, 0, 0, 0, 0))["inverter_state"], "Unknown")
        for state in range(6):
            value = decode_block(4, (state, 0, 0, 0, 0, 0))["inverter_state"]
            self.assertIn(value, INVERTER_STATE_OPTIONS)

    def test_fault_table_covers_every_fault_bit(self):
        self.assertEqual(set(FAULTS), {f"F{n:02d}" for n in range(1, 65)})
        for description, advice in FAULTS.values():
            self.assertTrue(description)
            self.assertIsInstance(advice, str)


if __name__ == "__main__":
    unittest.main()
