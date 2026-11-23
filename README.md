# ltspice-analog-tools

[![Python tests](https://github.com/Marumo66/ltspice-analog-tools/actions/workflows/python-tests.yml/badge.svg?branch=main)](https://github.com/Marumo66/ltspice-analog-tools/actions/workflows/python-tests.yml)

**Version: 1.0.0-rc.1**

Python tools for running LTspice netlists, sweeping MOSFET geometry, analysing AC/DC results, checking model declarations and selecting a design from user-defined constraints.

## Current capabilities

- Parse binary transient RAW files and export voltage/current traces to CSV.
- Calculate average slew rate over a selected 20%–80% transition interval.
- Parse binary AC RAW files and export complex traces to CSV.
- Calculate loop gain, unity-gain bandwidth (UGBW) and phase margin.
- Parse BSIM3 MOSFET operating-point data from LTspice log files.
- Parse supported single-point Operating Point RAW files for node voltages and currents.
- Calculate quiescent power for explicitly selected voltage sources.
- Run LTspice in batch mode on an existing `.net` netlist.
- Create copied netlist variants with selected MOSFET W/L values changed.
- Run DC, AC and matched AC/DC geometry sweeps and collect results in CSV files.
- Check MOSFET model-declared geometry limits and selected model/library warnings without modifying the input files.
- Run a finite parameter grid, apply performance constraints, rank feasible candidates and generate Markdown/JSON reports.

## Requirements

- Python 3.9 or newer.
- LTspice installed for real batch simulations and optimization runs.
- No third-party Python runtime dependencies; the tools use the Python standard library.

Clone the repository and run commands from the repository root. No package installation step is required.

## Run the tests

~~~bash
python3 -m unittest discover -s tests -v
~~~

The current regression suite contains **109 tests**. The project owner has run all 109 successfully on macOS. GitHub Actions also runs the suite on the Python versions configured in `.github/workflows/python-tests.yml`.

## Design optimization quick start

The optimizer reuses the matched AC/DC sweep runner. It evaluates only the W or L values listed in a JSON configuration.

1. Copy the example configuration:

~~~bash
cp examples/design_optimization.example.json examples/my_design.json
~~~

2. Edit `examples/my_design.json` and set `source_netlist` to the absolute path of your LTspice AC `.net` file. Replace the illustrative constraints with the requirements for your circuit.

3. Run the optimizer:

~~~bash
python3 -m src.design_optimizer examples/my_design.json
~~~

The source netlist must contain exactly one active `.ac` directive. The optimizer writes separate AC and Operating Point netlist variants for each candidate; the original source netlist is not modified.

### Example configuration

~~~json
{
  "source_netlist": "/absolute/path/to/opamp_ac.net",
  "devices": ["M1", "M2"],
  "parameter": "w",
  "values": ["6u", "7u", "8u", "9u", "10u"],
  "supply_voltages": {
    "VDD": 1.5
  },
  "constraints": {
    "phase_margin_deg": {
      "min": 46.0
    },
    "ugbw_Hz": {
      "min": 13000000
    },
    "selected_supplies_power_delivered_uW": {
      "max": 30.0
    }
  },
  "objective": {
    "metric": "ugbw_Hz",
    "direction": "maximize"
  },
  "report_directory": "design_reports",
  "timeout": 180
}
~~~

Constraint keys must match numeric fields produced by the unified sweep CSV. Bounds are inclusive. The objective must identify one available result field and use `maximize` or `minimize`.

### Optimization outputs

For a source named `opamp_ac.net`, the default outputs are:

- `opamp_ac_optimization_sweep.csv` beside the source netlist.
- `design_reports/opamp_ac_design_optimization.md`.
- `design_reports/opamp_ac_design_optimization.json`.

The report directory is relative to the source netlist unless an absolute path is supplied. The CSV must remain in the source netlist directory so the simulator variants and their relative model-library references stay together. Existing output files are not overwritten.

Full configuration fields, result metrics and search semantics are described in [docs/design-automation-v1.0.md](docs/design-automation-v1.0.md).

## Sweep commands

### Matched AC/DC sweep

~~~bash
python3 -m src.parameter_sweep_unified opamp_ac.net \
  --device M1 --device M2 \
  --parameter w \
  --values 6u 7u 8u 9u 10u \
  --supply VDD=1.5
~~~

Each candidate runs two LTspice simulations: one AC run and one Operating Point run. The combined CSV includes AC loop-gain metrics, DC operating-point values, explicitly selected supply power, convergence method and warning counts.

### DC-only sweep

~~~bash
python3 -m src.parameter_sweep opamp_dc.net \
  --device M1 --device M2 \
  --parameter w \
  --values 6u 8u 10u \
  --supply VDD=1.5
~~~

### AC-only sweep

~~~bash
python3 -m src.parameter_sweep_ac opamp_ac.net \
  --device M1 --device M2 \
  --parameter w \
  --values 6u 8u 10u
~~~

### Run a single existing netlist

~~~bash
python3 -m src.ltspice_runner opamp.net
~~~

If LTspice cannot be found automatically, set `LTSPICE_EXECUTABLE` to the executable inside the installed application. On macOS, for example:

~~~bash
export LTSPICE_EXECUTABLE="/Applications/LTspice.app/Contents/MacOS/LTspice"
~~~

## Model checker

The read-only checker compares numeric MOSFET W/L literals with `WMIN`, `WMAX`, `LMIN` and `LMAX` declared by referenced NMOS/PMOS model cards. It follows local `.include`, `.inc` and model-file `.lib` references and can resolve common Windows LTspice library paths on macOS.

~~~bash
python3 -m src.model_checker opamp.net --supply VDD=1.5
~~~

Add `--strict` to return exit code 2 when a declared geometry bound is violated:

~~~bash
python3 -m src.model_checker opamp.net --supply VDD=1.5 --strict
~~~

The checker does not edit the netlist or model library, infer diffusion geometry, or certify simulator/model compatibility. Comment-based supply ranges are reported as model-file recommendations, not as a complete safe-operating-area guarantee.

## Input formats and analysis assumptions

| Input | Supported format | Output |
|---|---|---|
| Transient RAW | Binary, real, forward | CSV |
| AC RAW | Binary, complex, forward | CSV with real/imaginary columns |
| DC log | BSIM3 Semiconductor Device Operating Points section | Per-device metrics and convergence/warning metadata |
| DC RAW | Binary, real, one-point Operating Point file | Node voltages, component/device currents and CSV |

The RAW layouts were validated against LTspice 26.0.2 on macOS. Other RAW variants are not claimed as supported. The DC RAW parser supports the verified real-valued, single-point Operating Point layout; it is not a general parser for every LTspice RAW format.

The AC metrics helper uses the return ratio `-V(out) / V(nfb)` and reports the first descending 0 dB crossing, interpolated on a log-frequency axis. Configure the output and feedback traces for the circuit under analysis; the helper does not infer a return ratio for arbitrary testbenches.

Quiescent power is calculated only for voltage sources explicitly selected by the caller. The caller must provide each source's signed positive-to-negative DC voltage. LTspice voltage-source current is positive into the source's positive terminal, so power delivered by a source is `-(V_source * I_source)`. The result is the net power delivered by the selected sources, not necessarily the total dissipation of a circuit if relevant supplies are omitted.

## Optimization scope and limitations

- The optimizer performs a finite, one-dimensional grid search over values supplied by the user. It does not generate new values or refine the grid adaptively.
- One geometry parameter (W or L) is varied per run. All selected MOSFETs receive the same value.
- One scalar objective is optimized at a time. Multiple numeric constraints may be applied.
- The selected candidate is the best feasible point among the supplied values, not a proof of a continuous or global optimum.
- A successful simulator run does not by itself certify circuit specifications, model accuracy or silicon-level performance.
- Simulator convergence information and model warnings remain part of the experiment record and should be reviewed by the engineer.

## Project records

- [v1.0 design automation guide](docs/design-automation-v1.0.md)
- [In-range CMOS op-amp sweep results](docs/in-range-sweep-summary.md)
- [Changelog](CHANGELOG.md)
