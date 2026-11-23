import csv
from pathlib import Path
import tempfile
import unittest

from src.metrics import calculate_slew_rate


class TestMetrics(unittest.TestCase):

    def test_known_slew_rates(self):
        samples = [
            (0.0, 0.0),
            (1e-6, 1.0),
            (2e-6, 1.0),
            (3e-6, -1.0),
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "known_waveform.csv"
            with path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.writer(file)
                writer.writerow(["time", "V(out)"])
                writer.writerows(samples)
            result = calculate_slew_rate(path)

        self.assertAlmostEqual(result["max_rising_V_per_us"], 1.0)
        self.assertAlmostEqual(result["max_falling_V_per_us"], 2.0)
        self.assertAlmostEqual(result["max_absolute_V_per_us"], 2.0)


if __name__ == "__main__":
    unittest.main()
