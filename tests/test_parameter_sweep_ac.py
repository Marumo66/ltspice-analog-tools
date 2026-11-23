import cmath
import csv
import math
from pathlib import Path
import struct
import tempfile
import unittest

from src.parameter_sweep_ac import run_ac_geometry_sweep


BASE_NETLIST = """* Minimal two-stage op-amp AC sweep test
M1 n1 out tail 0 nmosfet L=1u W=10u
M2 n2 in tail 0 nmosfet L=1u W=10u
VTEST out nfb 0 AC 1 0
.include cmos130.lib
.ac dec 100 1 1Meg
.end
"""


def make_ac_raw(path, include_feedback=True):
    variables = [("frequency", "frequency"), ("V(out)", "voltage")]
    if include_feedback:
        variables.append(("V(nfb)", "voltage"))
    samples = [
        (10.0, 20.0, -10.0),
        (100.0, 12.0, -65.0),
        (1000.0, 0.0, -120.0),
        (10000.0, -20.0, -150.0),
    ]
    lines = [
        "Title: AC sweep test", "Date: test", "Plotname: AC Analysis",
        "Flags: complex forward log", f"No. Variables: {len(variables)}",
        f"No. Points: {len(samples)}", "Variables:",
    ]
    lines.extend(
        f"\t{index}\t{name}\t{kind}"
        for index, (name, kind) in enumerate(variables)
    )
    lines.append("Binary:")
    header = "\r\n".join(lines) + "\r\n"

    binary = bytearray()
    for frequency, gain_db, phase_deg in samples:
        loop_gain = 10 ** (gain_db / 20) * cmath.exp(1j * math.radians(phase_deg))
        values = [complex(frequency, 0), -loop_gain]
        if include_feedback:
            values.append(1.0 + 0.0j)
        flat = []
        for value in values:
            flat.extend([value.real, value.imag])
        binary.extend(struct.pack("<" + "d" * len(flat), *flat))
    path.write_bytes(header.encode("utf-16-le") + bytes(binary))


def make_log(path):
    text = """LTspice 26.0.2 for MacOS
Options: logopinfo
temp = 27
Direct Newton iteration succeeded in finding the operating point.

Semiconductor Device Operating Points:
Name:       M1          M2
Model:   nmosfet     nmosfet
Id:      1.00e-06    1.10e-06
Gm:      3.00e-05    3.10e-05
Vgs:     2.28e-01    2.28e-01
Vds:     1.19e+00    1.10e+00
Vth:     2.75e-01    2.75e-01
Vdsat:   5.26e-02    5.28e-02
"""
    path.write_text(text, encoding="utf-8")


class FakeRunner:
    def __init__(self, *, include_feedback=True):
        self.calls = []
        self.include_feedback = include_feedback

    def run_netlist(self, netlist_path):
        netlist_path = Path(netlist_path)
        self.calls.append(netlist_path)
        raw_path = netlist_path.with_suffix(".raw")
        log_path = netlist_path.with_suffix(".log")
        make_ac_raw(raw_path, include_feedback=self.include_feedback)
        make_log(log_path)

        class Result:
            pass
        result = Result()
        result.raw_path = raw_path
        result.log_path = log_path
        return result


class TestACParameterSweep(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "opamp_ac.net"
        self.source.write_text(BASE_NETLIST, encoding="utf-8")
        self.original = self.source.read_text(encoding="utf-8")
        self.runner = FakeRunner()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_runs_each_value_and_writes_ac_summary(self):
        output = self.root / "ac_sweep.csv"
        result = run_ac_geometry_sweep(
            self.source, devices=["M1", "M2"], parameter="w",
            values=["10u", "12u", "16u"], runner=self.runner, output_csv=output,
        )
        self.assertEqual(result["run_count"], 3)
        self.assertEqual(len(self.runner.calls), 3)
        with output.open(newline="", encoding="utf-8") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual([row["sweep_value"] for row in rows], ["10u", "12u", "16u"])
        self.assertEqual(len(rows), 3)
        self.assertAlmostEqual(float(rows[0]["low_frequency_gain_dB"]), 20.0)
        self.assertAlmostEqual(float(rows[0]["ugbw_Hz"]), 1000.0)
        self.assertAlmostEqual(float(rows[0]["phase_margin_deg"]), 60.0)
        self.assertEqual(int(rows[0]["raw_points"]), 4)

    def test_source_netlist_is_not_modified(self):
        run_ac_geometry_sweep(
            self.source, devices=["M1", "M2"], values=["12u"],
            runner=self.runner, output_csv=self.root / "source_safe.csv",
        )
        self.assertEqual(self.source.read_text(encoding="utf-8"), self.original)

    def test_sweep_variants_have_requested_geometry(self):
        result = run_ac_geometry_sweep(
            self.source, devices=["M1", "M2"], values=["12u"],
            runner=self.runner, output_csv=self.root / "geometry.csv",
        )
        variant = result["results"][0]["netlist_path"].read_text(encoding="utf-8")
        self.assertIn("M1 n1 out tail 0 nmosfet L=1u W=12u", variant)
        self.assertIn("M2 n2 in tail 0 nmosfet L=1u W=12u", variant)
        self.assertIn(".ac dec 100 1 1Meg", variant)
        self.assertIn(".include cmos130.lib", variant)

    def test_rejects_duplicate_values(self):
        with self.assertRaisesRegex(ValueError, "Duplicate sweep value"):
            run_ac_geometry_sweep(
                self.source, devices=["M1", "M2"], values=["10u", "10u"],
                runner=self.runner, output_csv=self.root / "duplicate.csv",
            )

    def test_rejects_duplicate_devices(self):
        with self.assertRaisesRegex(ValueError, "Duplicate device"):
            run_ac_geometry_sweep(
                self.source, devices=["M1", "m1"], values=["10u"],
                runner=self.runner, output_csv=self.root / "duplicate_device.csv",
            )

    def test_rejects_invalid_parameter(self):
        with self.assertRaisesRegex(ValueError, "parameter must"):
            run_ac_geometry_sweep(
                self.source, devices=["M1"], parameter="nf", values=["2"],
                runner=self.runner, output_csv=self.root / "bad_parameter.csv",
            )

    def test_rejects_netlist_without_active_ac(self):
        self.source.write_text(
            self.original.replace(".ac dec 100 1 1Meg", "* .ac dec 100 1 1Meg"),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "active .ac directive"):
            run_ac_geometry_sweep(
                self.source, devices=["M1"], values=["12u"],
                runner=self.runner, output_csv=self.root / "no_ac.csv",
            )

    def test_rejects_missing_feedback_trace(self):
        with self.assertRaisesRegex(ValueError, "Feedback trace"):
            run_ac_geometry_sweep(
                self.source, devices=["M1"], values=["12u"],
                runner=FakeRunner(include_feedback=False),
                output_csv=self.root / "missing_feedback.csv",
            )

    def test_rejects_existing_summary_csv(self):
        output = self.root / "existing.csv"
        output.write_text("keep", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            run_ac_geometry_sweep(
                self.source, devices=["M1"], values=["12u"],
                runner=self.runner, output_csv=output,
            )


if __name__ == "__main__":
    unittest.main()
