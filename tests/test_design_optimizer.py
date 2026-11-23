import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.design_optimizer import (
    evaluate_candidates,
    render_markdown_report,
    run_design_optimization,
    validate_constraints,
    validate_design_config,
    validate_objective,
)


def candidate(value, ugbw, phase_margin, power):
    return {
        "sweep_value": value,
        "ugbw_Hz": ugbw,
        "phase_margin_deg": phase_margin,
        "selected_supplies_power_delivered_uW": power,
    }


class TestConstraintValidation(unittest.TestCase):
    def test_accepts_inclusive_min_and_max(self):
        result = validate_constraints({
            "phase_margin_deg": {"min": 46},
            "selected_supplies_power_delivered_uW": {"max": 30},
            "ugbw_Hz": {"min": 13e6, "max": 15e6},
        })
        self.assertEqual(result["phase_margin_deg"], {"min": 46.0})
        self.assertEqual(result["selected_supplies_power_delivered_uW"], {"max": 30.0})
        self.assertEqual(result["ugbw_Hz"], {"min": 13e6, "max": 15e6})

    def test_rejects_empty_constraints(self):
        with self.assertRaisesRegex(ValueError, "non-empty"):
            validate_constraints({})

    def test_rejects_minimum_greater_than_maximum(self):
        with self.assertRaisesRegex(ValueError, "Minimum exceeds maximum"):
            validate_constraints({"ugbw_Hz": {"min": 15, "max": 10}})

    def test_rejects_non_finite_constraint(self):
        with self.assertRaisesRegex(ValueError, "finite"):
            validate_constraints({"ugbw_Hz": {"min": float("nan")}})

    def test_rejects_unknown_bound_name(self):
        with self.assertRaisesRegex(ValueError, "Unsupported bound"):
            validate_constraints({"ugbw_Hz": {"target": 13e6}})

    def test_validates_objective_direction(self):
        self.assertEqual(
            validate_objective({"metric": "ugbw_Hz", "direction": "maximize"}),
            {"metric": "ugbw_Hz", "direction": "maximize"},
        )
        with self.assertRaisesRegex(ValueError, "maximize"):
            validate_objective({"metric": "ugbw_Hz", "direction": "highest"})


class TestCandidateEvaluation(unittest.TestCase):
    def setUp(self):
        self.constraints = {
            "phase_margin_deg": {"min": 46.0},
            "selected_supplies_power_delivered_uW": {"max": 30.0},
        }

    def test_selects_highest_objective_among_feasible_candidates(self):
        rows = [
            candidate("6u", 12.9e6, 47.7, 26.9),
            candidate("9u", 13.48e6, 46.2, 27.2),
            candidate("10u", 13.61e6, 45.9, 27.3),
        ]
        result = evaluate_candidates(
            rows, self.constraints,
            {"metric": "ugbw_Hz", "direction": "maximize"},
        )
        self.assertEqual(result["candidate_count"], 3)
        self.assertEqual(result["feasible_count"], 2)
        self.assertEqual(result["best_candidate"]["sweep_value"], "9u")
        self.assertFalse(result["candidates"][2]["feasible"])

    def test_selects_minimum_objective_when_requested(self):
        rows = [
            candidate("6u", 12.9e6, 47.7, 26.9),
            candidate("9u", 13.48e6, 46.2, 27.2),
        ]
        result = evaluate_candidates(
            rows, {"phase_margin_deg": {"min": 46}},
            {"metric": "selected_supplies_power_delivered_uW", "direction": "minimize"},
        )
        self.assertEqual(result["best_candidate"]["sweep_value"], "6u")

    def test_returns_no_best_candidate_when_all_fail(self):
        rows = [candidate("10u", 13.61e6, 45.9, 27.3)]
        result = evaluate_candidates(
            rows, self.constraints,
            {"metric": "ugbw_Hz", "direction": "maximize"},
        )
        self.assertEqual(result["feasible_count"], 0)
        self.assertIsNone(result["best_candidate"])

    def test_constraint_boundary_is_inclusive(self):
        rows = [candidate("9u", 13.4e6, 46.0, 30.0)]
        result = evaluate_candidates(
            rows, self.constraints,
            {"metric": "ugbw_Hz", "direction": "maximize"},
        )
        self.assertEqual(result["feasible_count"], 1)

    def test_rejects_unknown_metric(self):
        rows = [candidate("9u", 13.4e6, 46.0, 30.0)]
        with self.assertRaisesRegex(ValueError, "unavailable"):
            evaluate_candidates(
                rows, {"unknown_metric": {"min": 1}},
                {"metric": "ugbw_Hz", "direction": "maximize"},
            )

    def test_rejects_empty_candidate_list(self):
        with self.assertRaisesRegex(ValueError, "At least one candidate"):
            evaluate_candidates(
                [], self.constraints,
                {"metric": "ugbw_Hz", "direction": "maximize"},
            )


