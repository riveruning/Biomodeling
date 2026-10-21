# Frozen research bundle

Root: `dd9db3293de1582a58320c9c9909306cefcef21a780c11690f345159a95b11c2`

Replay without an LLM:

```bash
python -m biomodel_agent replay dd9db3293de1582a58320c9c9909306cefcef21a780c11690f345159a95b11c2 --store .
```

Install the project and its pinned Python dependencies first. Replaying an agent result uses its `run_ref`; re-running inference is a separate task.
