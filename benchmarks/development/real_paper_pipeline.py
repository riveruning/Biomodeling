"""Offline preparation and public-CLI execution adapter for the real-paper pack.

This module is an engineering adapter, not a scientific evaluator.  Preparation
validates the accepted taskpack, the unchanged full source PDF and a strict
public scenario/constraints projection without invoking a model.  Execution
composes only the existing public ``biomodel_agent.cli`` commands and keeps a
durable reservation/receipt journal: an interrupted or unknown command effect is
never automatically replayed.

The current taskpack has ``execution_authorized=false``.  ``run`` therefore
refuses to invoke any public command against it.  Preparation now freezes the
public ``research-project-run`` transport options (``--reasoning-effort max``,
``--max-output-tokens 8000`` and ``--provider-timeout 900``) and records that
they are forwarded by the public CLI; this is not an independent verification of
service-internal effort handling.  Preparation also freezes the default
source-ingest PDF parser (``pypdf``) together with its installed distribution
version, queried from the live environment and re-queried at every verification.
Legacy blocked-transport plans, PDF-only version drift, packs without a frozen
PDF binding and unqueryable engine versions are all refused before the first
command, while read-only ``show`` of their history remains available.
"""
from __future__ import annotations

import argparse
import datetime as _datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from biomodel_agent.local_ownership import OwnerBusy, owned
from biomodel_agent.recovery import implementation_fingerprint

MANIFEST_NAME = "preparation-manifest.json"
PLAN_NAME = "plan.json"
PUBLIC_INPUT_NAME = "public-input.json"
RUN_META_NAME = "run.json"
JOURNAL_NAME = "journal.jsonl"
RECEIPTS_NAME = "receipts.jsonl"
BUDGET_LEDGER_NAME = "budget-ledger.json"
STATUS_NAME = "status.json"
PROJECTS_DIR = "projects"
RUNTIME_DIR = "runtime"
DIAGNOSTICS_DIR = "diagnostics"
LOCK_NAME = ".adapter.lock"

