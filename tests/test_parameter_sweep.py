import csv
from pathlib import Path
import struct
import tempfile
import unittest

from src.parameter_sweep import run_dc_geometry_sweep


BASE_NETLIST = """* Minimal two-MOSFET DC sweep test
M1 n1 out tail 0 nmosfet L=1u W=10u
M2 n2 in tail 0 nmosfet L=1u W=10u
.include cmos130.lib
.op
.end
"""


def make_raw(path, vout=0.25, vn1=1.2, ivdd=-10e-6, ivtail=0.0):
    variables = [
        ("V(out)", "voltage"),
        ("V(n1)", "voltage"),
        ("I(VDD)", "device_current"),
        ("I(VTAIL)", "device_current"),
    ]
    lines = [
        "Title: test",
        "Date: test",
        "Plotname: Operating Point",
        "Flags: real",
        "No. Variables: 4",
        "No. Points: 1",
        "Variables:",
    ]
    lines.extend(
        f"\t{index}\t{name}\t{kind}"
        for index, (name, kind) in enumerate(variables)
    )
    lines.append("Binary:")
    header = "\r\n".join(lines) + "\r\n"
    payload = struct.pack("<dfff", vout, vn1, ivdd, ivtail)
    path.write_bytes(header.encode("utf-16-le") + payload)


def make_log(path, id1=1.0e-6, id2=1.1e-6, gm1=30e-6, gm2=31e-6):
    text = f"""LTspice 26.0.2 for MacOS
Options: logopinfo
temp = 27
Direct Newton iteration succeeded in finding the operating point.

Semiconductor Device Operating Points:
Name:       M1          M2
Model:   nmosfet     nmosfet
Id:      {id1:.9e}  {id2:.9e}
Gm:      {gm1:.9e}  {gm2:.9e}
Vgs:     2.28e-01  2.28e-01
Vds:     1.19e+00  1.10e+00
Vth:     2.75e-01  2.75e-01
Vdsat:   5.26e-02  5.28e-02
"""
    path.write_text(text, encoding="utf-8")


class FakeRunner:
    def __init__(self):
        self.calls = []

    def run_netlist(self, netlist_path):
        netlist_path = Path(netlist_path)
        text = netlist_path.read_text(encoding="utf-8")
        self.calls.append(netlist_path)

        import re
        widths = {}
        for device in ("M1", "M2"):
            match = re.search(
                rf"(?m)^{device}\b.*?\bW\s*=\s*([0-9.]+)u",
                text,
                re.IGNORECASE,
            )
            widths[device] = float(match.group(1)) if match else 10.0

        mean_width = (widths["M1"] + widths["M2"]) / 2.0
        raw_path = netlist_path.with_suffix(".raw")
        log_path = netlist_path.with_suffix(".log")
        make_raw(
            raw_path,
            vout=0.25 + mean_width * 1e-6,
            vn1=1.2,
            ivdd=-mean_width * 1e-6,
        )
        make_log(
            log_path,
            id1=widths["M1"] * 0.1e-6,
            id2=widths["M2"] * 0.11e-6,
            gm1=widths["M1"] * 3e-6,
            gm2=widths["M2"] * 3.1e-6,
        )

        class Result:
            pass
        result = Result()
        result.raw_path = raw_path
        result.log_path = log_path
        return result


class TestParameterSweep(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "opamp.net"
        self.source.write_text(BASE_NETLIST, encoding="utf-8")
        self.original = self.source.read_text(encoding="utf-8")
        self.runner = FakeRunner()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_runs_each_value_and_writes_summary_csv(self):
        output = self.root / "w_sweep.csv"
        result = run_dc_geometry_sweep(
            self.source,
            devices=["M1", "M2"],
            parameter="w",
            values=["8u", "10u", "12u"],
            supply_voltages={"VDD": 1.5},
            runner=self.runner,
            output_csv=output,
        )
        self.assertEqual(result["run_count"], 3)
        self.assertEqual(len(self.runner.calls), 3)
        self.assertTrue(output.is_file())
        with output.open(newline="", encoding="utf-8") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual([row["sweep_value"] for row in rows], ["8u", "10u", "12u"])
        self.assertEqual(len(rows), 3)
        # The fixture stores currents as 32-bit floats, so allow a small
        # tolerance when the value is converted back to power in micro-watts.
        self.assertAlmostEqual(
            float(rows[1]["selected_supplies_power_delivered_uW"]),
            15.0,
            delta=1e-5,
        )

    def test_source_netlist_is_not_modified(self):
        run_dc_geometry_sweep(
            self.source,
            devices=["M1", "M2"],
            parameter="w",
            values=["12u"],
            supply_voltages={"VDD": 1.5},
            runner=self.runner,
            output_csv=self.root / "source_safe.csv",
        )
        self.assertEqual(self.source.read_text(encoding="utf-8"), self.original)

    def test_sweep_variants_have_requested_geometry(self):
        result = run_dc_geometry_sweep(
            self.source,
            devices=["M1", "M2"],
            parameter="w",
            values=["12u"],
            supply_voltages={"VDD": 1.5},
            runner=self.runner,
            output_csv=self.root / "geometry.csv",
        )
        variant = result["results"][0]["netlist_path"].read_text(encoding="utf-8")
        self.assertIn("M1 n1 out tail 0 nmosfet L=1u W=12u", variant)
        self.assertIn("M2 n2 in tail 0 nmosfet L=1u W=12u", variant)
        self.assertIn(".include cmos130.lib", variant)

    def test_rejects_duplicate_values(self):
        with self.assertRaisesRegex(ValueError, "Duplicate sweep value"):
            run_dc_geometry_sweep(
                self.source,
                devices=["M1", "M2"],
                values=["10u", "10u"],
                supply_voltages={"VDD": 1.5},
                runner=self.runner,
                output_csv=self.root / "dup.csv",
            )

    def test_rejects_duplicate_devices(self):
        with self.assertRaisesRegex(ValueError, "Duplicate device"):
            run_dc_geometry_sweep(
                self.source,
                devices=["M1", "m1"],
                values=["10u"],
                supply_voltages={"VDD": 1.5},
                runner=self.runner,
                output_csv=self.root / "dup_device.csv",
            )

    def test_rejects_invalid_parameter(self):
        with self.assertRaisesRegex(ValueError, "parameter must"):
            run_dc_geometry_sweep(
                self.source,
                devices=["M1"],
                parameter="nf",
                values=["2"],
                supply_voltages={"VDD": 1.5},
                runner=self.runner,
                output_csv=self.root / "bad_parameter.csv",
            )

    def test_rejects_existing_output_csv(self):
        output = self.root / "existing.csv"
        output.write_text("keep", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            run_dc_geometry_sweep(
                self.source,
                devices=["M1"],
                values=["10u"],
                supply_voltages={"VDD": 1.5},
                runner=self.runner,
                output_csv=output,
            )


if __name__ == "__main__":
    unittest.main()
