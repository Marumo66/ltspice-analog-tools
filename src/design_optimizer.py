"""Constraint-based design selection over an LTspice AC/DC geometry sweep.

The first release intentionally performs a finite, user-defined one-dimensional
grid search. It does not claim to solve a continuous or global optimization
problem.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping, Optional, Sequence

from src.parameter_sweep_unified import run_unified_geometry_sweep


_REQUIRED_CONFIG_KEYS = {
    "source_netlist",
    "devices",
    "parameter",
    "values",
    "supply_voltages",
    "constraints",
    "objective",
}
_OPTIONAL_CONFIG_KEYS = {
    "output_csv",
    "report_directory",
    "timeout",
    "output_signal",
    "feedback_signal",
}
_ALLOWED_CONFIG_KEYS = _REQUIRED_CONFIG_KEYS | _OPTIONAL_CONFIG_KEYS


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a number, not a boolean")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def validate_constraints(constraints: Any) -> dict[str, dict[str, float]]:
    """Validate min/max bounds for numeric result fields."""
    if not isinstance(constraints, dict) or not constraints:
        raise ValueError("constraints must be a non-empty object")

    normalized: dict[str, dict[str, float]] = {}
    for metric, bounds in constraints.items():
        if not isinstance(metric, str) or not metric.strip():
            raise ValueError("Constraint metric names must be non-empty strings")
        if not isinstance(bounds, dict) or not bounds:
            raise ValueError(
                f"Constraint for {metric!r} must contain 'min' and/or 'max'"
            )
        unknown = set(bounds) - {"min", "max"}
        if unknown:
            raise ValueError(
                f"Unsupported bound(s) for {metric!r}: {', '.join(sorted(unknown))}"
            )
        if not ({"min", "max"} & set(bounds)):
            raise ValueError(f"Constraint for {metric!r} has no usable bound")

        item: dict[str, float] = {}
        for bound_name, value in bounds.items():
            item[bound_name] = _finite_number(value, f"{metric}.{bound_name}")
        if "min" in item and "max" in item and item["min"] > item["max"]:
            raise ValueError(f"Minimum exceeds maximum for constraint {metric!r}")
        normalized[metric] = item

    return normalized


def validate_objective(objective: Any) -> dict[str, str]:
    if not isinstance(objective, dict):
        raise ValueError("objective must be an object")
    if set(objective) != {"metric", "direction"}:
        raise ValueError("objective must contain exactly 'metric' and 'direction'")
    metric = objective["metric"]
    direction = objective["direction"]
    if not isinstance(metric, str) or not metric.strip():
        raise ValueError("objective.metric must be a non-empty result field name")
    if not isinstance(direction, str) or direction not in {"maximize", "minimize"}:
        raise ValueError("objective.direction must be 'maximize' or 'minimize'")
    return {"metric": metric, "direction": direction}


def validate_design_config(config: Any, config_directory: Path) -> dict[str, Any]:
    """Validate and normalize a JSON design-optimization configuration."""
    if not isinstance(config, dict):
        raise ValueError("Configuration root must be a JSON object")

    missing = _REQUIRED_CONFIG_KEYS - set(config)
    unknown = set(config) - _ALLOWED_CONFIG_KEYS
    if missing:
        raise ValueError(f"Missing configuration key(s): {', '.join(sorted(missing))}")
    if unknown:
        raise ValueError(f"Unknown configuration key(s): {', '.join(sorted(unknown))}")

    source_value = config["source_netlist"]
    if not isinstance(source_value, str) or not source_value.strip():
        raise ValueError("source_netlist must be a non-empty path")
    source = Path(source_value).expanduser()
    if not source.is_absolute():
        source = config_directory / source
    source = source.resolve()

    devices = config["devices"]
    if (
        not isinstance(devices, list)
        or not devices
        or any(not isinstance(item, str) or not item.strip() for item in devices)
    ):
        raise ValueError("devices must be a non-empty list of MOSFET instance names")

    parameter = config["parameter"]
    if not isinstance(parameter, str) or parameter.lower() not in {"w", "l"}:
        raise ValueError("parameter must be 'w' or 'l'")

    values = config["values"]
    if (
        not isinstance(values, list)
        or not values
        or any(not isinstance(item, str) or not item.strip() for item in values)
    ):
        raise ValueError("values must be a non-empty list of SPICE value strings")

    supplies = config["supply_voltages"]
    if not isinstance(supplies, dict) or not supplies:
        raise ValueError("supply_voltages must be a non-empty object")
    normalized_supplies = {
        name: _finite_number(value, f"supply_voltages.{name}")
        for name, value in supplies.items()
    }

    constraints = validate_constraints(config["constraints"])
    objective = validate_objective(config["objective"])

    timeout = _finite_number(config.get("timeout", 180.0), "timeout")
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")

    normalized: dict[str, Any] = {
        "source_netlist": source,
        "devices": devices,
        "parameter": parameter.lower(),
        "values": values,
        "supply_voltages": normalized_supplies,
        "constraints": constraints,
        "objective": objective,
        "timeout": timeout,
        "output_signal": config.get("output_signal", "V(out)"),
        "feedback_signal": config.get("feedback_signal", "V(nfb)"),
    }

    for signal_key in ("output_signal", "feedback_signal"):
        signal = normalized[signal_key]
        if not isinstance(signal, str) or not signal.strip():
            raise ValueError(f"{signal_key} must be a non-empty trace name")

    output_csv_value = config.get("output_csv")
    if output_csv_value is None:
        output_csv = source.with_name(f"{source.stem}_optimization_sweep.csv")
    else:
        if not isinstance(output_csv_value, str) or not output_csv_value.strip():
            raise ValueError("output_csv must be a non-empty path when specified")
        output_csv = Path(output_csv_value).expanduser()
        if not output_csv.is_absolute():
            output_csv = source.parent / output_csv
        output_csv = output_csv.resolve()
    if output_csv.parent != source.parent:
        raise ValueError(
            "output_csv must be in the same directory as source_netlist "
            "because the unified sweep writes sibling simulation files"
        )
    normalized["output_csv"] = output_csv

    report_directory_value = config.get("report_directory", "design_reports")
    if (
        not isinstance(report_directory_value, str)
        or not report_directory_value.strip()
    ):
        raise ValueError("report_directory must be a non-empty path")
    report_directory = Path(report_directory_value).expanduser()
    if not report_directory.is_absolute():
        report_directory = source.parent / report_directory
    normalized["report_directory"] = report_directory.resolve()

    return normalized


def _metric_value(row: Mapping[str, Any], metric: str) -> float:
    if metric not in row:
        raise ValueError(
            f"Result field {metric!r} is unavailable; check the metric name "
            "against the unified sweep CSV columns"
        )
    return _finite_number(row[metric], f"result.{metric}")


def evaluate_candidates(
    candidates: Sequence[Mapping[str, Any]],
    constraints: Mapping[str, Mapping[str, float]],
    objective: Mapping[str, str],
) -> dict[str, Any]:
    """Apply inclusive constraints and select the best feasible grid point."""
    if not candidates:
        raise ValueError("At least one candidate result is required")

    checked = []
    for index, candidate in enumerate(candidates):
        row = dict(candidate)
        metric = objective["metric"]
        objective_value = _metric_value(row, metric)

        checks = {}
        feasible = True
        for constraint_metric, bounds in constraints.items():
            actual = _metric_value(row, constraint_metric)
            passed = True
            if "min" in bounds and actual < bounds["min"]:
                passed = False
            if "max" in bounds and actual > bounds["max"]:
                passed = False
            checks[constraint_metric] = {
                "actual": actual,
                "bounds": dict(bounds),
                "passed": passed,
            }
            feasible = feasible and passed

        checked.append({
            "index": index,
            "sweep_value": row.get("sweep_value", ""),
            "objective_value": objective_value,
            "feasible": feasible,
            "checks": checks,
            "metrics": row,
        })

    feasible_candidates = [item for item in checked if item["feasible"]]
    best = None
    if feasible_candidates:
        reverse = objective["direction"] == "maximize"
        best = sorted(
            feasible_candidates,
            key=lambda item: item["objective_value"],
            reverse=reverse,
        )[0]

    return {
        "candidate_count": len(checked),
        "feasible_count": len(feasible_candidates),
        "objective": dict(objective),
        "constraints": {key: dict(value) for key, value in constraints.items()},
        "best_candidate": best,
        "candidates": checked,
    }


def _format_metric(metric: str, value: float) -> str:
    if metric == "ugbw_Hz":
        return f"{value / 1e6:.4f} MHz"
    if metric.endswith("_uW") or metric == "selected_supplies_power_delivered_uW":
        return f"{value:.4f} µW"
    if metric.endswith("_dB"):
        return f"{value:.3f} dB"
    if metric.endswith("_deg"):
        return f"{value:.3f}°"
    if metric.endswith("_V"):
        return f"{value:.5g} V"
    return f"{value:.6g}"


def render_markdown_report(
    evaluation: Mapping[str, Any],
    *,
    source_netlist: str,
    devices: Sequence[str],
    parameter: str,
    values: Sequence[str],
    csv_path: str,
    supply_voltages: Optional[Mapping[str, float]] = None,
    simulation_count: Optional[int] = None,
) -> str:
    objective = evaluation["objective"]
    metric = objective["metric"]
    direction_label = "maximum" if objective["direction"] == "maximize" else "minimum"
    supply_summary = ", ".join(
        f"{name}={voltage:g} V"
        for name, voltage in (supply_voltages or {}).items()
    ) or "not specified"
    simulation_summary = (
        str(simulation_count) if simulation_count is not None else "not specified"
    )
    lines = [
        "# LTspice Design Optimization Report",
        "",
        "## Search setup",
        "",
        f"- Source netlist: {source_netlist}",
        f"- Swept devices: {', '.join(devices)}",
        f"- Swept parameter: {parameter.upper()}",
        f"- Candidate values: {', '.join(values)}",
        "- Search type: finite one-dimensional grid search",
        f"- Objective: {direction_label} {metric}",
        f"- Supply sources: {supply_summary}",
        f"- LTspice simulations: {simulation_summary}",
        "",
        "## Design constraints",
        "",
    ]

    for name, bounds in evaluation["constraints"].items():
        parts = []
        if "min" in bounds:
            parts.append(f"minimum {_format_metric(name, bounds['min'])}")
        if "max" in bounds:
            parts.append(f"maximum {_format_metric(name, bounds['max'])}")
        lines.append(f"- {name}: {'; '.join(parts)}")

    lines.extend([
        "",
        "## Selection result",
        "",
        f"- Candidates evaluated: {evaluation['candidate_count']}",
        f"- Feasible candidates: {evaluation['feasible_count']}",
    ])

    best = evaluation["best_candidate"]
    if best is None:
        lines.extend([
            "- Selected design: **No feasible candidate**",
            "",
            "No tested parameter value satisfied every configured constraint. "
            "Review the candidate table and adjust the design or requirements.",
        ])
    else:
        lines.extend([
            f"- Selected {parameter.upper()} value: **{best['sweep_value']}**",
            f"- Objective value: **{_format_metric(metric, best['objective_value'])}**",
            "",
            "This is the best feasible candidate among the supplied grid values. "
            "It is not a claim of a continuous or global optimum.",
        ])

    constraint_names = list(evaluation["constraints"])
    diagnostic_names = [
        "ac_convergence_method", "ac_warning_count",
        "op_convergence_method", "op_warning_count",
    ]
    available_diagnostics = [
        name for name in diagnostic_names
        if any(name in item["metrics"] for item in evaluation["candidates"])
    ]
    headers = (
        ["Candidate", metric] + constraint_names
        + available_diagnostics + ["Feasible"]
    )
    lines.extend(["", "## Candidate comparison", ""])
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")

    for item in evaluation["candidates"]:
        cells = [
            str(item["sweep_value"]),
            _format_metric(metric, item["objective_value"]),
        ]
        for name in constraint_names:
            check = item["checks"][name]
            formatted = _format_metric(name, check["actual"])
            cells.append(f"{formatted} ({'pass' if check['passed'] else 'fail'})")
        for name in available_diagnostics:
            value = item["metrics"].get(name, "")
            cells.append(str(value))
        cells.append("yes" if item["feasible"] else "no")
        lines.append("| " + " | ".join(cells) + " |")

    lines.extend([
        "",
        "## Simulation output",
        "",
        f"- Summary CSV: {csv_path}",
        "",
        "The AC and operating-point results are generated from matched netlist "
        "variants for each candidate. The source netlist is not modified.",
        "",
        "## Scope and limitations",
        "",
        "- The optimizer evaluates only the values explicitly listed in the configuration.",
        "- One W or L parameter is varied per run; selected devices share the same value.",
        "- No adaptive search, continuous optimization, multi-parameter search, "
        "or topology modification is performed.",
        "- Results depend on the supplied LTspice model, simulator version, "
        "bias conditions, and selected supply sources.",
        "- Model warnings and convergence metadata remain in the CSV/log files "
        "and must be reviewed before treating a candidate as a final circuit design.",
        "",
    ])
    return "\n".join(lines)


def run_design_optimization(
    config_path,
    *,
    runner=None,
) -> dict[str, Any]:
    """Run a configured AC/DC sweep, rank feasible candidates and write reports."""
    config_file = Path(config_path).expanduser().resolve()
    with config_file.open(encoding="utf-8") as input_file:
        raw_config = json.load(input_file)
    config = validate_design_config(raw_config, config_file.parent)

    source = config["source_netlist"]
    report_directory = config["report_directory"]
    report_stem = f"{source.stem}_design_optimization"
    markdown_path = report_directory / f"{report_stem}.md"
    json_path = report_directory / f"{report_stem}.json"

    if report_directory.exists() and not report_directory.is_dir():
        raise NotADirectoryError(f"Report directory is not a directory: {report_directory}")
    existing = [
        path for path in (config["output_csv"], markdown_path, json_path)
        if path.exists()
    ]
    if existing:
        names = ", ".join(str(path) for path in existing)
        raise FileExistsError(
            "Refusing to overwrite existing optimization output(s): " + names
        )

    sweep_kwargs = {
        "devices": config["devices"],
        "parameter": config["parameter"],
        "values": config["values"],
        "supply_voltages": config["supply_voltages"],
        "output_csv": config["output_csv"],
        "timeout": config["timeout"],
        "output_signal": config["output_signal"],
        "feedback_signal": config["feedback_signal"],
    }
    if runner is not None:
        sweep_kwargs["runner"] = runner

    sweep = run_unified_geometry_sweep(source, **sweep_kwargs)
    candidate_rows = [item["metrics"] for item in sweep["results"]]
    evaluation = evaluate_candidates(
        candidate_rows,
        config["constraints"],
        config["objective"],
    )

    report_directory.mkdir(parents=True, exist_ok=True)
    report = render_markdown_report(
        evaluation,
        source_netlist=str(source),
        devices=config["devices"],
        parameter=config["parameter"],
        values=config["values"],
        csv_path=str(sweep["csv_path"]),
        supply_voltages=config["supply_voltages"],
        simulation_count=sweep["simulation_count"],
    )

    artifact = {
        "source_netlist": str(source),
        "devices": config["devices"],
        "parameter": config["parameter"],
        "values": config["values"],
        "supply_voltages": config["supply_voltages"],
        "simulation_count": sweep["simulation_count"],
        "csv_path": str(sweep["csv_path"]),
        "report_path": str(markdown_path),
        "evaluation": evaluation,
    }

    # Serialize before creating either report so invalid JSON data cannot leave
    # a partial Markdown-only report behind.
    artifact_json = json.dumps(
        artifact, indent=2, ensure_ascii=False, allow_nan=False
    ) + "\n"
    # Exclusive creation prevents silently replacing a previous experiment.
    with markdown_path.open("x", encoding="utf-8", newline="") as output_file:
        output_file.write(report)
    with json_path.open("x", encoding="utf-8", newline="") as output_file:
        output_file.write(artifact_json)

    return {
        "simulation_count": sweep["simulation_count"],
        "csv_path": sweep["csv_path"],
        "report_path": markdown_path,
        "json_path": json_path,
        "evaluation": evaluation,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run an LTspice AC/DC geometry grid search, apply design constraints "
            "and generate Markdown/JSON reports."
        )
    )
    parser.add_argument("config", help="Path to a JSON design-optimization config")
    args = parser.parse_args(argv)

    try:
        result = run_design_optimization(args.config)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Design optimization failed: {exc}", file=sys.stderr)
        return 1

    evaluation = result["evaluation"]
    print(
        f"Evaluated {evaluation['candidate_count']} candidate(s) using "
        f"{result['simulation_count']} LTspice simulation(s)."
    )
    print(f"Feasible candidates: {evaluation['feasible_count']}")
    print(f"Summary CSV: {result['csv_path']}")
    print(f"Markdown report: {result['report_path']}")
    print(f"JSON report: {result['json_path']}")

    best = evaluation["best_candidate"]
    if best is None:
        print("No candidate satisfies all configured constraints.")
        return 2

    objective = evaluation["objective"]
    print(
        f"Selected {best['sweep_value']}: "
        f"{objective['metric']}={_format_metric(objective['metric'], best['objective_value'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
