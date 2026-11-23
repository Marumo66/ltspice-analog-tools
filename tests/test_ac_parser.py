import csv
from pathlib import Path
import struct
import tempfile
import unittest

from src.ac_parser import export_ac_raw_to_csv


def make_test_ac_raw(path):
    header = "\r\n".join([
        "Title: AC parser test",
        "Date: test",
        "Plotname: AC Analysis",
        "Flags: complex forward log",
        "No. Variables: 3",
        "No. Points: 3",
        "Command: LTspice",
        "Variables:",
        "\t0\tfrequency\tfrequency",
        "\t1\tV(out)\tvoltage",
        "\t2\tV(nfb)\tvoltage",
        "Binary:",
    ]) + "\r\n"

    samples = [
        (10.0, 0.0, 2.0, -1.0, 1.0, 0.0),
        (100.0, 0.0, 1.0, -0.5, 1.0, 0.0),
        (1000.0, 0.0, 0.5, -0.2, 1.0, 0.0),
    ]
    binary = b"".join(struct.pack("<6d", *sample) for sample in samples)
    path.write_bytes(header.encode("utf-16-le") + binary)


class TestACParser(unittest.TestCase):

    def test_ac_raw_to_csv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            raw_path = temp / "test_ac.raw"
            csv_path = temp / "nested" / "test_ac.csv"
            make_test_ac_raw(raw_path)

            result = export_ac_raw_to_csv(raw_path, csv_path)

            self.assertEqual(result["points"], 3)
            self.assertEqual(result["variables"], ["frequency", "V(out)", "V(nfb)"])
            self.assertEqual(result["frequency_start"], 10.0)
            self.assertEqual(result["frequency_end"], 1000.0)

            with csv_path.open(newline="", encoding="utf-8") as file:
                reader = csv.DictReader(file)
                rows = list(reader)
                columns = reader.fieldnames

        self.assertEqual(
            columns,
            ["frequency", "V(out).real", "V(out).imag",
             "V(nfb).real", "V(nfb).imag"],
        )
        self.assertEqual(len(rows), 3)
        self.assertAlmostEqual(float(rows[1]["V(out).real"]), 1.0)
        self.assertAlmostEqual(float(rows[1]["V(out).imag"]), -0.5)

    def test_rejects_non_ac_raw(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_path = Path(temp_dir) / "transient.raw"
            raw_path.write_bytes(
                "Plotname: Transient Analysis\r\nFlags: real forward\r\nBinary:\r\n"
                .encode("utf-16-le")
            )
            with self.assertRaisesRegex(ValueError, "not an AC Analysis"):
                export_ac_raw_to_csv(raw_path, Path(temp_dir) / "out.csv")

    def test_rejects_truncated_binary_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_path = Path(temp_dir) / "truncated.raw"
            make_test_ac_raw(raw_path)
            raw_path.write_bytes(raw_path.read_bytes()[:-1])
            with self.assertRaisesRegex(ValueError, "Binary size mismatch"):
                export_ac_raw_to_csv(raw_path, Path(temp_dir) / "out.csv")


if __name__ == "__main__":
    unittest.main()
