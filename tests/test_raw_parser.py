import csv
from pathlib import Path
import struct
import tempfile
import unittest

from src.raw_parser import export_raw_to_csv


def make_test_raw(path):
    header = "\r\n".join([
        "Title: parser test",
        "Date: test",
        "Plotname: Transient Analysis",
        "Flags: real forward",
        "No. Variables: 3",
        "No. Points: 3",
        "Offset: 0.0",
        "Command: LTspice",
        "Variables:",
        "\t0\ttime\ttime",
        "\t1\tV(in)\tvoltage",
        "\t2\tV(out)\tvoltage",
        "Binary:",
    ]) + "\r\n"

    samples = [
        (0.0, 0.0, 0.0),
        (1e-6, 1.0, 0.5),
        (2e-6, 0.0, 0.75),
    ]
    binary = b"".join(struct.pack("<dff", *sample) for sample in samples)
    path.write_bytes(header.encode("utf-16-le") + binary)


class TestRawParser(unittest.TestCase):

    def test_transient_raw_to_csv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            raw_path = temp / "test.raw"
            csv_path = temp / "nested" / "test.csv"
            make_test_raw(raw_path)

            result = export_raw_to_csv(raw_path, csv_path)

            self.assertTrue(csv_path.exists())
            self.assertEqual(result["points"], 3)
            self.assertEqual(
                result["variables"], ["time", "V(in)", "V(out)"]
            )

            with csv_path.open(newline="", encoding="utf-8") as file:
                reader = csv.DictReader(file)
                rows = list(reader)

        self.assertEqual(len(rows), 3)
        self.assertAlmostEqual(float(rows[-1]["V(out)"]), 0.75)
        self.assertAlmostEqual(float(rows[-1]["time"]), 2e-6)

    def test_rejects_unsupported_flags(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_path = Path(temp_dir) / "unsupported.raw"
            raw_path.write_bytes(
                "Flags: complex forward\r\nBinary:\r\n".encode("utf-16-le")
            )
            with self.assertRaisesRegex(ValueError, "Only real forward"):
                export_raw_to_csv(raw_path, Path(temp_dir) / "out.csv")

    def test_rejects_truncated_binary_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_path = Path(temp_dir) / "truncated.raw"
            make_test_raw(raw_path)
            raw_path.write_bytes(raw_path.read_bytes()[:-1])

            with self.assertRaisesRegex(ValueError, "Binary data size mismatch"):
                export_raw_to_csv(raw_path, Path(temp_dir) / "out.csv")


if __name__ == "__main__":
    unittest.main()
