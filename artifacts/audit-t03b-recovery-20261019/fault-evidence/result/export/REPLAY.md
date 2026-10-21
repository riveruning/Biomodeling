# Frozen research bundle

Root: `cb396df93a943b2283ab3c05a5a8e05b5de09c5ecb184645a2d115a058806f02`

Replay without an LLM:

```bash
python -m biomodel_agent replay cb396df93a943b2283ab3c05a5a8e05b5de09c5ecb184645a2d115a058806f02 --store .
```

Install the project and its pinned Python dependencies first. Replaying an agent result uses its `run_ref`; re-running inference is a separate task.
