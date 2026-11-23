import csv
import cmath
import math
from pathlib import Path
import tempfile
import unittest

from src.ac_metrics import calculate_loop_gain_metrics


class TestACMetrics(unittest.TestCase):

    def test_known_loop_gain(self):
        frequencies = [10, 100, 1000, 10000]
        gains_db = [20, 12, 0, -20]
        phases = [-10, -65, -120, -150]

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "known_ac.csv"
            with path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.writer(file)
                writer.writerow([
                    "frequency",
                    "V(out).real", "V(out).imag",
                    "V(nfb).real", "V(nfb).imag",
                ])

                for frequency, gain, phase in zip(frequencies, gains_db, phases):
                    magnitude = 10 ** (gain / 20)
                    loop_gain = magnitude * cmath.exp(
                        1j * math.radians(phase)
                    )
                    # T = -V(out) / V(nfb)
                    writer.writerow([
                        frequency,
                        (-loop_gain).real,
                        (-loop_gain).imag,
                        1.0,
                        0.0,
                    ])

            result = calculate_loop_gain_metrics(path)

        self.assertAlmostEqual(result["low_frequency_gain_dB"], 20.0)
        self.assertAlmostEqual(result["ugbw_Hz"], 1000.0)
        self.assertAlmostEqual(result["phase_margin_deg"], 60.0)


if __name__ == "__main__":
    unittest.main()
