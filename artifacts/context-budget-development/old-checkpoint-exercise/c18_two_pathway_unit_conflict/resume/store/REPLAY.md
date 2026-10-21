# Frozen research bundle

Root: `304e318dbe32c2734465e8453b5563667bb6c1ecf4ed79030036f6bf41c938ac`

Replay without an LLM:

```bash
python -m biomodel_agent replay 304e318dbe32c2734465e8453b5563667bb6c1ecf4ed79030036f6bf41c938ac --store .
```

Install the project and its pinned Python dependencies first. Replaying an agent result uses its `run_ref`; re-running inference is a separate task.
