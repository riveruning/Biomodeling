# Frozen research bundle

Root: `813d545af732861d1d3a1846606179d9f9d917f2db35aafff938584a0f48dd4b`

Replay without an LLM:

```bash
python -m biomodel_agent replay 813d545af732861d1d3a1846606179d9f9d917f2db35aafff938584a0f48dd4b --store .
```

Install the project and its pinned Python dependencies first. Replaying an agent result uses its `run_ref`; re-running inference is a separate task.
