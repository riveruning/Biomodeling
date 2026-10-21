# Frozen research bundle

Root: `7add553b24e3fb2147568e2ca8fd2a7c4e813fcd48bd0d55173665e6be475b9d`

Replay without an LLM:

```bash
python -m biomodel_agent replay 7add553b24e3fb2147568e2ca8fd2a7c4e813fcd48bd0d55173665e6be475b9d --store .
```

Install the project and its pinned Python dependencies first. Replaying an agent result uses its `run_ref`; re-running inference is a separate task.
