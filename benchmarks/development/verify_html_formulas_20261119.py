"""Offline verification that projected MathML is readable through the normal
project source entrypoint (no model calls, no budget consumption).

Freezes the official PMC HTML by SHA256, ingests it, and asks the project
`inspect_source` operation for each display-formula span. Fails if a formula
cannot be read back verbatim or if the planner budget moves.
"""
import argparse
import hashlib
import json
from pathlib import Path

from biomodel_agent import background_html, evidence, research_projects as projects
from biomodel_agent.storage import ArtifactStore

EXPECTED_SHA256 = "bc0c60cc78900dc7890124e450f7d3becf41f6ac08ad17fa03b3ebf6331c64cd"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--html", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    raw = args.html.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if sha != EXPECTED_SHA256:
        raise ValueError("unexpected PMC HTML bytes: %s" % sha)
    args.output.mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(args.output / "store")
    source = evidence.ingest_source(store, args.html)
    payload = store.get(source)["payload"]
    page = payload["pages"][0]["text"]

    project = projects.create(store, dict(
        mode="reproduction",
        source_refs=[source],
        scenario="Offline formula-projection check only; no model calls.",
        policy=dict(planner_max_calls=0, max_literature_requests=0,
                    max_transcription_calls=0, max_iterations=1,
                    max_native_calls=1, native_calls_per_iteration=1,
                    total_timeout_s=60)))
    reads = []
    for record in payload["formula_sources"]:
        if not record["display"]:
            continue
        span = record["source_spans"][0]
        result = projects.operate(store, project, "inspect_source",
                                  dict(source_ref=source, page=1,
                                       start=span["start"], end=span["end"]))
        reads.append(dict(formula_id=record["formula_id"],
                          locator=record["locator"]["path"],
                          status=record["status"],
                          exact=result["text"] == span["quote"],
                          keeps_fraction="<mfrac>" in result["text"],
                          keeps_script=True if ("<msub>" in result["text"]
                                                or "<msup>" in result["text"]) else False,
                          characters=len(result["text"])))
    view = projects.inspect(store, project)
    report = dict(
        status="offline_formula_projection_verified",
        source_sha256=sha, parser_version=payload["parser_version"],
        raw_mathml=payload["formula_source_count"],
        display=len(reads), inline=payload["formula_source_count"] - len(reads),
        projected=sum(1 for r in payload["formula_sources"] if r["source_spans"]),
        unprojected=sum(1 for r in payload["formula_sources"] if not r["source_spans"]),
        unreviewed=sum(1 for r in payload["formula_sources"] if r["status"] == "unreviewed"),
        reads=reads,
        planner_calls=view["budget"]["planner_calls"],
        source_status=payload.get("warnings") and "needs_review" or "indexed",
        model_calls=0)
    (args.output / "formula-projection.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("status", "raw_mathml", "display",
                                            "projected", "unprojected", "planner_calls")}))
    if not all(r["exact"] and r["keeps_fraction"] for r in reads):
        raise SystemExit("formula read-back failed")
    if view["budget"]["planner_calls"] != 0:
        raise SystemExit("planner budget moved during an offline read")


if __name__ == "__main__":
    main()
