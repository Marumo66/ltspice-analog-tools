from pathlib import Path
import csv
import math


def _read_complex_csv(csv_path, output_signal, feedback_signal):
    required = [
        "frequency",
        f"{output_signal}.real",
        f"{output_signal}.imag",
        f"{feedback_signal}.real",
        f"{feedback_signal}.imag",
    ]

    frequencies = []
    loop_gains = []

    with Path(csv_path).open(newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        columns = reader.fieldnames or []
        for column in required:
            if column not in columns:
                raise ValueError(f"Missing column: {column}")

        for row in reader:
            frequency = float(row["frequency"])
            output_value = complex(
                float(row[f"{output_signal}.real"]),
                float(row[f"{output_signal}.imag"]),
            )
            feedback_value = complex(
                float(row[f"{feedback_signal}.real"]),
                float(row[f"{feedback_signal}.imag"]),
            )

            values = (
                frequency,
                output_value.real,
                output_value.imag,
                feedback_value.real,
                feedback_value.imag,
            )
            if not all(math.isfinite(value) for value in values):
                raise ValueError("AC CSV contains a non-finite value")
            if frequency <= 0:
                raise ValueError("Frequency must be positive")
            if abs(feedback_value) == 0:
                raise ValueError("Feedback signal is zero")

            frequencies.append(frequency)
            loop_gains.append(-output_value / feedback_value)

    if len(frequencies) < 2:
        raise ValueError("At least two AC points are required")
    if any(a >= b for a, b in zip(frequencies, frequencies[1:])):
        raise ValueError("Frequency must be strictly increasing")

    return frequencies, loop_gains


def calculate_loop_gain_metrics(
    csv_path,
    output_signal="V(out)",
    feedback_signal="V(nfb)",
):
    """Calculate loop gain, UGBW and phase margin.

    The return ratio is -V(out) / V(nfb). The first descending 0 dB
    crossing is interpolated on a log-frequency axis.
    """
    frequencies, loop_gains = _read_complex_csv(
        csv_path, output_signal, feedback_signal
    )

    gain_db = [20 * math.log10(abs(value)) for value in loop_gains]
    raw_phase = [
        math.degrees(math.atan2(value.imag, value.real))
        for value in loop_gains
    ]

    phase_deg = [raw_phase[0]]
    for phase in raw_phase[1:]:
        delta = phase - raw_phase[len(phase_deg) - 1]
        while delta > 180:
            delta -= 360
        while delta < -180:
            delta += 360
        phase_deg.append(phase_deg[-1] + delta)

    crossing_index = None
    for index in range(len(gain_db) - 1):
        if gain_db[index] >= 0 and gain_db[index + 1] <= 0:
            crossing_index = index
            break

    if crossing_index is None:
        raise ValueError("No descending 0 dB crossing found")

    index = crossing_index
    gain_delta = gain_db[index + 1] - gain_db[index]
    if gain_delta == 0:
        raise ValueError("Cannot interpolate a flat 0 dB segment")

    fraction = -gain_db[index] / gain_delta
    log_f1 = math.log10(frequencies[index])
    log_f2 = math.log10(frequencies[index + 1])
    ugbw = 10 ** (log_f1 + fraction * (log_f2 - log_f1))

    phase_at_ugbw = (
        phase_deg[index]
        + fraction * (phase_deg[index + 1] - phase_deg[index])
    )

    return {
        "low_frequency_gain_dB": gain_db[0],
        "low_frequency_phase_deg": phase_deg[0],
        "ugbw_Hz": ugbw,
        "phase_at_ugbw_deg": phase_at_ugbw,
        "phase_margin_deg": 180 + phase_at_ugbw,
    }


if __name__ == "__main__":
    csv_path = (
        Path.home()
        / "Analog-Design-Automation/results/opamp_ac.csv"
    )
    result = calculate_loop_gain_metrics(csv_path)

    print("Two-Stage CMOS Op-Amp AC Analysis")
    print("--------------------------------")
    print(f"Low-frequency gain: {result['low_frequency_gain_dB']:.4f} dB")
    print(f"UGBW: {result['ugbw_Hz'] / 1e6:.6f} MHz")
    print(f"Phase at UGBW: {result['phase_at_ugbw_deg']:.4f} degrees")
    print(f"Phase Margin: {result['phase_margin_deg']:.4f} degrees")
