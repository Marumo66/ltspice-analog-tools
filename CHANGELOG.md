## Unreleased — v1.0 development

### Added
- Initial `LTspiceRunner` for existing `.net` files.
- Platform-specific batch command construction: macOS uses `-b <file.net>`; Windows/Wine uses `-b -Run <file.net>`.
- macOS process launch redirects stderr to stdout to avoid the LTspice 26.0.2 shutdown issue reported for separate standard streams.
- LTspice executable discovery through `LTSPICE_EXECUTABLE`, common install paths and macOS Spotlight.
- Timeout, process error and fresh `.raw` / `.log` output checks.
- Platform-specific runner unit tests.

### Not included yet
- Schematic-to-netlist generation.
- Netlist parameter editing.
- Parameter sweeps, design constraints or optimisation.
- End-to-end verification against the user's installed LTspice application.

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

### Scope and limitations
- RAW parser layouts were validated with LTspice 26.0.2 on macOS; unsupported RAW variants are rejected or remain unverified.
- The DC RAW parser currently supports the verified real-valued, one-point Operating Point layout.
- Supply power requires the caller to select source names and provide signed source voltages. It is not inferred for arbitrary circuits.
- The project does not yet automate LTspice invocation, netlist editing, parameter sweeps or design optimisation; those are planned for v1.0.

## 0.1.0 — Transient Analysis

### Added
- Binary real/forward transient RAW parsing and CSV export.
- Maximum and average slew-rate calculations.
- Regression tests based on transient waveform data.
