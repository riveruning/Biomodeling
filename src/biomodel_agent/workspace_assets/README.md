# BioModel Agent workspace

This workspace lets a tool-capable external Agent use the installed BioModel scientific core. It includes local project guidance, an optional discoverable Skill, three synthetic development examples, and immutable source records. Initialization does not run a model or contact a provider.

Use the Python executable recorded in `workspace.json`, or another environment with the same project source and locked dependencies. Run `python -m biomodel_agent workspace-doctor` first. This package does not install dependencies or grant tools to the host Agent.

For an external Agent, open this directory as its workspace and ask:

> Use the local BioModel workflow to inspect `examples/complete-plan.json`, report its evidence and mathematical choices, compile it, and run `examples/execution.json`. Present the actual checks and remaining scientific unknowns.

`examples/ambiguous-plan.json` has competing mathematical choices and no selected candidate. `examples/missing-plan.json` lacks a parameter. Both should produce specific gaps, not a scientific run. These are synthetic developer controls, not independent research results.

Read `references/commands.md` for commands and exit codes, and `references/modeling-plan.md` when authoring a new plan. New user tasks should use their own source files and store directories.

The Skill is installed only inside this newly generated workspace. Hosts with different Skill-discovery conventions can explicitly read `.agents/skills/biomodel-ode/SKILL.md`. An attached directory alone does not provide file access, Python execution or model-tool integration.
