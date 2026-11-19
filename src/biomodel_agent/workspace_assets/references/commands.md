# CLI contracts

Replace `python` with the executable recorded in `workspace.json` when necessary. Run commands from this workspace. All JSON references are content hashes in the selected store.

```bash
python -m biomodel_agent workspace-doctor
python -m biomodel_agent source-ingest my-source.txt --store runs/task
python -m biomodel_agent source-search --help

python -m biomodel_agent model-plan-prepare --config examples/complete-plan.json --source-ref SOURCE_REF --store store
python -m biomodel_agent model-plan-show PLAN_REF --store store
python -m biomodel_agent model-plan-compile PLAN_REF --store store
python -m biomodel_agent model-plan-compilation-show COMPILATION_REF --store store
python -m biomodel_agent model-plan-execution-prepare COMPILATION_REF --config examples/execution.json --store store
python -m biomodel_agent model-plan-status TASK_REF --store store
python -m biomodel_agent model-plan-execute TASK_REF --store store
python -m biomodel_agent model-plan-summary RUN_REF --store store
python -m biomodel_agent agent-timeline RUN_REF --store store
python -m biomodel_agent report RUN_REF --output report.md --store store
python -m biomodel_agent export RUN_REF --output bundle --store store
```

Example source refs are in `workspace.json` under `example_source_refs`; corresponding JSON examples already contain their actual source refs. New task sources are explicitly ingested and their returned refs inserted into the plan. `--source-ref` may be repeated for an explicitly authorized source set.

To revise a proposal, supply a complete revised config and explicit source set to `model-plan-prepare`, plus `--parent-ref OLD_PLAN_REF --reason 'Specific change'`. Prior versions stay available. Revision does not mutate a frozen execution task or automatically continue a completed run.

`prepare` and `compile` return exit code 2 for unresolved/blocked proposals and still return their saved reference. Invalid inputs return 1. A numerically eligible execution returns 0; an executed failure returns 1 and preserves its run reference. Read-only inspection commands return 0 when their artifact is readable; inspect the returned state as well. Never infer scientific success from exit code alone.

`model-plan-execute` is idempotent for the same task in its local store. A confirmed receipt can be adopted after an interrupted final write. A reservation without a confirmed return blocks another computation. `model-plan-replay RUN_REF` explicitly creates a new task and computation. The local budget is not a global cross-store execution quota.

`agent-timeline --format json` exposes machine-readable rows. `--output PATH` requires a new file, preserving earlier timelines. Row indices record event order, not clock time. Provider calls are zero in this CLI-driven scientific workflow; the external Agent's own usage is not measured by the scientific core.
