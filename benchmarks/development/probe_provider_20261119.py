"""Explicitly budgeted provider probe: distinguish gateway queueing from a
parameter-induced hang.

Two single-shot POSTs with the same model/family as the live runs:
  A. plain chat (no response_format, no reasoning_effort)
  B. production shape (json_object + reasoning_effort=max)

Each attempt is frozen in ``probe-config.json`` before sending. A timeout is an
unknown outcome: it is recorded and never retried. Usage is stored exactly as
reported (missing stays missing).
"""
import json
import os
import time
from pathlib import Path

import httpx

OUT = Path("artifacts/development/agent-renewal-20261119/provider-probe-04")
BASE = "https://api.llm.ustc.edu.cn/v1/chat/completions"
MODEL = "glm-5.3-flash"
TIMEOUT_S = 75
MAX_TOKENS = 64


def main():
    if OUT.exists() and any(OUT.iterdir()):
        raise SystemExit("probe directory not empty; refusing to overwrite evidence")
    OUT.mkdir(parents=True, exist_ok=True)
    key = os.environ.get("BIOMODEL_API_KEY")
    if not key:
        raise SystemExit("BIOMODEL_API_KEY missing")
    config = dict(model=MODEL, max_tokens=MAX_TOKENS, timeout_s=TIMEOUT_S,
                  attempts=2, reasoning_effort="max", json_mode_variant=True,
                  note="two explicit single-shot probes; unknown outcomes are not retried")
    (OUT / "probe-config.json").write_text(json.dumps(config, indent=2) + "\n")
    messages = [{"role": "user", "content": 'Reply with exactly {"ok":true}.'}]
    variants = [
        ("plain_json_mode_off", dict(messages=messages, model=MODEL, max_tokens=MAX_TOKENS)),
        ("production_json_mode_on_reasoning_max",
         dict(messages=messages, model=MODEL, max_tokens=MAX_TOKENS,
              response_format={"type": "json_object"}, reasoning_effort="max")),
    ]
    results = []
    for name, payload in variants:
        started = time.monotonic()
        record = dict(variant=name, status="unknown", latency_s=None,
                      http_status=None, usage=None, finish_reason=None,
                      content_characters=None, error=None)
        try:
            with httpx.Client(timeout=TIMEOUT_S, trust_env=False) as client:
                response = client.post(BASE, json=payload,
                                       headers={"Authorization": "Bearer " + key})
            record["http_status"] = response.status_code
            body = response.json()
            record["usage"] = body.get("usage")
            choice = (body.get("choices") or [{}])[0]
            record["finish_reason"] = choice.get("finish_reason")
            content = (choice.get("message") or {}).get("content") or ""
            record["content_characters"] = len(content)
            record["status"] = "responded" if response.status_code == 200 else "http_error"
        except Exception as exc:
            record["error"] = type(exc).__name__ + ": " + str(exc)[:200]
            record["status"] = "timeout_unknown" if "imeout" in type(exc).__name__ else "transport_error"
        record["latency_s"] = round(time.monotonic() - started, 1)
        results.append(record)
        (OUT / "probe-results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps([{k: r[k] for k in ("variant", "status", "http_status",
                                        "latency_s", "finish_reason")} for r in results]))


if __name__ == "__main__":
    main()
