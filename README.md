# ltspice-analog-tools

[![Python tests](https://github.com/Marumo66/ltspice-analog-tools/actions/workflows/python-tests.yml/badge.svg?branch=main)](https://github.com/Marumo66/ltspice-analog-tools/actions/workflows/python-tests.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) **v1.0.0**

Python tools for automating repeatable LTspice analysis and MOSFET geometry exploration. Start from an existing LTspice `.net` netlist, run controlled W/L sweeps, extract AC/DC metrics, apply engineering constraints, and compare feasible candidates.

**Start here:** [Guided demo](docs/DEMO.md) · [Engineering workflow](#engineering-workflow) · [Development guide](CONTRIBUTING.md) · [v1.0.0 release](https://github.com/Marumo66/ltspice-analog-tools/releases/tag/v1.0.0)

## What it does

| Stage | Capabilities |
|---|---|
| **Run** | Launch an existing LTspice `.net` netlist in batch mode; create isolated candidate netlists without editing the source |
| **Analyse** | Parse supported transient/AC RAW files and DC log/Operating Point data; calculate slew rate, loop gain, UGBW, phase margin and selected-source power |
| **Explore** | Run DC, AC or matched AC/Operating Point MOSFET W/L sweeps and collect results in CSV |
| **Check** | Compare numeric MOSFET geometry against model-declared bounds; report selected library/model warnings without modifying model files |
| **Select** | Run a finite parameter grid, apply inclusive min/max constraints, rank feasible points against one objective, and write CSV/Markdown/JSON reports |

## Engineering workflow

The optimizer orchestrates an existing simulation workflow. It does not create a circuit schematic or infer a testbench.

~~~mermaid
flowchart TD
    A[Existing LTspice .net + model libraries] --> B[Optional model pre-check]
    A --> C[JSON sweep configuration]
    C --> D[Generate a separate netlist for each W/L candidate]
    D --> E[LTspice batch simulation]
    E --> F[AC analysis + Operating Point analysis]
    F --> G[Parse RAW and LOG results]
    G --> H[Compute loop gain, UGBW, phase margin and selected-source power]
    H --> I[Apply user-defined constraints]
    I --> J[Choose the best feasible supplied grid point]
    J --> K[CSV sweep data]
    J --> L[Markdown summary]
    J --> M[JSON results]
~~~

**Important:** the model checker is a separate, optional preflight command; the optimizer does not automatically run it. The source netlist must contain exactly one active `.ac` directive for the matched AC/DC optimizer.

## Requirements

- Python **3.9+**.
- LTspice installed for real batch simulations and optimization runs.
- No third-party Python packages are required; the code uses the Python standard library.
- A user-supplied LTspice `.net` file and every model/library file referenced by that netlist.

The repository does not include a ready-made circuit or model library. You must point the example configuration at your own compatible AC netlist and adjust the device names, supply sources and design limits. The [demo guide](docs/DEMO.md) walks through this setup.

## Quick start

~~~bash
git clone https://github.com/Marumo66/ltspice-analog-tools.git
cd ltspice-analog-tools

# Run the regression tests (LTspice is not required for the unit tests)
python3 -m unittest discover -s tests -v
~~~

### Run the design optimizer

~~~bash
cp examples/design_optimization.example.json examples/my_design.json
# Edit my_design.json: set source_netlist, devices, supply_voltages and constraints
python3 -m src.design_optimizer examples/my_design.json
~~~

The optimizer runs one AC simulation and one Operating Point simulation per supplied candidate. It leaves the original source netlist untouched and writes the candidate netlists and result CSV alongside that source. Existing output files are not overwritten.

See [DEMO.md](docs/DEMO.md) for the full setup, model pre-check, configuration walkthrough, output files and a real v1.0.0 validation example.

## Main commands

| Command | Purpose |
|---|---|
| `python3 -m src.ltspice_runner design.net` | Run one existing netlist in LTspice batch mode |
| `python3 -m src.model_checker design.net` | Inspect referenced models and declared MOSFET W/L limits |
| `python3 -m src.parameter_sweep design.net ...` | Run a DC-only geometry sweep |
| `python3 -m src.parameter_sweep_ac design_ac.net ...` | Run an AC-only geometry sweep |
| `python3 -m src.parameter_sweep_unified design_ac.net ...` | Run matched AC and Operating Point sweeps |
| `python3 -m src.design_optimizer config.json` | Apply constraints and select the best feasible supplied candidate |

Full command examples and configuration fields are documented in [the design automation guide](docs/design-automation-v1.0.md).

## Validation

- **109 Python unit tests** passed in the project owner's local macOS run.
- GitHub Actions runs the regression suite on **Python 3.9** and **Python 3.12**.
- The v1.0.0 real LTspice smoke test used LTspice 26.0.2 on macOS: five M1/M2 width candidates, ten simulations, three feasible points, and a 9 µm candidate selected under the configured constraints.

The recorded 9 µm result was UGBW **13.4876 MHz**, phase margin **46.256°**, and selected VDD source power delivered **27.2773 µW**. This is a result from one supplied course op-amp testbench, not a universal benchmark or a TUM specification. See [the full sweep record](docs/in-range-sweep-summary.md).

## Repository map

~~~text
src/                     Python modules: LTspice runner, parsers, sweeps, checker, optimizer
tests/                   Unit and regression tests
examples/                Example optimizer configuration
docs/                    Demo, architecture notes and recorded experiment results
.github/workflows/       GitHub Actions regression workflow
~~~

## Scope and limitations

- The automation starts from an existing `.net` netlist; it does not convert an `.asc` schematic into a netlist.
- The optimizer performs a finite, **one-dimensional** grid search over user-supplied W or L values. All selected MOSFET instances receive the same candidate value.
- It optimizes one scalar objective at a time. It is not a continuous, adaptive, global, multi-parameter or multi-objective optimizer.
- AC loop-gain metrics use the project's configured output/feedback traces and return-ratio convention; they are not inferred for arbitrary topologies.
- Power is calculated only for voltage sources explicitly selected in the configuration; it may not represent total circuit dissipation.
- RAW parsing is limited to the layouts documented and validated against LTspice 26.0.2 on macOS. Simulator convergence, model compatibility and warnings still require engineering review.

## Documentation

- [Guided demo: from netlist to design report](docs/DEMO.md)
- [Design automation v1.0: configuration and metrics](docs/design-automation-v1.0.md)
- [Recorded CMOS op-amp sweep and design decision](docs/in-range-sweep-summary.md)
- [Contributing and development workflow](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

## License

This project is licensed under the **MIT License**. You may use, modify, distribute and sell copies of the software, provided that the copyright and license notice are included. The software is provided without warranty. See [LICENSE](LICENSE) for the complete terms.

Copyright (c) 2026 Haoyuan Xu.

## Contributing

Bug reports, reproducible issues and focused improvements are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.