PUBLIC_INPUT_FIELDS = ("scenario", "constraints")
MAX_CONSTRAINTS = 13
SELECTED_TRACK = "original_fulltext"
ASSISTED_TRACK = "named_transcription_assisted"
ALLOWED_MODEL_NAMES = frozenset({"deepseek-flash-2", "glm-5.3-flash"})
# Product-reported states that make the current operation non-successful and
# stop dispatch.  Values are (durable adapter receipt state, error_type).
# Unknown effects stay unknown so recovery never replays them; timed-out and
# deadline-exhausted products use failed receipts with a specific error type.
# "stopped" is deliberately absent: it is a normal safe product stop and the
# existing successful-dispatch contract treats it as completed.
PRODUCT_NEGATIVE_STATES = {
    "failed": ("failed", "product_reported_failure"),
    "error": ("failed", "product_reported_failure"),
    "timed_out": ("failed", "product_reported_timed_out"),
    "deadline_exhausted": ("failed", "product_reported_deadline_exhausted"),
    "needs_recovery": ("unknown", "product_reported_unknown_effect"),
    "unknown_effects": ("unknown", "product_reported_unknown_effect"),
    "unknown": ("unknown", "product_reported_unknown_effect"),
}
PUBLIC_COMMANDS = (
    "source-ingest",
    "research-project-create",
    "research-project-show",
    "research-project-run",
    "research-project-snapshot",
)
# The default public source-ingest PDF parser.  The installed distribution
# version is queried from the live environment (importlib.metadata) when the
# version binding is frozen and again at every verification; it is never
# hard-coded here and never cached at module import.
PDF_PARSER_NAME = "pypdf"
PDF_PARSER_DISTRIBUTION = "pypdf"
MAX_OUTPUT_TOKENS_PER_CALL = 8000
PROVIDER_TIMEOUT_S = 900.0
PROJECT_RUN_WATCHDOG_GRACE_S = 60.0
OPERATION_TIMEOUTS = {
    "source-ingest": 300.0,
    "project_create": 120.0,
    "project_show": 120.0,
    # The public product deadline is 900 s.  The runner watchdog allows a small
    # supervision/receipt-persistence grace outside that product deadline; it
    # never extends the product budget.
    "project_run": PROVIDER_TIMEOUT_S + PROJECT_RUN_WATCHDOG_GRACE_S,
    "project_snapshot": 120.0,
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class AdapterError(ValueError):
    """A refused or malformed adapter operation."""


class ProtocolError(AdapterError):
    """The frozen taskpack does not satisfy the adapter's admission gates."""


class ExecutionNotAuthorized(AdapterError):
    """A real public execution was requested without protocol authorization."""


class InterfaceBlocked(AdapterError):
    """The public CLI cannot transmit a frozen protocol interface setting."""


class PdfEngineUnavailable(AdapterError):
    """The installed PDF parser version cannot be queried, so nothing is frozen."""


class ConcurrentRunError(RuntimeError):
    """Another writer currently owns the same run directory."""


@dataclass(frozen=True)
class RunnerResult:
    """Minimal injectable result of one public CLI invocation."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


class RunnerTimeout(TimeoutError):
    """A timed-out public CLI call that may still carry partial output."""

    def __init__(self, message: str, stdout: str = "", stderr: str = ""):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


Runner = Callable[[Sequence[str], float], RunnerResult]


def _require(condition: Any, message: str) -> None:
    if not condition:
        raise ProtocolError(message)


def _bounded_text(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise AdapterError(f"{label} must be nonempty text of at most {maximum} characters")
    return value


def _integer(value: Any, label: str, low: int | None = None, high: int | None = None) -> int:
    if type(value) is not int:
        raise ProtocolError(f"{label} must be an integer")
    if low is not None and value < low:
        raise ProtocolError(f"{label} is below the allowed bound")
    if high is not None and value > high:
        raise ProtocolError(f"{label} is above the allowed bound")
    return value


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2) + "\n"


def _decode_output(value: Any) -> str:
    """Decode CLI bytes explicitly as UTF-8, never via the Windows ANSI codec."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        # backslashreplace keeps undecodable bytes visible in diagnostics.
        return value.decode("utf-8", errors="backslashreplace")
    if isinstance(value, str):
        return value
    return str(value)


def _argv_number(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdapterError("plan transport value must be numeric")
    number = float(value)
    if not number.is_integer():
        return repr(number)
    return str(int(number))


def _read_json(path: os.PathLike[str] | str, label: str) -> dict:
    try:
        with open(path, encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterError(f"cannot read {label} JSON: {type(exc).__name__}") from None
    if not isinstance(value, dict):
        raise AdapterError(f"{label} must be a JSON object")
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        raise AdapterError(f"{label} is not finite JSON") from None
    return value


def _sha256_file(path: os.PathLike[str] | str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _file_receipt(path: os.PathLike[str] | str, expected_sha256: str, expected_bytes: int) -> dict:
    path = Path(path)
    if not path.is_file():
        raise AdapterError("source PDF does not exist")
    digest = hashlib.sha256()
    total = 0
    with open(path, "rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            total += len(block)
            digest.update(block)
    actual = digest.hexdigest()
    if total != expected_bytes:
        raise AdapterError(f"source PDF bytes mismatch: expected {expected_bytes}, got {total}")
    if actual != expected_sha256:
        raise AdapterError("source PDF sha256 mismatch")
    return {"sha256": actual, "bytes": total}


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _write_json(path: Path, value: Any) -> None:
    _atomic_write(path, _json_text(value).encode("utf-8"))


def _append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = _json_bytes(value) + b"\n"
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    try:
        written = 0
        while written < len(line):
            written += os.write(descriptor, line[written:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise AdapterError(f"cannot read {path.name}: {type(exc).__name__}") from None
    result = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            raise AdapterError(
                f"{path.name} line {number} is corrupt; refusing automatic replay"
            ) from None
        if not isinstance(value, dict):
            raise AdapterError(f"{path.name} line {number} is not an object")
        result.append(value)
    return result


class RunDirLock:
    """Exclusive writer lock for one run directory.

    Ownership comes from the project's public ``local_ownership`` primitive,
    which holds an OS descriptor rather than trusting the lock file's existence.
    Process exit therefore releases the lock; the marker file left behind is
    inert and must never become a permanent blocker.
    """

    def __init__(self, run_dir: os.PathLike[str] | str):
        self.run_dir = Path(run_dir)
        self.path = self.run_dir / LOCK_NAME
        self._context = None

    def __enter__(self) -> "RunDirLock":
        try:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            context = owned(self.path)
            context.__enter__()
        except OwnerBusy:
            raise ConcurrentRunError("another writer owns this run directory") from None
        except ValueError as exc:
            raise AdapterError(f"run directory lock is unusable: {exc}") from None
        self._context = context
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        context = self._context
        self._context = None
        if context is not None:
            context.__exit__(exc_type, exc, traceback)


def load_protocol(path: os.PathLike[str] | str) -> dict:
    """Load and validate the accepted schema-2 taskpack admission gates."""
    taskpack = _read_json(path, "taskpack")
    _require(taskpack.get("schema_version") == 2, "taskpack schema_version must be 2")
    _bounded_text(taskpack.get("task_id"), "taskpack.task_id", 300)
    _require(type(taskpack.get("execution_authorized")) is bool,
             "taskpack.execution_authorized must be boolean")
    _require(isinstance(taskpack.get("status"), str) and taskpack["status"],
             "taskpack.status must be nonempty text")
    _require(taskpack.get("scientific_support") == "unknown",
             "taskpack.scientific_support must remain unknown")
    _require(taskpack.get("independent_review") == "pending",
             "taskpack.independent_review must remain pending")

    models = taskpack.get("allowed_models")
    _require(isinstance(models, list) and len(models) == 2,
             "taskpack must declare exactly two allowed models")
    for model in models:
        _bounded_text(model, "allowed model", 200)
    _require(len(set(models)) == 2,
             "taskpack must declare exactly two distinct allowed models")
    _require(set(models) == ALLOWED_MODEL_NAMES,
             "taskpack.allowed_models must be exactly the frozen models: "
             "deepseek-flash-2 and glm-5.3-flash")
    _require(taskpack.get("requested_effort") == "max",
             "taskpack.requested_effort must be max")
    constraints = taskpack.get("model_constraints")
    _require(isinstance(constraints, dict), "taskpack.model_constraints must be an object")
    _bounded_text(constraints.get("selection"), "model_constraints.selection", 300)
    _require(constraints.get("fallback_models") == [],
             "taskpack must not declare fallback models")

    budget = taskpack.get("budget")
    _require(isinstance(budget, dict), "taskpack.budget must be an object")
    for key, expected in (
        ("papers", 1),
        ("project_tracks", 2),
        ("planner_calls_per_project", 12),
        ("native_calls_per_project", 4),
        ("planner_calls_total", 24),
        ("native_calls_total", 8),
        ("total_project_timeout_s", 1800),
    ):
        _integer(budget.get(key), f"budget.{key}", 0)
        _require(budget[key] == expected, f"budget.{key} differs from the frozen value")
    _integer(budget.get("total_timeout_s_per_project"),
             "budget.total_timeout_s_per_project", 1)
    _require(budget["total_timeout_s_per_project"] == 900,
             "budget.total_timeout_s_per_project differs from the frozen value")
    _integer(budget.get("max_output_tokens_per_call"),
             "budget.max_output_tokens_per_call", 1)
    _require(budget["max_output_tokens_per_call"] == MAX_OUTPUT_TOKENS_PER_CALL,
             "budget.max_output_tokens_per_call differs from the frozen value")
    _require(budget.get("cumulative_across_resumes") is True,
             "budget must be cumulative across resumes")
    _require(budget.get("reset_on_restart") is False,
             "budget must not reset on restart")
    _require(budget.get("increase_requires_new_freeze") is True,
             "budget increases require a new freeze")

    paper = taskpack.get("paper_candidate")
    _require(isinstance(paper, dict), "taskpack.paper_candidate must be an object")
    sha256 = paper.get("sha256")
    _require(isinstance(sha256, str) and bool(_SHA256_RE.fullmatch(sha256)),
             "paper_candidate.sha256 must be lowercase hex sha256")
    _integer(paper.get("bytes"), "paper_candidate.bytes", 1)
    _bounded_text(paper.get("input_path"), "paper_candidate.input_path", 2000)
    _require(paper.get("seen_status") == "previously_seen_development",
             "paper must remain previously_seen_development")
    _require(paper.get("development_use_only") is True,
             "paper must remain development-use-only")
    _require(paper.get("development_pilot_use_allowed") is True,
             "development pilot use must remain allowed")
    _require(paper.get("formal_unseen_holdout_use_forbidden") is True,
             "formal unseen-holdout use must remain forbidden")
    evidence = paper.get("local_pdf_metadata_evidence")
    if evidence is not None:
        _require(isinstance(evidence, dict),
                 "paper_candidate.local_pdf_metadata_evidence must be an object")
        _bounded_text(evidence.get("evidence_path"),
                      "local_pdf_metadata_evidence.evidence_path", 2000)
        _require(evidence.get("evidence_status") == "locally_checked_frozen_pdf_only",
                 "local PDF metadata evidence must remain local-only")
        _require(evidence.get("source_sha256") == sha256,
                 "local PDF metadata evidence must reference the frozen source sha256")
        _require(evidence.get("page") == 1,
                 "local PDF metadata evidence must reference page 1")
        _require(evidence.get("publication_verified") is False,
                 "local PDF metadata evidence must not promote external publication verification")
        _require(evidence.get("doi_candidate_status") == "not_verified_not_asserted",
                 "printed DOI candidate must remain not verified and not asserted")
        _require(evidence.get("independent_review") == "pending",
                 "local PDF metadata evidence must remain pending independent review")
        _require(paper.get("doi") is None,
                 "a printed DOI candidate must not be promoted to a verified DOI")
        _require(paper.get("doi_status") == "not_verified_not_asserted",
                 "paper DOI status must remain not verified and not asserted")
        _require(paper.get("publication_verified") is False,
                 "paper publication must remain externally unverified")

    accounting = taskpack.get("paper_accounting")
    _require(isinstance(accounting, dict), "taskpack.paper_accounting must be an object")
    for key, expected in (
        ("unique_seen_development_papers", 1),
        ("tracks_on_same_paper", 2),
        ("paper_level_denominator", 1),
        ("track_level_denominator", 2),
    ):
        _integer(accounting.get(key), f"paper_accounting.{key}", 0)
        _require(accounting[key] == expected, f"paper_accounting.{key} differs from the frozen value")
    _require(accounting.get("dedupe_key") == "sha256",
             "paper accounting must deduplicate by sha256")
    _require(accounting.get("tracks_count_as_additional_papers") is False,
             "tracks must not count as additional papers")

    tracks = taskpack.get("tracks")
    _require(isinstance(tracks, list) and len(tracks) == 2,
             "taskpack must contain exactly two tracks")
    by_id = {track.get("id"): track for track in tracks if isinstance(track, dict)}
    _require(set(by_id) == {SELECTED_TRACK, ASSISTED_TRACK},
             "taskpack track IDs differ from the frozen two-track protocol")
    _require(by_id[SELECTED_TRACK].get("uses_operator_transcription") is False,
             "original_fulltext must not use operator transcription")
    _require(by_id[ASSISTED_TRACK].get("uses_operator_transcription") is True,
             "named_transcription_assisted must declare operator transcription")
    for track in tracks:
        _require(track.get("source_sha256") == sha256,
                 "both tracks must map to the same frozen source sha256")
        _require(track.get("counts_as_additional_paper") is False,
                 "neither track may count as an additional paper")

    isolation = taskpack.get("isolation")
    _require(isinstance(isolation, dict), "taskpack.isolation must be an object")
    for key in ("private_numeric_reference", "scoring_rubric"):
        value = isolation.get(key)
        _require(isinstance(value, dict), f"isolation.{key} must be an object")
        _require(value.get("in_public_projection") is False,
                 f"isolation.{key} must stay outside the public projection")
    clarifications = isolation.get("future_clarifications")
    _require(isinstance(clarifications, dict), "isolation.future_clarifications must be an object")
    _require(clarifications.get("post_freeze_policy") == "quarantine",
             "future clarifications must remain quarantined")
    _require(clarifications.get("usable_without_amendment") is False,
             "future clarifications must not be usable without amendment")
    _require(isolation.get("canary_test_required") is True,
             "taskpack requires an isolation canary test")

    execution = taskpack.get("execution_policy")
    _require(isinstance(execution, dict), "taskpack.execution_policy must be an object")
    _require(execution.get("unknown_effect_replay") is False,
             "unknown effects must not be replayed")
    _require(execution.get("unknown_effect_retry") is False,
             "unknown effects must not be retried")
    _require(execution.get("failed_or_missing_runs_in_denominator") is True,
             "failed/missing runs must stay in the denominator")
    recovery = execution.get("session_recovery")
    _require(isinstance(recovery, dict), "execution_policy.session_recovery must be an object")
    _require(recovery.get("durable_event_log") is True,
             "durable event logging is required")
    _require(recovery.get("budget_reset_on_resume") is False,
             "budget must not reset on resume")
    _require(recovery.get("lost_work_policy") == "record_missing_and_retain_in_denominator",
             "lost work must be recorded as missing and retained")

    denominator = taskpack.get("denominator_policy")
    _require(isinstance(denominator, dict), "taskpack.denominator_policy must be an object")
    include = denominator.get("include")
    _require(isinstance(include, list)
             and {"planned", "started", "failed", "timed_out", "missing", "unknown"} <= set(include),
             "denominator must include planned/started/failed/timed-out/missing/unknown")
    _require(denominator.get("post_hoc_exclusion") is False,
             "post-hoc exclusion must remain forbidden")

    claims = taskpack.get("completion_claims")
    _require(isinstance(claims, dict), "taskpack.completion_claims must be an object")
    _require(claims.get("all_pipeline_tasks_complete") is False,
             "taskpack must not claim all pipeline tasks complete")
    _require(claims.get("self_signed_completion") is False,
             "taskpack must not self-sign completion")
    _require(claims.get("independent_review_signed") is False,
             "taskpack must not claim independent review")
    return taskpack


def _load_public_input(path: os.PathLike[str] | str) -> dict:
    value = _read_json(path, "public input")
    unknown = sorted(set(value) - set(PUBLIC_INPUT_FIELDS))
    if unknown:
        raise AdapterError("unknown public input field(s): " + ", ".join(unknown))
    scenario = _bounded_text(value.get("scenario"), "public input.scenario", 4000)
    constraints = value.get("constraints")
    if not isinstance(constraints, list) or len(constraints) > MAX_CONSTRAINTS:
        raise AdapterError(
            f"public input.constraints must be a list of at most {MAX_CONSTRAINTS} strings"
        )
    normalized = []
    for index, item in enumerate(constraints):
        if not isinstance(item, str) or not item.strip() or len(item) > 2000:
            raise AdapterError(
                f"public input.constraints[{index}] must be nonempty text of at most 2000 characters"
            )
        normalized.append(item)
    return {"scenario": scenario, "constraints": normalized}


def _load_canaries(paths: Iterable[os.PathLike[str] | str]) -> list[str]:
    values: list[str] = []
    for path in paths:
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise AdapterError(f"cannot read private canary file: {type(exc).__name__}") from None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            items = parsed
        elif isinstance(parsed, str):
            items = [parsed]
        else:
            items = raw.splitlines()
        for item in items:
            if not isinstance(item, str):
                raise AdapterError("private canary file must contain strings")
            if item and item not in values:
                values.append(item)
    if len(values) > 64 or any(len(item) > 512 for item in values):
        raise AdapterError("at most 64 private canaries of at most 512 characters are supported")
    return values


def _public_strings(value: Any) -> Iterable[str]:
    """Yield actual string keys/values from a generated public structure.

    This deliberately walks the decoded structure rather than searching encoded
    JSON text, where quotes, backslashes and newlines are escaped.  Each key and
    value is checked separately, so a literal canary cannot match across field
    boundaries or through JSON serialization.
    """
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str):
                yield key
            yield from _public_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _public_strings(item)


def _assert_canaries_absent(payloads: Sequence[Any], canaries: Sequence[str]) -> None:
    if not canaries:
        return
    for payload in payloads:
        for text in _public_strings(payload):
            for canary in canaries:
                if canary in text:
                    raise AdapterError(
                        "private canary value found in the public projection; "
                        "preparation refused"
                    )


def _project_policy(protocol: Mapping[str, Any]) -> dict:
    budget = protocol["budget"]
    return {
        "planner_max_calls": budget["planner_calls_per_project"],
        "max_iterations": budget["native_calls_per_project"],
        "max_native_calls": budget["native_calls_per_project"],
        "native_calls_per_iteration": budget["native_calls_per_project"],
        "max_literature_requests": 0,
        "context_max_chars": 60000,
        "assumptions": "agent_scoped",
        "total_timeout_s": budget["total_timeout_s_per_project"],
    }


def _project_config(public: Mapping[str, Any], protocol: Mapping[str, Any], track: Mapping[str, Any]) -> dict:
    if track["id"] == ASSISTED_TRACK:
        # Placeholder only: no assisted project is created or scheduled.  Its
        # constraints do not inherit the original track's blanket transcription
        # prohibition; they permit only named, attributed transcriptions while
        # still requiring the complete original to be retained unchanged.
        fixed_constraints = [
            f"Frozen taskpack track: {track['id']}.",
            "Development pilot only; this seen article must not be reported as a formal unseen holdout.",
            "Named human transcription assistance is permitted only when every revision carries actor, timestamp, reason, original region and affected fields; Retain the complete original PDF unchanged, and do not present revised text as the original article or as independent replication.",
        ]
    else:
        fixed_constraints = [
            f"Frozen taskpack track: {track['id']}.",
            "Development pilot only; this seen article must not be reported as a formal unseen holdout.",
            "Retain the complete original PDF unchanged; do not substitute a summary, transcription, synthetic declaration or historical answer.",
        ]
    constraints = fixed_constraints + list(public["constraints"])
    if len(constraints) > 16:
        raise AdapterError("project constraints exceed the public CLI limit")
    return {
        "scenario": public["scenario"],
        "mode": "reproduction",
        "policy": _project_policy(protocol),
        "constraints": constraints,
    }


def _operations() -> list[dict]:
    raw = [SELECTED_TRACK]
    return [
        {
            "id": "source-ingest",
            "sequence": 1,
            "kind": "source_ingest",
            "command": "source-ingest",
            "track_scope": [SELECTED_TRACK, ASSISTED_TRACK],
            "depends_on": [],
            "timeout_s": OPERATION_TIMEOUTS["source-ingest"],
            "argv_template": ["source-ingest", "{source_pdf}", "--store", "{store}", "--pdf-parser", "{pdf_parser}"],
            "output_ref_field": "ref",
        },
        {
            "id": "original_fulltext.create",
            "sequence": 2,
            "kind": "project_create",
            "command": "research-project-create",
            "track_scope": raw,
            "depends_on": ["source-ingest"],
            "timeout_s": OPERATION_TIMEOUTS["project_create"],
            "argv_template": ["research-project-create", "--store", "{store}", "--config", "{runtime_project_config}"],
            "output_ref_field": "project_ref",
        },
        {
            "id": "original_fulltext.show_after_create",
            "sequence": 3,
            "kind": "project_show",
            "command": "research-project-show",
            "track_scope": raw,
            "depends_on": ["original_fulltext.create"],
            "timeout_s": OPERATION_TIMEOUTS["project_show"],
            "argv_template": ["research-project-show", "{project_ref}", "--store", "{store}"],
            "output_ref_field": "project_ref",
        },
        {
            "id": "original_fulltext.run",
            "sequence": 4,
            "kind": "project_run",
            "command": "research-project-run",
            "track_scope": raw,
            "depends_on": ["original_fulltext.show_after_create"],
            "timeout_s": OPERATION_TIMEOUTS["project_run"],
            "product_deadline_s": PROVIDER_TIMEOUT_S,
            "watchdog_grace_s": PROJECT_RUN_WATCHDOG_GRACE_S,
            "watchdog_grace_scope": "supervision_and_receipt_persistence_only",
            "argv_template": [
                "research-project-run", "{project_ref}", "--store", "{store}", "--model", "{model}",
                "--reasoning-effort", "{requested_effort}",
                "--max-output-tokens", "{max_output_tokens}",
                "--provider-timeout", "{provider_timeout}",
            ],
        },
        {
            "id": "original_fulltext.show_after_run",
            "sequence": 5,
            "kind": "project_show",
            "command": "research-project-show",
            "track_scope": raw,
            "depends_on": ["original_fulltext.run"],
            "timeout_s": OPERATION_TIMEOUTS["project_show"],
            "argv_template": ["research-project-show", "{project_ref}", "--store", "{store}"],
            "output_ref_field": "project_ref",
        },
        {
            "id": "original_fulltext.snapshot",
            "sequence": 6,
            "kind": "project_snapshot",
            "command": "research-project-snapshot",
            "track_scope": raw,
            "depends_on": ["original_fulltext.show_after_run"],
            "timeout_s": OPERATION_TIMEOUTS["project_snapshot"],
            "argv_template": ["research-project-snapshot", "{project_ref}", "--store", "{store}"],
        },
    ]


def _interface_blockers(protocol: Mapping[str, Any]) -> list[dict]:
    forwarded_status = "resolved_public_cli_forwarding_not_service_verified"
    return [
        {
            "id": "reasoning_effort_not_forwarded",
            "public_entry": "research-project-run",
            "status": forwarded_status,
            "forwarded_option": "--reasoning-effort",
            "forwarded_value": protocol["requested_effort"],
            "note": (
                "Legacy blocker id retained for continuity. The new preparation plan passes "
                "--reasoning-effort=max to the public run command. This proves public-CLI "
                "option forwarding only; it does not inspect or independently verify the "
                "model service's internal effort tier."
            ),
        },
        {
            "id": "max_output_tokens_transport",
            "public_entry": "research-project-run",
            "status": forwarded_status,
            "forwarded_option": "--max-output-tokens",
            "forwarded_value": protocol["budget"]["max_output_tokens_per_call"],
            "note": (
                "The new preparation plan passes --max-output-tokens=8000 to the public run "
                "command. No service-internal output-limit behaviour is independently verified."
            ),
        },
        {
            "id": "provider_timeout_transport",
            "public_entry": "research-project-run",
            "status": forwarded_status,
            "forwarded_option": "--provider-timeout",
            "forwarded_value": protocol["budget"]["total_timeout_s_per_project"],
            "note": (
                "The public CLI receives the frozen per-project deadline as a single-call "
                "cap; the product's existing remaining_s still trims it to the project's "
                "remaining time. The adapter watchdog adds a supervision/persistence grace "
                "only and does not increase policy.total_timeout_s or the budget."
            ),
        },
        {
            "id": "service_internal_effort_verification",
            "public_entry": "research-project-run",
            "status": "not_independently_verified",
            "note": (
                "Option forwarding and a MockTransport HTTP-body check do not prove that the "
                "remote service honours max effort or 8000 output tokens internally. The "
                "adapter makes no such claim."
            ),
        },
        {
            "id": "batch_paper_identifier_unavailable",
            "public_entry": "research-batch-create",
            "status": "not_used",
            "note": (
                "A public batch case requires a DOI, PMCID or PMID. The frozen candidate DOI is "
                "null/unverified, so this adapter does not fabricate an identifier or emit a batch "
                "config; it uses the public per-project commands instead."
            ),
        },
        {
            "id": "scientific_execution_authorization",
            "public_entry": "research-project-run",
            "status": "blocked_by_protocol" if protocol.get("execution_authorized") is not True else "authorized_test_or_future_pack",
            "note": (
                "The adapter refuses real public run commands unless the supplied frozen taskpack "
                "has execution_authorized=true. The current accepted pack remains false."
            ),
        },
        {
            "id": "named_transcription_absent",
            "public_entry": "research-project-run",
            "status": "pending_human_input",
            "note": (
                "No named human transcription was supplied to this offline adapter. The assisted "
                "track therefore remains missing/pending in the denominator, actor is null, and no "
                "assisted run is scheduled. Its placeholder config permits only named, attributed "
                "transcription; no actor or revision is fabricated."
            ),
        },
    ]


def _build_outputs(
    protocol: Mapping[str, Any],
    public: Mapping[str, Any],
    source_receipt: dict,
    public_input_path: os.PathLike[str] | str,
    taskpack_path: os.PathLike[str] | str,
    taskpack_sha256: str,
    selected_model: str,
    canary_count: int,
    prepared_at: str,
    version_binding: Mapping[str, Any],
) -> tuple[dict, dict, dict[str, dict], dict, list[Any]]:
    tracks = {track["id"]: track for track in protocol["tracks"]}
    project_configs = {
        track_id: _project_config(public, protocol, tracks[track_id])
        for track_id in (SELECTED_TRACK, ASSISTED_TRACK)
    }
    operations = _operations()
    for operation in operations:
        if operation["kind"] == "project_run":
            # Resolve the frozen transport values in the reviewable plan itself;
            # only the run-time project/store/model references remain templated.
            operation["argv_template"] = [
                "research-project-run", "{project_ref}", "--store", "{store}",
                "--model", "{model}",
                "--reasoning-effort", protocol["requested_effort"],
                "--max-output-tokens",
                _argv_number(protocol["budget"]["max_output_tokens_per_call"]),
                "--provider-timeout",
                _argv_number(protocol["budget"]["total_timeout_s_per_project"]),
            ]
    operations_by_track = {SELECTED_TRACK: [operation["id"] for operation in operations],
                           ASSISTED_TRACK: []}
    budget = {
        "papers": protocol["budget"]["papers"],
        "project_tracks": protocol["budget"]["project_tracks"],
        "planner_calls_per_project": protocol["budget"]["planner_calls_per_project"],
        "native_calls_per_project": protocol["budget"]["native_calls_per_project"],
        "planner_calls_total": protocol["budget"]["planner_calls_total"],
        "native_calls_total": protocol["budget"]["native_calls_total"],
        "total_timeout_s_per_project": protocol["budget"]["total_timeout_s_per_project"],
        "total_project_timeout_s": protocol["budget"]["total_project_timeout_s"],
        "max_output_tokens_per_call": protocol["budget"]["max_output_tokens_per_call"],
        "provider_timeout_s": protocol["budget"]["total_timeout_s_per_project"],
        "project_run_watchdog_s": OPERATION_TIMEOUTS["project_run"],
        "project_run_watchdog_grace_s": PROJECT_RUN_WATCHDOG_GRACE_S,
        "watchdog_grace_scope": "supervision_and_receipt_persistence_only",
        "product_policy_total_timeout_s": protocol["budget"]["total_timeout_s_per_project"],
        "product_policy_total_timeout_s_unchanged": True,
        "cumulative_across_resumes": True,
        "reset_on_restart": False,
        "increase_requires_new_freeze": True,
        "adapter_policy_note": (
            "Per-project policy values come from the frozen taskpack. max_iterations and "
            "native_calls_per_iteration are bounded to native_calls_per_project; the public "
            "supervisor remains the authority for actual planner/native usage. The project-run "
            "watchdog grace is supervision/persistence only and is not additional model budget."
        ),
    }
    effort_transport = {
        "requested_effort": protocol["requested_effort"],
        "status": "forwarded_via_public_cli",
        "interface": "research-project-run",
        "options": ["--reasoning-effort", "--max-output-tokens", "--provider-timeout"],
        "forwarded": {
            "reasoning_effort": protocol["requested_effort"],
            "max_output_tokens": protocol["budget"]["max_output_tokens_per_call"],
            "provider_timeout_s": protocol["budget"]["total_timeout_s_per_project"],
        },
        "claim": "public_cli_options_forwarded_not_service_internally_verified",
        "service_internal_effort_independently_verified": False,
    }
    plan = {
        "version": 1,
        "kind": "real_paper_public_cli_execution_plan",
        "taskpack": {
            "task_id": protocol["task_id"],
            "path": str(taskpack_path),
            "sha256": taskpack_sha256,
        },
        "execution_gate": {
            "requires_execution_authorized": True,
            "execution_authorized_at_prepare": protocol["execution_authorized"],
        },
        "source": {
            **source_receipt,
            "taskpack_input_path": protocol["paper_candidate"]["input_path"],
            "full_pdf_required": True,
            "retained_unchanged": True,
            "content_substitution_forbidden": True,
        },
        "denominator": {
            "paper_level": 1,
            "track_level": 2,
            "include_states": ["planned", "started", "failed", "timed_out", "missing", "unknown"],
            "post_hoc_exclusion": False,
            "assisted_track_state": "missing_pending_named_transcription",
            "track_ids": [SELECTED_TRACK, ASSISTED_TRACK],
        },
        "budget": budget,
        "model": {
            "allowed": list(protocol["allowed_models"]),
            "selected": selected_model,
            "fallback_models": [],
            "requested_effort": protocol["requested_effort"],
            "max_output_tokens_per_call": protocol["budget"]["max_output_tokens_per_call"],
            "provider_timeout_s": protocol["budget"]["total_timeout_s_per_project"],
            "effort_transport": effort_transport,
        },
        "public_commands": list(PUBLIC_COMMANDS),
        "execution_options": {
            "pdf_parser": "pypdf",
            "store_relative_to_run_dir": "store",
            "python_launcher": [sys.executable, "-X", "utf8", "-m", "biomodel_agent.cli"],
            "provider_timeout_s": protocol["budget"]["total_timeout_s_per_project"],
            "project_run_watchdog_s": OPERATION_TIMEOUTS["project_run"],
            "project_run_watchdog_grace_s": PROJECT_RUN_WATCHDOG_GRACE_S,
            "watchdog_grace_scope": "supervision_and_receipt_persistence_only",
            "product_policy_total_timeout_s": protocol["budget"]["total_timeout_s_per_project"],
            "product_policy_total_timeout_s_unchanged": True,
            "diagnostics_directory": DIAGNOSTICS_DIR,
        },
        "version_binding": dict(version_binding),
        "operations": operations,
        "tracks": [
            {
                "id": SELECTED_TRACK,
                "label": tracks[SELECTED_TRACK]["label"],
                "denominator": "retained",
                "dispatch_state": "planned",
                "project_config": f"{PROJECTS_DIR}/{SELECTED_TRACK}.json",
                "operation_ids": operations_by_track[SELECTED_TRACK],
            },
            {
                "id": ASSISTED_TRACK,
                "label": tracks[ASSISTED_TRACK]["label"],
                "denominator": "retained",
                "dispatch_state": "missing_pending_named_transcription",
                "missing_reason": "named_human_transcription_not_supplied",
                "actor": None,
                "transcription_policy": "named_attributed_transcription_only",
                "original_text_retention_required": True,
                "project_config": f"{PROJECTS_DIR}/{ASSISTED_TRACK}.json",
                "operation_ids": [],
            },
        ],
        "interface_blockers": _interface_blockers(protocol),
        "public_input": {
            "path": str(public_input_path),
            "sha256": hashlib.sha256(_json_bytes(public)).hexdigest(),
            "field_whitelist": list(PUBLIC_INPUT_FIELDS),
            "unknown_fields_rejected": True,
            "constraint_count": len(public["constraints"]),
        },
        "canary": {
            "status": "passed_literal_check" if canary_count else "not_run_no_canaries_supplied",
            "checked_literals": canary_count,
            "scope": "literal_private_canary_values_in_generated_public_artifacts",
            "general_semantic_nonleak_claim": False,
            "isolation_canary_completed": bool(canary_count),
        },
        "claims": {
            "offline_preparation_only": True,
            "model_calls_made": 0,
            "execution_attempted": False,
            "scientific_evaluation_executed": False,
            "public_cli_option_forwarding_prepared": True,
            "service_internal_effort_independently_verified": False,
            "scientific_support": "unknown",
            "independent_review": "pending",
        },
    }

    project_hashes = {
        track_id: hashlib.sha256(_json_text(config).encode("utf-8")).hexdigest()
        for track_id, config in project_configs.items()
    }
    plan_file_sha256 = hashlib.sha256(_json_text(plan).encode("utf-8")).hexdigest()
    manifest = {
        "version": 1,
        "kind": "real_paper_offline_preparation",
        "revision": "offline_public_cli_adapter_v2",
        "prepared_at": prepared_at,
        "status": "prepared_not_executed" if protocol["execution_authorized"] is not True
        else "authorized_not_started",
        "taskpack": {
            "task_id": protocol["task_id"],
            "path": str(taskpack_path),
            "sha256": taskpack_sha256,
            "schema_version": protocol["schema_version"],
            "execution_authorized": protocol["execution_authorized"],
        },
        "paper": {
            "source_path": source_receipt["path"],
            "taskpack_input_path": protocol["paper_candidate"]["input_path"],
            "sha256": source_receipt["sha256"],
            "bytes": source_receipt["bytes"],
            "seen_status": protocol["paper_candidate"]["seen_status"],
            "development_use_only": True,
            "formal_unseen_holdout_use_forbidden": True,
            "unique_papers": 1,
            "tracks_on_same_paper": 2,
            "tracks_count_as_additional_papers": False,
            "raw_article_sent_to_model": False,
        },
        "model": {
            "allowed": list(protocol["allowed_models"]),
            "selected": selected_model,
            "fallback_models": [],
            "requested_effort": protocol["requested_effort"],
            "max_output_tokens_per_call": protocol["budget"]["max_output_tokens_per_call"],
            "provider_timeout_s": protocol["budget"]["total_timeout_s_per_project"],
            "effort_transport": effort_transport,
        },
        "public_input": {
            "path": str(public_input_path),
            "sha256": hashlib.sha256(_json_bytes(public)).hexdigest(),
            "fields": list(PUBLIC_INPUT_FIELDS),
            "unknown_fields_rejected": True,
            "constraint_count": len(public["constraints"]),
        },
        "tracks": plan["tracks"],
        "denominator": plan["denominator"],
        "budget": budget,
        "plan": {
            "path": PLAN_NAME,
            "sha256": plan_file_sha256,
            "operation_count": len(operations),
        },
        "version_binding": dict(version_binding),
        "diagnostics": {
            "directory": DIAGNOSTICS_DIR,
            "receipt_binding": ["path", "sha256", "bytes"],
            "empty_stream_policy": "omitted",
            "scope": "local_public_cli_streams_not_projected_to_public_model_config",
        },
        "project_configs": [
            {
                "track_id": track_id,
                "path": f"{PROJECTS_DIR}/{track_id}.json",
                "sha256": project_hashes[track_id],
            }
            for track_id in (SELECTED_TRACK, ASSISTED_TRACK)
        ],
        "interface_blockers": plan["interface_blockers"],
        "canary": plan["canary"],
        "claims": {
            "preparation_only": True,
            "model_calls_made": 0,
            "execution_attempted": False,
            "execution_authorized": protocol["execution_authorized"],
            "scientific_evaluation_executed": False,
            "isolation_canary_completed": bool(canary_count),
            "public_cli_option_forwarding_prepared": True,
            "service_internal_effort_independently_verified": False,
            "local_stream_diagnostics_projected_to_public_model_config": False,
            "scientific_support": "unknown",
            "independent_review": "pending",
            "self_signed": False,
        },
        "denominator_retained_for_future_failures": True,
        "private_reference_or_rubric_field_accepted": False,
        "future_clarification_field_accepted": False,
        "semantic_nonleak_guarantee": False,
    }

    public_input_projection = {
        "scenario": public["scenario"],
        "constraints": list(public["constraints"]),
        "source_policy": {
            "full_pdf_required": True,
            "content_substitution_forbidden": True,
            "private_reference_or_rubric_field_accepted": False,
            "future_clarification_field_accepted": False,
            "semantic_nonleak_guarantee": False,
        },
    }
    payloads = [plan, manifest, *project_configs.values(), public_input_projection]
    return manifest, plan, project_configs, public_input_projection, payloads


def _installed_pdf_parser_version() -> str:
    """Query the installed default PDF parser version from the live environment.

    The lookup goes through ``importlib.metadata`` on every call, so the version
    is re-queried when the binding is frozen and again at every verification.
    It is never hard-coded and never cached at module import; an unresolvable
    query is an explicit error, never an ``unknown`` value that counts as frozen.
    """
    try:
        import importlib.metadata as _metadata
    except ImportError as exc:  # pragma: no cover - stdlib is always present
        raise PdfEngineUnavailable(
            f"importlib.metadata is unavailable in this environment: {exc}"
        ) from None
    try:
        version = _metadata.version(PDF_PARSER_DISTRIBUTION)
    except _metadata.PackageNotFoundError as exc:
        raise PdfEngineUnavailable(
            f"the frozen default PDF parser distribution '{PDF_PARSER_DISTRIBUTION}' "
            "is not installed in this environment; its version cannot be frozen or "
            "verified"
        ) from None
    except (OSError, ValueError) as exc:
        raise PdfEngineUnavailable(
            f"the installed PDF parser version for '{PDF_PARSER_DISTRIBUTION}' cannot "
            f"be queried: {type(exc).__name__}"
        ) from None
    if not isinstance(version, str) or not version.strip():
        raise PdfEngineUnavailable(
            f"the installed PDF parser version for '{PDF_PARSER_DISTRIBUTION}' is empty"
        )
    return version


def _pdf_engine_binding() -> dict:
    """Freeze the actual default PDF parser name and its installed version."""
    return {
        "parser": PDF_PARSER_NAME,
        "distribution": PDF_PARSER_DISTRIBUTION,
        "version": _installed_pdf_parser_version(),
        "version_source": "importlib.metadata.version",
        "query_policy": "requery_live_environment_at_freeze_and_every_verification",
    }


def _current_version_binding() -> dict:
    """Freeze the adapter file, the public implementation fingerprint and the
    actually installed default PDF engine version."""
    adapter_path = Path(__file__).resolve()
    return {
        "version": 1,
        "adapter_path": str(adapter_path),
        "adapter_sha256": _sha256_file(adapter_path),
        "implementation_fingerprint": implementation_fingerprint(),
        "implementation_fingerprint_source": "biomodel_agent.recovery.implementation_fingerprint",
        "pdf_engine": _pdf_engine_binding(),
        "verification": (
            "exact_adapter_sha256_public_implementation_fingerprint_and_frozen_pdf_"
            "engine_version_before_dispatch"
        ),
        "readonly_history_preserved_on_mismatch": True,
    }


def _verify_frozen_versions(manifest: Mapping[str, Any], plan: Mapping[str, Any]) -> None:
    """Refuse dispatch after any adapter/public-implementation/PDF-engine drift."""
    declared = manifest.get("version_binding")
    if not isinstance(declared, dict) or declared.get("version") != 1:
        raise ProtocolError(
            "legacy preparation artifacts have no frozen adapter/implementation version "
            "binding; execution is refused, read-only show of historical preparation "
            "remains available, and no project, budget or preparation directory is reset"
        )
    plan_binding = plan.get("version_binding")
    if not isinstance(plan_binding, dict) or plan_binding.get("version") != 1:
        raise ProtocolError(
            "legacy execution plan has no frozen adapter/implementation version binding; "
            "execution is refused while read-only show remains available"
        )
    try:
        current = _current_version_binding()
    except PdfEngineUnavailable as exc:
        raise ProtocolError(
            "the installed default PDF engine version cannot be queried in the "
            "current environment; execution is refused, read-only show remains "
            "available, and no project, budget or preparation directory is reset"
        ) from exc
    if declared.get("adapter_sha256") != current["adapter_sha256"]:
        raise ProtocolError(
            "adapter file sha256 changed since preparation; execution is refused, "
            "read-only show remains available, and no project, budget or preparation "
            "directory is reset"
        )
    if declared.get("implementation_fingerprint") != current["implementation_fingerprint"]:
        raise ProtocolError(
            "public implementation fingerprint changed since preparation; execution is "
            "refused, read-only show remains available, and no project, budget or "
            "preparation directory is reset"
        )
    if (
        plan_binding.get("adapter_sha256") != declared.get("adapter_sha256")
        or plan_binding.get("implementation_fingerprint")
        != declared.get("implementation_fingerprint")
    ):
        raise ProtocolError(
            "plan and manifest version bindings differ; execution is refused while "
            "read-only show remains available"
        )
    # The frozen default PDF engine must still be the installed one and must be
    # the very parser that plan.execution_options dispatches with.
    declared_pdf = declared.get("pdf_engine")
    if (
        not isinstance(declared_pdf, dict)
        or not isinstance(declared_pdf.get("version"), str)
        or not declared_pdf["version"].strip()
    ):
        raise ProtocolError(
            "legacy preparation artifacts have no frozen default PDF engine binding; "
            "execution is refused rather than silently re-bound, read-only show of the "
            "historical preparation remains available, and no project, budget or "
            "preparation directory is reset"
        )
    if plan_binding.get("pdf_engine") != declared_pdf:
        raise ProtocolError(
            "plan and manifest PDF engine bindings differ; execution is refused while "
            "read-only show remains available"
        )
    execution_parser = plan.get("execution_options", {}).get("pdf_parser")
    if execution_parser != declared_pdf.get("parser"):
        raise ProtocolError(
            "frozen PDF engine binding does not match the plan execution_options parser; "
            "execution is refused while read-only show remains available"
        )
    current_pdf = current["pdf_engine"]
    if (
        declared_pdf.get("parser") != current_pdf["parser"]
        or declared_pdf.get("version") != current_pdf["version"]
    ):
        raise ProtocolError(
            "installed default PDF engine version changed since preparation; execution "
            "is refused, read-only show remains available, and no project, budget or "
            "preparation directory is reset"
        )


def _pdf_engine_binding_report(
    declared: Mapping[str, Any], current: Mapping[str, Any]
) -> dict:
    """Compare the frozen default PDF engine binding with the live environment."""
    prepared = declared.get("pdf_engine")
    current_pdf = current.get("pdf_engine")
    if (
        not isinstance(prepared, dict)
        or not isinstance(prepared.get("version"), str)
        or not prepared["version"].strip()
    ):
        return {
            "status": "legacy_missing_pdf_engine_binding",
            "prepared": prepared if isinstance(prepared, dict) else None,
            "current": current_pdf,
            "execution_refused": True,
            "diagnostic": (
                "This historical preparation predates the frozen default PDF engine "
                "binding. Execution is refused rather than silently re-bound; read-only "
                "show of the historical preparation remains available."
            ),
        }
    if (
        isinstance(current_pdf, dict)
        and prepared.get("parser") == current_pdf.get("parser")
        and prepared.get("version") == current_pdf.get("version")
    ):
        return {
            "status": "matches_prepared",
            "prepared": prepared,
            "current": current_pdf,
            "execution_refused": False,
        }
    return {
        "status": "pdf_engine_changed_since_preparation",
        "prepared": prepared,
        "current": current_pdf,
        "execution_refused": True,
        "diagnostic": (
            "The installed default PDF parser distribution version differs from the "
            "frozen binding. Execution is refused; read-only show remains available "
            "and no project, budget or preparation directory is reset."
        ),
    }


def _version_binding_report(
    manifest: Mapping[str, Any], plan: Mapping[str, Any] | None = None
) -> dict:
    declared = manifest.get("version_binding")
    if not isinstance(declared, dict) or declared.get("version") != 1:
        return {
            "status": "legacy_missing_version_binding",
            "readonly_history_available": True,
            "execution_refused": True,
            "diagnostic": (
                "This historical preparation predates the frozen adapter/implementation "
                "binding. Execution is refused; read-only show remains available."
            ),
        }
    if plan is not None:
        plan_binding = plan.get("version_binding")
        if not isinstance(plan_binding, dict) or plan_binding.get("version") != 1:
            return {
                "status": "legacy_missing_version_binding",
                "readonly_history_available": True,
                "execution_refused": True,
                "diagnostic": (
                    "This historical execution plan predates the frozen "
                    "adapter/implementation binding. Execution is refused; read-only "
                    "show remains available."
                ),
            }
    try:
        current = _current_version_binding()
    except PdfEngineUnavailable as exc:
        return {
            "status": "current_pdf_engine_unavailable",
            "error_type": type(exc).__name__,
            "prepared_pdf_engine": declared.get("pdf_engine"),
            "readonly_history_available": True,
            "execution_refused": True,
            "diagnostic": (
                "The installed default PDF parser version cannot be queried in this "
                "environment. Execution is refused; read-only show of the historical "
                "preparation remains available."
            ),
        }
    except (OSError, ValueError) as exc:
        return {
            "status": "current_version_unavailable",
            "error_type": type(exc).__name__,
            "readonly_history_available": True,
            "execution_refused": True,
        }
    matches = (
        declared.get("adapter_sha256") == current["adapter_sha256"]
        and declared.get("implementation_fingerprint")
        == current["implementation_fingerprint"]
    )
    if not matches:
        return {
            "status": "changed_since_preparation",
            "prepared_adapter_sha256": declared.get("adapter_sha256"),
            "current_adapter_sha256": current["adapter_sha256"],
            "prepared_implementation_fingerprint": declared.get("implementation_fingerprint"),
            "current_implementation_fingerprint": current["implementation_fingerprint"],
            "pdf_engine": _pdf_engine_binding_report(declared, current),
            "readonly_history_available": True,
            "execution_refused_on_mismatch": True,
        }
    if plan is not None:
        plan_binding = plan["version_binding"]
        if (
            plan_binding.get("adapter_sha256") != declared.get("adapter_sha256")
            or plan_binding.get("implementation_fingerprint")
            != declared.get("implementation_fingerprint")
            or plan_binding.get("pdf_engine") != declared.get("pdf_engine")
        ):
            return {
                "status": "plan_manifest_binding_mismatch",
                "pdf_engine": _pdf_engine_binding_report(declared, current),
                "readonly_history_available": True,
                "execution_refused": True,
            }
    pdf_report = _pdf_engine_binding_report(declared, current)
    if pdf_report["status"] != "matches_prepared":
        return {
            "status": pdf_report["status"],
            "prepared_adapter_sha256": declared.get("adapter_sha256"),
            "current_adapter_sha256": current["adapter_sha256"],
            "prepared_implementation_fingerprint": declared.get("implementation_fingerprint"),
            "current_implementation_fingerprint": current["implementation_fingerprint"],
            "pdf_engine": pdf_report,
            "readonly_history_available": True,
            "execution_refused": True,
        }
    return {
        "status": "matches_prepared",
        "prepared_adapter_sha256": declared.get("adapter_sha256"),
        "current_adapter_sha256": current["adapter_sha256"],
        "prepared_implementation_fingerprint": declared.get("implementation_fingerprint"),
        "current_implementation_fingerprint": current["implementation_fingerprint"],
        "pdf_engine": pdf_report,
        "readonly_history_available": True,
        "execution_refused_on_mismatch": True,
    }


def prepare(
    *,
    taskpack_path: os.PathLike[str] | str,
    public_input_path: os.PathLike[str] | str,
    pdf_path: os.PathLike[str] | str,
    out_dir: os.PathLike[str] | str,
    selected_model: str,
    private_canary_files: Iterable[os.PathLike[str] | str] = (),
) -> dict:
    """Validate frozen inputs and write reviewable preparation artifacts.

    This function performs only local reads/hashes/writes.  It imports no model
    provider and invokes neither the public CLI nor the network.  The three input
    paths are bound as absolute paths so a later working-directory change cannot
    redirect show/verify at a different file.
    """
    taskpack_path = os.path.abspath(os.fspath(taskpack_path))
    public_input_path = os.path.abspath(os.fspath(public_input_path))
    pdf_path = os.path.abspath(os.fspath(pdf_path))
    protocol = load_protocol(taskpack_path)
    _bounded_text(selected_model, "selected model", 200)
    if selected_model not in protocol["allowed_models"]:
        raise AdapterError("selected model is not in taskpack.allowed_models")
    public = _load_public_input(public_input_path)
    taskpack_sha256 = _sha256_file(taskpack_path)
    source = _file_receipt(
        pdf_path,
        protocol["paper_candidate"]["sha256"],
        protocol["paper_candidate"]["bytes"],
    )
    source_receipt = {
        "path": pdf_path,
        "sha256": source["sha256"],
        "bytes": source["bytes"],
        "verified_against_taskpack": True,
        "hash_scope": "exact_full_pdf_bytes",
    }
    canaries = _load_canaries(private_canary_files)
    prepared_at = _datetime.datetime.now(_datetime.timezone.utc).isoformat()
    version_binding = _current_version_binding()
    manifest, plan, project_configs, public_projection, payloads = _build_outputs(
        protocol=protocol,
        public=public,
        source_receipt=source_receipt,
        public_input_path=public_input_path,
        taskpack_path=taskpack_path,
        taskpack_sha256=taskpack_sha256,
        selected_model=selected_model,
        canary_count=len(canaries),
        prepared_at=prepared_at,
        version_binding=version_binding,
    )
    _assert_canaries_absent(payloads, canaries)

    destination = Path(out_dir)
    if destination.exists() and not destination.is_dir():
        raise AdapterError("preparation output path exists and is not a directory")
    with RunDirLock(destination):
        # The ownership marker is created by the lock itself, so it is the one
        # permitted entry when checking that this is still a fresh directory.
        if any(entry.name != LOCK_NAME for entry in destination.iterdir()):
            raise AdapterError("preparation output directory is not empty")
        _write_json(destination / PUBLIC_INPUT_NAME, public_projection)
        for track_id, config in project_configs.items():
            _write_json(destination / PROJECTS_DIR / f"{track_id}.json", config)
        _write_json(destination / PLAN_NAME, plan)
        _write_json(destination / MANIFEST_NAME, manifest)
    return manifest


def _load_prepared(run_dir: Path) -> tuple[dict, dict, dict[str, dict]]:
    manifest_path = run_dir / MANIFEST_NAME
    plan_path = run_dir / PLAN_NAME
    manifest = _read_json(manifest_path, "preparation manifest")
    if manifest.get("kind") != "real_paper_offline_preparation":
        raise AdapterError("run directory does not contain an offline preparation manifest")
    if manifest.get("version") != 1:
        raise AdapterError("unsupported preparation manifest version")
    expected_plan = manifest.get("plan")
    if not isinstance(expected_plan, dict) or expected_plan.get("path") != PLAN_NAME:
        raise AdapterError("preparation manifest has no valid plan binding")
    if _sha256_file(plan_path) != expected_plan.get("sha256"):
        raise AdapterError("plan.json changed after preparation")
    plan = _read_json(plan_path, "execution plan")
    if plan.get("kind") != "real_paper_public_cli_execution_plan":
        raise AdapterError("unexpected execution plan kind")
    project_configs: dict[str, dict] = {}
    project_rows = manifest.get("project_configs")
    if not isinstance(project_rows, list) or len(project_rows) != 2:
        raise AdapterError("preparation manifest must list exactly two project configs")
    for row in project_rows:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise AdapterError("invalid project config binding")
        path = run_dir / row["path"]
        if _sha256_file(path) != row.get("sha256"):
            raise AdapterError("prepared project config changed after preparation")
        project_configs[row.get("track_id")] = _read_json(path, "project config")
    return manifest, plan, project_configs


def _verify_source(plan: Mapping[str, Any]) -> dict:
    source = plan.get("source")
    if not isinstance(source, dict):
        raise AdapterError("plan has no source receipt")
    return _file_receipt(source.get("path"), source.get("sha256"), source.get("bytes"))


def _initialise_run(
    run_dir: Path,
    manifest: Mapping[str, Any],
    plan: Mapping[str, Any],
    taskpack_sha256: str,
) -> dict:
    state_path = run_dir / RUN_META_NAME
    expected = {
        "version": 1,
        "kind": "real_paper_offline_run",
        "taskpack_sha256": taskpack_sha256,
        "plan_sha256": manifest["plan"]["sha256"],
        "paper_sha256": plan["source"]["sha256"],
        "selected_model": manifest["model"]["selected"],
        "budget": deepcopy(plan["budget"]),
        "execution_authorized_at_start": True,
    }
    if state_path.exists():
        state = _read_json(state_path, "run metadata")
        for key, value in expected.items():
            if state.get(key) != value:
                raise AdapterError(f"run metadata changed: {key}")
        return state
    state = {
        **expected,
        "created_at": _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
    }
    _write_json(state_path, state)
    return state


def _load_ledger(run_dir: Path, plan: Mapping[str, Any]) -> dict:
    path = run_dir / BUDGET_LEDGER_NAME
    expected_limits = deepcopy(plan["budget"])
    if path.exists():
        ledger = _read_json(path, "budget ledger")
        if ledger.get("limits") != expected_limits:
            raise AdapterError("budget ledger limits changed; refusing to reset the budget")
        if ledger.get("reset_on_restart") is not False:
            raise AdapterError("budget ledger must not reset on restart")
        if ledger.get("cumulative_across_resumes") is not True:
            raise AdapterError("budget ledger must remain cumulative across resumes")
        count = ledger.get("resume_count", 0)
        if type(count) is not int or count < 0:
            raise AdapterError("budget ledger resume_count is invalid")
        return ledger
    ledger = {
        "version": 1,
        "kind": "real_paper_adapter_budget_ledger",
        "limits": expected_limits,
        "cumulative_across_resumes": True,
        "reset_on_restart": False,
        "resume_count": 0,
        "note": (
            "The public research supervisor remains authoritative for actual planner/native "
            "usage. This ledger freezes adapter limits across resumes and never resets them."
        ),
    }
    _write_json(path, ledger)
    return ledger


def _bump_resume(run_dir: Path, ledger: dict) -> dict:
    ledger = deepcopy(ledger)
    ledger["resume_count"] = int(ledger.get("resume_count", 0)) + 1
    ledger["updated_at"] = _datetime.datetime.now(_datetime.timezone.utc).isoformat()
    _write_json(run_dir / BUDGET_LEDGER_NAME, ledger)
    return ledger


def _receipt_map(run_dir: Path) -> dict[str, dict]:
    receipts = {}
    for receipt in _read_jsonl(run_dir / RECEIPTS_NAME):
        op_id = receipt.get("op_id")
        if not isinstance(op_id, str) or not op_id:
            raise AdapterError("receipt has no operation id")
        if op_id in receipts:
            raise AdapterError(f"duplicate receipt for operation {op_id}")
        receipts[op_id] = receipt
    return receipts


def _reservation_map(run_dir: Path) -> dict[str, dict]:
    reservations = {}
    for event in _read_jsonl(run_dir / JOURNAL_NAME):
        if event.get("event") != "reserve":
            continue
        op_id = event.get("op_id")
        if not isinstance(op_id, str) or not op_id:
            raise AdapterError("reservation has no operation id")
        if op_id in reservations:
            raise AdapterError(f"duplicate reservation for operation {op_id}")
        reservations[op_id] = event
    return reservations


def _has_blocking_terminal(receipts: Mapping[str, Mapping[str, Any]]) -> bool:
    return any(receipt.get("state") in ("failed", "unknown") for receipt in receipts.values())


def _record_receipt(run_dir: Path, receipt: dict) -> None:
    _append_jsonl(run_dir / RECEIPTS_NAME, receipt)
    _append_jsonl(run_dir / JOURNAL_NAME, {"event": "receipt", **receipt})


def _diagnostic_relpath(op_id: str, stream: str) -> str:
    if not _OPERATION_ID_RE.fullmatch(op_id) or stream not in ("stdout", "stderr"):
        raise AdapterError("invalid operation id or stream for diagnostics")
    return f"{DIAGNOSTICS_DIR}/{op_id}.{stream}.txt"


def _diagnostic_entry(relpath: str, data: bytes) -> dict:
    return {
        "path": relpath,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }


def _existing_diagnostics(run_dir: Path, op_id: str) -> dict:
    """Read diagnostic files left by an interrupted call; never overwrite them."""
    entries = {}
    for stream in ("stdout", "stderr"):
        relpath = _diagnostic_relpath(op_id, stream)
        path = run_dir / relpath
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise AdapterError(
                f"cannot read existing {stream} diagnostic for {op_id}: {type(exc).__name__}"
            ) from None
        entries[stream] = _diagnostic_entry(relpath, data)
    return entries


def _persist_diagnostics(run_dir: Path, op_id: str, stdout: str, stderr: str) -> dict:
    """Persist complete public-CLI streams before the receipt is appended.

    File names are derived only from the fixed operation id.  Existing files are
    never overwritten, so a recovery/reconciliation path cannot replace older
    evidence.  Empty streams are omitted.  The adapter adds no environment
    variables, request headers or credentials to these local files.
    """
    entries = {}
    for stream, text in (("stdout", stdout), ("stderr", stderr)):
        if not text:
            continue
        relpath = _diagnostic_relpath(op_id, stream)
        path = run_dir / relpath
        if path.exists():
            raise AdapterError(
                f"diagnostic file already exists for {op_id} {stream}; refusing to overwrite"
            )
        data = text.encode("utf-8")
        _atomic_write(path, data)
        entries[stream] = _diagnostic_entry(relpath, data)
    return entries


def _reconcile_reservations(run_dir: Path) -> None:
    """Turn a reservation without a receipt into a durable unknown, never a retry."""
    receipts = _receipt_map(run_dir)
    for op_id, reservation in _reservation_map(run_dir).items():
        if op_id in receipts:
            continue
        diagnostics = _existing_diagnostics(run_dir, op_id)
        receipt = {
            "version": 1,
            "op_id": op_id,
            "track_scope": list(reservation.get("track_scope", [])),
            "command": reservation.get("command"),
            "argv": list(reservation.get("argv", [])),
            "external_call": True,
            "started_at": reservation.get("started_at"),
            "finished_at": _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
            "duration_s": None,
            "returncode": None,
            "state": "unknown",
            "error_type": "reservation_without_receipt_after_interruption",
            "stdout_present": "stdout" in diagnostics,
            "stdout_sha256": diagnostics.get("stdout", {}).get("sha256"),
            "stdout_path": diagnostics.get("stdout", {}).get("path"),
            "stdout_bytes": diagnostics.get("stdout", {}).get("bytes"),
            "stdout_chars": None,
            "stderr_present": "stderr" in diagnostics,
            "stderr_sha256": diagnostics.get("stderr", {}).get("sha256"),
            "stderr_path": diagnostics.get("stderr", {}).get("path"),
            "stderr_bytes": diagnostics.get("stderr", {}).get("bytes"),
            "diagnostics": diagnostics,
        }
        _record_receipt(run_dir, receipt)


def _receipt(
    *,
    op: Mapping[str, Any],
    reservation: Mapping[str, Any],
    state: str,
    returncode: int | None,
    response: Any = None,
    stdout: str = "",
    stderr: str = "",
    diagnostics: Mapping[str, Mapping[str, Any]] | None = None,
    error_type: str | None = None,
    started_at: str,
    finished_at: str,
    duration_s: float | None,
    external_call: bool = True,
) -> dict:
    stdout = _decode_output(stdout)
    stderr = _decode_output(stderr)
    diagnostics = dict(diagnostics or {})
    receipt = {
        "version": 1,
        "op_id": op["id"],
        "track_scope": list(op.get("track_scope", [])),
        "command": op.get("command"),
        "argv": list(reservation.get("argv", [])),
        "reservation_id": reservation.get("reservation_id"),
        "external_call": external_call,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_s": round(duration_s, 6) if isinstance(duration_s, (int, float)) else None,
        "returncode": returncode,
        "state": state,
        "stdout_present": bool(stdout),
        "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest() if stdout else None,
        "stdout_path": diagnostics.get("stdout", {}).get("path"),
        "stdout_bytes": diagnostics.get("stdout", {}).get("bytes"),
        "stdout_chars": len(stdout),
        "stderr_present": bool(stderr),
        "stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest() if stderr else None,
        "stderr_path": diagnostics.get("stderr", {}).get("path"),
        "stderr_bytes": diagnostics.get("stderr", {}).get("bytes"),
        "diagnostics": diagnostics,
    }
    if error_type:
        receipt["error_type"] = error_type
    if response is not None and state in ("succeeded", "failed", "unknown"):
        receipt["response"] = response
    return receipt


def _classify_result(
    op: Mapping[str, Any],
    reservation: Mapping[str, Any],
    result: RunnerResult,
    started_at: str,
    diagnostics: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict:
    finished_at = _datetime.datetime.now(_datetime.timezone.utc).isoformat()
    duration = None
    if isinstance(reservation.get("monotonic_started"), (int, float)):
        import time
        duration = max(0.0, time.monotonic() - reservation["monotonic_started"])
    if result.returncode != 0:
        return _receipt(
            op=op, reservation=reservation, state="unknown", returncode=result.returncode,
            stdout=result.stdout, stderr=result.stderr, diagnostics=diagnostics,
            error_type="nonzero_exit",
            started_at=started_at, finished_at=finished_at, duration_s=duration,
        )
    try:
        response = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return _receipt(
            op=op, reservation=reservation, state="unknown", returncode=result.returncode,
            stdout=result.stdout, stderr=result.stderr, diagnostics=diagnostics,
            error_type="invalid_json_output",
            started_at=started_at, finished_at=finished_at, duration_s=duration,
        )
    if not isinstance(response, dict):
        return _receipt(
            op=op, reservation=reservation, state="unknown", returncode=result.returncode,
            stdout=result.stdout, stderr=result.stderr, diagnostics=diagnostics,
            error_type="non_object_json_output",
            started_at=started_at, finished_at=finished_at, duration_s=duration,
        )
    product_state = response.get("state", response.get("status"))
    if isinstance(product_state, str):
        negative = PRODUCT_NEGATIVE_STATES.get(product_state.lower())
        if negative is not None:
            receipt_state, error_type = negative
            return _receipt(
                op=op, reservation=reservation, state=receipt_state,
                returncode=result.returncode, response=response, stdout=result.stdout,
                stderr=result.stderr, diagnostics=diagnostics, error_type=error_type,
                started_at=started_at, finished_at=finished_at, duration_s=duration,
            )
    required = op.get("output_ref_field")
    if required and not (isinstance(response.get(required), str) and response[required].strip()):
        return _receipt(
            op=op, reservation=reservation, state="failed", returncode=result.returncode,
            response=response, stdout=result.stdout, stderr=result.stderr,
            diagnostics=diagnostics, error_type="missing_required_output",
            started_at=started_at, finished_at=finished_at, duration_s=duration,
        )
    return _receipt(
        op=op, reservation=reservation, state="succeeded", returncode=result.returncode,
        response=response, stdout=result.stdout, stderr=result.stderr,
        diagnostics=diagnostics,
        started_at=started_at, finished_at=finished_at, duration_s=duration,
    )


def _response_ref(receipts: Mapping[str, Mapping[str, Any]], op_id: str) -> str | None:
    receipt = receipts.get(op_id)
    if not receipt or receipt.get("state") != "succeeded":
        return None
    response = receipt.get("response")
    if not isinstance(response, dict):
        return None
    for field in ("ref", "project_ref"):
        value = response.get(field)
        if isinstance(value, str) and value:
            return value
    return None


def _build_argv(
    run_dir: Path,
    op: Mapping[str, Any],
    plan: Mapping[str, Any],
    project_configs: Mapping[str, Mapping[str, Any]],
    receipts: Mapping[str, Mapping[str, Any]],
    store: Path,
) -> list[str]:
    prefix = [sys.executable, "-X", "utf8", "-m", "biomodel_agent.cli"]
    kind = op["kind"]
    if kind == "source_ingest":
        parser = plan.get("execution_options", {}).get("pdf_parser", "pypdf")
        return prefix + [
            "source-ingest", plan["source"]["path"], "--store", str(store),
            "--pdf-parser", parser,
        ]
    source_ref = _response_ref(receipts, "source-ingest")
    if kind == "project_create":
        if source_ref is None:
            raise AdapterError("source-ingest has no committed source reference")
        track_id = op["track_scope"][0]
        config = deepcopy(project_configs[track_id])
        config["source_refs"] = [source_ref]
        runtime = run_dir / RUNTIME_DIR / f"{track_id}-project.json"
        _write_json(runtime, config)
        return prefix + [
            "research-project-create", "--store", str(store), "--config", str(runtime),
        ]
    project_ref = _response_ref(receipts, "original_fulltext.create")
    if project_ref is None:
        raise AdapterError("research-project-create has no committed project reference")
    if kind == "project_show":
        return prefix + ["research-project-show", project_ref, "--store", str(store)]
    if kind == "project_run":
        model = plan.get("model")
        if not isinstance(model, Mapping):
            raise AdapterError("plan has no frozen model configuration")
        try:
            selected_model = model["selected"]
            max_output_tokens = model["max_output_tokens_per_call"]
            provider_timeout = model["provider_timeout_s"]
            reasoning_effort = model["requested_effort"]
        except KeyError as exc:
            raise AdapterError(f"plan lacks frozen public transport value: {exc.args[0]}") from None
        return prefix + [
            "research-project-run", project_ref, "--store", str(store),
            "--model", selected_model,
            "--reasoning-effort", reasoning_effort,
            "--max-output-tokens", _argv_number(max_output_tokens),
            "--provider-timeout", _argv_number(provider_timeout),
        ]
    if kind == "project_snapshot":
        return prefix + ["research-project-snapshot", project_ref, "--store", str(store)]
    raise AdapterError(f"unsupported plan operation kind: {kind}")


class SubprocessRunner:
    """Default real runner: the public CLI only, with a hard wall-clock cap.

    ``-X utf8`` is set by the adapter argv builder and bytes are decoded here
    explicitly as UTF-8, so Chinese paths/output never depend on the Windows
    pipe ANSI code page.  Any partial timeout output is carried as diagnostics.
    """

    def __call__(self, argv: Sequence[str], timeout_s: float) -> RunnerResult:
        try:
            completed = subprocess.run(
                list(argv), capture_output=True, text=False, timeout=timeout_s, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise RunnerTimeout(
                "public CLI invocation timed out",
                stdout=_decode_output(exc.stdout),
                stderr=_decode_output(exc.stderr),
            ) from None
        return RunnerResult(
            completed.returncode,
            _decode_output(completed.stdout),
            _decode_output(completed.stderr),
        )


def _refuse_untransmitted_max_effort(plan: Mapping[str, Any]) -> None:
    """Refuse legacy plans whose max-effort request has no public transport.

    New plans freeze ``forwarded_via_public_cli`` and proceed.  Older artifacts
    with ``blocked_not_transmitted`` (or no transport record) are refused before
    any runner is invoked, including injected offline contract runners.
    """
    model = plan.get("model")
    if not isinstance(model, Mapping):
        raise AdapterError("plan has no model configuration")
    if model.get("requested_effort") != "max":
        return
    transport = model.get("effort_transport")
    if (
        isinstance(transport, Mapping)
        and isinstance(transport.get("status"), str)
        and transport["status"].startswith("forwarded_via_public_cli")
    ):
        return
    raise InterfaceBlocked(
        "legacy preparation artifact has a blocked_not_transmitted max reasoning-effort "
        "transport; execution is refused before the first public command. Re-prepare with "
        "the current adapter that forwards --reasoning-effort max via the public CLI. "
        "Read-only show of the historical preparation remains available."
    )


def execute(
    run_dir: os.PathLike[str] | str,
    *,
    taskpack_path: os.PathLike[str] | str,
    runner: Runner | None = None,
    model: str | None = None,
) -> dict:
    """Dispatch the prepared plan through public CLI commands or an injected fake.

    A call is refused unless the supplied taskpack is authorized and the frozen
    adapter/public-implementation/PDF-engine version binding still matches.
    Legacy blocked max-effort plans and packs without a frozen PDF binding are
    refused before any runner.  Unknown or failed effects
    stop the dispatcher; remaining operations stay missing in the denominator and
    are never automatically replayed.  Complete stdout/stderr diagnostics are
    persisted before each receipt; local diagnostics never enter the public model
    configuration or increase the product deadline/budget.
    """
    run_dir = Path(run_dir)
    manifest, plan, project_configs = _load_prepared(run_dir)
    protocol = load_protocol(taskpack_path)
    taskpack_sha256 = _sha256_file(taskpack_path)
    if taskpack_sha256 != manifest["taskpack"]["sha256"]:
        raise ProtocolError("taskpack changed after offline preparation")
    _refuse_untransmitted_max_effort(plan)
    if protocol.get("execution_authorized") is not True:
        raise ExecutionNotAuthorized(
            "execution_authorized=false in the frozen taskpack; no public command was invoked"
        )
    selected = manifest["model"]["selected"]
    if model is not None and model != selected:
        raise AdapterError("selected model differs from the prepared frozen model")
    if selected not in protocol["allowed_models"]:
        raise ProtocolError("prepared model is no longer allowed by the taskpack")
    _verify_frozen_versions(manifest, plan)
    _verify_source(plan)
    if runner is None:
        runner = SubprocessRunner()

    with RunDirLock(run_dir):
        already_started = (run_dir / RUN_META_NAME).exists() or (run_dir / JOURNAL_NAME).exists()
        _initialise_run(run_dir, manifest, plan, taskpack_sha256)
        _reconcile_reservations(run_dir)
        ledger = _load_ledger(run_dir, plan)
        if already_started:
            ledger = _bump_resume(run_dir, ledger)
        receipts = _receipt_map(run_dir)
        if _has_blocking_terminal(receipts):
            return _persist_status(run_dir)
        store = run_dir / "store"
        for operation in plan["operations"]:
            op_id = operation["id"]
            if op_id in receipts:
                continue
            if _has_blocking_terminal(receipts):
                break
            try:
                argv = _build_argv(run_dir, operation, plan, project_configs, receipts, store)
            except AdapterError as exc:
                now = _datetime.datetime.now(_datetime.timezone.utc).isoformat()
                reservation = {"argv": [], "track_scope": operation.get("track_scope", [])}
                receipt = _receipt(
                    op=operation, reservation=reservation, state="failed", returncode=None,
                    error_type="local_precondition_failed", started_at=now, finished_at=now,
                    duration_s=0.0, external_call=False,
                )
                receipt["local_error"] = type(exc).__name__
                _record_receipt(run_dir, receipt)
                receipts[op_id] = receipt
                break
            reservation_id = f"{op_id}:{_datetime.datetime.now(_datetime.timezone.utc).timestamp()}"
            started_at = _datetime.datetime.now(_datetime.timezone.utc).isoformat()
            reservation = {
                "event": "reserve",
                "version": 1,
                "reservation_id": reservation_id,
                "op_id": op_id,
                "track_scope": list(operation.get("track_scope", [])),
                "command": operation.get("command"),
                "argv": argv,
                "timeout_s": operation["timeout_s"],
                "started_at": started_at,
                "monotonic_started": __import__("time").monotonic(),
                "state": "reserved",
            }
            _append_jsonl(run_dir / JOURNAL_NAME, reservation)
            try:
                result = runner(argv, float(operation["timeout_s"]))
            except KeyboardInterrupt:
                finished_at = _datetime.datetime.now(_datetime.timezone.utc).isoformat()
                receipt = _receipt(
                    op=operation, reservation=reservation, state="unknown", returncode=None,
                    error_type="process_interrupted", started_at=started_at,
                    finished_at=finished_at, duration_s=None,
                )
                _record_receipt(run_dir, receipt)
                receipts[op_id] = receipt
                _write_json(run_dir / STATUS_NAME, status(run_dir))
                raise
            except BaseException as exc:
                finished_at = _datetime.datetime.now(_datetime.timezone.utc).isoformat()
                error_type = "timeout" if isinstance(exc, TimeoutError) else type(exc).__name__
                stdout = _decode_output(getattr(exc, "stdout", ""))
                stderr = _decode_output(getattr(exc, "stderr", ""))
                try:
                    diagnostics = _persist_diagnostics(run_dir, op_id, stdout, stderr)
                except BaseException:
                    try:
                        diagnostics = _existing_diagnostics(run_dir, op_id)
                    except AdapterError:
                        diagnostics = {}
                receipt = _receipt(
                    op=operation, reservation=reservation, state="unknown", returncode=None,
                    stdout=stdout, stderr=stderr, diagnostics=diagnostics,
                    error_type=error_type, started_at=started_at, finished_at=finished_at,
                    duration_s=None,
                )
                _record_receipt(run_dir, receipt)
                receipts[op_id] = receipt
                break
            if not isinstance(result, RunnerResult):
                finished_at = _datetime.datetime.now(_datetime.timezone.utc).isoformat()
                receipt = _receipt(
                    op=operation, reservation=reservation, state="unknown", returncode=None,
                    error_type="invalid_runner_result", started_at=started_at,
                    finished_at=finished_at, duration_s=None,
                )
                _record_receipt(run_dir, receipt)
                receipts[op_id] = receipt
                break
            stdout_text = _decode_output(result.stdout)
            stderr_text = _decode_output(result.stderr)
            normalized_result = RunnerResult(result.returncode, stdout_text, stderr_text)
            try:
                diagnostics = _persist_diagnostics(
                    run_dir, op_id, stdout_text, stderr_text
                )
                receipt = _classify_result(
                    operation, reservation, normalized_result, started_at, diagnostics
                )
            except KeyboardInterrupt:
                finished_at = _datetime.datetime.now(_datetime.timezone.utc).isoformat()
                try:
                    diagnostics = _existing_diagnostics(run_dir, op_id)
                except AdapterError:
                    diagnostics = {}
                receipt = _receipt(
                    op=operation, reservation=reservation, state="unknown", returncode=None,
                    stdout=stdout_text, stderr=stderr_text, diagnostics=diagnostics,
                    error_type="process_interrupted_during_diagnostics", started_at=started_at,
                    finished_at=finished_at, duration_s=None,
                )
                _record_receipt(run_dir, receipt)
                receipts[op_id] = receipt
                _write_json(run_dir / STATUS_NAME, status(run_dir))
                raise
            except BaseException as exc:
                finished_at = _datetime.datetime.now(_datetime.timezone.utc).isoformat()
                try:
                    diagnostics = _existing_diagnostics(run_dir, op_id)
                except AdapterError:
                    diagnostics = {}
                receipt = _receipt(
                    op=operation, reservation=reservation, state="unknown", returncode=None,
                    stdout=stdout_text, stderr=stderr_text, diagnostics=diagnostics,
                    error_type="diagnostics_or_classification_failed", started_at=started_at,
                    finished_at=finished_at, duration_s=None,
                )
                receipt["local_error"] = type(exc).__name__
                _record_receipt(run_dir, receipt)
                receipts[op_id] = receipt
                break
            _record_receipt(run_dir, receipt)
            receipts[op_id] = receipt
            if receipt["state"] != "succeeded":
                break
        return _persist_status(run_dir)


def _latest_product_state(
    receipts: Mapping[str, Mapping[str, Any]], plan: Mapping[str, Any]
) -> str | None:
    for operation in reversed(plan.get("operations", [])):
        receipt = receipts.get(operation.get("id"))
        if not receipt or receipt.get("state") not in ("succeeded", "failed", "unknown"):
            continue
        response = receipt.get("response")
        if isinstance(response, dict):
            value = response.get("state", response.get("status"))
            if isinstance(value, str) and value:
                return value
    return None


def _reported_public_usage(receipts: Mapping[str, Mapping[str, Any]]) -> dict:
    usage = {}
    for op_id, receipt in receipts.items():
        response = receipt.get("response")
        if not isinstance(response, dict):
            continue
        for field in ("budget", "usage"):
            value = response.get(field)
            if isinstance(value, dict):
                usage.setdefault(op_id, {})[field] = value
    return usage


def status(run_dir: os.PathLike[str] | str) -> dict:
    """Read-only durable status; derives missing states without replaying calls."""
    run_dir = Path(run_dir)
    manifest, plan, _ = _load_prepared(run_dir)
    state_path = run_dir / RUN_META_NAME
    run_meta = _read_json(state_path, "run metadata") if state_path.exists() else None
    receipts = _receipt_map(run_dir) if (run_dir / RECEIPTS_NAME).exists() else {}
    reservations = _reservation_map(run_dir) if (run_dir / JOURNAL_NAME).exists() else {}
    ledger_path = run_dir / BUDGET_LEDGER_NAME
    ledger = _read_json(ledger_path, "budget ledger") if ledger_path.exists() else None
    started = run_meta is not None or bool(receipts) or bool(reservations)
    operation_rows = []
    operation_states = {}
    for operation in plan["operations"]:
        op_id = operation["id"]
        receipt = receipts.get(op_id)
        if receipt is not None:
            state = receipt.get("state", "unknown")
            receipt_ref = op_id
        elif op_id in reservations:
            state = "unknown"
            receipt_ref = None
        elif started:
            state = "missing"
            receipt_ref = None
        else:
            state = "planned"
            receipt_ref = None
        operation_states[op_id] = state
        operation_rows.append({
            "id": op_id,
            "sequence": operation.get("sequence"),
            "command": operation.get("command"),
            "track_scope": list(operation.get("track_scope", [])),
            "state": state,
            "receipt_ref": receipt_ref,
            "external_call": receipt.get("external_call") if receipt else None,
            "error_type": receipt.get("error_type") if receipt else None,
        })
    blocking = [
        op_id for op_id, state in operation_states.items() if state in ("failed", "unknown")
    ]
    raw_states = [operation_states[op_id] for op_id in plan["tracks"][0]["operation_ids"]]
    if "unknown" in raw_states:
        raw_state = "unknown"
    elif "failed" in raw_states:
        raw_state = "failed"
    elif not started:
        raw_state = "planned"
    elif "missing" in raw_states:
        raw_state = "missing"
    else:
        raw_state = "succeeded"
    assisted_state = "missing_pending_named_transcription"
    source_state = operation_states.get("source-ingest", "planned")
    if not started:
        adapter_status = "prepared_not_executed"
    elif "unknown" in raw_states:
        adapter_status = "stopped_unknown"
    elif "failed" in raw_states:
        adapter_status = "stopped_failed"
    elif "missing" in raw_states:
        adapter_status = "stopped_missing"
    else:
        adapter_status = "dispatch_completed"
    tracks = [
        {
            "id": SELECTED_TRACK,
            "denominator": "retained",
            "dispatch_state": raw_state,
            "product_reported_state": _latest_product_state(receipts, plan),
            "shared_source_state": source_state,
            "operations": {
                op_id: operation_states[op_id]
                for op_id in plan["tracks"][0]["operation_ids"]
            },
        },
        {
            "id": ASSISTED_TRACK,
            "denominator": "retained",
            "dispatch_state": assisted_state,
            "missing_reason": "named_human_transcription_not_supplied",
            "actor": None,
            "shared_source_state": source_state,
            "operations": {},
        },
    ]
    return {
        "version": 1,
        "kind": "real_paper_offline_run_status",
        "adapter_status": adapter_status,
        "execution_authorized": manifest["taskpack"]["execution_authorized"],
        "taskpack_sha256": manifest["taskpack"]["sha256"],
        "paper_sha256": plan["source"]["sha256"],
        "denominator": {
            "paper_level": 1,
            "track_level": 2,
            "retained_tracks": 2,
            "excluded_tracks": 0,
            "post_hoc_exclusion": False,
            "tracks": tracks,
        },
        "operations": operation_rows,
        "blocking_operation_ids": blocking,
        "unresolved_reservation_ids": sorted(set(reservations) - set(receipts)),
        "budget": {
            "limits": deepcopy(plan["budget"]),
            "cumulative_across_resumes": True,
            "reset_on_restart": False,
            "resume_count": int(ledger.get("resume_count", 0)) if ledger else 0,
            "authority": "public_research_supervisor_internal_budget",
        },
        "reported_public_usage": _reported_public_usage(receipts),
        "receipt_count": len(receipts),
        "version_binding": _version_binding_report(manifest, plan),
        "diagnostics": {
            "directory": DIAGNOSTICS_DIR,
            "receipts_with_local_streams": sum(
                1 for receipt in receipts.values() if receipt.get("diagnostics")
            ),
            "scope": "local_public_cli_streams_not_projected_to_public_model_config",
        },
        "journal_event_count": len(_read_jsonl(run_dir / JOURNAL_NAME))
        if (run_dir / JOURNAL_NAME).exists() else 0,
        "authoritative_sources": [
            RUN_META_NAME, JOURNAL_NAME, RECEIPTS_NAME, BUDGET_LEDGER_NAME
        ],
        "diagnostic_sources": [DIAGNOSTICS_DIR],
        "scientific_support": "unknown",
        "scientific_evaluation_executed": False,
        "notice": (
            "Dispatch status only. It is not scientific support, independent review, or a "
            "claim that any planned track succeeded scientifically. Failed/missing/unknown "
            "tracks remain in the frozen denominator. Version-binding mismatch or legacy "
            "missing binding keeps this read-only history available but refuses new calls."
        ),
    }


def _persist_status(run_dir: Path) -> dict:
    snapshot = status(run_dir)
    _write_json(run_dir / STATUS_NAME, snapshot)
    return snapshot


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="real_paper_pipeline.py",
        description=(
            "Offline preparation and public-CLI execution adapter for the frozen real-paper "
            "taskpack. Preparation never calls a model; run refuses unauthorized protocols."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser(
        "prepare", help="validate frozen inputs and write a public preparation manifest/plan"
    )
    prepare_parser.add_argument("--taskpack", required=True, help="accepted schema-2 taskpack JSON")
    prepare_parser.add_argument("--public-input", required=True, help="strict scenario/constraints JSON")
    prepare_parser.add_argument("--pdf", required=True, help="complete original PDF to hash/verify")
    prepare_parser.add_argument("--out", required=True, help="new/empty preparation output directory")
    prepare_parser.add_argument("--model", required=True, help="one model from taskpack.allowed_models")
    prepare_parser.add_argument(
        "--private-canary-file", action="append", default=[],
        help="optional file of literal private canaries to check for accidental projection",
    )
    prepare_parser.set_defaults(handler=_handle_prepare)

    show_parser = subparsers.add_parser("show", help="read durable run status without changing it")
    show_parser.add_argument("--run-dir", required=True)
    show_parser.set_defaults(handler=_handle_show)

    run_parser = subparsers.add_parser(
        "run", help="dispatch through public CLI; requires execution_authorized=true"
    )
    run_parser.add_argument("--run-dir", required=True)
    run_parser.add_argument("--taskpack", required=True)
    run_parser.add_argument("--model", help="must equal the prepared selected model")
    run_parser.set_defaults(handler=_handle_run)

    arguments = parser.parse_args(argv)
    try:
        return arguments.handler(arguments)
    except (AdapterError, ConcurrentRunError, OSError) as exc:
        print(f"real paper adapter failed: {exc}", file=sys.stderr)
        return 1


def _handle_prepare(arguments) -> int:
    manifest = prepare(
        taskpack_path=arguments.taskpack,
        public_input_path=arguments.public_input,
        pdf_path=arguments.pdf,
        out_dir=arguments.out,
        selected_model=arguments.model,
        private_canary_files=arguments.private_canary_file,
    )
    print(_json_text(manifest), end="")
    return 0


def _handle_show(arguments) -> int:
    print(_json_text(status(arguments.run_dir)), end="")
    return 0


def _handle_run(arguments) -> int:
    result = execute(
        arguments.run_dir,
        taskpack_path=arguments.taskpack,
        model=arguments.model,
    )
    print(_json_text(result), end="")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
