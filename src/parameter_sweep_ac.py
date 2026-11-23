"""Run AC geometry sweeps and collect loop-gain metrics from LTspice."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import re
import sys
from typing import Optional, Sequence

from src.ac_metrics import calculate_loop_gain_metrics
from src.ac_parser import export_ac_raw_to_csv
from src.dc_parser import parse_dc_operating_point_log
from src.ltspice_runner import LTspiceRunner
from src.netlist_controller import write_mosfet_geometry_variant


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value.strip()).strip("_")


def _validate_sweep(devices, parameter, values):
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


def _require_active_ac_analysis(netlist_text: str) -> None:
    """Require exactly one active .ac directive and no competing analysis."""
    active_ac = []
    competing = []
    for line_number, line in enumerate(netlist_text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("*", ";")):
            continue
        directive = re.match(r"^\.(\S+)", stripped)
        if directive is None:
            continue
        name = directive.group(1).lower()
        if name == "ac":
            active_ac.append(line_number)
        elif name in {"op", "tran", "dc", "noise", "tf", "pz"}:
            competing.append((line_number, name))

    if len(active_ac) != 1:
        raise ValueError(
            f"AC sweep requires exactly one active .ac directive; found {len(active_ac)}"
        )
    if competing:
        details = ", ".join(f".{name} (line {number})" for number, name in competing)
        raise ValueError(f"AC sweep netlist contains competing analysis directive(s): {details}")


def run_ac_geometry_sweep(
    source_netlist,
    *,
    devices,
    parameter="w",
    values,
    runner=None,
    output_csv=None,
    timeout=180.0,
    output_signal="V(out)",
    feedback_signal="V(nfb)",
):
    """Run an AC sweep over a shared MOSFET W or L value and write summary CSV.

    Each point receives its own .net, .raw, .log and intermediate .ac.csv file.
    The source netlist is never modified. Loop gain is calculated by the
    existing AC metrics function using -V(output_signal)/V(feedback_signal).

    DC MOSFET operating-point values are taken from the SPICE Output Log.
    AC RAW current traces are small-signal phasors and are deliberately not
    interpreted as DC supply current or quiescent power.
    """
    source = Path(source_netlist).expanduser().resolve()
    if source.suffix.lower() != ".net":
        raise ValueError("source_netlist must be an LTspice .net file")
    if not source.is_file():
        raise FileNotFoundError(f"Source netlist not found: {source}")

    source_text = source.read_text(encoding="utf-8")
    _require_active_ac_analysis(source_text)
    selected_devices, parameter, values = _validate_sweep(devices, parameter, values)

    if not isinstance(output_signal, str) or not output_signal.strip():
        raise ValueError("output_signal must be a non-empty trace name")
    if not isinstance(feedback_signal, str) or not feedback_signal.strip():
        raise ValueError("feedback_signal must be a non-empty trace name")

    base_stem = f"{source.stem}_{'_'.join(selected_devices)}_{parameter}"
    if output_csv is None:
        output_csv = source.with_name(f"{base_stem}_ac_sweep.csv")
    output_csv = Path(output_csv).expanduser().resolve()
    if output_csv.parent != source.parent:
        raise ValueError("output_csv must be in the same directory as the source netlist")
    if output_csv.exists():
        raise FileExistsError(f"Summary CSV already exists: {output_csv}")

    variants = []
    for value in values:
        value_slug = _slug(value)
        stem = f"{base_stem}_{value_slug}"
        variant = source.with_name(f"{stem}.net")
        ac_csv = source.with_name(f"{stem}.ac.csv")
        related = [variant, variant.with_suffix(".raw"), variant.with_suffix(".log"), ac_csv]
        existing = [path.name for path in related if path.exists()]
        if existing:
            raise FileExistsError(
                "Sweep output(s) already exist; refusing to overwrite: " + ", ".join(existing)
            )
        variants.append((value, variant, ac_csv))

    active_runner = runner or LTspiceRunner(timeout=timeout)
    rows = []
    run_results = []

    for value, variant_path, ac_csv_path in variants:
        updates = {device: {parameter: value} for device in selected_devices}
        write_mosfet_geometry_variant(source, variant_path, updates, overwrite=False)

        simulation = active_runner.run_netlist(variant_path)
        raw_info = export_ac_raw_to_csv(simulation.raw_path, ac_csv_path)
        if output_signal not in raw_info["variables"]:
            raise ValueError(f"Output trace {output_signal!r} is not present in AC RAW file")
        if feedback_signal not in raw_info["variables"]:
            raise ValueError(f"Feedback trace {feedback_signal!r} is not present in AC RAW file")

        metrics = calculate_loop_gain_metrics(
            ac_csv_path,
            output_signal=output_signal,
            feedback_signal=feedback_signal,
        )
        log_data = parse_dc_operating_point_log(simulation.log_path)
        log_devices = {name.upper(): data for name, data in log_data["devices"].items()}

        row = {
            "sweep_parameter": parameter.upper(),
            "sweep_value": value,
            "devices": ";".join(selected_devices),
            "netlist": variant_path.name,
            "raw_points": raw_info["points"],
            "frequency_start_Hz": raw_info["frequency_start"],
            "frequency_end_Hz": raw_info["frequency_end"],
            "low_frequency_gain_dB": metrics["low_frequency_gain_dB"],
            "low_frequency_phase_deg": metrics["low_frequency_phase_deg"],
            "ugbw_Hz": metrics["ugbw_Hz"],
            "phase_at_ugbw_deg": metrics["phase_at_ugbw_deg"],
            "phase_margin_deg": metrics["phase_margin_deg"],
            "convergence_method": log_data["metadata"]["convergence_method"] or "",
            "warning_count": len(log_data["metadata"]["warnings"]),
            "output_signal": output_signal,
            "feedback_signal": feedback_signal,
        }

        for device in selected_devices:
            if device not in log_devices:
                raise ValueError(
                    f"{device} is not present in the MOSFET operating-point log "
                    f"for sweep value {value}"
                )
            device_metrics = log_devices[device]
            for key in ("Id", "Gm", "Vgs", "Vds", "Vth", "Vdsat"):
                if key in device_metrics:
                    row[f"{device}_{key}"] = device_metrics[key]

        rows.append(row)
        run_results.append({
            "value": value,
            "netlist_path": variant_path,
            "raw_path": simulation.raw_path,
            "log_path": simulation.log_path,
            "ac_csv_path": ac_csv_path,
            "metrics": row,
        })

    preferred_columns = [
        "sweep_parameter", "sweep_value", "devices", "netlist",
        "raw_points", "frequency_start_Hz", "frequency_end_Hz",
        "low_frequency_gain_dB", "low_frequency_phase_deg",
        "ugbw_Hz", "phase_at_ugbw_deg", "phase_margin_deg",
        "convergence_method", "warning_count", "output_signal", "feedback_signal",
    ]
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

    return {"run_count": len(run_results), "results": run_results, "csv_path": output_csv}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run an AC sweep by changing selected MOSFET W/L values."
    )
    parser.add_argument("source", help="Original LTspice AC .net file")
    parser.add_argument("--device", action="append", required=True,
                        help="MOSFET instance; repeat for symmetric devices")
    parser.add_argument("--parameter", choices=("w", "l"), default="w")
    parser.add_argument("--values", nargs="+", required=True,
                        help="SPICE values, e.g. 10u 12u 16u")
    parser.add_argument("--output-signal", default="V(out)")
    parser.add_argument("--feedback-signal", default="V(nfb)")
    parser.add_argument("--output-csv", help="Summary CSV path (defaults beside source netlist)")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args(argv)

    try:
        result = run_ac_geometry_sweep(
            args.source,
            devices=args.device,
            parameter=args.parameter,
            values=args.values,
            output_signal=args.output_signal,
            feedback_signal=args.feedback_signal,
            output_csv=args.output_csv,
            timeout=args.timeout,
        )
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"AC sweep failed: {exc}", file=sys.stderr)
        return 1

    print(f"Completed {result['run_count']} AC simulation(s).")
    print(f"Summary CSV: {result['csv_path']}")
    for item in result["results"]:
        metrics = item["metrics"]
        print(
            f"{item['value']}: Gain={metrics['low_frequency_gain_dB']:.3f} dB, "
            f"UGBW={metrics['ugbw_Hz'] / 1e6:.4f} MHz, "
            f"PM={metrics['phase_margin_deg']:.3f} deg"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