class TestReportAndConfig(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def base_config(self):
        return {
            "source_netlist": "opamp.net",
            "devices": ["M1", "M2"],
            "parameter": "w",
            "values": ["6u", "9u", "10u"],
            "supply_voltages": {"VDD": 1.5},
            "constraints": {
                "phase_margin_deg": {"min": 46},
                "selected_supplies_power_delivered_uW": {"max": 30},
            },
            "objective": {"metric": "ugbw_Hz", "direction": "maximize"},
        }

    def test_resolves_paths_relative_to_config_directory(self):
        config = validate_design_config(self.base_config(), self.root)
        self.assertEqual(config["source_netlist"], (self.root / "opamp.net").resolve())
        self.assertEqual(
            config["output_csv"],
            (self.root / "opamp_optimization_sweep.csv").resolve(),
        )
        self.assertEqual(
            config["report_directory"],
            (self.root / "design_reports").resolve(),
        )

    def test_rejects_unknown_config_key(self):
        raw = self.base_config()
        raw["optimisation"] = True
        with self.assertRaisesRegex(ValueError, "Unknown configuration"):
            validate_design_config(raw, self.root)

    def test_rejects_output_csv_outside_source_directory(self):
        raw = self.base_config()
        raw["output_csv"] = "../outside.csv"
        with self.assertRaisesRegex(ValueError, "same directory"):
            validate_design_config(raw, self.root)

    def test_report_explains_grid_search_and_best_candidate(self):
        evaluation = evaluate_candidates(
            [candidate("6u", 12.9e6, 47.7, 26.9),
             candidate("9u", 13.48e6, 46.2, 27.2),
             candidate("10u", 13.61e6, 45.9, 27.3)],
            {
                "phase_margin_deg": {"min": 46},
                "ugbw_Hz": {"min": 13e6},
            },
            {"metric": "ugbw_Hz", "direction": "maximize"},
        )
        report = render_markdown_report(
            evaluation,
            source_netlist="opamp.net",
            devices=["M1", "M2"],
            parameter="w",
            values=["6u", "9u", "10u"],
            csv_path="sweep.csv",
        )
        self.assertIn("Selected W value: **9u**", report)
        self.assertIn("finite one-dimensional grid search", report)
        self.assertIn("not a claim of a continuous or global optimum", report)
        self.assertIn("10u", report)
        table_header = next(
            line for line in report.splitlines()
            if line.startswith("| Candidate |")
        )
        self.assertIn("ugbw_Hz (objective; constraint)", table_header)
        self.assertEqual(table_header.count("ugbw_Hz"), 1)
        self.assertIn("12.9000 MHz (fail)", report)
        self.assertIn("13.4800 MHz (pass)", report)

    def test_end_to_end_writes_csv_reference_and_reports_with_fake_sweep(self):
        config = self.base_config()
        config_path = self.root / "design.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")

        def fake_sweep(source, **kwargs):
            csv_path = Path(kwargs["output_csv"])
            csv_path.write_text("sweep_value,ugbw_Hz\n", encoding="utf-8")
            rows = [
                candidate("6u", 12.9e6, 47.7, 26.9),
                candidate("9u", 13.48e6, 46.2, 27.2),
                candidate("10u", 13.61e6, 45.9, 27.3),
            ]
            return {
                "simulation_count": 6,
                "csv_path": csv_path,
                "results": [
                    {"value": row["sweep_value"], "metrics": row}
                    for row in rows
                ],
            }

        with patch("src.design_optimizer.run_unified_geometry_sweep", side_effect=fake_sweep):
            result = run_design_optimization(config_path)

        self.assertEqual(result["simulation_count"], 6)
        self.assertEqual(result["evaluation"]["best_candidate"]["sweep_value"], "9u")
        self.assertTrue(result["report_path"].is_file())
        self.assertTrue(result["json_path"].is_file())
        saved = json.loads(result["json_path"].read_text(encoding="utf-8"))
        self.assertEqual(saved["evaluation"]["feasible_count"], 2)

    def test_refuses_to_overwrite_previous_reports(self):
        config = self.base_config()
        report_dir = self.root / "design_reports"
        report_dir.mkdir()
        (report_dir / "opamp_design_optimization.md").write_text("existing", encoding="utf-8")
        config_path = self.root / "design.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaisesRegex(FileExistsError, "Refusing to overwrite"):
            run_design_optimization(config_path)


if __name__ == "__main__":
    unittest.main()
