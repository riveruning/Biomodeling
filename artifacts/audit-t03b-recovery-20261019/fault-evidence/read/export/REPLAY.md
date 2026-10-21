# Frozen research bundle

Root: `0b283030f4b69cd833924ecfca94cf651c465560d340e1588cde5294082a63a5`

Replay without an LLM:

```bash
python -m biomodel_agent replay 0b283030f4b69cd833924ecfca94cf651c465560d340e1588cde5294082a63a5 --store .
```

Install the project and its pinned Python dependencies first. Replaying an agent result uses its `run_ref`; re-running inference is a separate task.
