"""Read-only MOSFET geometry checks against LTspice model cards."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import re
import shlex
import sys
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


_NUMBER_RE = re.compile(
    r"^(?P<number>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    r"(?P<suffix>meg|mil|[tgkmunpf]?)$",
    re.IGNORECASE,
)
_MODEL_RE = re.compile(
    r"^\s*\.model\s+(?P<name>\S+)\s+(?P<kind>nmos|pmos)\b",
    re.IGNORECASE,
)
_ASSIGNMENT_RE = re.compile(
    r"\b(?P<name>[A-Za-z][A-Za-z0-9_]*)\s*=\s*(?P<value>[^\s()]+)"
)
_GEOMETRY_KEYS = {"w", "l", "pd", "ps"}
_MODEL_SUFFIXES = {".lib", ".mod", ".mos", ".model", ".spice", ".cir"}


def parse_spice_number(value: str) -> Optional[float]:
    """Parse a numeric SPICE literal into SI units; expressions are not evaluated."""
    if not isinstance(value, str):
        return None
    match = _NUMBER_RE.fullmatch(value.strip().rstrip(",;"))
    if match is None:
        return None
    scale = {
        "": 1.0, "t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3,
        "m": 1e-3, "mil": 25.4e-6, "u": 1e-6, "n": 1e-9,
        "p": 1e-12, "f": 1e-15,
    }[match.group("suffix").lower()]
    result = float(match.group("number")) * scale
    return result if math.isfinite(result) else None


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _parse_model_cards(text: str, source: str) -> Dict[str, dict]:
    """Read NMOS/PMOS .MODEL cards, including '+' continuation lines."""
    lines = text.splitlines()
    models: Dict[str, dict] = {}
    index = 0
    while index < len(lines):
        match = _MODEL_RE.match(lines[index])
        if match is None:
            index += 1
            continue
        fragments = [lines[index]]
        index += 1
        while index < len(lines):
            line = lines[index].lstrip()
            if not line or line.startswith(("*", ";")):
                index += 1
                continue
            if line.startswith("+"):
                fragments.append(line[1:])
                index += 1
                continue
            break
        parameters = {
            item.group("name").upper(): item.group("value")
            for item in _ASSIGNMENT_RE.finditer(" ".join(fragments))
        }
        name = match.group("name")
        models[name.casefold()] = {
            "name": name,
            "kind": match.group("kind").lower(),
            "parameters": parameters,
            "source": source,
        }
    return models


def _library_references(text: str) -> List[str]:
    references = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("*", ";")):
            continue
        match = re.match(r"^\.(include|inc|lib)\s+(.+)$", stripped, re.IGNORECASE)
        if match is None:
            continue
        directive, remainder = match.groups()
        try:
            tokens = shlex.split(remainder, posix=False)
        except ValueError:
            tokens = remainder.split()
        if not tokens:
            continue
        candidate = tokens[0].strip().strip("\"'")
        if directive.lower() == "lib":
            suffix = Path(candidate.replace("\\", "/")).suffix.lower()
            if suffix not in _MODEL_SUFFIXES and not Path(candidate).exists():
                continue
        references.append(candidate)
    return references


def _resolve_library(reference: str, directory: Path) -> Optional[Path]:
    candidate = Path(reference.replace("\\", "/")).expanduser()
    possible = [candidate] if candidate.is_absolute() else [directory / candidate]
    for path in possible:
        if path.is_file():
            return path.resolve()
    # Netlists moved between Windows and macOS may retain an old absolute path.
    basename = candidate.name.casefold()
    if basename:
        try:
            for child in directory.iterdir():
                if child.is_file() and child.name.casefold() == basename:
                    return child.resolve()
        except OSError:
            pass
    return None


def _load_models(netlist_path: Path, netlist_text: str,
                 explicit_paths: Sequence[str]) -> Tuple[Dict[str, dict], List[Path], List[str]]:
    initial_paths: List[Path] = []
    unresolved: List[str] = []
    for reference in [*_library_references(netlist_text), *explicit_paths]:
        path = _resolve_library(reference, netlist_path.parent)
        if path is None:
            unresolved.append(reference)
        elif path not in initial_paths:
            initial_paths.append(path)

    models = _parse_model_cards(netlist_text, str(netlist_path))
    loaded: List[Path] = []
    visited = set()

    def visit(path: Path) -> None:
        path = path.resolve()
        if path in visited:
            return
        visited.add(path)
        loaded.append(path)
        content = _read_text(path)
        models.update(_parse_model_cards(content, str(path)))
        for reference in _library_references(content):
            nested = _resolve_library(reference, path.parent)
            if nested is None:
                unresolved.append(reference)
            else:
                visit(nested)

    for path in initial_paths:
        visit(path)
    return models, loaded, unresolved


def _parse_mosfet_instances(text: str) -> List[dict]:
    instances = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("*", ";", "+")):
            continue
        tokens = stripped.split(";", 1)[0].split()
        if not tokens or not re.match(r"^M\S+$", tokens[0], re.IGNORECASE):
            continue
        if len(tokens) < 6:
            instances.append({
                "name": tokens[0], "line": line_number, "model": None,
                "geometry": {}, "parse_error": "MOSFET line has too few tokens",
            })
            continue
        geometry = {}
        for item in _ASSIGNMENT_RE.finditer(" ".join(tokens[6:])):
            key = item.group("name").lower()
            if key in _GEOMETRY_KEYS:
                geometry[key] = item.group("value")
        instances.append({
            "name": tokens[0], "line": line_number, "model": tokens[5],
            "geometry": geometry, "parse_error": None,
        })
    return instances


def _recommended_supply_range(texts: Iterable[str]) -> Optional[Tuple[float, float]]:
    pattern = re.compile(
        r"(?:recommended|raccomanded)\s+voltage\s+supply\s+range\s*:\s*"
        r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*V?\s*--\s*"
        r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*V?",
        re.IGNORECASE,
    )
    for text in texts:
        match = pattern.search(text)
        if match:
            low, high = float(match.group(1)), float(match.group(2))
            if math.isfinite(low) and math.isfinite(high) and low <= high:
                return low, high
    return None


def _um(value: float) -> str:
    return f"{value * 1e6:.6g} um"


def check_netlist_models(netlist_path, *, model_paths: Optional[Sequence[str]] = None,
                         supply_voltages: Optional[Dict[str, float]] = None) -> dict:
    """Compare MOSFET W/L literals with bounds declared by their model cards.

    The report is advisory. A value outside a declared bound is not a claim
    that LTspice will refuse to simulate; no input file is modified.
    """
    path = Path(netlist_path).expanduser().resolve()
    if path.suffix.lower() != ".net":
        raise ValueError("netlist_path must point to an LTspice .net file")
    if not path.is_file():
        raise FileNotFoundError(f"Netlist not found: {path}")

    netlist_text = _read_text(path)
    models, library_paths, unresolved = _load_models(path, netlist_text, model_paths or [])
    instances = _parse_mosfet_instances(netlist_text)
    warnings: List[str] = []
    out_of_range = False

    for reference in unresolved:
        warnings.append(f"Model library could not be resolved: {reference}")
    if not models:
        warnings.append("No NMOS/PMOS .MODEL cards were found in the netlist or loaded libraries")

    for instance in instances:
        name = instance["name"]
        if instance["parse_error"]:
            warnings.append(f"{name} at line {instance['line']}: {instance['parse_error']}")
            continue
        model = models.get(instance["model"].casefold())
        if model is None:
            warnings.append(f"{name}: model '{instance['model']}' was not found; W/L range was not checked")
            continue

        parameters = model["parameters"]
        for geometry, lower_key, upper_key in (
            ("w", "WMIN", "WMAX"), ("l", "LMIN", "LMAX")
        ):
            literal = instance["geometry"].get(geometry)
            if literal is None:
                warnings.append(f"{name}: {geometry.upper()} is not specified; range check skipped")
                continue
            value = parse_spice_number(literal)
            if value is None:
                warnings.append(f"{name}: {geometry.upper()}='{literal}' is not numeric; range check skipped")
                continue
            lower = parse_spice_number(parameters.get(lower_key, ""))
            upper = parse_spice_number(parameters.get(upper_key, ""))
            if lower is not None and value < lower:
                warnings.append(
                    f"{name}: {geometry.upper()}={_um(value)} is below "
                    f"{model['name']}.{lower_key}={_um(lower)}"
                )
                out_of_range = True
            if upper is not None and value > upper:
                warnings.append(
                    f"{name}: {geometry.upper()}={_um(value)} exceeds "
                    f"{model['name']}.{upper_key}={_um(upper)}"
                )
                out_of_range = True
            if lower is None and upper is None:
                warnings.append(f"{name}: model '{model['name']}' does not declare {lower_key}/{upper_key}")

        for key in ("pd", "ps"):
            literal = instance["geometry"].get(key)
            if literal is None:
                warnings.append(
                    f"{name}: {key.upper()} is not specified; check the simulator's effective value and .log warning"
                )
            elif parse_spice_number(literal) == 0:
                warnings.append(f"{name}: {key.upper()} is explicitly zero; verify diffusion geometry")
            elif parse_spice_number(literal) is None:
                warnings.append(f"{name}: {key.upper()}='{literal}' is not numeric")

    texts = [_read_text(item) for item in library_paths] + [netlist_text]
    recommended_range = _recommended_supply_range(texts)
    supply_checks = []
    for source_name, voltage in (supply_voltages or {}).items():
        numeric = float(voltage)
        if not math.isfinite(numeric):
            raise ValueError(f"Supply voltage for {source_name} must be finite")
        state = "not_checked"
        if recommended_range is not None:
            low, high = recommended_range
            state = "within_range" if low <= numeric <= high else "outside_range"
            if state == "outside_range":
                warnings.append(
                    f"{source_name}={numeric:g} V is outside the model comment's "
                    f"recommended supply range {low:g}–{high:g} V"
                )
        supply_checks.append({"source": source_name, "voltage_V": numeric, "status": state})

    return {
        "netlist_path": str(path),
        "model_files": [str(item) for item in library_paths],
        "models": models,
        "instances": instances,
        "warnings": warnings,
        "out_of_range": out_of_range,
        "recommended_supply_range_V": recommended_range,
        "supply_checks": supply_checks,
        "summary": {
            "mosfet_count": len(instances),
            "model_count": len(models),
            "warning_count": len(warnings),
        },
    }


def _parse_supplies(items: Sequence[str]) -> Dict[str, float]:
    supplies = {}
    for item in items:
        match = re.fullmatch(r"(?P<name>[A-Za-z][A-Za-z0-9_]*)=(?P<value>.+)", item)
        if match is None:
            raise ValueError(f"Invalid --supply {item!r}; expected VDD=1.5")
        name = match.group("name")
        if name in supplies:
            raise ValueError(f"Duplicate supply source: {name}")
        value = parse_spice_number(match.group("value"))
        if value is None:
            raise ValueError(f"Invalid numeric supply voltage in --supply {item!r}")
        supplies[name] = value
    return supplies


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only check of MOSFET geometry against LTspice model-card declarations."
    )
    parser.add_argument("netlist", help="LTspice .net file")
    parser.add_argument("--model", action="append", default=[], metavar="PATH",
                        help="Additional model file; may be repeated")
    parser.add_argument("--supply", action="append", default=[], metavar="SOURCE=VOLTAGE",
                        help="Supply to compare with a stated model-file range, e.g. VDD=1.5")
    parser.add_argument("--strict", action="store_true",
                        help="Return exit code 2 if a MOSFET W/L is outside a declared bound")
    args = parser.parse_args(argv)

    try:
        report = check_netlist_models(
            args.netlist,
            model_paths=args.model,
            supply_voltages=_parse_supplies(args.supply),
        )
    except (OSError, ValueError) as exc:
        print(f"Model check failed: {exc}", file=sys.stderr)
        return 1

    print(f"Netlist: {report['netlist_path']}")
    for library in report["model_files"]:
        print(f"Model file: {library}")
    print(
        f"Found {report['summary']['mosfet_count']} MOSFET instance(s) and "
        f"{report['summary']['model_count']} NMOS/PMOS model card(s)."
    )
    for model in sorted(report["models"].values(), key=lambda item: item["name"].casefold()):
        p = model["parameters"]
        def bound(key):
            value = parse_spice_number(p.get(key, ""))
            return _um(value) if value is not None else "n/a"
        print(
            f"  {model['name']} ({model['kind'].upper()}): VERSION={p.get('VERSION', 'n/a')}, "
            f"W={bound('WMIN')}–{bound('WMAX')}, L={bound('LMIN')}–{bound('LMAX')}"
        )
    if report["recommended_supply_range_V"] is not None:
        low, high = report["recommended_supply_range_V"]
        print(f"Model-file comment supply range: {low:g}–{high:g} V")
    for item in report["supply_checks"]:
        print(f"Supply {item['source']}={item['voltage_V']:g} V: {item['status'].replace('_', ' ')}")
    if report["warnings"]:
        print("\nWarnings:")
        for warning in report["warnings"]:
            print(f"  - {warning}")
    else:
        print("No model-range or geometry warnings found.")
    print(f"\nWarning count: {report['summary']['warning_count']}")
    if args.strict and report["out_of_range"]:
        print("Strict check failed: MOSFET W/L is outside declared model bounds.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
