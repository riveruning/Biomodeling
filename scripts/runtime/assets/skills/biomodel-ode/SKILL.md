---
name: biomodel-ode
description: Build and inspect evidence-linked rate-rule ODE proposals and run them through the installed BioModel Agent CLI. Use for supported small ODE reconstruction or explicit exploratory modeling in a generated BioModel workspace.
---

# Evidence-linked ODE workflow

Read the workspace `references/commands.md`. For a new proposal, read `references/modeling-plan.md`; an existing example can be inspected directly. Run the environment doctor if interpreter or dependency readiness is unknown.

Ingest only the task's source files. Construct explicit evidence slices, entity mappings and candidate mathematical choices. `model-plan-prepare` freezes a proposal; `model-plan-show` derives current gaps. Every initial value, parameter and rate expression needs a decision with origin `reported`, `assumed` or `unresolved`.

Qualitative mechanisms do not select equations. A `reported` formula must match its source declaration through the existing bounded binding check. Unknown syntax can remain unresolved even when the text is scientifically adequate; report that limitation rather than paraphrasing it into fabricated source text. In reproduction mode, assumptions cannot fill scientific gaps. Exploration can use stated assumptions when consistent with the user's task.

Compile a ready plan with `model-plan-compile`. Inspect its returned state before preparing execution. Execution settings cannot override compiled initial values or parameters. Freeze a task with `model-plan-execution-prepare`, then explicitly invoke `model-plan-execute` once. Repeating that task returns the verified original result or adopts its confirmed receipt; an unknown reserved effect blocks retries.

Use `model-plan-summary`, `agent-timeline`, `report` and `export` for the actual artifacts. `model-plan-replay` starts a new scientific execution. Reference agreement and numerical convergence do not establish biological truth or independent source review.

For missing information, state the target, what is absent or conflicting, and the smallest useful clarification. An explicit new source and `--parent-ref/--reason` preserve the old proposal when preparing a revision. Finish with artifact references, actual checks, declared assumptions and unresolved scientific support.
