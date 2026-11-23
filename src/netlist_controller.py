"""Safe, non-destructive MOSFET geometry updates for LTspice .net files."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import re
import sys
from typing import Mapping


_VALUE_RE = re.compile(
    r"^(?P<number>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    r"(?P<suffix>meg|mil|[tgkmunpf]?)$",
    re.IGNORECASE,
)
_ASSIGNMENT_RE = re.compile(
    r"(?<!\S)(?P<name>w|l)\s*=\s*(?P<value>\S+)",
    re.IGNORECASE,
)


def _normalise_geometry_value(value: object, *, device: str, parameter: str) -> str:
    """Validate a positive numeric SPICE literal and return its string form."""
    if not isinstance(value, str):
        raise ValueError(
            f"{device}.{parameter} must be a SPICE value string such as '10u'"
        )

    candidate = value.strip()
    match = _VALUE_RE.fullmatch(candidate)
    if match is None:
        raise ValueError(
            f"Invalid SPICE geometry value for {device}.{parameter}: {value!r}"
        )

    number = float(match.group("number"))
    suffix = match.group("suffix").lower()
    scale = {
        "": 1.0,
        "t": 1e12,
        "g": 1e9,
        "meg": 1e6,
        "k": 1e3,
        "m": 1e-3,
        "mil": 25.4e-6,
        "u": 1e-6,
        "n": 1e-9,
        "p": 1e-12,
        "f": 1e-15,
    }[suffix]

    if not math.isfinite(number) or number * scale <= 0:
        raise ValueError(f"{device}.{parameter} must be greater than zero")

    return candidate


def update_mosfet_geometry_text(
    netlist_text: str,
    updates: Mapping[str, Mapping[str, str]],
) -> str:
    """Update selected MOSFET W/L literals without changing other netlist text.

    Example:
        updates = {
            "M1": {"w": "12u"},
            "M5": {"l": "1.2u"},
        }

    Device names and parameter names are matched case-insensitively. Every
    requested device and geometry parameter must occur exactly once.
    """
    if not isinstance(netlist_text, str):
        raise TypeError("netlist_text must be a string")
    if not isinstance(updates, Mapping) or not updates:
        raise ValueError("updates must be a non-empty device-to-geometry mapping")

    normalized_updates = {}
    for device, parameters in updates.items():
        if not isinstance(device, str) or not re.fullmatch(
            r"M[A-Za-z0-9_]+", device, re.IGNORECASE
        ):
            raise ValueError(f"Invalid MOSFET instance name: {device!r}")
        if not isinstance(parameters, Mapping) or not parameters:
            raise ValueError(f"Updates for {device} must be a non-empty mapping")

        parameter_updates = {}
        for parameter, value in parameters.items():
            if not isinstance(parameter, str):
                raise ValueError(f"Invalid geometry parameter for {device}: {parameter!r}")
            key = parameter.lower()
            if key not in {"w", "l"}:
                raise ValueError(
                    f"Unsupported geometry parameter {parameter!r} for {device}; use 'w' or 'l'"
                )
            if key in parameter_updates:
                raise ValueError(f"Duplicate {key} update for {device}")
            parameter_updates[key] = _normalise_geometry_value(
                value, device=device, parameter=key
            )
        normalized_updates[device.lower()] = parameter_updates

    lines = netlist_text.splitlines(keepends=True)
    found_devices = set()
    applied = {device: set() for device in normalized_updates}
    output_lines = []

    for line in lines:
        stripped = line.lstrip()
        if not stripped or stripped.startswith(("*", ";", ".", "+")):
            output_lines.append(line)
            continue

        token_match = re.match(r"(\S+)", stripped)
        if token_match is None:
            output_lines.append(line)
            continue

        instance = token_match.group(1)
        device_key = instance.lower()
        if device_key not in normalized_updates:
            output_lines.append(line)
            continue

        if device_key in found_devices:
            raise ValueError(f"Duplicate MOSFET instance line: {instance}")
        found_devices.add(device_key)

        line_body = line.rstrip("\r\n")
        line_ending = line[len(line_body):]
        assignments = list(_ASSIGNMENT_RE.finditer(line_body))

        counts = {}
        for assignment in assignments:
            key = assignment.group("name").lower()
            counts[key] = counts.get(key, 0) + 1

        for parameter in normalized_updates[device_key]:
            count = counts.get(parameter, 0)
            if count != 1:
                raise ValueError(
                    f"{instance} must contain exactly one {parameter}= assignment; found {count}"
                )

        def replace_assignment(match):
            parameter = match.group("name").lower()
            if parameter not in normalized_updates[device_key]:
                return match.group(0)
            applied[device_key].add(parameter)
            return f"{match.group('name')}={normalized_updates[device_key][parameter]}"

        updated_body = _ASSIGNMENT_RE.sub(replace_assignment, line_body)
        output_lines.append(updated_body + line_ending)

    missing_devices = [
        device for device in normalized_updates if device not in found_devices
    ]
    if missing_devices:
        raise ValueError(
            "MOSFET instance(s) not found in netlist: "
            + ", ".join(sorted(missing_devices))
        )

    incomplete = []
    for device, parameters in normalized_updates.items():
        for parameter in parameters:
            if parameter not in applied[device]:
                incomplete.append(f"{device}.{parameter}")
    if incomplete:
        raise ValueError("Requested update(s) not applied: " + ", ".join(incomplete))

    return "".join(output_lines)


def write_mosfet_geometry_variant(
    source_path: Path,
    output_path: Path,
    updates: Mapping[str, Mapping[str, str]],
    *,
    overwrite: bool = False,
) -> Path:
    """Write an updated .net sibling, leaving the source file untouched.

    The output must be in the same directory as the source so relative
    .include/.lib paths (for example cmos130.lib) continue to resolve.
    """
    source = Path(source_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()

    if source.suffix.lower() != ".net" or output.suffix.lower() != ".net":
        raise ValueError("Source and output files must both use the .net extension")
    if not source.is_file():
        raise FileNotFoundError(f"Source netlist not found: {source}")
    if source == output:
        raise ValueError("Output netlist must be different from the source netlist")
    if source.parent != output.parent:
        raise ValueError(
            "Output netlist must be in the same directory as the source "
            "to preserve relative model-library paths"
        )
    if output.exists() and not overwrite:
        raise FileExistsError(f"Output netlist already exists: {output}")

    original = source.read_text(encoding="utf-8")
    updated = update_mosfet_geometry_text(original, updates)
    with output.open("w", encoding="utf-8", newline="") as output_file:
        output_file.write(updated)
    return output


def _parse_cli_updates(items):
    updates = {}
    for item in items:
        match = re.fullmatch(r"(M[A-Za-z0-9_]+)\.(w|l)=(.+)", item, re.IGNORECASE)
        if match is None:
            raise ValueError(
                f"Invalid --set value {item!r}; expected M1.w=12u or M5.l=1.2u"
            )
        device, parameter, value = match.groups()
        key = device.upper()
        parameter = parameter.lower()
        if parameter in updates.setdefault(key, {}):
            raise ValueError(f"Duplicate --set for {key}.{parameter}")
        updates[key][parameter] = value
    if not updates:
        raise ValueError("At least one --set option is required")
    return updates


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Create a modified LTspice .net copy with selected MOSFET W/L values."
    )
    parser.add_argument("source", help="Original LTspice .net file")
    parser.add_argument("output", help="New .net file to create beside the original")
    parser.add_argument(
        "--set",
        dest="updates",
        action="append",
        default=[],
        metavar="MOSFET.PARAM=VALUE",
        help="Geometry update, e.g. --set M1.w=12u --set M2.l=1.2u",
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    try:
        updates = _parse_cli_updates(args.updates)
        output = write_mosfet_geometry_variant(
            Path(args.source), Path(args.output), updates, overwrite=args.overwrite
        )
    except (ValueError, OSError) as exc:
        print(f"Netlist update failed: {exc}", file=sys.stderr)
        return 1

    print(f"Updated netlist written to: {output}")
    for device, parameters in updates.items():
        for parameter, value in parameters.items():
            print(f"{device}.{parameter} = {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
