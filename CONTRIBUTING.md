# Contributing and Development Workflow

Thanks for taking an interest in `ltspice-analog-tools`. The project aims to keep its analysis deterministic, its assumptions explicit and its original LTspice inputs unchanged.

## Project workflow

1. **Report or scope the change.** Open an issue with the intended behavior, a minimal reproducible input and the observed/expected result.
2. **Create a focused branch.** Use a name such as `fix/raw-header-parser` or `feature/sweep-report-field`.
3. **Implement and test.** Keep simulator-independent logic testable with Python's standard `unittest` framework. Mock the LTspice process when a test is checking orchestration rather than simulator behavior.
4. **Document assumptions.** Update the README or the relevant guide when a change affects commands, input formats, output metrics, supported LTspice behavior or known limitations.
5. **Open a pull request.** Explain the user-visible change, list the tests run and call out any compatibility or model assumptions.
6. **Review CI and merge.** The GitHub Actions workflow runs the regression suite on Python 3.9 and Python 3.12.

## Local checks

Run the full regression suite from the repository root:

~~~bash
python3 -m unittest discover -s tests -v
~~~

The test suite does not require an installed LTspice application. A passing unit-test run therefore does not prove that a real simulator integration works.

## Changes that affect LTspice integration

For changes to batch execution, netlist editing, RAW/LOG parsing, sweep orchestration or model-library handling:

- Add or update focused regression tests for the parsing/configuration behavior.
- Preserve the original source netlist and model files; generated candidate netlists should be separate artifacts.
- Keep simulator warnings, convergence information and unsupported-format boundaries visible.
- When practical, run a real LTspice smoke test and record the LTspice version, operating system, testbench, candidate values and observed outputs.
- Do not claim support for RAW variants, simulator versions or operating systems that have not been tested.

## Code and test conventions

- Use only the Python standard library unless a dependency is justified and documented.
- Prefer small, deterministic unit tests for parsers, validation, metric calculations and report formatting.
- Test invalid inputs and boundary conditions as well as successful cases.
- Keep units and sign conventions explicit in field names and documentation.
- Avoid silently guessing model parameters, device geometry or circuit specifications.

## Pull request checklist

- [ ] The change has a focused description and a clear reason.
- [ ] Relevant tests have been added or updated.
- [ ] `python3 -m unittest discover -s tests -v` passes locally.
- [ ] README or documentation has been updated where behavior or assumptions changed.
- [ ] Any real LTspice test is described with its simulator version and platform.
- [ ] Known limitations and warnings are preserved rather than hidden.

## Release checklist

Maintainers preparing a release should:

1. Update `VERSION` and `CHANGELOG.md`.
2. Run the full local regression suite and verify the GitHub Actions matrix is green.
3. Perform a real LTspice smoke test for changes that affect simulator integration, and record the exact environment.
4. Review README, usage documentation and known limitations.
5. Create a version tag and publish release notes that distinguish unit-test results from real LTspice validation.
