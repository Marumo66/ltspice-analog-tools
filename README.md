# ltspice-analog-tools

A few Python tools I wrote while working with LTspice transient simulations.

For now, it can:

- read binary `real forward` RAW files and export the signals to CSV;
- calculate the largest adjacent-sample voltage slopes;
- calculate average slew rate between the 20% and 80% levels, with linear interpolation between samples.

The RAW reader has been tested with transient files from LTspice 26.0.2 on macOS. It does not handle complex AC data or other RAW layouts yet.

## Run the tests

Python 3.9 or newer is enough. There are no third-party dependencies.

From the project folder:

```bash
python3 -m unittest discover -s tests -v
```

## Example

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

`v_low` and `v_high` are the endpoints of the output swing being measured. The function uses 20% and 80% of that range; it does not guess the levels from the waveform.

The Phase 4 test fixture contains only the samples around the four threshold crossings from one two-stage op-amp simulation. It keeps the regression test small; it is not the full waveform.
