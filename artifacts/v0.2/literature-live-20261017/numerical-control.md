# BioModel research report

Artifact: `5f969831da19670b0c9b5be534d803b16adbe0f28a34bbfe71c597803973a119`
Kind: `run`

Execution: **succeeded**
Interpretation: **not_verified**
Mode: `strict_reproduction`
Backend: `libRoadRunner/cvode`
Input: `bdf5aa26f01c2fb41546c052b5d69e7c047e6b250ebdb045de528364d656c6b5`
Solver: `{"atol": 1e-10, "max_steps": 100000, "rtol": 1e-07, "timeout_s": 30.0}`
Cached from: `None`
Replay of: `None`


## Executable model

Model ID: `gonze2013_exploratory`
SBML SHA-256: `50a933863595a9e1f1bd9b97df97b5949424639a8e3f8a5eafdb24d7d538f0d6`

| Symbol | Initial/value | Representation |
|---|---|---|
| k1 | 1.0 | parameter; constant=True |
| k2 | 0.1 | parameter; constant=True |
| k3 | 1.0 | parameter; constant=True |
| k4 | 0.1 | parameter; constant=True |
| k5 | 1.0 | parameter; constant=True |
| k6 | 0.1 | parameter; constant=True |
| Ki | 1.0 | parameter; constant=True |
| n | 10.0 | parameter; constant=True |
| X | 0.1 | parameter; constant=False |
| Y | 0.2 | parameter; constant=False |
| Z | 0.3 | parameter; constant=False |

Rate rules and reaction fluxes use native SBML amount/concentration semantics. Experiment overrides are stored in the frozen input.

- `d(X)/dt = k1 * Ki^n / (Ki^n + Z^n) - k2 * X`
- `d(Y)/dt = k3 * X - k4 * Y`
- `d(Z)/dt = k5 * Y - k6 * Z`
## Frozen comparison

Status: **pass**
- max_scaled_error: `1.2165045512801953e-05`
- threshold: `0.001`
- scales: `{"X": 10.0, "Y": 10.0, "Z": 10.0}`

## static_validation

- **unknown** (warning) `units_consistency`: libSBML reported unit diagnostics; dimensional correctness is not claimed: In situations where a mathematical expression refers to time, the units of time should be consistent with the global time units of the model. In models where the 'timeUnits' attribute has not been declared, libSBML does not yet have the functionality to verify accurately the consistency of the units in such expressions.   | In situations where a mathematical expression refers to a compartment, species or parameter, it is necessary to know the units of the object to establish unit consistency. In models where the units of an object have not been declared, libSBML does not yet have the functionality to accurately verify
- **pass** (blocking) `sbml_parse`: document parsed
- **pass** (blocking) `sbml_structure`: no structural errors at or above ERROR
- **unknown** (warning) `units_consistency`: libSBML reported unit diagnostics; dimensional correctness is not claimed: In situations where a mathematical expression refers to time, the units of time should be consistent with the global time units of the model. In models where the 'timeUnits' attribute has not been declared, libSBML does not yet have the functionality to verify accurately the consistency of the units in such expressions.   | In situations where a mathematical expression refers to a compartment, species or parameter, it is necessary to know the units of the object to establish unit consistency. In models where the units of an object have not been declared, libSBML does not yet have the functionality to accurately verify
- **unknown** (warning) `units_missing`: units not declared for: parameter 'k1', parameter 'k2', parameter 'k3', parameter 'k4', parameter 'k5', parameter 'k6', parameter 'Ki', parameter 'n', parameter 'X', parameter 'Y', parameter 'Z', time (unknown, not assumed dimensionless)
- **pass** (blocking) `artifact_valid`: artifact is a supported ODE/SBML model

## numeric_validation

- **pass** (blocking) `finite`: all output values are finite
- **pass** (blocking) `nonnegative`: 'X' stayed >= -1e-09
- **pass** (blocking) `nonnegative`: 'Y' stayed >= -1e-09
- **pass** (blocking) `nonnegative`: 'Z' stayed >= -1e-09
- **not_run** (informational) `conservation`: conservation/mass-balance checking is not implemented; not a pass
- **pass** (blocking) `convergence`: solver refinement (rtol/atol / 10): 1.0929900162182093e-05 vs threshold 0.001

## Evidence and limits

Citation coverage: 0.0
Semantic support: **unknown**
Missing targets: `["Ki/value", "X/initial", "Y/initial", "Z/initial", "k1/value", "k2/value", "k3/value", "k4/value", "k5/value", "k6/value", "n/value", "rule:X/expression", "rule:Y/expression", "rule:Z/expression"]`
Invalid refs: `[]`
Units: **unknown**
- A matching trajectory does not establish paper fidelity or biological truth.
- Citation presence is not a semantic support proof.
- Unrequested biological invariants are not assumed; conservation checking is not_run.
- passed_structured_reference denotes numerical agreement on the frozen execution benchmark only.

## Environment

Python: `3.12.3`
Environment: `{"biomodel-agent": "0.1.0", "libroadrunner": "2.10.0", "libsbml": "absent", "numpy": "2.5.4", "pypdf": "6.20.1", "python-libsbml": "5.21.2", "roadrunner": "absent", "scipy": "1.18.1"}`

## Direct provenance refs

- `bdf5aa26f01c2fb41546c052b5d69e7c047e6b250ebdb045de528364d656c6b5`
- `30b177e6d512de538943a5442c0cfc65954aa560bacc6806ba88e6104b089bb3`
