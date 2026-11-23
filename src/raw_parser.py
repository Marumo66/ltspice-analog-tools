from pathlib import Path
import csv
import re
import struct


def _header_value(header, pattern, field_name):
    match = re.search(pattern, header)
    if match is None:
        raise ValueError(f"Missing {field_name} in RAW header")
    return int(match.group(1))


def export_raw_to_csv(raw_path, csv_path):
    """Export an LTspice real/forward binary RAW file to CSV.

    This currently handles the real-valued binary format used by the
    transient RAW files tested with LTspice 26.0.2. Complex RAW files
    and other formats are not handled yet.
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

    if re.search(r"Flags:\s*real\s+forward\b", header) is None:
        raise ValueError("Only real forward RAW files are supported")

    variables = _header_value(
        header, r"No\. Variables:\s*(\d+)", "No. Variables"
    )
    points = _header_value(
        header, r"No\. Points:\s*(\d+)", "No. Points"
    )
    if variables < 1 or points < 1:
        raise ValueError("RAW file must contain at least one variable and point")

    variables_match = re.search(r"(?m)^Variables:\s*$", header)
    if variables_match is None:
        raise ValueError("Variables section not found")
    variable_block = header[variables_match.end():]

    entries = re.findall(
        r"(?m)^\s*(\d+)\s+(\S+)\s+\S+\s*$",
        variable_block,
    )
    indices = [int(index) for index, _ in entries]
    names = [name for _, name in entries]

    if indices != list(range(variables)):
        raise ValueError("Variable list does not match No. Variables")

    binary_start = marker_pos + len(marker)
    for separator in (b"\r\x00\n\x00", b"\n\x00", b"\r\x00"):
        if data.startswith(separator, binary_start):
            binary_start += len(separator)
            break

    # The first variable (time) is a double; remaining values are floats.
    record_format = "<d" + "f" * (variables - 1)
    record_size = struct.calcsize(record_format)
    expected_size = points * record_size
    actual_size = len(data) - binary_start
    if actual_size != expected_size:
        raise ValueError(
            f"Binary data size mismatch: expected {expected_size}, got {actual_size}"
        )

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(names)
        for point_index in range(points):
            offset = binary_start + point_index * record_size
            writer.writerow(struct.unpack_from(record_format, data, offset))

    return {
        "points": points,
        "variables": names,
        "csv_path": csv_path,
    }
