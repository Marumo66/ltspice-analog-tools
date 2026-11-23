import tempfile
from pathlib import Path
import unittest

from src.model_checker import (
    _parse_model_cards,
    check_netlist_models,
    main,
    parse_spice_number,
)


MODEL_TEXT = """* Raccomanded Voltage Supply Range: 1.2V -- 1.5V
.MODEL nMOSFET NMOS (LEVEL=7
+ VERSION=3.20000E+00 WMIN=1.50000E-07 WMAX=1.00000E-05
+ LMIN=1.30000E-07 LMAX=1.00000E-05
+ )
.MODEL pMOSFET PMOS (LEVEL=7
+ VERSION=3.20000E+00 WMIN=1.50000E-07 WMAX=1.00000E-05
+ LMIN=1.30000E-07 LMAX=1.00000E-05
+ )
"""


class TestParseSpiceNumber(unittest.TestCase):
    def test_numeric_suffixes(self):
        cases = {
            "10u": 10e-6,
            "0.15u": 0.15e-6,
            "1e-6": 1e-6,
            "1M": 1e-3,
            "1meg": 1e6,
            "1k": 1e3,
            "1mil": 25.4e-6,
        }
        for literal, expected in cases.items():
            with self.subTest(literal=literal):
                self.assertAlmostEqual(parse_spice_number(literal), expected)

    def test_non_numeric_expression_is_not_evaluated(self):
        for literal in ["{W_IN}", "nan", "1x", ""]:
            with self.subTest(literal=literal):
                self.assertIsNone(parse_spice_number(literal))


class TestModelChecker(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.model = self.root / "cmos130.lib"
        self.model.write_text(MODEL_TEXT, encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_netlist(self, text):
        path = self.root / "test.net"
        path.write_text(text, encoding="utf-8")
        return path

    def test_parses_multiline_nmos_and_pmos_cards(self):
        models = _parse_model_cards(MODEL_TEXT, "test.lib")
        self.assertEqual(set(models), {"nmosfet", "pmosfet"})
        self.assertEqual(models["nmosfet"]["kind"], "nmos")
        self.assertEqual(models["pmosfet"]["parameters"]["VERSION"], "3.20000E+00")
        self.assertAlmostEqual(
            parse_spice_number(models["nmosfet"]["parameters"]["WMAX"]), 10e-6
        )

    def test_detects_out_of_range_width_and_length(self):
        net = self.write_netlist(
            "M1 d g s b nMOSFET L=1u W=12u\n"
            "M2 d g s b pMOSFET L=12u W=8u\n"
            ".include cmos130.lib\n.end\n"
        )
        report = check_netlist_models(net)
        self.assertTrue(report["out_of_range"])
        self.assertTrue(any("M1: W=12 um exceeds" in item for item in report["warnings"]))
        self.assertTrue(any("M2: L=12 um exceeds" in item for item in report["warnings"]))

    def test_accepts_values_on_declared_bounds(self):
        net = self.write_netlist(
            "M1 d g s b nMOSFET L=0.13u W=10u PD=1u PS=1u\n"
            ".include cmos130.lib\n.end\n"
        )
        report = check_netlist_models(net)
        self.assertFalse(report["out_of_range"])
        self.assertFalse(any("exceeds" in item or "below" in item for item in report["warnings"]))

    def test_detects_width_below_wmin(self):
        net = self.write_netlist(
            "M1 d g s b pMOSFET L=1u W=0.1u\n"
            ".include cmos130.lib\n.end\n"
        )
        report = check_netlist_models(net)
        self.assertTrue(report["out_of_range"])
        self.assertTrue(any("W=0.1 um is below" in item for item in report["warnings"]))

    def test_reads_course_model_supply_range_and_checks_supply(self):
        net = self.write_netlist("VDD vdd 0 1.5\n.include cmos130.lib\n.end\n")
        report = check_netlist_models(net, supply_voltages={"VDD": 1.5})
        self.assertEqual(report["recommended_supply_range_V"], (1.2, 1.5))
        self.assertEqual(report["supply_checks"][0]["status"], "within_range")

        outside = check_netlist_models(net, supply_voltages={"VDD": 1.8})
        self.assertEqual(outside["supply_checks"][0]["status"], "outside_range")
        self.assertTrue(any("outside the model comment's recommended supply range" in x
                            for x in outside["warnings"]))

    def test_handles_windows_include_path_by_local_basename(self):
        net = self.write_netlist(
            "M1 d g s b nMOSFET L=1u W=10u\n"
            ".include C:\\\\Course\\\\Models\\\\cmos130.lib\n.end\n"
        )
        report = check_netlist_models(net)
        self.assertEqual(report["summary"]["model_count"], 2)
        self.assertEqual(Path(report["model_files"][0]).name, "cmos130.lib")

    def test_flags_unspecified_and_zero_diffusion_perimeters(self):
        net = self.write_netlist(
            "M1 d g s b nMOSFET L=1u W=10u\n"
            "M2 d g s b nMOSFET L=1u W=10u PD=0 PS=0\n"
            ".include cmos130.lib\n.end\n"
        )
        report = check_netlist_models(net)
        self.assertTrue(any("M1: PD is not explicitly specified" in x for x in report["warnings"]))
        self.assertTrue(any("M2: PD is explicitly zero" in x for x in report["warnings"]))
        self.assertTrue(any("M2: PS is explicitly zero" in x for x in report["warnings"]))
        self.assertFalse(report["out_of_range"])

    def test_skips_expression_based_geometry_with_warning(self):
        net = self.write_netlist(
            "M1 d g s b nMOSFET L={LVAL} W={WVAL}\n"
            ".include cmos130.lib\n.end\n"
        )
        report = check_netlist_models(net)
        self.assertFalse(report["out_of_range"])
        self.assertTrue(any("W='{WWAL}' is not a numeric literal" in x for x in report["warnings"]))

    def test_warns_when_model_is_not_found(self):
        net = self.write_netlist("M1 d g s b missing_model L=1u W=1u\n.end\n")
        report = check_netlist_models(net, model_paths=[str(self.model)])
        self.assertTrue(any("model 'missing_model' was not found" in x for x in report["warnings"]))

    def test_strict_cli_returns_two_for_out_of_range_geometry(self):
        net = self.write_netlist(
            "M1 d g s b nMOSFET L=1u W=12u\n.include cmos130.lib\n.end\n"
        )
        self.assertEqual(main([str(net), "--strict"]), 2)

    def test_checker_does_not_modify_netlist_or_model(self):
        net = self.write_netlist(
            "M1 d g s b nMOSFET L=1u W=12u\n.include cmos130.lib\n.end\n"
        )
        net_before = net.read_text(encoding="utf-8")
        model_before = self.model.read_text(encoding="utf-8")
        check_netlist_models(net)
        self.assertEqual(net.read_text(encoding="utf-8"), net_before)
        self.assertEqual(self.model.read_text(encoding="utf-8"), model_before)


if __name__ == "__main__":
    unittest.main()
