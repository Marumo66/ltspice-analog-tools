import csv
import unittest
from pathlib import Path

from src.ac_metrics import calculate_loop_gain_metrics


FIXTURE = Path(__file__).parent / "fixtures" / "phase4_opamp_ac.csv"


class TestPhase4ACReference(unittest.TestCase):

    def test_fixture_point_count(self):
        with FIXTURE.open(newline="", encoding="utf-8") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(len(rows), 93)

    def test_reference_metrics(self):
        result = calculate_loop_gain_metrics(FIXTURE)

        self.assertAlmostEqual(
            result["low_frequency_gain_dB"], 79.5780, delta=0.001
        )
        self.assertAlmostEqual(
            result["ugbw_Hz"] / 1e6, 13.610241, delta=0.005
        )
        self.assertAlmostEqual(
            result["phase_margin_deg"], 45.9219, delta=0.02
        )


if __name__ == "__main__":
    unittest.main()
