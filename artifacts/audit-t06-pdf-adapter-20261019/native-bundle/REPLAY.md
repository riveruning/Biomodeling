# Frozen research bundle

Root: `da2674be1a26ab8183a928b98f74da1d7f48cb992a9d50f5d8c375d18d3b0bfb`

Replay without an LLM:

```bash
python -m biomodel_agent replay da2674be1a26ab8183a928b98f74da1d7f48cb992a9d50f5d8c375d18d3b0bfb --store .
```

Install the project and its pinned Python dependencies first. Replaying an agent result uses its `run_ref`; re-running inference is a separate task.
