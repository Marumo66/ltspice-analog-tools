# Guided Demo — from an LTspice netlist to a design report

This walkthrough demonstrates the v1.0.0 workflow using your own LTspice design. It is intended to take a few minutes once a compatible AC netlist and its model files are ready.

> **Before you start:** this repository does not ship a circuit schematic, `.net` file or model library. You must supply a netlist generated from your own LTspice project and keep its referenced model files available. The checked-in JSON file is a configuration template, not a self-contained simulation.

## 1. Check the environment

Required:

- Python 3.9 or newer.
- LTspice installed and able to run batch simulations.
- An LTspice AC `.net` netlist containing exactly one active `.ac` directive.
- The model files referenced by that netlist.

Clone the repository and run its tests:

~~~bash
git clone https://github.com/Marumo66/ltspice-analog-tools.git
cd ltspice-analog-tools
python3 -m unittest discover -s tests -v
~~~

The unit tests do not require LTspice; they check the Python logic and supported file parsing. A real optimization run does require a working LTspice installation.

## 2. Prepare a netlist

Export or generate the `.net` netlist for your AC testbench in LTspice. Make a separate working copy beside the original so relative `.include` and model-library paths are more likely to remain valid. Use a fresh filename for each run because generated outputs are not overwritten.

Example only — replace this path with your own file:

~~~bash
cp "/path/to/my_opamp_ac.net" "/path/to/my_opamp_ac_demo.net"
~~~

Run the read-only model checker as a separate preflight step:

~~~bash
python3 -m src.model_checker "/path/to/my_opamp_ac_demo.net" --supply VDD=1.5
~~~

Replace `VDD=1.5` with the actual voltage-source name and its signed positive-to-negative DC voltage. Review any geometry or model/library warnings; the checker reports issues but does not edit the netlist or model files.

## 3. Configure the sweep

Copy the example configuration:

~~~bash
cp examples/design_optimization.example.json examples/my_design.json
~~~

Edit `examples/my_design.json`. At minimum, configure these fields for your circuit:

| Field | What to enter |
|---|---|
| `source_netlist` | Absolute path to your AC `.net` file |
| `devices` | MOSFET instance names as they appear in the netlist, e.g. `M1`, `M2` |
| `parameter` | `w` or `l` |
| `values` | Finite list of SPICE numeric values to test, e.g. `6u`, `7u`, `8u` |
| `supply_voltages` | Explicit voltage-source names and signed positive-to-negative DC voltages |
| `constraints` | Numeric result fields with inclusive `min` and/or `max` limits |
| `objective` | One available result field and `maximize` or `minimize` |

The checked-in example uses the project's earlier CMOS op-amp exploration. Its PM, UGBW and power thresholds are illustrative filters, **not** universal targets or TUM course specifications. Replace them with the requirements appropriate to your design.

Important configuration rules:

- Every selected MOSFET receives the same swept W or L value in a run.
- One geometry parameter is varied per optimization run.
- The objective and constraint fields must exist in the unified sweep results for every candidate.
- The source must contain exactly one active `.ac` directive.
- Provide the correct supply voltage sign and value; power is calculated only for the sources explicitly listed.

## 4. Run the optimizer

From the repository root:

~~~bash
python3 -m src.design_optimizer examples/my_design.json
~~~

For each supplied value, the tool creates separate candidate AC and Operating Point netlists, runs LTspice in batch mode, parses the resulting data, evaluates the constraints and ranks feasible candidates. The original source netlist is not modified.

## 5. Inspect the outputs

For a source named `my_opamp_ac_demo.net`, the default outputs are:

- `my_opamp_ac_demo_optimization_sweep.csv` beside the source netlist.
- `design_reports/my_opamp_ac_demo_design_optimization.md`.
- `design_reports/my_opamp_ac_demo_design_optimization.json`.

Use the CSV for candidate-by-candidate analysis, the Markdown report for a readable decision summary, and the JSON file for structured results. Also inspect the LTspice logs: a candidate being selected does not remove simulator warnings or prove that the circuit meets specifications beyond the constraints you entered.

## 6. What a real v1.0.0 run looked like

The project was exercised on macOS with LTspice 26.0.2 using a two-stage CMOS op-amp testbench and its course-provided model library. M1 and M2 were swept together from 6 µm to 10 µm in 1 µm steps.

| Item | Recorded result |
|---|---:|
| Candidate points | 5 |
| LTspice simulations | 10 (one AC + one Operating Point per point) |
| Feasible candidates | 3 |
| Selected M1/M2 width | **9 µm** |
| UGBW | 13.4876 MHz |
| Phase margin | 46.256° |
| Selected VDD source power delivered | 27.2773 µW |

Configured constraints for that run were PM ≥ 46°, UGBW ≥ 13 MHz and selected VDD power ≤ 30 µW. These were project screening filters, not official course specifications. The 9 µm point was the best feasible point among the five values tested, not a proof of a continuous or global optimum.

The recorded logs showed Direct Newton failure followed by successful Gmin stepping. The course model's VERSION and PD/PS warnings were retained rather than silently edited. See [the complete experiment record](in-range-sweep-summary.md).

## Troubleshooting

- **LTspice executable not found:** set `LTSPICE_EXECUTABLE` to the executable in your installed LTspice application. On macOS, a common path is `/Applications/LTspice.app/Contents/MacOS/LTspice`.
- **A model/include file cannot be resolved:** check the `.include`, `.inc` and `.lib` paths in the source netlist. Keep relative model files in their expected locations.
- **The optimizer rejects a metric name:** use a numeric field present in every candidate row of the unified sweep CSV.
- **An output already exists:** choose a fresh source filename or move the previous run outputs; the tools intentionally do not overwrite existing artifacts.
- **No candidate is feasible:** review the limits, parameter grid, simulation logs and available metrics. The tool reports that no candidate was selected rather than relaxing constraints automatically.

## Supported scope

This demo covers the current finite, one-dimensional W/L grid-search workflow. The tool does not generate a schematic, invent design limits, repair model warnings, adaptively refine the search grid or certify silicon-level performance.
