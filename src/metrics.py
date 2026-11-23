from pathlib import Path
import csv
import math


def _read_signal(csv_path, signal):
    times = []
    values = []

    with Path(csv_path).open(newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        columns = reader.fieldnames or []
        if "time" not in columns:
            raise ValueError("CSV must contain a 'time' column")
        if signal not in columns:
            raise ValueError(f"Signal not found: {signal}")

        for row in reader:
            time_value = float(row["time"])
            signal_value = float(row[signal])
            if not math.isfinite(time_value) or not math.isfinite(signal_value):
                raise ValueError("CSV contains a non-finite value")
            times.append(time_value)
            values.append(signal_value)

    if len(times) < 2:
        raise ValueError("At least two samples are required")
    if any(a >= b for a, b in zip(times, times[1:])):
        raise ValueError("Time axis must be strictly increasing")

    return times, values


def calculate_slew_rate(csv_path, signal="V(out)"):
    """Return the largest adjacent-sample slopes in V/us."""
    times, voltages = _read_signal(csv_path, signal)
    slopes = [
        (voltages[index + 1] - voltages[index])
        / (times[index + 1] - times[index])
        for index in range(len(times) - 1)
    ]

    rising = max((slope for slope in slopes if slope > 0), default=0.0)
    falling = min((slope for slope in slopes if slope < 0), default=0.0)

    return {
        "signal": signal,
        "max_rising_V_per_us": rising / 1e6,
        "max_falling_V_per_us": abs(falling) / 1e6,
        "max_absolute_V_per_us": max(abs(slope) for slope in slopes) / 1e6,
    }


def calculate_average_slew_rate(
    csv_path,
    v_low,
    v_high,
    direction="rising",
    signal="V(out)",
):
    """Calculate average slew rate between the 20% and 80% levels.

    v_low and v_high are the low and high endpoints of the output swing.
    Crossing times are linearly interpolated between adjacent samples.
    """
    if not math.isfinite(v_low) or not math.isfinite(v_high) or v_high <= v_low:
        raise ValueError("v_high must be finite and greater than finite v_low")
    if direction not in ("rising", "falling"):
        raise ValueError("direction must be 'rising' or 'falling'")

    times, voltages = _read_signal(csv_path, signal)
    swing = v_high - v_low
    low_threshold = v_low + 0.2 * swing
    high_threshold = v_low + 0.8 * swing

    if direction == "rising":
        first_level, second_level = low_threshold, high_threshold
    else:
        first_level, second_level = high_threshold, low_threshold

    def find_crossing(level, after_time=None):
        for index in range(len(times) - 1):
            t0, t1 = times[index], times[index + 1]
            y0, y1 = voltages[index], voltages[index + 1]

            if direction == "rising":
                crossed = y0 <= level <= y1 and y1 > y0
            else:
                crossed = y0 >= level >= y1 and y1 < y0

            if not crossed:
                continue

            crossing_time = t0 + (level - y0) * (t1 - t0) / (y1 - y0)
            if after_time is None or crossing_time > after_time:
                return crossing_time

        raise ValueError(f"Could not find {direction} crossing at {level} V")

    first_time = find_crossing(first_level)
    second_time = find_crossing(second_level, after_time=first_time)
    delta_time = second_time - first_time
    slew_rate = (0.6 * swing) / delta_time / 1e6

    return {
        "signal": signal,
        "direction": direction,
        "first_crossing_time_s": first_time,
        "second_crossing_time_s": second_time,
        "voltage_interval_V": 0.6 * swing,
        "average_slew_rate_V_per_us": slew_rate,
    }
