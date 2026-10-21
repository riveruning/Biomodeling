# Frozen research bundle

Root: `40aa028a4bc3a742fa1d53dfa9fa96af01d6696251063b5ee67b1c40bb451156`

Replay without an LLM:

```bash
python -m biomodel_agent replay 40aa028a4bc3a742fa1d53dfa9fa96af01d6696251063b5ee67b1c40bb451156 --store .
```

Install the project and its pinned Python dependencies first. Replaying an agent result uses its `run_ref`; re-running inference is a separate task.
