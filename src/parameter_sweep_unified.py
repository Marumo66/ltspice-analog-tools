"""Run matched AC and Operating Point sweeps from one LTspice netlist."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import re
import sys
from typing import Mapping, Optional, Sequence

from src.ac_metrics import calculate_loop_gain_metrics
from src.ac_parser import export_ac_raw_to_csv
from src.dc_parser import parse_dc_operating_point_log
from src.dc_raw_parser import calculate_quiescent_power, parse_dc_operating_point_raw
from src.ltspice_runner import LTspiceRunner
from src.netlist_controller import update_mosfet_geometry_text


_ANALYSIS_DIRECTIVES = {"op", "tran", "dc", "noise", "tf", "pz"}


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value.strip()).strip("_")


def _validate_devices_and_values(devices, parameter, values):
    if not devices or not values:
        raise ValueError("devices and values must both be non-empty")
    if not isinstance(parameter, str) or parameter.lower() not in {"w", "l"}:
        raise ValueError("parameter must be 'w' or 'l'")

    selected = []
    for device in devices:
        if not isinstance(device, str) or not re.fullmatch(
            r"M[A-Za-z0-9_]+", device, re.IGNORECASE
        ):
            raise ValueError(f"Invalid MOSFET instance name: {device!r}")
        normalized = device.upper()
        if normalized in selected:
            raise ValueError(f"Duplicate device in sweep: {normalized}")
        selected.append(normalized)

    normalized_values = []
    seen = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Sweep values must be non-empty SPICE value strings")
        normalized = value.strip()
        key = normalized.lower()
        if key in seen:
            raise ValueError(f"Duplicate sweep value: {value}")
        seen.add(key)
        normalized_values.append(normalized)

    return selected, parameter.lower(), normalized_values


def _validate_supplies(supply_voltages):
    if not isinstance(supply_voltages, Mapping) or not supply_voltages:
        raise ValueError("supply_voltages must be a non-empty mapping")
    for name, voltage in supply_voltages.items():
        if not isinstance(name, str) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_]*", name
        ):
            raise ValueError(f"Invalid voltage-source instance name: {name!r}")
        try:
            numeric_voltage = float(voltage)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid voltage for supply {name}") from exc
        if not math_is_finite(numeric_voltage):
            raise ValueError(f"Supply voltage for {name} must be finite")


def math_is_finite(value):
    # Kept local to avoid making the analysis module depend on NumPy.
    return value == value and value not in (float("inf"), float("-inf"))


def _active_directives(netlist_text):
    directives = []
    for line_number, line in enumerate(netlist_text.splitlines(), start=1):
        stripped = line.lstrip()
        if not stripped or stripped.startswith(("*", ";")):
            continue
        match = re.match(r"^\s*\.(\S+)", line)
        if match:
            directives.append((line_number, match.group(1).lower()))
    return directives


def _make_op_netlist_text(ac_netlist_text: str) -> str:
    """Convert exactly one active .ac directive to .op, preserving other text."""
    lines = ac_netlist_text.splitlines(keepends=True)
    active_ac = []
    competing = []
    end_index = None
    backanno_index = None

    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped or stripped.startswith(("*", ";")):
            continue
        match = re.match(r"^(\s*)\.(\S+)", line)
        if match is None:
            continue
        directive = match.group(2).lower()
        if directive == "ac":
            active_ac.append(index)
        elif directive in _ANALYSIS_DIRECTIVES:
            competing.append((index + 1, directive))
        elif directive == "backanno" and backanno_index is None:
            backanno_index = index
        elif directive == "end" and end_index is None:
            end_index = index

    if len(active_ac) != 1:
        raise ValueError(
            f"Unified sweep requires exactly one active .ac directive; found {len(active_ac)}"
        )
    if competing:
        detail = ", ".join(
            f".{name} at line {number}" for number, name in competing
        )
        raise ValueError(
            "Unified sweep source must contain AC analysis only; found " + detail
        )
    if end_index is None:
        raise ValueError("Netlist is missing an .end directive")

    ac_index = active_ac[0]
    line = lines[ac_index]
    indent_match = re.match(r"^\s*", line)
    indent = indent_match.group(0) if indent_match else ""
    lines[ac_index] = indent + "* " + line[len(indent):]

    insertion_index = backanno_index if backanno_index is not None else end_index
    newline = "\r\n" if "\r\n" in ac_netlist_text else "\n"
    lines.insert(insertion_index, indent + ".op" + newline)
    return "".join(lines)


def _write_new_netlist(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="") as output_file:
        output_file.write(content)


def run_unified_geometry_sweep(
    source_netlist,
    *,
    devices,
    parameter="w",
    values,
    supply_voltages,
    runner=None,
    output_csv=None,
    timeout=180.0,
    output_signal="V(out)",
    feedback_signal="V(nfb)",
):
    """Run paired AC and .op analyses for each geometry from a single source.

    The AC source netlist is the sole circuit template. For every sweep value,
    identical geometry edits are applied and two sibling netlists are emitted:
    one retaining .ac and one with .ac replaced by .op. This ensures both
    analyses use the same topology, bias sources, model libraries and device
    geometry.

    AC RAW currents are phasors and are not used for quiescent power. The .op
    RAW file supplies node voltages and DC source currents for explicit-supply
    power calculation.
    """
    source = Path(source_netlist).expanduser().resolve()
    if source.suffix.lower() != ".net":
        raise ValueError("source_netlist must be an LTspice .net file")
    if not source.is_file():
        raise FileNotFoundError(f"Source netlist not found: {source}")

    selected_devices, parameter, values = _validate_devices_and_values(
        devices, parameter, values
    )
    _validate_supplies(supply_voltages)

    source_text = source.read_text(encoding="utf-8")
    directives = _active_directives(source_text)
    active_ac_count = sum(name == "ac" for _, name in directives)
    if active_ac_count != 1:
        raise ValueError(
            f"Unified sweep requires exactly one active .ac directive; found {active_ac_count}"
        )
    _make_op_netlist_text(source_text)  # Validate that an .op sibling can be produced.

    if not isinstance(output_signal, str) or not output_signal.strip():
        raise ValueError("output_signal must be a non-empty trace name")
    if not isinstance(feedback_signal, str) or not feedback_signal.strip():
        raise ValueError("feedback_signal must be a non-empty trace name")

    device_slug = "_".join(selected_devices)
    base_stem = f"{source.stem}_{device_slug}_{parameter}"
    if output_csv is None:
        output_csv = source.with_name(f"{base_stem}_unified_sweep.csv")
    output_csv = Path(output_csv).expanduser().resolve()
    if output_csv.parent != source.parent:
        raise ValueError("output_csv must be in the same directory as the source netlist")
    if output_csv.exists():
        raise FileExistsError(f"Summary CSV already exists: {output_csv}")

    variants = []
    for value in values:
        value_slug = _slug(value)
        stem = f"{base_stem}_{value_slug}"
        ac_net = source.with_name(f"{stem}_ac.net")
        op_net = source.with_name(f"{stem}_op.net")
        ac_raw = ac_net.with_suffix(".raw")
        ac_log = ac_net.with_suffix(".log")
        op_raw = op_net.with_suffix(".raw")
        op_log = op_net.with_suffix(".log")
        ac_csv = ac_net.with_suffix(".csv")
        outputs = [ac_net, op_net, ac_raw, ac_log, op_raw, op_log, ac_csv]
        existing = [path.name for path in outputs if path.exists()]
        if existing:
            raise FileExistsError(
                "Unified sweep output(s) already exist; refusing to overwrite: "
                + ", ".join(existing)
            )
        variants.append({
            "value": value,
            "ac_net": ac_net,
            "op_net": op_net,
            "ac_raw": ac_raw,
            "ac_log": ac_log,
            "op_raw": op_raw,
            "op_log": op_log,
            "ac_csv": ac_csv,
        })

    active_runner = runner or LTspiceRunner(timeout=timeout)
    rows = []
    run_results = []

    for item in variants:
        value = item["value"]
        updates = {device: {parameter: value} for device in selected_devices}
        modified_text = update_mosfet_geometry_text(source_text, updates)
        op_text = _make_op_netlist_text(modified_text)

        _write_new_netlist(item["ac_net"], modified_text)
        _write_new_netlist(item["op_net"], op_text)

        # Two fresh simulations are required for each parameter point.
        ac_simulation = active_runner.run_netlist(item["ac_net"])
        ac_info = export_ac_raw_to_csv(ac_simulation.raw_path, item["ac_csv"])
        if output_signal not in ac_info["variables"]:
            raise ValueError(
                f"Output trace {output_signal!r} is missing from {ac_simulation.raw_path.name}"
            )
        if feedback_signal not in ac_info["variables"]:
            raise ValueError(
                f"Feedback trace {feedback_signal!r} is missing from {ac_simulation.raw_path.name}"
            )
        ac_metrics = calculate_loop_gain_metrics(
            item["ac_csv"],
            output_signal=output_signal,
            feedback_signal=feedback_signal,
        )
        ac_log_data = parse_dc_operating_point_log(ac_simulation.log_path)

        op_simulation = active_runner.run_netlist(item["op_net"])
        op_raw_data = parse_dc_operating_point_raw(op_simulation.raw_path)
        op_log_data = parse_dc_operating_point_log(op_simulation.log_path)
        power_data = calculate_quiescent_power(
            op_simulation.raw_path,
            dict(supply_voltages),
        )

        row = {
            "sweep_parameter": parameter.upper(),
            "sweep_value": value,
            "devices": ";".join(selected_devices),
            "ac_netlist": item["ac_net"].name,
            "op_netlist": item["op_net"].name,
            "ac_raw_points": ac_info["points"],
            "ac_frequency_start_Hz": ac_info["frequency_start"],
            "ac_frequency_end_Hz": ac_info["frequency_end"],
            "low_frequency_gain_dB": ac_metrics["low_frequency_gain_dB"],
            "low_frequency_phase_deg": ac_metrics["low_frequency_phase_deg"],
            "ugbw_Hz": ac_metrics["ugbw_Hz"],
            "phase_at_ugbw_deg": ac_metrics["phase_at_ugbw_deg"],
            "phase_margin_deg": ac_metrics["phase_margin_deg"],
            "op_raw_variable_count": op_raw_data["variable_count"],
            "ac_convergence_method": ac_log_data["metadata"]["convergence_method"] or "",
            "ac_warning_count": len(ac_log_data["metadata"]["warnings"]),
            "op_convergence_method": op_log_data["metadata"]["convergence_method"] or "",
            "op_warning_count": len(op_log_data["metadata"]["warnings"]),
        }

        for node in ("V(out)", "V(n1)", "V(n2)", "V(tail)"):
            if node in op_raw_data["node_voltages"]:
                row[f"{node}_V"] = op_raw_data["node_voltages"][node]

        for supply_name, details in power_data["supplies"].items():
            row[f"I({supply_name})_A"] = details["current_A"]
            row[f"P({supply_name})_delivered_uW"] = details["power_delivered_W"] * 1e6
        row["selected_supplies_power_delivered_uW"] = (
            power_data["total_power_delivered_uW"]
        )

        op_devices = {
            name.upper(): metrics
            for name, metrics in op_log_data["devices"].items()
        }
        for device in selected_devices:
            if device not in op_devices:
                raise ValueError(
                    f"{device} is missing from .op log for sweep value {value}"
                )
            metrics = op_devices[device]
            for key in ("Id", "Gm", "Vgs", "Vds", "Vth", "Vdsat"):
                if key in metrics:
                    row[f"{device}_{key}"] = metrics[key]

        rows.append(row)
        run_results.append({
            "value": value,
            "ac_netlist_path": item["ac_net"],
            "op_netlist_path": item["op_net"],
            "ac_raw_path": ac_simulation.raw_path,
            "ac_log_path": ac_simulation.log_path,
            "op_raw_path": op_simulation.raw_path,
            "op_log_path": op_simulation.log_path,
            "ac_csv_path": item["ac_csv"],
            "metrics": row,
        })

    preferred_columns = [
        "sweep_parameter", "sweep_value", "devices", "ac_netlist", "op_netlist",
        "ac_raw_points", "ac_frequency_start_Hz", "ac_frequency_end_Hz",
        "low_frequency_gain_dB", "low_frequency_phase_deg", "ugbw_Hz",
        "phase_at_ugbw_deg", "phase_margin_deg", "op_raw_variable_count",
        "V(out)_V", "V(n1)_V", "V(n2)_V", "V(tail)_V",
    ]
    for supply_name in supply_voltages:
        preferred_columns.extend([
            f"I({supply_name})_A",
            f"P({supply_name})_delivered_uW",
        ])
    preferred_columns.extend([
        "selected_supplies_power_delivered_uW",
        "ac_convergence_method", "ac_warning_count",
        "op_convergence_method", "op_warning_count",
    ])
    for device in selected_devices:
        preferred_columns.extend(
            f"{device}_{key}" for key in ("Id", "Gm", "Vgs", "Vds", "Vth", "Vdsat")
        )

    columns = [key for key in preferred_columns if any(key in row for row in rows)]
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    return {
        "run_count": len(run_results),
        "simulation_count": 2 * len(run_results),
        "results": run_results,
        "csv_path": output_csv,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run matched AC and Operating Point sweeps from one LTspice netlist."
    )
    parser.add_argument("source", help="Original LTspice AC .net file")
    parser.add_argument("--device", action="append", required=True,
                        help="MOSFET instance; repeat for symmetric devices")
    parser.add_argument("--parameter", choices=("w", "l"), default="w")
    parser.add_argument("--values", nargs="+", required=True,
                        help="SPICE values, e.g. 10u 12u 16u")
    parser.add_argument("--supply", action="append", required=True,
                        help="Supply source and DC voltage, e.g. VDD=1.5")
    parser.add_argument("--output-signal", default="V(out)")
    parser.add_argument("--feedback-signal", default="V(nfb)")
    parser.add_argument("--output-csv", help="Summary CSV path (defaults beside source netlist)")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args(argv)

    supplies = {}
    try:
        for item in args.supply:
            match = re.fullmatch(
                r"([A-Za-z][A-Za-z0-9_]*)=([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)",
                item,
            )
            if match is None:
                raise ValueError(f"Invalid --supply {item!r}; expected VDD=1.5")
            name, value = match.groups()
            if name in supplies:
                raise ValueError(f"Duplicate supply source: {name}")
            supplies[name] = float(value)

        result = run_unified_geometry_sweep(
            args.source,
            devices=args.device,
            parameter=args.parameter,
            values=args.values,
            supply_voltages=supplies,
            output_signal=args.output_signal,
            feedback_signal=args.feedback_signal,
            output_csv=args.output_csv,
            timeout=args.timeout,
        )
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"Unified AC/DC sweep failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"Completed {result['run_count']} parameter point(s) "
        f"using {result['simulation_count']} LTspice simulation(s)."
    )
    print(f"Summary CSV: {result['csv_path']}")
    for item in result["results"]:
        metrics = item["metrics"]
        print(
            f"{item['value']}: Gain={metrics['low_frequency_gain_dB']:.3f} dB, "
            f"UGBW={metrics['ugbw_Hz'] / 1e6:.4f} MHz, "
            f"PM={metrics['phase_margin_deg']:.3f} deg, "
            f"selected-supply power="
            f"{metrics['selected_supplies_power_delivered_uW']:.4f} uW"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
