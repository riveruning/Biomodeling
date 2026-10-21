# Frozen research bundle

Root: `87c63f080d32cdfee468f9fd05d6f2c408ced0ab82bf92cf7072a836f9cee161`

Replay without an LLM:

```bash
python -m biomodel_agent replay 87c63f080d32cdfee468f9fd05d6f2c408ced0ab82bf92cf7072a836f9cee161 --store .
```

Install the project and its pinned Python dependencies first. Replaying an agent result uses its `run_ref`; re-running inference is a separate task.
