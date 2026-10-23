# BioModel research report

Artifact: `18e57a2233999e0e3537c0c814b7ab9bac1197d4a53c700554382aca56cf68b8`
Kind: `run`

Execution: **succeeded**
Interpretation: **not_verified**
Mode: `strict_reproduction`
Backend: `libRoadRunner/cvode`
Input: `095348a3f4c4b33733f891ee01c28945ae84fbe8010b13532b1195c0d1c685d4`
Solver: `{"atol": 1e-10, "max_steps": 100000, "rtol": 1e-07, "timeout_s": 30.0}`
Cached from: `None`
Replay of: `None`


## Executable model

Model ID: `goodwin2013_exploratory`
SBML SHA-256: `ee357ba77788a667924994bbb9b14ef8d657bf54d8d607bb230e55a31bd9fee0`

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
- time_unit: `"unknown"`
- time_unit_status: `"unknown"`

## static_validation

- **unknown** (warning) `units_consistency`: libSBML reported unit diagnostics; dimensional correctness is not claimed: In situations where a mathematical expression refers to time, the units of time should be consistent with the global time units of the model. In models where the 'timeUnits' attribute has not been declared, libSBML does not yet have the functionality to verify accurately the consistency of the units in such expressions.   | In situations where a mathematical expression refers to a compartment, species or parameter, it is necessary to know the units of the object to establish unit consistency. In models where the units of an object have not been declared, libSBML does not yet have the functionality to accurately verify
- **pass** (blocking) `sbml_parse`: document parsed
- **pass** (blocking) `sbml_structure`: no structural errors at or above ERROR
- **unknown** (warning) `units_consistency`: libSBML reported unit diagnostics; dimensional correctness is not claimed: In situations where a mathematical expression refers to time, the units of time should be consistent with the global time units of the model. In models where the 'timeUnits' attribute has not been declared, libSBML does not yet have the functionality to verify accurately the consistency of the units in such expressions.   | In situations where a mathematical expression refers to a compartment, species or parameter, it is necessary to know the units of the object to establish unit consistency. In models where the units of an object have not been declared, libSBML does not yet have the functionality to accurately verify
- **unknown** (warning) `units_missing`: units not declared for: parameter 'k1', parameter 'k2', parameter 'k3', parameter 'k4', parameter 'k5', parameter 'k6', parameter 'Ki', parameter 'n', parameter 'X', parameter 'Y', parameter 'Z', time (unknown, not assumed dimensionless)
- **pass** (blocking) `artifact_valid`: artifact is a supported ODE/SBML model
- **unknown** (warning) `experiment_time_unit`: Experiment time unit is unknown; alignment with the model clock was not checked.

## numeric_validation

- **pass** (blocking) `finite`: all output values are finite
- **pass** (blocking) `nonnegative`: 'X' stayed >= -1e-09
- **pass** (blocking) `nonnegative`: 'Y' stayed >= -1e-09
- **pass** (blocking) `nonnegative`: 'Z' stayed >= -1e-09
- **not_run** (informational) `conservation`: conservation/mass-balance checking is not implemented; not a pass
- **pass** (blocking) `convergence`: solver refinement (rtol/atol / 10): 1.0929900162182093e-05 vs threshold 0.001

Target binding: **matched**
Binding coverage: 1.0
A matched binding verifies only a declaration in the supported grammar. Scientific and semantic support remain separate.

## Evidence and limits

Citation coverage: 1.0
Semantic support: **unknown**
Missing targets: `[]`
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

- `095348a3f4c4b33733f891ee01c28945ae84fbe8010b13532b1195c0d1c685d4`
- `9ba50183cd5eb52e8d102380e675dfbfa5f7d02fdc75b53efd59e6527661cbb7`
