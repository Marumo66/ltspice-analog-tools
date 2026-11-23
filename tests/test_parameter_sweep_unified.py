import cmath
import csv
import math
from pathlib import Path
import struct
import tempfile
import unittest

from src.parameter_sweep_unified import (
    _make_op_netlist_text,
    run_unified_geometry_sweep,
)


BASE_NETLIST = """* Minimal two-stage op-amp
M1 n1 nfb tail 0 nmosfet L=1u W=10u
M2 n2 N003 tail 0 nmosfet L=1u W=10u
VTEST out nfb 0 AC 1 0
VINP N003 0 0.45 AC 0
VDD N001 0 1.5
.include cmos130.lib
.op
.ac dec 100 1 1Meg
.end
"""


def make_ac_raw(path):
    variables = [
        ("frequency", "frequency"),
        ("V(out)", "voltage"),
        ("V(nfb)", "voltage"),
    ]
    samples = [
        (10.0, 20.0, -10.0),
        (100.0, 12.0, -65.0),
        (1000.0, 0.0, -120.0),
        (10000.0, -20.0, -150.0),
    ]
    lines = [
        "Title: AC sweep test",
        "Date: test",
        "Plotname: AC Analysis",
        "Flags: complex forward log",
        "No. Variables: 3",
        "No. Points: 4",
        "Variables:",
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
        values = [complex(frequency, 0.0), -loop_gain, 1.0 + 0.0j]
        flattened = []
        for value in values:
            flattened.extend([value.real, value.imag])
        binary.extend(struct.pack("<" + "d" * len(flattened), *flattened))
    path.write_bytes(header.encode("utf-16-le") + bytes(binary))


def make_op_raw(path):
    variables = [
        ("V(out)", "voltage"),
        ("V(n1)", "voltage"),
        ("V(n2)", "voltage"),
        ("V(tail)", "voltage"),
        ("I(VDD)", "device_current"),
    ]
    lines = [
        "Title: OP sweep test",
        "Date: test",
        "Plotname: Operating Point",
        "Flags: real",
        "No. Variables: 5",
        "No. Points: 1",
        "Variables:",
    ]
    lines.extend(
        f"\t{index}\t{name}\t{kind}"
        for index, (name, kind) in enumerate(variables)
    )
    lines.append("Binary:")
    header = "\r\n".join(lines) + "\r\n"
    payload = struct.pack("<dffff", 0.25, 1.2, 1.1, 0.02, -10e-6)
    path.write_bytes(header.encode("utf-16-le") + payload)


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
    def __init__(self):
        self.calls = []

    def run_netlist(self, netlist_path):
        netlist_path = Path(netlist_path)
        self.calls.append(netlist_path)
        text = netlist_path.read_text(encoding="utf-8")
        raw_path = netlist_path.with_suffix(".raw")
        log_path = netlist_path.with_suffix(".log")

        if netlist_path.stem.endswith("_op"):
            self.assert_op_mode(text)
            make_op_raw(raw_path)
        else:
            self.assert_ac_mode(text)
            make_ac_raw(raw_path)
        make_log(log_path)

        class Result:
            pass
        result = Result()
        result.raw_path = raw_path
        result.log_path = log_path
        return result

    @staticmethod
    def assert_op_mode(text):
        active_ac = [line for line in text.splitlines()
                     if line.strip().lower().startswith(".ac")]
        active_op = [line for line in text.splitlines()
                     if line.strip().lower() == ".op"]
        if active_ac or len(active_op) != 1:
            raise AssertionError("OP variant must contain .op and no active .ac")

    @staticmethod
    def assert_ac_mode(text):
        active_ac = [line for line in text.splitlines()
                     if line.strip().lower().startswith(".ac")]
        active_op = [line for line in text.splitlines()
                     if line.strip().lower() == ".op"]
        if len(active_ac) != 1 or active_op:
            raise AssertionError("AC variant must contain .ac and no active .op")


class TestUnifiedACDCSweep(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "opamp_ac.net"
        self.source.write_text(BASE_NETLIST, encoding="utf-8")
        self.original = self.source.read_text(encoding="utf-8")
        self.runner = FakeRunner()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_op_variant_replaces_ac_with_op(self):
        result = _make_op_netlist_text(BASE_NETLIST)
        self.assertIn("* .ac dec 100 1 1Meg", result)
        self.assertIn(".op\n.end", result)
        self.assertIn("VINP N003 0 0.45 AC 0", result)

    def test_runs_paired_ac_and_op_for_each_value(self):
        output = self.root / "unified.csv"
        result = run_unified_geometry_sweep(
            self.source,
            devices=["M1", "M2"],
            parameter="w",
            values=["10u", "12u", "16u"],
            supply_voltages={"VDD": 1.5},
            runner=self.runner,
            output_csv=output,
        )
        self.assertEqual(result["run_count"], 3)
        self.assertEqual(result["simulation_count"], 6)
        self.assertEqual(len(self.runner.calls), 6)
        self.assertTrue(output.is_file())

    def test_summary_contains_ac_and_dc_metrics(self):
        output = self.root / "unified.csv"
        run_unified_geometry_sweep(
            self.source,
            devices=["M1", "M2"],
            values=["12u"],
            supply_voltages={"VDD": 1.5},
            runner=self.runner,
            output_csv=output,
        )
        with output.open(newline="", encoding="utf-8") as file:
            rows = list(csv.DictReader(file))
        row = rows[0]
        self.assertAlmostEqual(float(row["ugbw_Hz"]), 1000.0)
        self.assertAlmostEqual(float(row["phase_margin_deg"]), 60.0)
        self.assertAlmostEqual(float(row["V(out)_V"]), 0.25)
        self.assertAlmostEqual(float(row["selected_supplies_power_delivered_uW"]), 15.0)
        self.assertAlmostEqual(float(row["M1_Gm"]), 30e-6)
        self.assertEqual(row["op_convergence_method"], "direct_newton")

    def test_ac_and_op_variants_use_identical_device_geometry_and_bias(self):
        result = run_unified_geometry_sweep(
            self.source,
            devices=["M1", "M2"],
            values=["12u"],
            supply_voltages={"VDD": 1.5},
            runner=self.runner,
            output_csv=self.root / "same_condition.csv",
        )
        ac_text = result["results"][0]["ac_netlist_path"].read_text(encoding="utf-8")
        op_text = result["results"][0]["op_netlist_path"].read_text(encoding="utf-8")
        for fragment in (
            "M1 n1 nfb tail 0 nmosfet L=1u W=12u",
            "M2 n2 N003 tail 0 nmosfet L=1u W=12u",
            "VINP N003 0 0.45 AC 0",
            "VDD N001 0 1.5",
            ".include cmos130.lib",
        ):
            self.assertIn(fragment, ac_text)
            self.assertIn(fragment, op_text)
        self.assertIn(".ac dec 100 1 1Meg", ac_text)
        self.assertNotIn("\n.ac dec", op_text)
        self.assertIn(".op", op_text)

    def test_source_netlist_is_not_modified(self):
        run_unified_geometry_sweep(
            self.source, devices=["M1", "M2"], values=["12u"],
            supply_voltages={"VDD": 1.5}, runner=self.runner,
            output_csv=self.root / "source_safe.csv",
        )
        self.assertEqual(self.source.read_text(encoding="utf-8"), self.original)

    def test_rejects_netlist_with_no_active_ac(self):
        self.source.write_text(
            BASE_NETLIST.replace(".ac dec 100 1 1Meg", "* .ac dec 100 1 1Meg"),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "active .ac directive"):
            run_unified_geometry_sweep(
                self.source, devices=["M1"], values=["12u"],
                supply_voltages={"VDD": 1.5}, runner=self.runner,
                output_csv=self.root / "no_ac.csv",
            )

    def test_rejects_competing_analysis_directive(self):
        self.source.write_text(BASE_NETLIST.replace(".end", ".tran 0 1u\n.end"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "AC analysis only"):
            run_unified_geometry_sweep(
                self.source, devices=["M1"], values=["12u"],
                supply_voltages={"VDD": 1.5}, runner=self.runner,
                output_csv=self.root / "competing.csv",
            )

    def test_rejects_duplicate_sweep_values(self):
        with self.assertRaisesRegex(ValueError, "Duplicate sweep value"):
            run_unified_geometry_sweep(
                self.source, devices=["M1"], values=["10u", "10u"],
                supply_voltages={"VDD": 1.5}, runner=self.runner,
                output_csv=self.root / "duplicate.csv",
            )

    def test_rejects_missing_feedback_trace(self):
        class MissingFeedbackRunner(FakeRunner):
            def run_netlist(self, netlist_path):
                path = Path(netlist_path)
                if path.stem.endswith("_op"):
                    return super().run_netlist(path)
                result = super().run_netlist(path)
                raw_path = result.raw_path
                # Overwrite with a valid AC RAW that has no V(nfb).
                variables = [("frequency", "frequency"), ("V(out)", "voltage")]
                samples = [(1.0, 1.0, 0.0), (10.0, 0.1, 0.0)]
                lines = [
                    "Title: test", "Date: test", "Plotname: AC Analysis",
                    "Flags: complex forward log", "No. Variables: 2",
                    "No. Points: 2", "Variables:",
                    "\t0\tfrequency\tfrequency", "\t1\tV(out)\tvoltage", "Binary:",
                ]
                header = "\r\n".join(lines) + "\r\n"
                binary = b"".join(struct.pack("<4d", f, 0.0, re, im) for f, re, im in samples)
                raw_path.write_bytes(header.encode("utf-16-le") + binary)
                return result
        with self.assertRaisesRegex(ValueError, "Feedback trace"):
            run_unified_geometry_sweep(
                self.source, devices=["M1"], values=["12u"],
                supply_voltages={"VDD": 1.5}, runner=MissingFeedbackRunner(),
                output_csv=self.root / "no_feedback.csv",
            )

    def test_refuses_existing_output(self):
        output = self.root / "existing.csv"
        output.write_text("keep", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            run_unified_geometry_sweep(
                self.source, devices=["M1"], values=["12u"],
                supply_voltages={"VDD": 1.5}, runner=self.runner,
                output_csv=output,
            )


if __name__ == "__main__":
    unittest.main()
