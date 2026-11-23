"""Run DC geometry sweeps by editing netlists, invoking LTspice and collecting metrics."""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
import re
import sys
from typing import Mapping, Optional, Sequence

from src.dc_parser import parse_dc_operating_point_log
from src.dc_raw_parser import calculate_quiescent_power, parse_dc_operating_point_raw
from src.ltspice_runner import LTspiceRunner
from src.netlist_controller import write_mosfet_geometry_variant


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value.strip()).strip("_")


def _validate_sweep(devices, parameter, values):
    if not devices or not values:
        raise ValueError("devices and values must both be non-empty")
    if parameter.lower() not in {"w", "l"}:
        raise ValueError("parameter must be 'w' or 'l'")

    normalized_devices = []
    for device in devices:
        if not isinstance(device, str) or not re.fullmatch(
            r"M[A-Za-z0-9_]+", device, re.IGNORECASE
        ):
            raise ValueError(f"Invalid MOSFET instance name: {device!r}")
        key = device.upper()
        if key in normalized_devices:
            raise ValueError(f"Duplicate device in sweep: {key}")
        normalized_devices.append(key)

    normalized_values = []
    seen = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Sweep values must be non-empty SPICE value strings")
        key = value.strip().lower()
        if key in seen:
            raise ValueError(f"Duplicate sweep value: {value}")
        seen.add(key)
        normalized_values.append(value.strip())

    return normalized_devices, parameter.lower(), normalized_values


