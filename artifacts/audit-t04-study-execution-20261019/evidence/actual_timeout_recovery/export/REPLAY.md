# Frozen research bundle

Root: `6afcb73dfa39624f78e3e05922ec6c32934e3c7ecb4679156f81425627ac0e7d`

Replay without an LLM:

```bash
python -m biomodel_agent replay 6afcb73dfa39624f78e3e05922ec6c32934e3c7ecb4679156f81425627ac0e7d --store .
```

Install the project and its pinned Python dependencies first. Replaying an agent result uses its `run_ref`; re-running inference is a separate task.
