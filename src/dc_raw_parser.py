"""Parser for LTspice real-valued Operating Point RAW files."""

from pathlib import Path
import csv
import math
import re
import struct


def _header_int(header, pattern, label):
    match = re.search(pattern, header, re.IGNORECASE)
    if match is None:
        raise ValueError(f"Missing {label} in RAW header")
    return int(match.group(1))


def parse_dc_operating_point_raw(raw_path):
    """Parse an LTspice Operating Point RAW file.

    The supported layout is the real-valued, one-point Operating Point
    format verified with LTspice 26.0.2 on macOS: the first variable is
    stored as a little-endian double and remaining variables as floats.
    """
    raw_path = Path(raw_path)
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
    if plotname is None or plotname.group(1).strip().lower() != "operating point":
        raise ValueError("This is not an Operating Point RAW file")
    if flags is None:
        raise ValueError("RAW flags not found")

    flag_set = set(flags.group(1).lower().split())
    if "real" not in flag_set or "complex" in flag_set:
        raise ValueError("Only real-valued Operating Point RAW files are supported")

    variable_count = _header_int(
        header, r"(?m)^No\.\s*Variables:\s*(\d+)\s*$", "No. Variables"
    )
    point_count = _header_int(
        header, r"(?m)^No\.\s*Points:\s*(\d+)\s*$", "No. Points"
    )
    if variable_count < 1:
        raise ValueError("RAW file must contain at least one variable")
    if point_count != 1:
        raise ValueError(
            f"Operating Point RAW must contain exactly one point; found {point_count}"
        )

    variables_match = re.search(r"(?m)^Variables:\s*$", header)
    if variables_match is None:
        raise ValueError("Variables section not found")

    entries = re.findall(
        r"(?m)^\s*(\d+)\s+(\S+)\s+(\S+)\s*$",
        header[variables_match.end():],
    )
    indices = [int(index) for index, _, _ in entries]
    names = [name for _, name, _ in entries]
    types = [variable_type for _, _, variable_type in entries]

    if indices != list(range(variable_count)):
        raise ValueError("Variable list does not match No. Variables")

    binary_start = marker_pos + len(marker)
    for separator in (b"\r\x00\n\x00", b"\n\x00", b"\r\x00"):
        if data.startswith(separator, binary_start):
            binary_start += len(separator)
            break

    # LTspice 26.0.2 macOS OP RAW fixture: first variable is a double,
    # remaining variables are 32-bit floats.
    record_format = "<d" + "f" * (variable_count - 1)
    record_size = struct.calcsize(record_format)
    expected_size = point_count * record_size
    actual_size = len(data) - binary_start
    if actual_size != expected_size:
        raise ValueError(
            f"Binary data size mismatch: expected {expected_size}, got {actual_size}"
        )

    values = struct.unpack_from(record_format, data, binary_start)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("RAW data contains a non-finite value")

    variables = {
        name: {"type": variable_type, "value": value}
        for name, variable_type, value in zip(names, types, values)
    }
    node_voltages = {
        name: item["value"]
        for name, item in variables.items()
        if name.startswith("V(") and name.endswith(")")
    }
    component_currents = {
        name: item["value"]
        for name, item in variables.items()
        if name.startswith("I(") and name.endswith(")")
    }
    device_terminal_currents = {
        name: item["value"]
        for name, item in variables.items()
        if re.match(r"^I[bgsd]\(.+\)$", name, re.IGNORECASE)
    }

    return {
        "plotname": plotname.group(1).strip(),
        "flags": sorted(flag_set),
        "point_count": point_count,
        "variable_count": variable_count,
        "variables": variables,
        "node_voltages": node_voltages,
        "component_currents": component_currents,
        "device_terminal_currents": device_terminal_currents,
        "raw_path": raw_path,
    }


def export_dc_operating_point_raw_to_csv(raw_path, csv_path):
    """Export every Operating Point RAW variable as one tidy CSV row."""
    result = parse_dc_operating_point_raw(raw_path)
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file, lineterminator="\n")
        writer.writerow(["variable", "type", "value", "unit"])
        for name, item in result["variables"].items():
            unit = {
                "voltage": "V",
                "device_current": "A",
            }.get(item["type"], "")
            writer.writerow([name, item["type"], item["value"], unit])

    return {
        "variable_count": result["variable_count"],
        "csv_path": csv_path,
    }


def calculate_quiescent_power(raw_path, supply_voltages):
    """Calculate net power delivered by explicitly selected DC supply sources.

    supply_voltages maps voltage-source instance names to their signed
    positive-to-negative terminal voltage, e.g. {"VDD": 1.5}. LTspice
    voltage-source current is positive into the source's positive terminal,
    so delivered power is -(V_source * I_source).
    """
    if not isinstance(supply_voltages, dict) or not supply_voltages:
        raise ValueError("supply_voltages must be a non-empty {source: voltage} mapping")

    result = parse_dc_operating_point_raw(raw_path)
    currents = result["component_currents"]
    details = {}
    total_delivered = 0.0

    for source_name, voltage in supply_voltages.items():
        if not isinstance(source_name, str) or not source_name.strip():
            raise ValueError("Supply source names must be non-empty strings")
        try:
            voltage = float(voltage)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid supply voltage for {source_name}") from exc
        if not math.isfinite(voltage):
            raise ValueError(f"Supply voltage for {source_name} must be finite")

        current_name = f"I({source_name})"
        if current_name not in currents:
            raise ValueError(
                f"Current {current_name} not found in Operating Point RAW variables"
            )

        current = currents[current_name]
        absorbed = voltage * current
        delivered = -absorbed
        details[source_name] = {
            "voltage_V": voltage,
            "current_A": current,
            "power_absorbed_W": absorbed,
            "power_delivered_W": delivered,
        }
        total_delivered += delivered

    return {
        "supplies": details,
        "total_power_delivered_W": total_delivered,
        "total_power_delivered_uW": total_delivered * 1e6,
    }
