import csv
from pathlib import Path
import tempfile
import unittest

from src.dc_parser import (
    export_dc_operating_points_to_csv,
    parse_dc_operating_point_log,
    parse_dc_operating_point_text,
)


FIXTURE = Path(__file__).parent / "fixtures" / "phase4_opamp_dc.log"


class TestDCParser(unittest.TestCase):

    def test_parses_all_seven_mosfets(self):
        result = parse_dc_operating_point_log(FIXTURE)
        self.assertEqual(result["device_count"], 7)
        self.assertEqual(
            list(result["devices"]),
            ["M1", "M2", "M5", "M7", "M3", "M4", "M6"],
        )
        self.assertEqual(result["devices"]["M1"]["model"], "nmosfet")
        self.assertEqual(result["devices"]["M3"]["model"], "pmosfet")

    def test_preserves_signed_pmos_values_and_si_units(self):
        result = parse_dc_operating_point_log(FIXTURE)
        self.assertAlmostEqual(result["devices"]["M1"]["Id"], 1.83e-6)
        self.assertAlmostEqual(result["devices"]["M7"]["Gm"], 1.60e-4)
        self.assertAlmostEqual(result["devices"]["M6"]["Id"], -7.55e-6)
        self.assertAlmostEqual(result["devices"]["M6"]["Vgs"], -3.75e-1)

    def test_parses_convergence_and_warnings(self):
        result = parse_dc_operating_point_log(FIXTURE)
        metadata = result["metadata"]
        self.assertTrue(metadata["converged"])
        self.assertFalse(metadata["direct_newton_succeeded"])
        self.assertTrue(metadata["gmin_stepping_succeeded"])
        self.assertEqual(metadata["convergence_method"], "gmin_stepping")
        self.assertEqual(metadata["temperature_C"], 27.0)
        self.assertEqual(metadata["nominal_temperature_C"], 27.0)
        self.assertEqual(len(metadata["warnings"]), 6)

    def test_parses_crlf_text(self):
        text = FIXTURE.read_text(encoding="utf-8").replace("\n", "\r\n")
        result = parse_dc_operating_point_text(text)
        self.assertEqual(result["device_count"], 7)

    def test_exports_one_csv_row_per_device(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "nested" / "dc_operating_points.csv"
            result = export_dc_operating_points_to_csv(FIXTURE, output)
            self.assertEqual(result["device_count"], 7)
            with output.open(newline="", encoding="utf-8") as file:
                rows = list(csv.DictReader(file))
        self.assertEqual(len(rows), 7)
        by_name = {row["device"]: row for row in rows}
        self.assertEqual(by_name["M6"]["model"], "pmosfet")
        self.assertAlmostEqual(float(by_name["M6"]["Id"]), -7.55e-6)

    def test_rejects_log_without_operating_point_section(self):
        with self.assertRaisesRegex(ValueError, "section not found"):
            parse_dc_operating_point_text("LTspice log without device data")


if __name__ == "__main__":
    unittest.main()
