# Frozen research bundle

Root: `f5af19e1d5cc0fb76a23bbc49e89c94162753e08418ceb48fb997c5c1e801352`

Replay without an LLM:

```bash
python -m biomodel_agent replay f5af19e1d5cc0fb76a23bbc49e89c94162753e08418ceb48fb997c5c1e801352 --store .
```

Install the project and its pinned Python dependencies first. Replaying an agent result uses its `run_ref`; re-running inference is a separate task.
