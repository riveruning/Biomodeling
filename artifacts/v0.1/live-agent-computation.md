# BioModel research report

Artifact: `15f16a4c654a5bcbc6673aed21526561b514c3697cbc3551f2a067e56b4b6c21`
Kind: `run`

Execution: **succeeded**
Interpretation: **not_verified**
Mode: `strict_reproduction`
Backend: `libRoadRunner/cvode`
Input: `98c36fa2dff3653453d0892d38cefabe88b9ac64c7a2d7290f57841d59dbe1ab`
Solver: `{"atol": 1e-10, "max_steps": 100000, "rtol": 1e-07, "timeout_s": 30.0}`
Cached from: `None`
Replay of: `None`


## Executable model

Model ID: `synthetic_ode_model`
SBML SHA-256: `6702c740ab414a2d09681d7d134214417bf20d8bb05263b6fd65e24f96bbca4d`

| Symbol | Initial/value | Representation |
|---|---|---|
| k | 2.0 | parameter; constant=True |
| d | 1.0 | parameter; constant=True |
| x | 0.0 | parameter; constant=False |

Rate rules and reaction fluxes use native SBML amount/concentration semantics. Experiment overrides are stored in the frozen input.

- `d(x)/dt = k - d * x`
## Frozen comparison

Status: **not_run**
- reason: `"no reference result: self-comparison is not a reproduction"`

## static_validation

- **unknown** (warning) `units_consistency`: libSBML reported unit diagnostics; dimensional correctness is not claimed: When the 'variable' in a <rateRule> definition refers to a <parameter>, the units of the rule's right-hand side are expected to be of the form _x per time_, where _x_ is the 'units' in that <parameter> definition, and _time_ refers to the units of time for the model. Reference: L3V2 Sections 4.7.3, 4.2.3 AND 4.9.4   Expected units are second (exponent = -1, multiplier = 1, scale = 0) but the units returned by the <math> expression of the <rateRule> with variable 'x' are dimensionless (exponent = 1, multiplier = 1, scale = 0). 
- **pass** (blocking) `sbml_parse`: document parsed
- **pass** (blocking) `sbml_structure`: no structural errors at or above ERROR
- **unknown** (warning) `units_consistency`: libSBML reported unit diagnostics; dimensional correctness is not claimed: When the 'variable' in a <rateRule> definition refers to a <parameter>, the units of the rule's right-hand side are expected to be of the form _x per time_, where _x_ is the 'units' in that <parameter> definition, and _time_ refers to the units of time for the model. Reference: L3V2 Sections 4.7.3, 4.2.3 AND 4.9.4   Expected units are second (exponent = -1, multiplier = 1, scale = 0) but the units returned by the <math> expression of the <rateRule> with variable 'x' are dimensionless (exponent = 1, multiplier = 1, scale = 0). 
- **pass** (blocking) `units_missing`: units declared for every species/parameter/compartment/time
- **pass** (blocking) `artifact_valid`: artifact is a supported ODE/SBML model

## numeric_validation

- **pass** (blocking) `finite`: all output values are finite
- **not_applicable** (informational) `nonnegative`: no nonnegative variables requested
- **not_run** (informational) `conservation`: conservation/mass-balance checking is not implemented; not a pass
- **pass** (blocking) `convergence`: solver refinement (rtol/atol / 10): 2.1946301979480627e-07 vs threshold 0.001

## Evidence and limits

Citation coverage: 1.0
Semantic support: **unknown**
Missing targets: `[]`
Invalid refs: `[]`
Units: **declared**
- A matching trajectory does not establish paper fidelity or biological truth.
- Citation presence is not a semantic support proof.
- Unrequested biological invariants are not assumed; conservation checking is not_run.
- passed_structured_reference denotes numerical agreement on the frozen execution benchmark only.

## Environment

Python: `3.12.3`
Environment: `{"biomodel-agent": "0.1.0", "libroadrunner": "2.10.0", "libsbml": "absent", "numpy": "2.5.4", "pypdf": "6.20.1", "python-libsbml": "5.21.2", "roadrunner": "absent", "scipy": "1.18.1"}`

## Direct provenance refs

- `98c36fa2dff3653453d0892d38cefabe88b9ac64c7a2d7290f57841d59dbe1ab`
- `5e2e8319c66f0937341d6cc2fd5025cb4b69fb994debc482ac2d5feff918f812`
