"""Check MOSFET geometry against limits declared in LTspice model cards.

The checker is read-only: it never edits a netlist or a model library.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import re
import shlex
import sys
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


_SPICE_NUMBER_RE = re.compile(
    r"^(?P<number>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    r"(?P<suffix>meg|mil|[tgkmunpf]?)$",
    re.IGNORECASE,
)
_MODEL_START_RE = re.compile(
    r"^\s*\.model\s+(?P<name>\S+)\s+(?P<kind>nmos|pmos)\b",
    re.IGNORECASE,
)
_PARAMETER_RE = re.compile(
    r"\b(?P<name>[A-Za-z][A-Za-z0-9_]*)\s*=\s*(?P<value>[^\s()]+)"
)
_GEOMETRY_NAMES = {"w", "l", "pd", "ps"}
_MODEL_FILE_SUFFIXES = {".lib", ".mod", ".mos", ".model", ".spice", ".cir"}


def parse_spice_number(value: str) -> Optional[float]:
    """Convert a numeric SPICE literal to its SI value; return None otherwise.

    SPICE suffixes follow LTspice conventions, including M=milli and MEG=mega.
    Expressions such as {W_IN} are intentionally not evaluated.
    """
    if not isinstance(value, str):
        return None
    candidate = value.strip().rstrip(",;")
    match = _SPICE_NUMBER_RE.fullmatch(candidate)
    if match is None:
        return None
    number = float(match.group("number"))
    suffix = match.group("suffix").lower()
    scales = {
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
    }
    result = number * scales[suffix]
    return result if math.isfinite(result) else None


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _parse_model_cards(text: str, source: str) -> Dict[str, dict]:
    """Parse simple .MODEL NMOS/PMOS cards with '+' continuation lines."""
    lines = text.splitlines()
    models: Dict[str, dict] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        match = _MODEL_START_RE.match(line)
        if match is None:
            index += 1
            continue

        fragments = [line]
        index += 1
        while index < len(lines):
            continuation = lines[index].lstrip()
            if not continuation or continuation.startswith(("*", ";")):
                index += 1
                continue
            if continuation.startswith("+"):
                fragments.append(continuation[1:])
                index += 1
                continue
            break

        parameters = {}
        for parameter in _PARAMETER_RE.finditer(" ".join(fragments)):
            parameters[parameter.group("name").upper()] = parameter.group("value")

        name = match.group("name")
        models[name.casefold()] = {
            "name": name,
            "kind": match.group("kind").lower(),
            "parameters": parameters,
            "source": source,
        }
    return models


def _include_candidates(netlist_text: str) -> List[str]:
    """Return model-library paths named by .include/.inc/.lib directives."""
    candidates: List[str] = []
    for line in netlist_text.splitlines():
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
        # '.lib TT' can denote a section in an already opened library. Treat
        # it as a path only when it looks like a model file or exists locally.
        if directive.lower() == "lib":
            suffix = Path(candidate.replace("\\", "/")).suffix.lower()
            if suffix not in _MODEL_FILE_SUFFIXES and not Path(candidate).exists():
                continue
        candidates.append(candidate)
    return candidates


def _resolve_library_path(candidate: str, netlist_dir: Path) -> Optional[Path]:
    normalized = candidate.replace("\\", "/")
    candidate_path = Path(normalized).expanduser()
    possible = []
    if candidate_path.is_absolute():
        possible.append(candidate_path)
    else:
        possible.append(netlist_dir / candidate_path)
    for path in possible:
        if path.is_file():
            return path.resolve()

    # A Windows absolute path in a netlist may have been moved with the
    # project to macOS. Check for the same filename beside the netlist.
    basename = candidate_path.name
    if basename:
        try:
            for child in netlist_dir.iterdir():
                if child.is_file() and child.name.casefold() == basename.casefold():
                    return child.resolve()
        except OSError:
            pass
    return None


def _load_models(
    netlist_path: Path,
    netlist_text: str,
    explicit_model_paths: Sequence[str],
) -> Tuple[Dict[str, dict], List[str], List[str]]:
    model_paths: List[Path] = []
    unresolved: List[str] = []
    for candidate in [*_include_candidates(netlist_text), *explicit_model_paths]:
        resolved = _resolve_library_path(candidate, netlist_path.parent)
        if resolved is None:
            unresolved.append(candidate)
        elif resolved not in model_paths:
            model_paths.append(resolved)

    models = _parse_model_cards(netlist_text, str(netlist_path))
    visited = set()
    loaded_paths: List[Path] = []

    def visit(path: Path) -> None:
        resolved = path.resolve()
        if resolved in visited:
            return
        visited.add(resolved)
        loaded_paths.append(resolved)
        content = _read_text(resolved)
        models.update(_parse_model_cards(content, str(resolved)))
        for nested in _include_candidates(content):
            nested_path = _resolve_library_path(nested, resolved.parent)
            if nested_path is not None:
                visit(nested_path)
            else:
                unresolved.append(nested)

    for path in model_paths:
        visit(path)
    return models, [str(path) for path in loaded_paths], unresolved


def _parse_mosfet_instances(netlist_text: str) -> List[dict]:
    instances = []
    for line_number, line in enumerate(netlist_text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("*", ";", "+")):
            continue
        # A semicolon starts an inline SPICE comment in LTspice netlists.
        body = stripped.split(";", 1)[0].strip()
        tokens = body.split()
        if not tokens or not re.match(r"^M\S+$", tokens[0], re.IGNORECASE):
            continue
        if len(tokens) < 6:
            instances.append({
                "name": tokens[0], "line": line_number, "model": None,
                "geometry": {}, "parse_error": "MOSFET line has lessThan 4 nodes and a model name",
            })
            continue

        if len(tokens) < 6:
            instances.append({
                "name": tokens[0], "line": line_number, "model": None,
                "geometry": {}, "parse_error": "MOSFET line has too few tokens",
            })
            continue

        geometry = {}
        for assignment in _PARAMETER_RE.finditer(" ".join(tokens[6:])):
            key = assignment.group("name").lower()
            if key in _GEOMETRY_NAMES:
                geometry[key] = assignment.group("value")

        instances.append({
            "name": tokens[0],
            "line": line_number,
            "model": tokens[5],
            "geometry": geometry,
            "parse_error": None,
        })
    return instances


def _recommended_supply_range(model_texts: Iterable[str]) -> Optional[Tuple[float, float]]]:
    pattern = re.compile(
        r"{(_:|recommended|raccomanded)\s|voltage\s+supply\s+range\s*: "
ˆŠÊËWOÊÎ—
ÊÎ——
Š_—
ÊJWÊ•×ÊŠK_KW8 $ÊWÊˆ‚ˆˆŠÊËWOÊÎ—
ÊÎ——
Š_—
ÊJWÊ•È‹ˆ™K’QÓ“Ô‘PÐTÑKˆ
Bˆ›Üˆ^[ˆ[Ù[Ý^Î‚ˆX]ÚH]\›‹œÙX\˜Ú
^
BˆYˆX]Ú‚ˆÝËYÚH›Ø]
X]Ú™Ü›Ý\
JJK›Ø]
X]Ú™Ü›Ý\
ŠJBˆYˆX]š\Ùš[š]JÝÊH[™X]š\Ùš[š]JYÚ
H[™ÝÈHYÚ‚ˆ™]\›ˆÝËYÚˆ™]\›ˆ›Û™B‚‚™YˆÙ›Ü›X]Ý[J˜[YNˆ›Ø]
HOˆÝŽ‚ˆ™]\›ˆˆžÝ˜[YH
ˆYMŽ‹™ßH[H‚‚‚™YˆÚXÚ×Û™]\ÝÛ[Ù[Êˆ™]\ÝÜ]ˆ
‹ˆ[Ù[Ü]ÎˆÜ[Û˜[ÔÙ\]Y[˜ÙVÜÝ—WHH›Û™KˆÝ\WÝ›ÛYÙ\ÎˆÜ[Û˜[ÑXÝÜÝ‹›Ø]WHH›Û™KŠHOˆXÝ‚ˆˆˆÚXÚÈSÔÑ‘UËÓYØZ[œÝ[Z]È[ˆ™Y™\™[˜ÙY[Ù[Ø\™Ë‚‚ˆH™]\›™Y™\ÜÛÛZ[œÈØ\›š[™ÜÈ˜]\ˆ[ˆ[ÙYžZ[™Èš[\Ëˆ[Ù[ˆ[Z]È\™H™X]Y\ÈXÛ\™Y›Ý[™ÎÈHÚXÚÙ\ˆÙ\È›ÝÛZ[H]ˆHÚ[][][ÛˆÝ]ÚYH[HÚ[˜Z[‚ˆˆˆ‚ˆ]H]
™]\ÝÜ]
K™^[™\Ù\Š
Kœ™\ÛÛ™J
BˆYˆ]œÝY™š^›ÝÙ\Š
HOH‹›™]Ž‚ˆ˜Z\ÙH˜[YQ\œ›ÜŠ›™]\ÝÜ]]\ÝÚ[È[ˆÜXÙH›™]š[HŠBˆYˆ›Ý]š\×Ùš[J
N‚ˆ˜Z\ÙHš[S›Ý›Ý[™\œ›ÜŠ†æWFÆ—7Bæ÷Bf÷VæC¢·F‡Ò" ¢æWFÆ—7E÷FW‡BÒ÷&VE÷FW‡B‡F‚¢W‡Æ–6—E÷F‡2ÒÆ—7B†ÖöFVÅ÷F‡2÷"µÒ¢ÖöFVÇ2Â&W6öÇfVE÷F‡2ÂVç&W6öÇfVE÷F‡2ÒöÆöEöÖöFVÇ2€¢F‚ÂæWFÆ—7E÷FW‡BÂW‡Æ–6—E÷F‡0¢¢–ç7Fæ6W2Ò÷'6UöÖ÷6fWEö–ç7Fæ6W2†æWFÆ—7E÷FW‡B¢v&æ–æw3¢Æ—7E·7G%ÒÒµÐ ¢f÷"Ö—76–ær–âVç&W6öÇfVE÷F‡3 ¢v&æ–æw2æVæB†b$ÖöFVÂÆ–'&'’6÷VÆBæ÷B&R&W6öÇfVC¢¶Ö—76–æwÒ"¢–bæ÷BÖöFVÇ3 ¢v&æ–æw2æVæB‚$æòäÔõ2õÔõ2äÔôDTÂ6&G2vW&Rf÷VæB–âF†RæWFÆ—7B÷"ÆöFVBÆ–'&&–W2" ¢f÷"–ç7Fæ6R–â–ç7Fæ6W3 ¢æÖRÒ–ç7Fæ6U²&æÖR%Ð¢–b–ç7Fæ6U²''6UöW'&÷"%Ó ¢v&æ–æw2æVæB†b'¶æÖWÒBÆ–æR¶–ç7Fæ6U²vÆ–æRu×Ó¢¶–ç7Fæ6U²w'6UöW'&÷"u×Ò"¢6öçF–çVP¢ÖöFVÂÒÖöFVÇ2ævWB†–ç7Fæ6U²&ÖöFVÂ%Òæ66VföÆB‚’¢–bÖöFVÂ—2æöæS ¢v&æ–æw2æVæB€¢b'¶æÖWÓ¢ÖöFVÂw¶–ç7Fæ6U²vÖöFVÂu×Òrv2æ÷Bf÷VæC²rôÂ&ævRv2æ÷B6†V6¶VB ¢¢6öçF–çVP ¢&ÖWFW'2ÒÖöFVÅ²'&ÖWFW'2%Ð ¤€€€™½È•½µ•ÑÉå}¹…µ”°µ¥¹¥µÕµ}­•ä°µ…á¥µÕµ}­•ä¥¸€ (€€€€€€€€€€€€ ‰Üˆ°€‰]5%8ˆ°€‰]5`ˆ¤°(€€€€€€€€€€€€ ‰°ˆ°€‰15%8ˆ°€‰15`ˆ¤°(€€€€€€€€¤è(€€€€€€€€€€€±¥Ñ•É…°€ô¥¹ÍÑ…¹•l‰•½µ•ÑÉä‰t¹•Ð¡•½µ•ÑÉå}¹…µ”¤(€€€€€€€€€€€¥˜±¥Ñ•É…°¥Ì9½¹”è(€€€€€€€€€€€€€€€Ý…É¹¥¹Ì¹…ÁÁ•¹¡˜‰í¹…µ•ôèí•½µ•ÑÉå}¹…µ”¹ÕÁÁ•È ¥ô¥Ì¹½Ð•áÁ±¥¥Ñ±äÍÁ•¥™¥•ìÉ…¹”¡•¬Í­¥ÁÁ•ˆ¤(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€€€€€Ù…±Õ”€ôÁ…ÉÍ•}ÍÁ¥•}¹Õµ‰•È¡±¥Ñ•É…°¤(€€€€€€€€€€€¥˜Ù…±Õ”¥Ì9½¹”è(€€€€€€€€€€€€€€€Ý…É¹¥¹Ì¹…ÁÁ•¹¡˜‰í¹…µ•ôèí•½µ•ÑÉå}¹…µ”¹ÕÁÁ•È ¥ôôí±¥Ñ•É…±ôœ¥Ì¹½Ð„¹Õµ•É¥Œ±¥Ñ•É…°ìÉ…¹”¡•¬Í­¥ÁÁ•ˆ¤(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”((€€€€€€€€€€€µ¥¹¥µÕ´€ôÁ…ÉÍ•}ÍÁ¥•}¹Õµ‰•È¡Á…É…µ•Ñ•ÉÌ¹•Ð¡µ¥¹¥µÕµ}­•ä°€ˆˆ¤¤(€€€€€€€€€€€µ…á¥µÕ´€ôÁ…ÉÍ•}ÍÁ¥•}¹Õµ‰•È¡Á…É…µ•Ñ•ÉÌ¹•Ð¡µ…á¥µÕµ}­•ä°€ˆˆ¤¤(€€€€€€€€€€€¥˜µ¥¹¥µÕ´¥Ì¹½Ð9½¹”…¹Ù…±Õ”€ðµ¥¹¥µÕ´è(€€€€€€€€€€€€€€€Ý…É¹¥¹Ì¹…ÁÁ•¹ (€€€€€€€€€€€€€€€€€€€˜‰í¹…µ•ôèí•½µ•ÑÉå}¹…µ”¹ÕÁÁ•È ¥ôõí}™½Éµ…Ñ}Õ´¡Ù…±Õ”¥ô¥Ì‰•±½Ü€ˆ(€€€€€€€€€€€€€€€€€€€˜‰íµ½‘•±l¹…µ”uô¹íµ¥¹¥µÕµ}­•åôõí}™½Éµ…Ñ}Õ´¡µ¥¹¥µÕ´¥ôˆ(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€¥˜µ…á¥µÕ´¥Ì¹½Ð9½¹”…¹Ù…±Õ”€øµ…á¥µÕ´è(€€€€€€€€€€€€€€€Ý…É¹¥¹Ì¹…ÁÁ•¹ (€€€€€€€€€€€€€€€€€€€˜‰í¹…µ•ôèí•½µ•ÑÉå}¹…µ”¹ÕÁÁ•È ¥ôõí}™½Éµ…Ñ}Õ´¡Ù…±Õ”¥ô•á••‘Ì€ˆ(€€€€€€€€€€€€€€€€€€€˜‰íµ½‘•±l¹…µ”uô¹íµ…á¥µÕµ}­•åôõí}™½Éµ…Ñ}Õ´¡µ…á¥µÕ´¥ôˆ(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€¥˜µ¥¹¥µÕ´¥Ì9½¹”…¹µ…á¥µÕ´¥Ì9½¹”è(€€€€€€€€€€€€€€€Ý…É¹¥¹Ì¹…ÁÁ•¹ (€€€€€€€€€€€€€€€€€€€˜‰í¹…µ•ôèµ½‘•°€íµ½‘•±l¹…µ”uôœ‘½•Ì¹½Ð‘•±…É”íµ¥¹¥µÕµ}­•åô½íµ…á¥µÕµ}­•åôì€ˆ(€€€€€€€€€€€€€€€€€€€˜‰í•½µ•ÑÉå}¹…µ”¹ÕÁÁ•È ¥ôÉ…¹”Ý…Ì¹½Ð¡•­•ˆ(€€€€€€€€€€€€€€€€¤((€€€€€€€™½È‘¥™™ÕÍ¥½¹}Á…É…µ•Ñ•È¥¸€ ‰Áˆ°€‰ÁÌˆ¤è(€€€€€€€€€€€±¥Ñ•É…°€ô¥¹ÍÑ…¹•l‰•½µ•ÑÉä‰t¹•Ð¡‘¥™™ÕÍ¥½¹}Á…É…µ•Ñ•È¤(€€€€€€€€€€€¥˜±¥Ñ•É…°¥Ì9½¹”è(€€€€€€€€€€€€€€€Ý…É¹¥¹Ì¹…ÁÁ•¹ (€€€€€€€€€€€€€€€€€€€˜‰í¹…µ•ôèí‘¥™™ÕÍ¥½¹}Á…É…µ•Ñ•È¹ÕÁÁ•È ¥ô¥Ì¹½Ð•áÁ±¥¥Ñ±äÍÁ•¥™¥•ì€ˆ(€€€€€€€€€€€€€€€€€€€€‰¡•¬Ñ¡”Í¥µÕ±…Ñ½ÈÌ•™™•Ñ¥Ù”Ù…±Õ”…¹Ñ¡”€¹±½œÝ…É¹¥¹œˆ(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€¹Õµ•É¥Œ€ôÁ…ÉÍ•}ÍÁ¥•}¹Õµ‰•È¡±¥Ñ•É…°¤(€€€€€€€€€€€€€€€¥˜¹Õµ•É¥Œ€ôô€Àè(€€€€€€€€€€€€€€€€€€€Ý…É¹¥¹Ì¹…ÁÁ•¹ (€€€€€€€€€€€€€€€€€€€€€€€˜‰í¹…µ•ôèí‘¥™™ÕÍ¥½¹}Á…É…µ•Ñ•È¹ÕÁÁ•È ¥ô¥Ì•áÁ±¥¥Ñ±äé•É¼ì€ˆ(€€€€€€€€€€€€€€€€€€€€€€€€‰Ù•É¥™äÑ¡”¥¹Ñ•¹‘•‘¥™™ÕÍ¥½¸•½µ•ÑÉäˆ(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€•±¥˜¹Õµ•É¥Œ¥Ì9½¹”è(€€€€€€€€€€€€€€€Ý…É¹¥¹Ì¹…ÁÁ•¹ (€€€€€€€€€€€€€€€€€€€˜‰í¹…µ•ôèí‘¥™™ÕÍ¥½¹}Á…É…µ•Ñ•È¹ÕÁÁ•È ¥ôôí±¥Ñ•É…±ôœ¥Ì¹½Ð„¹Õµ•É¥Œ±¥Ñ•É…°ˆ(€€€€€€€€€€€€€€€€€€€€¤((€€€µ½‘•±}Ñ•áÑÌ€ôm}É•…‘}Ñ•áÐ¡A…Ñ ¡¥Ñ•´¤™½È¥Ñ•´¥¸É•Í½±Ù•‘}Á…Ñ¡Ít(€€€µ½‘•±}Ñ•áÑÌ¹…ÁÁ•¹¡¹•Ñ±¥ÍÑ}Ñ•áÐ¤(€€€É•½µµ•¹‘•‘}É…¹”€ô}É•½µµ•¹‘•‘}ÍÕÁÁ±å}É…¹”¡µ½‘•±}Ñ•áÑÌ¤(€€€ÍÕÁÁ±å}¡•­Ì€ômt(€€€™½ÈÍ½ÕÉ•}¹…µ”°Ù½±Ñ…”¥¸€¡ÍÕÁÁ±å}Ù½±Ñ…•Ì½Èíô¤¹¥Ñ•µÌ ¤è(€€€€€€€¹Õµ•É¥}Ù½±Ñ…”€ô™±½…Ð¡Ù½±Ñ…”¤(€€€€€€€¥˜¹½Ðµ…Ñ ¹¥Í™¥¹¥Ñ”¡¹Õµ•É¥}Ù½±Ñ…”¤è(€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È¡˜‰MÕÁÁ±äÙ½±Ñ…”™½ÈíÍ½ÕÉ•}¹…µ•ôµÕÍÐ‰”™¥¹¥Ñ”ˆ¤(€€€€€€€ÍÑ…Ñ”€ô€‰¹½Ñ}¡•­•ˆ(€€€€€€€¥˜É•½µµ•¹‘•‘}É…¹”¥Ì¹½Ð9½¹”è(€€€€€€€€€€€±½Ü°¡¥ €ôÉ•½µµ•¹‘•‘}É…¹”(€€€€€€€€€€€ÍÑ…Ñ”€ô€‰Ý¥Ñ¡¥¹}É…¹”ˆ¥˜±½Ü€ðô¹Õµ•É¥}Ù½±Ñ…”€ðô¡¥ •±Í”€‰½ÕÑÍ¥‘•}É…¹”ˆ(€€€€€€€€€€€¥˜ÍÑ…Ñ”€ôô€‰½ÕÑÍ¥‘•}É…¹”ˆè(€€€€€€€€€€€€€€€Ý…É¹¥¹Ì¹…ÁÁ•¹ (€€€€€€€€€€€€€€€€€€€˜‰íÍ½ÕÉ•}¹…µ•ôõí¹Õµ•É¥}Ù½±Ñ…”éôXˆ€‰¥Ì½ÕÑÍ¥‘”Ñ¡”µ½‘•°½µµ•¹ÐÌ€ˆ(€€€€€€€€€€€€€€€€€€€˜‰É•½µµ•¹‘•ÍÕÁÁ±äÉ…¹”í±½Üéô·ŠMí¡¥ éôXˆ(€€€€€€€€€€€€€€€€¤(€€€€€€€ÍÕÁÁ±å}¡•­Ì¹…ÁÁ•¹¡ì(€€€€€€€€€€€€‰Í½ÕÉ”ˆèÍ½ÕÉ•}¹…µ”°(€€€€€€€€€€€€‰Ù½±Ñ…•}Xˆè¹Õµ•É¥}Ù½±Ñ…”°(€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆèÍÑ…Ñ”°(€€€€€€€ô¤((€€€½ÕÑ}½™}É…¹”€ô…¹ä (€€€€€€€€ˆ¥Ì‰•±½Ü€ˆ¥¸Ý…É¹¥¹œ½È€ˆ•á••‘Ìˆ¥¸Ý…É¹¥¹œ™½ÈÝ…É¹¥¹œ¥¸Ý…É¹¥¹Ì(€€€€¤(€€€É•ÑÕÉ¸ì(€€€€€€€€‰¹•Ñ±¥ÍÑ}Á…Ñ ˆèÍÑÈ¡Á…Ñ ¤°(€€€€€€€€‰µ½‘•±}™¥±•ÌˆèÉ•Í½±Ù•‘}Á…Ñ¡Ì°(€€€€€€€€‰µ½‘•±Ìˆèµ½‘•±Ì°(€€€€€€€€‰¥¹ÍÑ…¹•Ìˆè¥¹ÍÑ…¹•Ì°(€€€€€€€€‰Ý…É¹¥¹ÌˆèÝ…É¹¥¹Ì°(€€€€€€€€‰½ÕÑ}½™}É…¹”ˆè½ÕÑ}½™}É…¹”°(€€€€€€€€‰É•½µµ•¹‘•‘}ÍÕÁÁ±å}É…¹•}XˆèÉ•½µµ•¹‘•‘}É…¹”°(€€€€€€€€‰ÍÕÁÁ±å}¡•­ÌˆèÍÕÁÁ±å}¡•­Ì°(€€€€€€€€‰ÍÕµµ…Éäˆèì(€€€€€€€€€€€€‰µ½Í™•Ñ}½Õ¹Ðˆè±•¸¡¥¹ÍÑ…¹•Ì¤°(€€€€€€€€€€€€‰µ½‘•±}½Õ¹Ðˆè±•¸¡µ½‘•±Ì¤°(€€€€€€€€€€€€‰Ý…É¹¥¹}½Õ¹Ðˆè±•¸¡Ý…É¹¥¹Ì¤°(€€€€€€€ô°(€€€ô(()‘•˜}Á…ÉÍ•}ÍÕÁÁ±å}…ÉÕµ•¹ÑÌ¡¥Ñ•µÌèM•ÅÕ•¹•mÍÑÉt¤€´ø¥ÑmÍÑÈ°™±½…Ñtè(€€€ÍÕÁÁ±¥•Ì€ôíô(€€€™½È¥Ñ•´¥¸¥Ñ•µÌè(€€€€€€€µ…Ñ €ôÉ”¹™Õ±±µ…Ñ  (€€€€€€€€€€€Èˆ ý@ñ¹…µ”ùmµi„µéumµi„µèÀ´åt¨¤ô ý@ñÙ…±Õ”ø¸¬¤ˆ°¥Ñ•´(€€€€€€€€¤(€€€€€€€¥˜µ…Ñ ¥Ì9½¹”è(€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È¡˜‰%¹Ù…±¥€´µÍÕÁÁ±äí¥Ñ•´…Éôì•áÁ•Ñ•YôÄ¸Ôˆ¤(€€€€€€€¹…µ”€ôµ…Ñ ¹É½ÕÀ ‰¹…µ”ˆ¤(€€€€€€€¥˜¹…µ”¥¸ÍÕÁÁ±¥•Ìè(€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È¡˜‰ÕÁ±¥…Ñ”ÍÕÁÁ±äÍ½ÕÉ”èí¹…µ•ôˆ¤(€€€€€€€Ù…±Õ”€ôÁ…ÉÍ•}ÍÁ¥•}¹Õµ‰•È¡µ…Ñ ¹É½ÕÀ ‰Ù…±Õ”ˆ¤¤(€€€€€€€¥˜Ù…±Õ”¥Ì9½¹”è(€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È¡˜‰%¹Ù…±¥¹Õµ•É¥ŒÍÕÁÁ±äÙ½±Ñ…”¥¸€´µÍÕÁÁ±äí¥Ñ•´…Éôˆ¤(€€€€€€€ÍÕÁÁ±¥•Ím¹…µ•t€ôÙ…±Õ”(€€€É•ÑÕÉ¸ÍÕÁÁ±¥•Ì(()‘•˜µ…¥¸¡…ÉØè=ÁÑ¥½¹…±mM•ÅÕ•¹•mÍÑÉut€ô9½¹”¤€´ø¥¹Ðè(€€€Á…ÉÍ•È€ô…ÉÁ…ÉÍ”¹ÉÕµ•¹ÑA…ÉÍ•È (€€€€€€€‘•ÍÉ¥ÁÑ¥½¸ô‰¡•¬5=MP•½µ•ÑÉä……¥¹ÍÐ1QÍÁ¥”µ½‘•°µ…É±¥µ¥ÑÌ€¡É•…µ½¹±ä¤¸ˆ(€€€€¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ‰¹•Ñ±¥ÍÐˆ°¡•±Àô‰1QÍÁ¥”€¹¹•Ð™¥±”Ñ¼¥¹ÍÁ•Ðˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð (€€€€€€€€ˆ´µµ½‘•°ˆ°…Ñ¥½¸ô‰…ÁÁ•¹ˆ°‘•™…Õ±Ðõmt°µ•Ñ…Ù…Èô‰AQ ˆ°(€€€€€€€¡•±Àô‰‘‘¥Ñ¥½¹…°µ½‘•°±¥‰É…ÉäÑ¼¥¹ÍÁ•Ðìµ…ä‰”É•Á•…Ñ•ˆ°(€€€€¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð (€€€€€€€€ˆ´µÍÕÁÁ±äˆ°…Ñ¥½¸ô‰…ÁÁ•¹ˆ°‘•™…Õ±Ðõmt°µ•Ñ…Ù…Èô‰M=UIõY=1Qˆ°(€€€€€€€¡•±Àô‰MÕÁÁ±äÙ½±Ñ…”Ñ¼½µÁ…É”Ý¥Ñ „‘•±…É•É•½µµ•¹‘•É…¹”°”¹œ¸YôÄ¸Ôˆ°(€€€€¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð (€€€€€€€€ˆ´µÍÑÉ¥Ðˆ°…Ñ¥½¸ô‰ÍÑ½É•}ÑÉÕ”ˆ°(€€€€€€€¡•±Àô‰I•ÑÕÉ¸•á¥Ð½‘”€ÈÝ¡•¸„5=MP\½0¥Ì½ÕÑÍ¥‘”„‘•±…É•µ½‘•°‰½Õ¹ˆ°(€€€€¤(€€€…ÉÌ€ôÁ…ÉÍ•È¹Á…ÉÍ•}…ÉÌ¡…ÉØ¤((€€€ÑÉäè(€€€€€€€ÍÕÁÁ±¥•Ì€ô}Á…ÉÍ•}ÍÕÁÁ±å}…ÉÕµ•¹ÑÌ¡…ÉÌ¹ÍÕÁÁ±ä¤(€€€€€€€É•Á½ÉÐ€ô¡•­}¹•Ñ±¥ÍÑ}µ½‘•±Ì (€€€€€€€€€€€…ÉÌ¹¹•Ñ±¥ÍÐ°(€€€€€€€€€€€µ½‘•±}Á…Ñ¡Ìõ…ÉÌ¹µ½‘•°°(€€€€€€€€€€€ÍÕÁÁ±å}Ù½±Ñ…•ÌõÍÕÁÁ±¥•Ì°(€€€€€€€€¤(€€€•á•ÁÐ€¡=MÉÉ½È°Y…±Õ•ÉÉ½È¤…Ì•áŒè(€€€€€€€ÁÉ¥¹Ð¡˜‰5½‘•°¡•¬™…¥±•èí•áôˆ°™¥±”õÍåÌ¹ÍÑ‘•ÉÈ¤(€€€€€€€É•ÑÕÉ¸€Ä((€€€ÁÉ¥¹Ð¡˜‰9•Ñ±¥ÍÐèíÉ•Á½ÉÑl¹•Ñ±¥ÍÑ}Á…Ñ uôˆ¤(€€€¥˜É•Á½ÉÑl‰µ½‘•±}™¥±•Ì‰tè(€€€€€€€ÁÉ¥¹Ð ‰5½‘•°™¥±”¡Ì¤èˆ¤(€€€€€€€™½Èµ½‘•±}™¥±”¥¸É•Á½ÉÑl‰µ½‘•±}™¥±•Ì‰tè(€€€€€€€€€€€ÁÉ¥¹Ð¡˜ˆ€íµ½‘•±}™¥±•ôˆ¤(€€€ÁÉ¥¹Ð (€€€€€€€˜‰½Õ¹íÉ•Á½ÉÑlÍÕµµ…Éäulµ½Í™•Ñ}½Õ¹Ðuô5=MP¥¹ÍÑ…¹”¡Ì¤…¹€ˆ(€€€€€€€˜‰íÉ•Á½ÉÑlÍÕµµ…Éäulµ½‘•±}½Õ¹Ðuô95=L½A5=Lµ½‘•°…É¡Ì¤¸ˆ(€€€€¤(€€€™½Èµ½‘•°¥¸Í½ÉÑ•¡É•Á½ÉÑl‰µ½‘•±Ì‰t¹Ù…±Õ•Ì ¤°­•äõ±…µ‰‘„¥Ñ•´è¥Ñ•µl‰¹…µ”‰t¹…Í•™½± ¤¤è(€€€€€€€Á…É…µ•Ñ•ÉÌ€ôµ½‘•±l‰Á…É…µ•Ñ•ÉÌ‰t(€€€€€€€Ù•ÉÍ¥½¸€ôÁ…É…µ•Ñ•ÉÌ¹•Ð ‰YIM%=8ˆ°€‰¹½Ð‘•±…É•ˆ¤(€€€€€€€Ý¥‘Ñ¡}µ¥¸€ôÁ…ÉÍ•}ÍÁ¥•}¹Õµ‰•È¡Á…É…µ•Ñ•ÉÌ¹•Ð ‰]5%8ˆ°€ˆˆ¤¤(€€€€€€€Ý¥‘Ñ¡}µ…à€ôÁ…ÉÍ•}ÍÁ¥•}¹Õµ‰•È¡Á…É…µ•Ñ•ÉÌ¹•Ð ‰]5`ˆ°€ˆˆ¤¤(€€€€€€€±•¹Ñ¡}µ¥¸€ôÁ…ÉÍ•}ÍÁ¥•}¹Õµ‰•È¡Á…É…µ•Ñ•ÉÌ¹•Ð ‰15%8ˆ°€ˆˆ¤¤(€€€€€€€±•¹Ñ¡}µ…à€ôÁ…ÉÍ•}ÍÁ¥•}¹Õµ‰•È¡Á…É…µ•Ñ•ÉÌ¹•Ð ‰15`ˆ°€ˆˆ¤¤(€€€€€€€Ý¥‘Ñ¡}É…¹”€ô€ (€€€€€€€€€€€˜‰í}™½Éµ…Ñ}Õ´¡Ý¥‘Ñ¡}µ¥¸¥ôƒŠMyí}™½Éµ…Ñ}Õ´¡Ý¥‘Ñ¡}µ…à¥ôˆ(€€€€€€€€€€€¥˜Ý¥‘Ñ¡}µ¥¸¥Ì¹½Ð9½¹”…¹Ý¥‘Ñ¡}µ…à¥Ì¹½Ð9½¹”•±Í”€‰¹½Ð™Õ±±ä‘•±…É•ˆ(€€€€€€€€¤(€€€€€€€±•¹Ñ¡}É…¹”€ô€ (€€€€€€€€€€€˜‰í}™½Éµ…Ñ}Õ´¡±•¹Ñ¡}µ¥¸¥ôƒŠMyí}™½Éµ…Ñ}Õ´¡±•¹Ñ¡}µ…à¥ôˆ(€€€€€€€€€€€¥˜±•¹Ñ¡}µ¥¸¥Ì¹½Ð9½¹”…¹±•¹Ñ¡}µ…à¥Ì¹½Ð9½¹”•±Í”€‰¹½Ð™Õ±±ä‘•±…É•ˆ(€€€€€€€€¤(€€€€€€€ÁÉ¥¹Ð (€€€€€€€€€€€˜ˆ€íµ½‘•±l¹…µ”uô€¡íµ½‘•±l­¥¹œt¹ÕÁÁ•È ¥ô¤èYIM%=8õíÙ•ÉÍ¥½¹ô°€ˆ(€€€€€€€€€€€˜‰\õíÝ¥‘Ñ¡}É…¹•ô°0õí±•¹Ñ¡}É…¹•ôˆ(€€€€€€€€¤(€€€¥˜É•Á½ÉÑl‰É•½µµ•¹‘•‘}ÍÕÁÁ±å}É…¹•}X‰t¥Ì¹½Ð9½¹”è(€€€€€€€±½Ü°¡¥ €ôÉ•Á½ÉÑl‰É•½µµ•¹‘•‘}ÍÕÁÁ±å}É…¹•}X‰t(€€€€€€€ÁÉ¥¹Ð¡˜‰5½‘•°½µµ•¹ÐÍÕÁÁ±äÉ…¹”èí±½Üé÷ŠMí¡¥ éôXˆ¤(€€€™½È¥Ñ•´¥¸É•Á½ÉÑl‰ÍÕÁÁ±å}¡•­Ì‰tè(€€€€€€€ÁÉ¥¹Ð (€€€€€€€€€€€˜‰MÕÁÁ±äí¥Ñ•µl‰Í½ÕÉ”‰uôõí¥Ñ•µlÙ½±Ñ…•}XtèôXè€ˆ(€€€€€€€€€€€˜‰í¥Ñ•µlÍÑ…ÑÕÌt¹É•Á±…” |œ°€œ€œ¥ôˆ(€€€€€€€€¤((€€€¥˜É•Á½ÉÑl"warnings"]:
        print("\nWarnings:")
        for warning in report["warnings"]:
            print(f"  - {warning}")
    else:
        print("No model-range or geometry warnings found.")

    print(f"\nWarning count: {report['summary']['warning_count']}")
    if args.strict and report["out_of_range"]:
        print("Strict check failed: one or more W/L values are outside declared model bounds.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
