# Frozen research bundle

Root: `68304601f6fb9d10182eb0c0f1d2cdc93a5235bd481e0dd25b5c29e0024a4690`

Replay without an LLM:

```bash
python -m biomodel_agent replay 68304601f6fb9d10182eb0c0f1d2cdc93a5235bd481e0dd25b5c29e0024a4690 --store .
```

Install the project and its pinned Python dependencies first. Replaying an agent result uses its `run_ref`; re-running inference is a separate task.
