# Changelog

All notable project changes are documented here.

## 1.0.0-rc.1 — 2026-11-23 (Release Candidate)

### Added

- Cross-platform LTspice batch execution for existing `.net` netlists.
- MOSFET geometry editing for selected devices' W/L values, writing separate netlist variants while preserving the source file.
- DC-only and AC-only geometry sweep runners.
- Matched AC/Operating Point sweep orchestration from one AC netlist template.
- Unified CSV output with loop-gain metrics, UGBW, phase margin, DC node voltages, MOSFET operating-point data, explicitly selected supply currents and delivered power, plus convergence and warning metadata.
- Read-only MOSFET model checker for model-declared W/L bounds, include/library resolution, stated supply ranges, model VERSION values and PD/PS review warnings.
- Finite one-dimensional design grid search driven by JSON configuration.
- Inclusive minimum/maximum result constraints and single-objective candidate selection (maximize or minimize).
- Markdown and JSON optimization reports alongside the unified sweep CSV.
- GitHub Actions regression workflow for Python 3.9 and 3.12.

### Validation

- 109 Python unit tests passed on the project owner's macOS environment.
- End-to-end smoke test completed with LTspice 26.0.2 on macOS: five M1/M2 width candidates, ten LTspice simulations, three feasible candidates, and 9 µm selected under the configured constraints (PM ≥ 46°, UGBW ≥ 13 MHz, selected VDD power ≤ 30 µW).
- Detailed experiment record: [In-Range CMOS Op-Amp Sweep](docs/in-range-sweep-summary.md).

### Known limitations

- The automation starts from an existing LTspice `.net` netlist; it does not generate a netlist from an `.asc` schematic.
- One geometry parameter (W or L) is swept per optimization run. All selected MOSFET devices receive the same candidate value.
- The optimizer evaluates only user-supplied grid values. It is not a continuous, adaptive, global, multi-parameter or multi-objective optimizer.
- Power calculation includes only voltage sources explicitly named with their signed DC voltages in the configuration.
- AC loop-gain analysis uses the configured output/feedback traces and the project's return ratio convention; it does not infer a loop gain for an arbitrary circuit.
- RAW parser support is limited to the documented layouts validated against LTspice 26.0.2 on macOS. The DC RAW parser supports real-valued, single-point Operating Point files.
- Model-checker results are advisory. Declared model bounds and comment-based supply ranges are not a complete electrical operating-area or reliability certification.
- The checked CMOS130 example required Gmin stepping after Direct Newton iteration did not find the operating point. The course model was preserved unchanged; simulator warnings remain visible for engineering review.

## 0.2.0 — AC/DC Analysis

### Added

- Binary complex AC RAW parsing and CSV export.
- Loop-gain calculation from the documented return ratio `-V(out) / V(nfb)`.
- UGBW and phase-margin extraction using log-frequency interpolation.
- DC BSIM3 MOSFET operating-point log parsing, including signed device parameters, simulator warnings and convergence metadata.
- Real, single-point Operating Point RAW parsing for node voltages, component currents and MOSFET terminal currents.
- Explicit-source quiescent power calculation using signed voltage-source currents.
- Full 901-point AC regression fixture and real LTspice-generated DC log/RAW regression fixtures.
- Automated unit tests for AC and DC analysis.

### Fixed

- AC RAW header parsing for CRLF-terminated headers.
- CSV fixture line endings normalised to LF.

### Scope at the 0.2.0 release

- RAW parser layouts were validated with LTspice 26.0.2 on macOS; unsupported RAW variants were not claimed.
- The DC RAW parser supported the verified real-valued, one-point Operating Point layout.
- Supply power required the caller to select source names and provide signed source voltages.
- LTspice batch execution, netlist editing and parameter sweeps were not yet included in this release; they were added during v1.0 development.

## 0.1.0 — Transient Analysis

### Added

- Binary real/forward transient RAW parsing and CSV export.
- Maximum and average slew-rate calculations.
- Regression tests based on transient waveform data.
