import csv
from pathlib import Path
import tempfile
import unittest

from src.metrics import calculate_average_slew_rate


class TestAverageSlewRate(unittest.TestCase):

    def test_rising_and_falling(self):
        samples = [
            (0.0, 0.0),
            (1e-6, 1.0),
            (2e-6, 1.0),
            (3e-6, 0.0),
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "waveform.csv"
            with path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.writer(file)
                writer.writerow(["time", "V(out)"])
                writer.writerows(samples)

            rising = calculate_average_slew_rate(path, 0.0, 1.0, "rising")
            falling = calculate_average_slew_rate(path, 0.0, 1.0, "falling")

        self.assertAlmostEqual(rising["average_slew_rate_V_per_us"], 1.0)
        self.assertAlmostEqual(falling["average_slew_rate_V_per_us"], 1.0)


if __name__ == "__main__":
    unittest.main()