def _parse_supply_specs(items):
    supplies = {}
    for item in items:
        match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_]*)=([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)", item)
        if match is None:
            raise ValueError(
                f"Invalid --supply value {item!r}; expected VDD=1.5"
            )
        name, value = match.groups()
        if name in supplies:
            raise ValueError(f"Duplicate supply source: {name}")
        voltage = float(value)
        if not math.isfinite(voltage):
            raise ValueError(f"Supply voltage must be finite: {item}")
        supplies[name] = voltage
    if not supplies:
        raise ValueError("At least one --supply NAME=VOLTAGE is required")
    return supplies


def run_dc_geometry_sweep(
    source_netlist,
    *,
    devices,
    parameter="w",
    values,
    supply_voltages,
    runner=None,
    output_csv=None,
    timeout=180.0,
):
    """Run a one-dimensional MOSFET geometry sweep and write a summary CSV.

    Every sweep point gets its own sibling .net/.raw/.log files. The source
    netlist is never edited. One shared W or L value is applied to each
    selected MOSFET to preserve symmetry (for example, M1 and M2 together).
    """
    source = Path(source_netlist).expanduser().resolve()
    if source.suffix.lower() != ".net":
        raise ValueError("source_netlist must be an LTspice .net file")
    if not source.is_file():
        raise FileNotFoundError(f"Source netlist not found: {source}")

    selected_devices, parameter, values = _validate_sweep(
        devices, parameter, values
    )
    if not isinstance(supply_voltages, Mapping) or not supply_voltages:
        raise ValueError("supply_voltages must be a non-empty mapping")

    if output_csv is None:
        output_csv = source.with_name(
            f"{source.stem}_{'_'.join(selected_devices)}_{parameter}_sweep.csv"
        )
    output_csv = Path(output_csv).expanduser().resolve()
    if output_csv.parent != source.parent:
        raise ValueError("output_csv must be in the same directory as the source netlist")
    if output_csv.exists():
        raise FileExistsError(f"Sweep CSV already exists: {output_csv}")

    active_runner = runner or LTspiceRunner(timeout=timeout)
    rows = []
    run_results = []

    for value in values:
        value_slug = _slug(value)
        device_slug = "_".join(selected_devices)
        variant_name = f"{source.stem}_{device_slug}_{parameter}_{value_slug}.net"
        variant_path = source.with_name(variant_name)

        updates = {
            device: {parameter: value}
            for device in selected_devices
        }
        write_mosfet_geometry_variant(
            source,
            variant_path,
            updates,
            overwrite=False,
        )

        simulation = active_runner.run_netlist(variant_path)
        raw_data = parse_dc_operating_point_raw(simulation.raw_path)
        log_data = parse_dc_operating_point_log(simulation.log_path)
        power_data = calculate_quiescent_power(
            simulation.raw_path,
            dict(supply_voltages),
        )

        nodes = raw_data["node_voltages"]
        currents = raw_data["component_currents"]
        row = {
            "sweep_parameter": parameter.upper(),
            "sweep_value": value,
            "devices": ";".join(selected_devices),
            "netlist": variant_path.name,
            "convergence_method": log_data["metadata"]["convergence_method"] or "",
            "warning_count": len(log_data["metadata"]["warnings"]),
            "raw_variable_count": raw_data["variable_count"],
        }

        for node_name in ("V(out)", "V(n1)", "V(n2)", "V(tail)"):
            if node_name in nodes:
                row[f"{node_name}_V"] = nodes[node_name]

        for source_name, details in power_data["supplies"].items():
            row[f"I({source_name})_A"] = details["current_A"]
            row[f"P({source_name})_delivered_uW"] = details["power_delivered_W"] * 1e6

        row["selected_supplies_power_delivered_uW"] = (
            power_data["total_power_delivered_uW"]
        )

        for device in selected_devices:
            if device not in log_data["devices"]:
                raise ValueError(
                    f"{device} is not present in the MOSFET operating-point log "
                    f"for sweep value {value}"
                )
            metrics = log_data["devices"][device]
            for key in ("Id", "Gm", "Vgs", "Vds", "Vth", "Vdsat"):
                if key in metrics:
                    row[f"{device}_{key}"] = metrics[key]

        rows.append(row)
        run_results.append({
            "value": value,
            "netlist_path": variant_path,
            "raw_path": simulation.raw_path,
            "log_path": simulation.log_path,
            "metrics": row,
        })

    # Keep a stable, useful column ordering; append only variables that exist
    # in the selected circuit/log.
    preferred_columns = [
        "sweep_parameter", "sweep_value", "devices", "netlist",
        "convergence_method", "warning_count", "raw_variable_count",
        "V(out)_V", "V(n1)_V", "V(n2)_V", "V(tail)_V",
    ]
    for source_name in supply_voltages:
        preferred_columns.extend([
            f"I({source_name})_A",
            f"P({source_name})_delivered_uW",
        ])
    preferred_columns.append("selected_supplies_power_delivered_uW")
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
        "results": run_results,
        "csv_path": output_csv,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a DC sweep by changing selected MOSFET W/L values."
    )
    parser.add_argument("source", help="Original LTspice .net file")
    parser.add_argument("--device", action="append", required=True, help="MOSFET instance; repeat for symmetric devices")
    parser.add_argument("--parameter", choices=("w", "l"), default="w")
    parser.add_argument("--values", nargs="+", required=True, help="SPICE values, e.g. 8u 10u 12u 14u 16u")
    parser.add_argument("--supply", action="append", required=True, help="Supply source and DC voltage, e.g. VDD=1.5")
    parser.add_argument("--output-csv", help="Summary CSV path (defaults beside source netlist)")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args(argv)

    try:
        supplies = _parse_supply_specs(args.supply)
        result = run_dc_geometry_sweep(
            args.source,
            devices=args.device,
            parameter=args.parameter,
            values=args.values,
            supply_voltages=supplies,
            output_csv=args.output_csv,
            timeout=args.timeout,
        )
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"DC sweep failed: {exc}", file=sys.stderr)
        return 1

    print(f"Completed {result['run_count']} simulation(s).")
    print(f"Summary CSV: {result['csv_path']}")
    for item in result["results"]:
        print(
            f"{item['value']}: {item['raw_path'].name}, "
            f"{item['log_path'].name}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
