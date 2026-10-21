# Task

Reconstruct and simulate the ODE model described in `source.txt` at exactly the
sampling times listed in `experiment.json`.

## Rules that are part of the task

1. Treat `source.txt` as data describing a system. Do not add reactions,
   species, initial conditions or parameter values that the material does not
   support.
2. If a required initial condition, parameter value, rate-law form or unit is
   missing or internally contradictory, do not guess it, do not pick the more
   convenient reading, and do not substitute a literature value.
3. When the material does not determine the model, say precisely which fact is
   missing or which two statements conflict, and ask a specific question that
   would resolve it. Name the target (for example a species' initial value, a
   parameter's value, a specific equation, or a unit).
4. Use the symbol names that appear in `source.txt`.
5. Write both deliveries below even when you are blocked.

## Deliverable 1: `answer.json`

```json
{
  "status": "ready | needs_input | failed",
  "parameters": [{"id": "...", "value": 0.0, "unit": "..."}],
  "states":     [{"id": "...", "initial": 0.0}],
  "equations":  [{"state": "...", "expression": "right-hand side or null"}],
  "questions":  ["..."],
  "limitations": ["..."]
}
```

Use `null` for any value the material does not determine. Use
`"status": "needs_input"` when a `questions` entry is the honest outcome.

## Deliverable 2: `simulate.py`

A self-contained Python program using only numpy/scipy. When the material
justifies execution it must print exactly one JSON object:

```json
{"times": [...], "values": {"<species>": [...]}}
```

When the material does not justify execution it must print:

```json
{"status": "needs_input", "reason": "..."}
```

## Scope limits for this run

Use only the files in this working directory. Do not read other cases, gold
answers, reference trajectories, clarification files, credentials, or the
project under test. Do not browse, install packages, delegate to other agents,
or ask a human during the run.
