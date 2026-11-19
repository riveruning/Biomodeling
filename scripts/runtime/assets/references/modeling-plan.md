# Explicit ODE decision profile

Version 1 supports 1–16 states, at most 32 parameters, a default unit compartment and explicit rate-rule equations. Reactions, derived variables, non-default compartments and other model types retain their existing product entrypoints. Shared concepts can be extended later without forcing those types into this ODE representation.

Start from `templates/ode-plan.json` for the exact JSON layout. Author only these top-level fields:

| Field | Meaning |
|---|---|
| `schema_version` | Integer 1 |
| `id`, `model_type`, `mode` | Stable plan ID, `ode`, and `reproduction` or `exploration` |
| `context` | Up to 16 declared string conditions, such as organism and assay |
| `entities` | Semantic entity IDs and readable labels |
| `evidence` | Source ref, one-based page, zero-based Unicode start/end offsets, exact quote, interpretation and conditions |
| `mechanisms` | Qualitative subject/relation/object, evidence IDs, conditions and interpretation rationale |
| `candidates` | Candidate IDs/labels, entity-to-state variable maps, mathematical drafts and decisions |
| `selected_candidate` | Explicit candidate ID, or null when selection is unresolved |

Each evidence record needs a distinct ID. Its quote must equal the stored source slice; `interpretation` is an unverified reading. Mechanism relations are `promotes`, `inhibits`, `production`, `degradation`, `conversion`, `association` or `unspecified`. They are not compiled into equations automatically.

Each candidate contains a normal `ModelDraft` using explicit rate rules. Leave its `element_map` empty, `hypothesis_refs` empty and `parent_ref` null; tools own these links. `variable_map` maps known semantic entities one-to-one onto all candidate states.

Every draft target needs exactly one decision:

- `x/initial` for each state.
- `k/value` for each parameter.
- `rule:x/expression` for each rate rule.

A decision contains `target`, `origin`, `evidence_ids`, `mechanism_ids`, `rationale` and `alternatives`. `reported` needs exact source evidence that matches the numeric value or expression. `assumed` records a mathematical choice and is usable only in exploration mode. `unresolved` blocks compilation even if the proposed draft contains a provisional value. Numeric null stays missing; zero is a real value.

Conditions on used evidence and mechanisms must match the task's explicit context. This is exact declared-field matching, not general scientific applicability reasoning. Unknown units and biological interpretation remain visible scientific unknowns; schema or binding success cannot approve them.

The execution config contains `experiment`, with optional `solver`, `observations`, `reference`, `scales`, `threshold`, `nonnegative`. It cannot override mathematical choices using initial/parameter overrides or segments. A reference is a full `SimulationResult` with the same grid/time unit, all returned state/observation columns and explicit positive fixed scales. When requested, comparison must actually pass for the task to complete; an unavailable comparison remains incomplete even if numerical solver checks pass. The runtime template contains no expected results or reference answers. Select any required validation data explicitly for the new task.
