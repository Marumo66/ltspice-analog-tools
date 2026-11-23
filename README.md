# ltspice-analog-tools

**Release target: 0.2.0 — AC/DC Analysis**

A lightweight Python toolkit for extracting and analysing simulation results produced by LTspice.

## Current capabilities

- Read binary `real forward` transient RAW files and export them to CSV.
- Read binary `complex forward` AC RAW files and export real and imaginary trace components.
- Calculate transient voltage slopes and average slew rate between the 20% and 80% levels.
- Calculate loop gain, UGBW and phase margin from AC sweep data.
- Parse BSIM3 MOSFET operating-point tables from LTspice SPICE Output Log files.
- Parse real-valued, single-point Operating Point RAW files to extract node voltages, component currents and MOSFET terminal currents.
- Calculate quiescent power for explicitly selected supply voltage sources.

## Supported input scope

| Input | Supported format | Output |
|---|---|---|
| Transient RAW | Binary, real, forward | CSV |
| AC RAW | Binary, complex, forward | CSV with real/imaginary columns |
| DC log | BSIM3 MOSFET Semiconductor Device Operating Points section | Per-device CSV and convergence metadata |
| DC RAW | Binary, real, one-point Operating Point file | Node voltages, component/device currents and variable CSV |

The RAW layouts have been validated with LTspice 26.0.2 on macOS. Other RAW layouts, compressed/native alternatives, multi-point Operating Point files and untested LTspice versions are not claimed as supported.

## AC analysis assumptions

The AC metrics helper uses the return ratio `-V(out) / V(nfb)` and reports the first descending 0 dB crossing. Interpolation is performed on a log-frequency axis. The input CSV must contain the corresponding output and feedback traces. The helper does not infer a loop gain from an arbitrary circuit or testbench.

## DC analysis assumptions and limitations

The DC log parser targets the `Semiconductor Device Operating Points` section for BSIM3 MOSFETs. It preserves signed values as reported by LTspice and retains simulator warnings and convergence information.

The DC RAW parser supports the real-valued, one-point Operating Point layout validated against the supplied LTspice 26.0.2 macOS fixture. The first variable is read as a little-endian double and subsequent variables as 32-bit floats, matching that verified file layout. This is not a claim of support for every LTspice RAW variant.

Quiescent power is calculated only for voltage sources explicitly named by the caller. The caller must supply each source's signed positive-to-negative DC voltage. LTspice voltage-source current is positive into the source's positive terminal, so power delivered by a source is `-(V_source * I_source)`. This avoids guessing which independent sources should count as supply rails. The reported total is the net power delivered by the selected sources; it is not automatically the total dissipation of an arbitrary circuit if relevant supplies are omitted.

## Requirements and tests

Python 3.9 or newer is required. The toolkit uses only the Python standard library; there are no third-party runtime dependencies.

Run the complete test suite from the repository root:

```bash
python3 -m unittest discover -s tests -v
```

The project regression suite contains 28 tests. The current macOS working-copy run reported 28 passing tests.

## Transient example

```python
from src.raw_parser import export_raw_to_csv
from src.metrics import calculate_average_slew_rate

export_raw_to_csv("opamp_transient.raw", "opamp_transient.csv")

result = calculate_average_slew_rate(
    "opamp_transient.csv",
    v_low=0.25,
    v_high=0.75,
    direction="rising",
    signal="V(out)",
)

print(result["average_slew_rate_V_per_us"])
```

## AC example

```python
from src.ac_parser import export_ac_raw_to_csv
from src.ac_metrics import calculate_loop_gain_metrics

export_ac_raw_to_csv("opamp_ac.raw", "opamp_ac.csv")
result = calculate_loop_gain_metrics("opamp_ac.csv")

print(result["ugbw_Hz"])
print(result["phase_margin_deg"])
```

## DC log example

```python
from src.dc_parser import (
    parse_dc_operating_point_log,
    export_dc_operating_points_to_csv,
)

result = parse_dc_operating_point_log("opamp_dc.log")
print(result["devices"]["M1"]["Gm"])
print(result["metadata"]["convergence_method"])

export_dc_operating_points_to_csv(
    "opamp_dc.log",
    "results/opamp_dc_operating_points.csv",
)
```

## DC RAW and power example

```python
from src.dc_raw_parser import (
    parse_dc_operating_point_raw,
    export_dc_operating_point_raw_to_csv,
    calculate_quiescent_power,
)

result = parse_dc_operating_point_raw("opamp_dc.raw")
print(result["node_voltages"]["V(out)"])
print(result["component_currents"]["I(VDD)"])

export_dc_operating_point_raw_to_csv(
    "opamp_dc.raw",
    "results/opamp_dc_variables.csv",
)

power = calculate_quiescent_power(
    "opamp_dc.raw",
    supply_voltages={"VDD": 1.5},
)
print(power["total_power_delivered_uW"])
```

## Regression fixtures

The `tests/fixtures` directory contains regression data from a two-stage CMOS op-amp example. These fixtures support parser and metric regression tests; they are not a complete standalone LTspice project and do not include the external 130 nm model library.


## v1.0 development: LTspice batch runner

The initial runner is in `src/ltspice_runner.py`. It accepts an existing
`.net` file and checks that fresh, non-empty `.raw` and `.log` files were
written next to the netlist. On native macOS it invokes LTspice with
`-b <file.net>` and redirects stderr to stdout; Windows/Wine uses
`-b -Run <file.net>`.

Example:

```bash
python3 -m src.ltspice_runner /path/to/opamp.net
```

If LTspice is not found automatically, set `LTSPICE_EXECUTABLE` to the
executable inside `LTspice.app` on macOS or to `LTspice.exe` on Windows:

```bash
export LTSPICE_EXECUTABLE="/Applications/LTspice.app/Contents/MacOS/LTspice"
python3 -m src.ltspice_runner /path/to/opamp.net
```

This first version runs an existing netlist only. It does not generate a
netlist from an `.asc` schematic, edit parameters, or perform parameter
sweeps. The automated tests use a mocked simulator process; batch invocation
must still be verified with the LTspice installation and operating system
used for a real simulation.
