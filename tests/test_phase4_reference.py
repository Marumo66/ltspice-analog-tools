import csv
from pathlib import Path
import unittest

from src.metrics import calculate_average_slew_rate


FIXTURE = Path(__file__).parent / "fixtures" / "phase4_opamp_vout.csv"


class TestPhase4Reference(unittest.TestCase):

    def test_fixture_has_selected_transition_samples(self):
        with FIXTURE.open(newline="", encoding="utf-8") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(len(rows), 8)

    def test_rising_slew_rate(self):
        result = calculate_average_slew_rate(FIXTURE, 0.25, 0.75, "rising")
        self.assertAlmostEqual(
            result["average_slew_rate_V_per_us"], 10.841553, delta=0.0001
        )

    def test_falling_slew_rate(self):
        result = calculate_average_slew_rate(FIXTURE, 0.25, 0.75, "falling")
        self.assertAlmostEqual(
            result["average_slew_rate_V_per_us"], 3.926793, delta=0.0001
        )


if __name__ == "__main__":
    unittest.main()
