from pathlib import Path
import csv
import re
import struct


def export_ac_raw_to_csv(raw_path, csv_path):
    """Export an LTspice binary AC RAW file to CSV.

    This handles the complex, forward AC RAW layout tested with
    LTspice 26.0.2 on macOS.
    """
    raw_path = Path(raw_path)
    csv_path = Path(csv_path)
    data = raw_path.read_bytes()

    marker = "Binary:".encode("utf-16-le")
    marker_pos = data.find(marker)
    if marker_pos < 0:
        raise ValueError("Binary section not found")

    try:
        header = data[:marker_pos].decode("utf-16-le")
    except UnicodeDecodeError as exc:
        raise ValueError("RAW header is not UTF-16 LE") from exc

    plotname = re.search(r"(?m)^Plotname:\s*(.+)$", header)
    flags = re.search(r"(?m)^Flags:\s*(.+)$", header)
    if plotname is None or plotname.group(1).strip().lower() != "ac analysis":
        raise ValueError("This is not an AC Analysis RAW file")
    if flags is None:
        raise ValueError("RAW flags not found")

    flag_set = set(flags.group(1).lower().split())
    if not {"complex", "forward"}.issubset(flag_set):
        raise ValueError("Only complex forward AC RAW files are supported")

    variables_match = re.search(r"(?m)^No\. Variables:[ \t]*(\d+)\r?$", header)
    points_match = re.search(r"(?m)^No\. Points:[ \t]*(\d+)\r?$", header)
    if variables_match is None or points_match is None:
        raise ValueError("RAW variable or point count not found")

    variable_count = int(variables_match.group(1))
    point_count = int(points_match.group(1))
    if variable_count < 1 or point_count < 1:
        raise ValueError("RAW file must contain at least one variable and point")

    section_match = re.search(r"(?m)^Variables:\s*$", header)
    if section_match is None:
        raise ValueError("Variables section not found")

    entries = re.findall(
        r"(?m)^\s*(\d+)\s+(\S+)\s+\S+\s*$",
        header[section_match.end():],
    )
    indices = [int(index) for index, _ in entries]
    names = [name for _, name in entries]
    if indices != list(range(variable_count)):
        raise ValueError("Variable list does not match No. Variables")

    binary_start = marker_pos + len(marker)
    for separator in (b"\r\x00\n\x00", b"\n\x00", b"\r\x00"):
        if data.startswith(separator, binary_start):
            binary_start += len(separator)
            break

    # LTspice AC points store each variable as a pair of doubles.
    record_format = "<" + "d" * (2 * variable_count)
    record_size = struct.calcsize(record_format)
    expected_size = point_count * record_size
    actual_size = len(data) - binary_start
    if actual_size != expected_size:
        raise ValueError(
            f"Binary size mismatch: expected {expected_size}, got {actual_size}"
        )

    csv_columns = [names[0]]
    for name in names[1:]:
        csv_columns.extend([f"{name}.real", f"{name}.imag"])

    frequencies = []
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(csv_columns)

        for point_index in range(point_count):
            offset = binary_start + point_index * record_size
            values = struct.unpack_from(record_format, data, offset)

            frequency = values[0]
            frequency_imag = values[1]
            if frequency <= 0:
                raise ValueError("AC frequency must be positive")
            if abs(frequency_imag) > 1e-12:
                raise ValueError("Frequency axis has a non-zero imaginary part")

            frequencies.append(frequency)
            row = [frequency]
            for variable_index in range(1, variable_count):
                real_index = 2 * variable_index
                row.extend([values[real_index], values[real_index + 1]])
            writer.writerow(row)

    if any(a >= b for a, b in zip(frequencies, frequencies[1:])):
        raise ValueError("Frequency axis must be strictly increasing")

    return {
        "points": point_count,
        "variables": names,
        "frequency_start": frequencies[0],
        "frequency_end": frequencies[-1],
        "csv_path": csv_path,
    }
