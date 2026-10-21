# Frozen research bundle

Root: `f1d8b414f012fe93a0a6807b539a47ad4947845170794021526dc5128170151d`

Replay without an LLM:

```bash
python -m biomodel_agent replay f1d8b414f012fe93a0a6807b539a47ad4947845170794021526dc5128170151d --store .
```

Install the project and its pinned Python dependencies first. Replaying an agent result uses its `run_ref`; re-running inference is a separate task.
