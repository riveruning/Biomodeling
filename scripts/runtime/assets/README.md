# BioModel Agent workspace

This is an empty user workspace for the installed BioModel scientific core. It contains guidance, an optional discoverable Skill and runtime input templates. Initialization creates an empty store; it does not preload sources, model runs or expected answers, run a model or contact a provider.

Use the Python executable recorded in `workspace.json`. Run `python -m biomodel_agent workspace-doctor` to inspect dependencies. Ingest your own source using `source-ingest`, then create a task-specific modeling plan with the returned source reference and exact evidence spans.

`templates/ode-plan.json` describes the ODE plan layout and deliberately leaves initial values, parameters and decisions unresolved. Fill these from your authorized source or explicit allowed exploration assumptions before compiling. Other templates describe native input syntax; their numeric values are illustrative, never evidence or reference answers for a user task.

Read `references/commands.md` and `references/modeling-plan.md` for command and modeling contracts. The Skill is copied only into this generated workspace. The host still needs file access and CLI execution capabilities.
