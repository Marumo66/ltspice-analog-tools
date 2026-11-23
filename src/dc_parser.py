"""Parser for LTspice semiconductor device operating-point log sections."""

from pathlib import Path
import csv
import math
import re


_NAME_RE = re.compile(r"^\s*Name:\s*(.*?)\s*$", re.IGNORECASE)
_MODEL_RE = re.compile(r"^\s*Model:\s*(.*?)\s*$", re.IGNORECASE)
_ROW_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*:?\s+(.*?)\s*$")


def _parse_number_tokens(text, field_name):
    tokens = text.split()
    try:
        values = [float(token) for token in tokens]
    except ValueError as exc:
        raise ValueError(f"Invalid numeric value in operating-point row {field_name}") from exc

    if not all(math.isfinite(value) for value in values):
        raise ValueError(f"Non-finite numeric value in operating-point row {field_name}")
    return values


def _parse_metadata(lines):
    metadata = {
        "ltspice_version": None,
        "solver": None,
        "temperature_C": None,
        "nominal_temperature_C": None,
        "options": [],
        "warnings": [],
        "direct_newton_succeeded": None,
        "gmin_stepping_succeeded": None,
        "converged": None,
        "convergence_method": None,
        "elapsed_time_s": None,
    }

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("LTspice "):
            metadata["ltspice_version"] = stripped
        elif stripped.startswith("Options:"):
            metadata["options"] = stripped.partition(":")[2].strip().split()
        elif stripped.startswith("solver ="):
            metadata["solver"] = stripped.partition("=")[2].strip()
        elif stripped.startswith("temp ="):
            metadata["temperature_C"] = float(stripped.partition("=")[2].strip())
        elif stripped.startswith("tnom ="):
            metadata["nominal_temperature_C"] = float(stripped.partition("=")[2].strip())
        elif stripped.startswith("Warning:"):
            metadata["warnings"].append(stripped)
        elif "Direct Newton iteration succeeded" in stripped:
            metadata["direct_newton_succeeded"] = True
        elif "Direct Newton iteration failed" in stripped:
            metadata["direct_newton_succeeded"] = False
        elif "Gmin stepping succeeded" in stripped:
            metadata["gmin_stepping_succeeded"] = True
        elif "Gmin stepping failed" in stripped:
            metadata["gmin_stepping_succeeded"] = False
        elif stripped.startswith("Total elapsed time:"):
            match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s+seconds", stripped)
            if match:
                metadata["elapsed_time_s"] = float(match.group(1))

    if metadata["direct_newton_succeeded"] is True:
        metadata["converged"] = True
        metadata["convergence_method"] = "direct_newton"
    elif metadata["gmin_stepping_succeeded"] is True:
        metadata["converged"] = True
        metadata["convergence_method"] = "gmin_stepping"
    elif metadata["gmin_stepping_succeeded"] is False:
        metadata["converged"] = False
        metadata["convergence_method"] = "gmin_stepping_failed"
    elif metadata["direct_newton_succeeded"] is False:
        metadata["converged"] = False
        metadata["convergence_method"] = "direct_newton_failed"

    return metadata


def parse_dc_operating_point_text(log_text):
    """Parse MOSFET operating points and run metadata from LTspice log text.

    Numeric device parameters are retained in LTspice SI units and signed
    exactly as LTspice reports them. For example, PMOS Id/Vgs/Vds remain
    negative where the simulator reports negative values.
    """
    if not isinstance(log_text, str):
        raise TypeError("log_text must be a string")

    lines = log_text.splitlines()
    heading_index = next(
        (index for index, line in enumerate(lines)
         if "Semiconductor Device Operating Points:" in line),
        None,
    )
    if heading_index is None:
        raise ValueError("Semiconductor Device Operating Points section not found")

    devices = {}
    index = heading_index + 1
    while index < len(lines):
        name_match = _NAME_RE.match(lines[index])
        if name_match is None:
            index += 1
            continue

        names = name_match.group(1).split()
        if not names:
            raise ValueError("Operating-point Name row is empty")

        index += 1
        while index < len(lines) and not lines[index].strip():
            index += 1
        if index >= len(lines):
            raise ValueError(f"Missing Model row for devices: {', '.join(names)}")

        model_match = _MODEL_RE.match(lines[index])
        if model_match is None:
            raise ValueError(f"Missing Model row for devices: {', '.join(names)}")
        models = model_match.group(1).split()
        if len(models) != len(names):
            raise ValueError("Model count does not match device Name count")

        for name, model in zip(names, models):
            if name in devices:
                raise ValueError(f"Duplicate device name in operating-point log: {name}")
            devices[name] = {"model": model}

        index += 1
        while index < len(lines):
            if _NAME_RE.match(lines[index]):
                break
            if not lines[index].strip():
                # Blank lines separate the column groups in LTspice output.
                index += 1
                break

            row_match = _ROW_RE.match(lines[index])
            if row_match is not None:
                field_name, raw_values = row_match.groups()
                try:
                    values = _parse_number_tokens(raw_values, field_name)
                except ValueError:
                    # Ignore non-data lines rather than mistaking them for metrics.
                    index += 1
                    continue

                if len(values) != len(names):
                    raise ValueError(
                        f"Operating-point row {field_name} has {len(values)} values; "
                        f"expected {len(names)}"
                    )
                for name, value in zip(names, values):
                    devices[name][field_name] = value
            index += 1

    if not devices:
        raise ValueError("No semiconductor device operating points were parsed")

    missing_model = [name for name, data in devices.items() if "model" not in data]
    if missing_model:
        raise ValueError(f"Missing model for devices: {', '.join(missing_model)}")

    metadata = _parse_metadata(lines[:heading_index])
    return {
        "device_count": len(devices),
        "devices": devices,
        "metadata": metadata,
    }


def parse_dc_operating_point_log(log_path):
    """Read and parse an LTspice operating-point log file."""
    log_path = Path(log_path)
    try:
        log_text = log_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("LTspice log must be UTF-8 or ASCII text") from exc
    result = parse_dc_operating_point_text(log_text)
    result["log_path"] = log_path
    return result


def export_dc_operating_points_to_csv(log_path, csv_path):
    """Export parsed device operating points to a tidy one-row-per-device CSV."""
    result = parse_dc_operating_point_log(log_path)
    csv_path = Path(csv_path)

    parameter_names = []
    for data in result["devices"].values():
        for name in data:
            if name != "model" and name not in parameter_names:
                parameter_names.append(name)

    fieldnames = ["device", "model", *parameter_names]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for device_name, data in result["devices"].items():
            row = {"device": device_name, **data}
            writer.writerow(row)

    return {
        "device_count": result["device_count"],
        "devices": list(result["devices"]),
        "csv_path": csv_path,
    }
