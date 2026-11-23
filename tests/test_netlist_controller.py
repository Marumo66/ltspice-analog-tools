import tempfile
from pathlib import Path
import unittest

from src.netlist_controller import (
    update_mosfet_geometry_text,
    write_mosfet_geometry_variant,
    _parse_cli_updates,
)


BASE_NETLIST = """* Example two-stage CMOS op-amp
M3 n1 n1 N001 N001 Pmosfet l=1u w=10u
M4 n2 n1 N001 N001 Pmosfet l=1u w=10u
M1 n1 out tail 0 Nmosfet l=1u w=10u
M2 n2 N003 tail 0 Nmosfet l=1u w=10u
M5 tail N005 0 0 Nmosfet l=1u w=10u
M6 out n2 N001 N001 Pmosfet l=1u w=10u
M7 out N004 0 0 Nmosfet l=1u w=10u
RZ n2 N002 10.5k
CL out 0 1p
CC N002 out 1p
.include cmos130.lib
.op
.end
"""


class TestNetlistController(unittest.TestCase):

    def test_updates_one_mosfet_width_only(self):
        result = update_mosfet_geometry_text(BASE_NETLIST, {"M1": {"w": "12u"}})
        self.assertIn("M1 n1 out tail 0 Nmosfet l=1u w=12u", result)
        self.assertIn("M2 n2 N003 tail 0 Nmosfet l=1u w=10u", result)
        self.assertIn("RZ n2 N002 10.5k", result)

    def test_updates_multiple_width_and_length_values(self):
        result = update_mosfet_geometry_text(
            BASE_NETLIST,
            {
                "M1": {"w": "12u", "l": "1.2u"},
                "M2": {"w": "12u"},
                "M5": {"l": "1.5u"},
            },
        )
        self.assertIn("M1 n1 out tail 0 Nmosfet l=1.2u w=12u", result)
        self.assertIn("M2 n2 N003 tail 0 Nmosfet l=1u w=12u", result)
        self.assertIn("M5 tail N005 0 0 Nmosfet l=1.5u w=10u", result)

    def test_preserves_comments_models_sources_and_analysis(self):
        result = update_mosfet_geometry_text(BASE_NETLIST, {"M6": {"w": "15u"}})
        for line in [
            "* Example two-stage CMOS op-amp",
            ".include cmos130.lib",
            ".op",
            "RZ n2 N002 10.5k",
            "CL out 0 1p",
            "CC N002 out 1p",
        ]:
            self.assertIn(line, result)

    def test_rejects_unknown_device(self):
        with self.assertRaisesRegex(ValueError, "not found"):
            update_mosfet_geometry_text(BASE_NETLIST, {"M8": {"w": "12u"}})

    def test_rejects_missing_geometry_assignment(self):
        text = BASE_NETLIST.replace(
            "M1 n1 out tail 0 Nmosfet l=1u w=10u",
            "M1 n1 out tail 0 Nmosfet l=1u",
        )
        with self.assertRaisesRegex(ValueError, "exactly one w"):
            update_mosfet_geometry_text(text, {"M1": {"w": "12u"}})

    def test_rejects_invalid_or_non_positive_values(self):
        for value in ["0", "-1u", "1u; .end", "{W_NEW}", "nan", "1x"]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    update_mosfet_geometry_text(BASE_NETLIST, {"M1": {"w": value}})

    def test_rejects_unsupported_parameter(self):
        with self.assertRaisesRegex(ValueError, "use 'w' or 'l'"):
            update_mosfet_geometry_text(BASE_NETLIST, {"M1": {"nf": "2"}})

    def test_rejects_duplicate_device_lines(self):
        text = BASE_NETLIST.replace(
            "M1 n1 out tail 0 Nmosfet l=1u w=10u",
            "M1 n1 out tail 0 Nmosfet l=1u w=10u\nM1 n1 out tail 0 Nmosfet l=1u w=10u",
        )
        with self.assertRaisesRegex(ValueError, "Duplicate MOSFET"):
            update_mosfet_geometry_text(text, {"M1": {"w": "12u"}})

    def test_cli_update_syntax(self):
        self.assertEqual(
            _parse_cli_updates(["M1.w=12u", "M2.l=1.2u", "M2.w=14u"]),
            {"M1": {"w": "12u"}, "M2": {"l": "1.2u", "w": "14u"}},
        )

    def test_writes_new_sibling_without_modifying_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "opamp_dc.net"
            output = root / "opamp_dc_variant.net"
            source.write_text(BASE_NETLIST, encoding="utf-8")
            original = source.read_text(encoding="utf-8")

            actual = write_mosfet_geometry_variant(
                source, output, {"M1": {"w": "12u"}}
            )

            self.assertEqual(actual, output.resolve())
            self.assertEqual(source.read_text(encoding="utf-8"), original)
            self.assertIn("w=12u", output.read_text(encoding="utf-8"))
            self.assertIn(".include cmos130.lib", output.read_text(encoding="utf-8"))

    def test_refuses_overwrite_and_different_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            other = root / "other"
            other.mkdir()
            source = root / "opamp_dc.net"
            source.write_text(BASE_NETLIST, encoding="utf-8")
            output = root / "variant.net"
            output.write_text("existing", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                write_mosfet_geometry_variant(source, output, {"M1": {"w": "12u"}})
            with self.assertRaisesRegex(ValueError, "same directory"):
                write_mosfet_geometry_variant(
                    source, other / "variant.net", {"M1": {"w": "12u"}}
                )


if __name__ == "__main__":
    unittest.main()
