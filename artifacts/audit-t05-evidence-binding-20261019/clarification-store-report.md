# BioModel research report

Artifact: `10618a0b1d38ea3272acbc5c5d0e3d68af814af2a3df15a37bf81f69d3e5fc8f`
Kind: `run`

Execution: **succeeded**
Interpretation: **not_verified**
Mode: `strict_reproduction`
Backend: `libRoadRunner/cvode`
Input: `264b231f6e2bd3f682a77a8495b62c2130ceefb6967e45d3d2289b03aff07213`
Solver: `{"atol": 1e-10, "max_steps": 100000, "rtol": 1e-07, "timeout_s": 30.0}`
Cached from: `None`
Replay of: `None`


## Executable model

Model ID: `decay`
SBML SHA-256: `12d34faea737a014a56dd4639d582aaad3a0e81ac6d66e72b9364af95630354f`

| Symbol | Initial/value | Representation |
|---|---|---|
| k | 0.4 | parameter; constant=True |
| x | 1.0 | parameter; constant=False |

Rate rules and reaction fluxes use native SBML amount/concentration semantics. Experiment overrides are stored in the frozen input.

- `d(x)/dt = -k * x`
## Frozen comparison

Status: **not_run**
- reason: `"no reference result: self-comparison is not a reproduction"`

## static_validation

- **unknown** (warning) `units_consistency`: libSBML reported unit diagnostics; dimensional correctness is not claimed: In situations where a mathematical expression refers to a compartment, species or parameter, it is necessary to know the units of the object to establish unit consistency. In models where the units of an object have not been declared, libSBML does not yet have the functionality to accurately verify the consistency of the units in mathematical expressions referring to that object.   The units of the <parameter> 'k' cannot be fully checked. Unit consistency reported as either no errors or further unit errors related to this object may not be accurate.  | If the attribute 'units' on a given Parameter object has not been 
- **pass** (blocking) `sbml_parse`: document parsed
- **pass** (blocking) `sbml_structure`: no structural errors at or above ERROR
- **unknown** (warning) `units_consistency`: libSBML reported unit diagnostics; dimensional correctness is not claimed: In situations where a mathematical expression refers to a compartment, species or parameter, it is necessary to know the units of the object to establish unit consistency. In models where the units of an object have not been declared, libSBML does not yet have the functionality to accurately verify the consistency of the units in mathematical expressions referring to that object.   The units of the <parameter> 'k' cannot be fully checked. Unit consistency reported as either no errors or further unit errors related to this object may not be accurate.  | If the attribute 'units' on a given Parameter object has not been 
- **unknown** (warning) `units_missing`: units not declared for: parameter 'k', parameter 'x' (unknown, not assumed dimensionless)
- **pass** (blocking) `artifact_valid`: artifact is a supported ODE/SBML model
- **unknown** (warning) `experiment_time_unit`: Experiment time unit is unknown; alignment with the model clock was not checked.

## numeric_validation

- **pass** (blocking) `finite`: all output values are finite
- **not_applicable** (informational) `nonnegative`: no nonnegative variables requested
- **not_run** (informational) `conservation`: conservation/mass-balance checking is not implemented; not a pass
- **pass** (blocking) `convergence`: solver refinement (rtol/atol / 10): 2.8017044817474357e-07 vs threshold 0.001

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

- `264b231f6e2bd3f682a77a8495b62c2130ceefb6967e45d3d2289b03aff07213`
- `06d0dbfdfc68ce2cfa262c62c04394fb3f23a77608b612328cb01f9b98685a92`
