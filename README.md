# ltspice-analog-tools

A few Python tools I wrote while working with LTspice simulations.

For now, it can:

- read binary `real forward` transient RAW files and export them to CSV;
- read binary `complex forward` AC RAW files and export the real and imaginary parts;
- calculate transient voltage slopes and average slew rate between the 20% and 80% levels;
- calculate loop gain, UGBW and phase margin from an AC sweep.

The RAW readers have been tested with LTspice 26.0.2 on macOS. They only handle the binary layouts described above; other RAW formats are not supported yet.

The AC metrics helper uses the return ratio `-V(out) / V(nfb)` and reports the first descending 0 dB crossing. The interpolation is done on a log-frequency axis. The input CSV must contain the corresponding output and feedback traces.

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

The Phase 4 fixtures are small samples from one two-stage CMOS op-amp simulation. The AC fixture keeps selected sweep points, including the points around the 0 dB crossing. They are used for regression tests, not as full simulation results.
