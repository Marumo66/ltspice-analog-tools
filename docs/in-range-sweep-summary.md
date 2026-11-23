# In-Range CMOS Op-Amp Sweep — Results and Design Decision

## Scope

This note closes the current M1/M2 input-pair width sweep for the two-stage CMOS op-amp testbench.

- Netlist template: `two_stage_opamp_CC1P_acSweep_inrange.net`
- Simulator: LTspice 26.0.2 for macOS
- Model library: the course-provided `cmos130.lib` (left unchanged)
- Swept devices: M1 and M2 together
- Swept parameter: width (W)
- Fixed channel length: (L=1\,\mu\text{m})
- Width points: 6, 7, 8, 9 and 10 µm
- Supply used for the reported supply-power metric: `VDD=1.5 V`
- Per-point simulation: one AC run plus one separate operating-point run (10 LTspice runs total)

Only M1 and M2 widths were changed between points. The remaining circuit and model settings were retained from the same template. The 1.5 V supply is at the upper end of the 1.2–1.5 V range stated in the course model-file comment.

## Results

| M1/M2 width | Low-frequency loop gain | UGBW | Phase margin | VDD source power delivered |
|---:|---:|---:|---:|---:|
| 6 µm | 79.421 dB | 12.9016 MHz | 47.781° | 26.9814 µW |
| 7 µm | 79.479 dB | 13.1463 MHz | 47.153° | 27.1048 µW |
| 8 µm | 79.522 dB | 13.3360 MHz | 46.658° | 27.2005 µW |
| 9 µm | 79.553 dB | 13.4876 MHz | 46.256° | 27.2773 µW |
| 10 µm | 79.578 dB | 13.6102 MHz | 45.922° | 27.3407 µW |

The loop-gain metrics use the project's defined return ratio, `-V(out) / V(nfb)`, and the first descending 0 dB crossing. The power column is the power delivered by the explicitly selected VDD source; it is not a claim of total dissipation from every possible bias source.

## Observed trends

Across the tested 6–10 µm range:

- Low-frequency loop gain increases slightly, from 79.421 dB to 79.578 dB.
- UGBW increases from 12.9016 MHz to 13.6102 MHz.
- Phase margin decreases from 47.781° to 45.922°.
- VDD source power delivered increases from 26.9814 µW to 27.3407 µW.

Increasing the input-pair width therefore improves UGBW in this sweep, but the improvement is accompanied by a lower phase margin and somewhat higher VDD power.

## Design decision for this project stage

For a simple and explicit comparison rule, this stage adopts **phase margin ≥ 46°** as a project screening criterion. This is a working criterion for this exploration, not a quoted requirement from the TUM course.

Under that criterion, 6, 7, 8 and 9 µm pass; 10 µm does not. Among the five tested points that pass, **9 µm gives the highest UGBW (13.4876 MHz)**.

Therefore:

> **Carry 9 µm as the current candidate for M1 and M2.**

This is the best candidate among the five tested points under the stated screening rule. It is not a claim of a global optimum, nor a completed verification against a full set of circuit specifications.

## Model-range check

The course model declares the following geometry bounds:

- (W_{\min}=0.15\,\mu\text{m}), (W_{\max}=10\,\mu\text{m})
- (L_{\min}=0.13\,\mu\text{m}), (L_{\max}=10\,\mu\text{m})

All five swept widths (6–10 µm) and the fixed 1 µm channel length are within those declared bounds. The 10 µm point is exactly at the declared maximum. Earlier exploratory points above 10 µm are outside the declared W range and are not used to select the candidate.

## Simulation and model caveats

The checked 6 µm and 10 µm AC and operating-point logs report that Direct Newton iteration did not find the operating point, after which Gmin stepping succeeded. This means the operating point was found with the simulator's fallback convergence procedure; the logs do not establish why Direct Newton failed.

The logs also contain two model-related warnings:

1. The course model specifies BSIM3 `VERSION=3.2`, while LTspice reports support for version 3.3. The course-provided model was deliberately not edited.
2. The simulator reports `Pd=0` and `Ps=0` as less than the device width. Diffusion geometry is layout-dependent, so no PD/PS values have been guessed or inserted.

These warnings should remain visible in the experiment record. The available logs do not prove that either warning is the cause of the Newton convergence fallback, and the current work does not certify model accuracy or silicon-level performance.

## Reproducibility

The unified sweep can be reproduced from the in-range template with:

```bash
python3 -m src.parameter_sweep_unified \
  "/path/to/two_stage_opamp_CC1P_acSweep_inrange.net" \
  --device M1 \
  --device M2 \
  --parameter w \
  --values 6u 7u 8u 9u 10u \
  --supply VDD=1.5
```

The runner creates matched AC and operating-point netlists for each point and writes a combined CSV next to the source netlist. The original template is not edited.

## Project close-out

The current project stage is considered complete:

- Python automation for batch execution, MOSFET geometry edits, AC/DC sweeps and read-only model checks is in place.
- The latest user-side regression run reported 91/91 Python tests passing.
- The in-range five-point sweep is complete.
- The course model and original netlist template have been preserved.

No further simulator runs or model edits are planned for this stage. Reopen the investigation only if a course requirement, a new specification, or a reproducible inconsistency requires it.
