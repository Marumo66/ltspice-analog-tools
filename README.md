# ltspice-analog-tools

A few Python tools I wrote while working with LTspice simulations.

## Current capabilities

- Read binary `real forward` transient RAW files and export them to CSV.
- Read binary `complex forward` AC RAW files and export the real and imaginary parts.
- Calculate transient voltage slopes and average slew rate between the 20% and 80% levels.
- Calculate loop gain, UGBW and phase margin from an AC sweep.
- Parse BSIM3 MOSFET operating-point tables from LTspice SPICE Output Log files and export one row per device to CSV.

The RAW readers have been tested with LTspice 26.0.2 on macOS. They only handle the binary layouts described above; other RAW formats are not supported yet.

The AC metrics helper uses the return ratio `-V(out) / V(nfb)` and reports the first descending 0 dB crossing. The interpolation is done on a log-frequency axis. The input CSV must contain the corresponding output and feedback traces.

The DC parser currently targets the `Semiconductor Device Operating Points` section for BSIM3 MOSFETs. It preserves signed values as reported by LTspice and retains simulator warnings and convergence information. The current parser does not yet extract the separate node-voltage / independent-source-current operating-point listing, so total supply power is not calculated by this version.

## Run the tests

Python 3.9 or newer is enough. There are no third-party dependencies.

From the project folder:

```bash
python3 -m unittest discover -s tests -v
```

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

For the AC example, the testbench needs to provide the return-ratio traces used by the calculation. The helper does not infer a loop gain from an arbitrary circuit.

## DC operating-point example

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

Parsed device parameters are in the SI units used by LTspice (for example, current in A, voltage in V, transconductance in S, and capacitance in F). The parser reports convergence through Gmin stepping as converged, while retaining the fact that direct Newton iteration failed.

The Phase 4 fixtures are regression data from one two-stage CMOS op-amp simulation. They are used for tests, not as a complete standalone LTspice project. The AC fixture includes the 901-point sweep. The DC fixture contains the simulator log's BSIM3 device operating-point section; machine-specific file paths have been removed.
