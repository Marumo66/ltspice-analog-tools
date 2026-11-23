"""Run LTspice netlists in batch mode and verify the generated result files.

The runner deliberately accepts a SPICE netlist (.net), not a schematic
(.asc). Netlisting and safe parameter editing are separate steps in the
v1.0 workflow.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple


class LTspiceRunnerError(RuntimeError):
    """Raised when LTspice cannot be started or does not produce outputs."""


@dataclass(frozen=True)
class SimulationResult:
    """Files and process information returned by a successful simulation."""

    netlist_path: Path
    raw_path: Path
    log_path: Path
    command: Tuple[str, ...]
    return_code: int
    stdout: str
    stderr: str


def _file_signature(path: Path):
    """Return a comparable file signature, or None when the file is absent."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _candidate_executables():
    """Yield common LTspice executable locations for the current platform."""
    system = platform.system()

    configured = os.environ.get("LTSPICE_EXECUTABLE")
    if configured and configured.strip():
        yield configured.strip()

    if system == "Darwin":
        app_locations = [
            Path("/Applications/LTspice.app"),
            Path("/Applications/ADI/LTspice.app"),
            Path.home() / "Applications" / "LTspice.app",
            Path.home() / "Applications" / "ADI" / "LTspice.app",
        ]
        for app in app_locations:
            yield str(app / "Contents" / "MacOS" / "LTspice")

        # Spotlight also finds LTspice when the user installed it elsewhere.
        mdfind = shutil.which("mdfind")
        if mdfind:
            try:
                result = subprocess.run(
                    [mdfind, "kMDItemFSName == 'LTspice.app'"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if result.returncode == 0:
                    for app_path in result.stdout.splitlines():
                        app_path = app_path.strip()
                        if app_path:
                            yield str(Path(app_path) / "Contents" / "MacOS" / "LTspice")
            except (OSError, subprocess.TimeoutExpired):
                pass

    elif system == "Windows":
        local_app_data = os.environ.get("LOCALAPPDATA")
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        if local_app_data:
            yield str(Path(local_app_data) / "Programs" / "ADI" / "LTspice" / "LTspice.exe")
        yield str(Path(program_files) / "ADI" / "LTspice" / "LTspice.exe")
        yield "LTspice.exe"

    else:
        yield "ltspice"


def find_ltspice_executable() -> Path:
    """Find the LTspice executable or raise an actionable error.

    Set LTSPICE_EXECUTABLE to override automatic discovery.
    """
    checked = []
    for candidate in _candidate_executables():
        candidate_path = Path(candidate).expanduser()
        checked.append(str(candidate_path))
        if candidate_path.is_file():
            return candidate_path.resolve()

        located = shutil.which(candidate)
        if located:
            return Path(located).resolve()

    details = "\n".join(f"  - {item}" for item in checked)
    raise LTspiceRunnerError(
        "Could not locate the LTspice executable. Set the LTSPICE_EXECUTABLE "
        "environment variable to the executable inside LTspice.app (macOS) "
        "or to LTspice.exe (Windows). Checked:\n" + details
    )


class LTspiceRunner:
    """Run a saved LTspice SPICE netlist using LTspice batch mode."""

    def __init__(self, executable: Optional[os.PathLike] = None, timeout: float = 180.0):
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        self.executable = (
            Path(executable).expanduser().resolve()
            if executable is not None
            else find_ltspice_executable()
        )
        if not self.executable.is_file():
            raise FileNotFoundError(f"LTspice executable not found: {self.executable}")
        self.timeout = float(timeout)

    def build_command(self, netlist_path: os.PathLike) -> Tuple[str, ...]:
        """Build the LTspice batch command for a .net input file."""
        netlist = Path(netlist_path).expanduser().resolve()
        if netlist.suffix.lower() != ".net":
            raise ValueError("LTspiceRunner currently accepts .net files only")
        if not netlist.is_file():
            raise FileNotFoundError(f"Netlist not found: {netlist}")
        if platform.system() == "Darwin":
            # Native LTspice for macOS batch-runs an existing .net file with -b.
            return (str(self.executable), "-b", str(netlist))
        # Windows / Wine LTspice uses -Run to start the simulation.
        return (str(self.executable), "-b", "-Run", str(netlist))

    def run_netlist(self, netlist_path: os.PathLike) -> SimulationResult:
        """Run a netlist and require fresh, non-empty .raw and .log outputs.

        Output files are expected beside the input .net file. Existing output
        files are not deleted; their timestamps and sizes are checked to ensure
        that this invocation updated them.
        """
        netlist = Path(netlist_path).expanduser().resolve()
        command = self.build_command(netlist)
        raw_path = netlist.with_suffix(".raw")
        log_path = netlist.with_suffix(".log")
        previous = {
            raw_path: _file_signature(raw_path),
            log_path: _file_signature(log_path),
        }

        try:
            run_kwargs = {
                "cwd": str(netlist.parent),
                "timeout": self.timeout,
                "check": False,
            }
            if platform.system() == "Darwin":
                # LTspice 26.0.2 for macOS can crash on exit if stdout and
                # stderr use separate file descriptors. This is equivalent
                # to redirecting stderr to stdout in the launching shell.
                run_kwargs["stderr"] = subprocess.STDOUT
            else:
                run_kwargs.update({
                    "capture_output": True,
                    "text": True,
                    "errors": "replace",
                })

            completed = subprocess.run(list(command), **run_kwargs)
        except subprocess.TimeoutExpired as exc:
            raise LTspiceRunnerError(
                f"LTspice did not finish within {self.timeout:g} seconds: {netlist}"
            ) from exc
        except OSError as exc:
            raise LTspiceRunnerError(
                f"Could not start LTspice executable {self.executable}: {exc}"
            ) from exc

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if completed.returncode != 0:
            raise LTspiceRunnerError(
                "LTspice returned a non-zero exit code "
                f"({completed.returncode}).\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
            )

        missing_or_stale = []
        for output_path in (raw_path, log_path):
            current = _file_signature(output_path)
            if current is None:
                missing_or_stale.append(f"missing: {output_path.name}")
            elif current[1] <= 0:
                missing_or_stale.append(f"empty: {output_path.name}")
            elif current == previous[output_path]:
                missing_or_stale.append(f"not updated: {output_path.name}")

        if missing_or_stale:
            raise LTspiceRunnerError(
                "LTspice process exited, but expected fresh output files were not "
                "found (the process exit code alone is not treated as proof of a "
                "successful simulation): " + "; ".join(missing_or_stale)
                + f"\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
            )

        return SimulationResult(
            netlist_path=netlist,
            raw_path=raw_path,
            log_path=log_path,
            command=command,
            return_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run an LTspice .net file in batch mode and verify its outputs."
    )
    parser.add_argument("netlist", help="Path to the LTspice .net netlist")
    parser.add_argument(
        "--executable",
        help="LTspice executable path (overrides LTSPICE_EXECUTABLE and auto-detection)",
    )
    parser.add_argument("--timeout", type=float, default=180.0, help="Timeout in seconds")
    args = parser.parse_args(argv)

    try:
        runner = LTspiceRunner(args.executable, timeout=args.timeout)
        result = runner.run_netlist(args.netlist)
    except (LTspiceRunnerError, FileNotFoundError, ValueError) as exc:
        print(f"LTspice run failed: {exc}", file=sys.stderr)
        return 1

    print("LTspice simulation completed.")
    print(f"RAW: {result.raw_path}")
    print(f"LOG: {result.log_path}")
    print(f"Exit code: {result.return_code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
