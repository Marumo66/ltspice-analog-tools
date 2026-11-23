import csv
import gzip
from pathlib import Path
import struct
import tempfile
import unittest

from src.dc_raw_parser import (
    calculate_quiescent_power,
    export_dc_operating_point_raw_to_csv,
    parse_dc_operating_point_raw,
)


FIXTURE = Path(__file__).parent / "fixtures" / "phase4_opamp_dc.raw.gz"


def make_op_raw(path, *, plotname="Operating Point", flags="real", truncate=False):
    names = [
        ("V(n001)", "voltage"),
        ("V(out)", "voltage"),
        ("I(VDD)", "device_current"),
        ("I(VTAIL)", "device_current"),
    ]
    header = "\r\n".join([
        "Title: test",
        "Date: test",
        f"Plotname: {plotname}",
        f"Flags: {flags}",
        "No. Variables: 4",
        "No. Points: 1",
        "Variables:",
        *[f"\t{i}\t{name}\t{kind}" for i, (name, kind) in enumerate(names)],
        "Binary:",
    ]) + "\r\n"
    payload = struct.pack("<dfff", 1.5, 0.25, -1.0e-5, 0.0)
    if truncate:
        payload = payload[:-1]
    path.write_bytes(header.encode("utf-16-le") + payload)


class TestDCOperatingPointRawParser(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._temp_dir = tempfile.TemporaryDirectory()
        cls.raw_fixture = Path(cls._temp_dir.name) / "phase4_opamp_dc.raw"
        with gzip.open(FIXTURE, "rb") as compressed:
            cls.raw_fixture.write_bytes(compressed.read())

    @classmethod
    def tearDownClass(cls):
        cls._temp_dir.cleanup()

    def test_parses_real_opamp_raw_fixture(self):
        result = parse_dc_operating_point_raw(self.raw_fixture)
        self.assertEqual(result["plotname"], "Operating Point")
        self.assertEqual(result["point_count"], 1)
        self.assertEqual(result["variable_count"], 44)
        self.assertEqual(len(result["node_voltages"]), 9)
        self.assertEqual(len(result["component_currents"]), 7)
        self.assertEqual(len(result["device_terminal_currents"]), 28)

    def test_extracts_reference_node_voltages(self):
        result = parse_dc_operating_point_raw(self.raw_fixture)
        self.assertAlmostEqual(result["node_voltages"]["V(n001)"], 1.5)
        self.assertAlmostEqual(result["node_voltages"]["V(out)"], 0.24929383397, places=7)
        self.assertAlmostEqual(result["node_voltages"]["V(tail)"], 0.0217319969, places=7)

    def test_extracts_supply_and_device_currents(self):
        result = parse_dc_operating_point_raw(self.raw_fixture)
        self.assertAlmostEqual(
            result["component_currents"]["I(VDD)"], -1.1217771316296421e-5
        )
        self.assertAlmostEqual(
            result["device_terminal_currents"]["Id(M6)"], -7.554984222224448e-6
        )

    def test_calculates_vdd_quiescent_power(self):
        result = calculate_quiescent_power(self.raw_fixture, {"VDD": 1.5})
        self.assertAlmostEqual(
            result["total_power_delivered_uW"], 16.82665697444463, places=5
        )
        self.assertGreater(result["total_power_delivered_W"], 0)

    def test_exports_variables_to_csv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "nested" / "op.csv"
            result = export_dc_operating_point_raw_to_csv(self.raw_fixture, output)
            self.assertEqual(result["variable_count"], 44)
            with output.open(newline="", encoding="utf-8") as file:
                rows = list(csv.DictReader(file))
        self.assertEqual(len(rows), 44)
        by_name = {row["variable"]: row for row in rows}
        self.assertEqual(by_name["V(out)"]["unit"], "V")
        self.assertEqual(by_name["I(VDD)"]["unit"], "A")

    def test_rejects_non_operating_point_raw(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "wrong.raw"
            make_op_raw(path, plotname="Transient Analysis")
            with self.assertRaisesRegex(ValueError, "not an Operating Point"):
                parse_dc_operating_point_raw(path)

    def test_rejects_truncated_binary_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "truncated.raw"
            make_op_raw(path, truncate=True)
            with self.assertRaisesRegex(ValueError, "Binary data size mismatch"):
                parse_dc_operating_point_raw(path)

    def test_rejects_missing_supply_current(self):
        with self.assertRaises(ValueError):
            calculate_quiescent_power(self.raw_fixture, {"VUNKNOWN": 1.0})


if __name__ == "__main__":
    unittest.main()
