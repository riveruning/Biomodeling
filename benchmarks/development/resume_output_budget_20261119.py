"""Resume a known output-budget failure with an explicit higher cap.

Never resets the call budget, never replays the unknown request, and records
the plan receipt before the new attempt. Only ``max_tokens`` may increase; the
provider/model/reasoning/transport profile stays identical.
"""
import argparse
import json
import os
import time
from pathlib import Path

from biomodel_agent import research_output_budget as budgets
from biomodel_agent import research_projects as projects
from biomodel_agent import research_supervisor as supervisor
from biomodel_agent.llm import ChatProvider
from biomodel_agent.storage import ArtifactStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True, type=Path)
    parser.add_argument("--max-tokens", required=True, type=int)
    parser.add_argument("--actor", default="project_operator")
    parser.add_argument("--reason", default=(
        "The provider terminated the previous attempt at its output cap with no "
        "parseable action; retry the same request scope with a higher output cap."))
    args = parser.parse_args()
    config = json.loads((args.dir / "run-config.json").read_text())
    project_ref = json.loads((args.dir / "project.json").read_text())["project_ref"]
    store = ArtifactStore(args.dir / "store")
    view = projects.inspect(store, project_ref)
    if view["state"] != "output_budget_exhausted":
        raise SystemExit("project is not in output_budget_exhausted: %s" % view["state"])
    old_cap = view.get("output_budget_attempt_ref") and json.loads(
        (args.dir / "run-config.json").read_text()).get("max_tokens")
    if not os.environ.get("BIOMODEL_API_KEY"):
        raise SystemExit("BIOMODEL_API_KEY missing")
    provider = ChatProvider(config["base_url"] if "base_url" in config
                            else "https://api.llm.ustc.edu.cn/v1",
                            config["model"], timeout_s=config["timeout_s"],
                            max_tokens=args.max_tokens, json_mode=True,
                            reasoning_effort="max", stream=bool(config.get("stream")))
    plan_ref = budgets.plan(store, project_ref, view["output_budget_attempt_ref"],
                            provider, actor=args.actor, reason=args.reason)
    started = time.time()
    error = None
    try:
        snapshot = supervisor.run(store, provider, project_ref)
    except Exception as exc:
        error = type(exc).__name__ + ": " + str(exc)[:300]
        snapshot = projects.snapshot(store, project_ref)
    after = projects.inspect(store, project_ref)
    result = dict(status="development_diagnostic_not_reproduction_success",
                  plan_ref=plan_ref, previous_max_tokens=config.get("max_tokens"),
                  new_max_tokens=args.max_tokens, error=error,
                  elapsed_s=round(time.time() - started, 1),
                  snapshot_ref=snapshot, view=after,
                  usage=supervisor.usage(store, project_ref))
    (args.dir / "resume-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(state=after["state"],
                          planner_calls=after["budget"]["planner_calls"],
                          tokens=result["usage"]["reported_total_tokens"],
                          plan_ref=plan_ref)))


if __name__ == "__main__":
    main()
