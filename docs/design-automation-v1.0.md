# v1.0 Design Automation

This first design-automation release adds a finite grid-search optimizer on top of the existing matched AC/DC sweep. It does not replace LTspice, infer a circuit topology, or search continuously.

## What it does

Given a JSON configuration, the optimizer:

1. Runs the existing matched AC and operating-point sweep for every supplied W or L value.
2. Evaluates numeric result constraints using inclusive minimum and maximum limits.
3. Selects the feasible candidate that best satisfies the requested single objective.
4. Writes the sweep CSV, a human-readable Markdown report, and a machine-readable JSON report.

The original source netlist is not edited. The unified sweep writes separate candidate AC and operating-point netlists beside the source.

## Quick start

Copy examples/design_optimization.example.json and edit source_netlist to point to your AC .net file. Replace the example limits with the actual requirements for your circuit.

Run from the repository root:

~~~bash
python3 -m src.design_optimizer examples/design_optimization.example.json
~~~

The configuration must identify:
- source_netlist: an LTspice netlist containing exactly one active .ac directive.
- devices: one or more MOSFET instance names. All selected devices receive the same swept value.
- parameter: w or l.
- values: a finite list of SPICE numeric literals such as 6u, 7u, 8u.
- supply_voltages: explicit voltage-source names and signed positive-to-negative DC voltages, used for delivered-power calculation.
- constraints: result fields with an inclusive min, max, or both.
- objective: one result field and a direction (maximize or minimize).

Optional settings are output_csv, report_directory, timeout, output_signal and feedback_signal. The CSV output must remain in the source netlist's directory because the unified sweep keeps generated simulation files beside the source.

## Example configuration

The checked-in example uses the current CMOS op-amp exploration:

~~~json
{
  "source_netlist": "/absolute/path/to/two_stage_opamp_CC1P_acSweep_inrange.net",
  "devices": ["M1", "M2"],
  "parameter": "w",
  "values": ["6u", "7u", "8u", "9u", "10u"],
  "supply_voltages": {"VDD": 1.5},
  "constraints": {
    "phase_margin_deg": {"min": 46.0},
    "ugbw_Hz": {"min": 13000000},
    "selected_supplies_power_delivered_uW": {"max": 30.0}
  },
  "objective": {
    "metric": "ugbw_Hz",
    "direction": "maximize"
  }
}
~~~

The numerical limits above are illustrative project filters, not TUM course specifications. In particular, change the power ceiling and bandwidth target to the actual design requirements before using the configuration for another circuit.

## Available result fields

Constraints and objectives refer to fields written by src.parameter_sweep_unified to the summary CSV. The common scalar fields include:

| Field | Meaning |
|---|---|
| low_frequency_gain_dB | Low-frequency loop gain |
| ugbw_Hz | First descending 0 dB crossing frequency |
| phase_margin_deg | Phase margin at that crossing |
| selected_supplies_power_delivered_uW | Net power delivered by explicitly selected voltage sources |
| V(out)_V, V(n1)_V, V(n2)_V, V(tail)_V | Operating-point node voltages when present |
| M1_Id, M1_Gm, M1_Vgs, etc. | Operating-point values for each selected MOSFET |

A field must exist in every candidate result to be used as a constraint or objective. Unknown or unavailable fields cause a clear error rather than silently passing a candidate.

## Output files

For a source named two_stage_opamp_CC1P_acSweep_inrange.net, the default outputs are:

- two_stage_opamp_CC1P_acSweep_inrange_optimization_sweep.csv beside the source netlist.
- design_reports/two_stage_opamp_CC1P_acSweep_inrange_design_optimization.md.
- design_reports/two_stage_opamp_CC1P_acSweep_inrange_design_optimization.json.

Existing outputs are not overwritten. Choose new output names or move the old run artifacts before repeating a run.

The Markdown report records the search settings, constraints, candidate comparison, chosen feasible result (if any), and scope limitations. The JSON report retains the structured metrics and pass/fail checks for downstream processing.

## Search semantics and limitations

- This is a finite, one-dimensional grid search over values supplied by the user. It does not generate new W/L values or refine the grid adaptively.
- One geometry parameter is changed per run. Multiple selected devices share the same W or L value.
- One scalar objective is optimized at a time. Constraints can include multiple result fields.
- Constraint boundaries are inclusive: a value equal to min or max passes.
- If no candidate satisfies every constraint, a report is still written and no candidate is selected. The CLI exits with status code 2 in this case.
- A selected candidate is only the best among the supplied grid points. It is not a proof of a continuous/global optimum or of compliance with specifications not entered in the configuration.
- Simulator warnings, model compatibility and convergence information remain in the sweep logs and CSV. A candidate must still be reviewed by the engineer.
- The optimizer requires a working LTspice installation and the same model files as the source netlist. Unit tests mock simulation runs; they do not replace a real LTspice smoke test.

## Validation

Run the full Python regression suite from the repository root:

~~~bash
python3 -m unittest discover -s tests -v
~~~

For a real smoke test, run the example configuration against a local copy of the in-range AC netlist and inspect all three output artifacts plus the LTspice logs.
