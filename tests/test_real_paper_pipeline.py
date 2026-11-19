"""Offline adapter contracts for the frozen real-paper protocol.

These tests never read the candidate paper or any gitignored workspace artifact.
They build a minimal schema-2 public protocol inside this file, use tiny local
fixture bytes and an injected fake public-CLI runner, plus real in-process
public source-ingest/research-project-create/show calls against local material.
"""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks/development/real_paper_pipeline.py"
FIXTURE_PDF = b"%PDF-1.4\nOffline adapter fixture bytes; not the candidate article.\n%%EOF\n"
MODEL = "deepseek-flash-2"
JSON_METACHAR_CANARIES = (
    'PRIVATE"CANARY',
    "PRIVATE\\CANARY",
    "PRIVATE\nCANARY",
)


def _public_protocol_fixture(pdf=FIXTURE_PDF):
    """Minimal self-contained schema-2 protocol accepted by the adapter."""
    sha = hashlib.sha256(pdf).hexdigest()
    return {
        "schema_version": 2,
        "task_id": "offline-adapter-fixture-taskpack",
        "status": "prepared_not_executed",
        "execution_authorized": False,
        "scientific_support": "unknown",
        "independent_review": "pending",
        "allowed_models": ["deepseek-flash-2", "glm-5.3-flash"],
        "requested_effort": "max",
        "model_constraints": {
            "selection": "exactly_one_allowed_model_per_frozen_protocol",
            "fallback_models": [],
        },
        "budget": {
            "papers": 1,
            "project_tracks": 2,
            "planner_calls_per_project": 12,
            "native_calls_per_project": 4,
            "planner_calls_total": 24,
            "native_calls_total": 8,
            "total_project_timeout_s": 1800,
            "total_timeout_s_per_project": 900,
            "max_output_tokens_per_call": 8000,
            "cumulative_across_resumes": True,
            "reset_on_restart": False,
            "increase_requires_new_freeze": True,
        },
        "paper_candidate": {
            "input_path": "fixtures/offline-source.pdf",
            "sha256": sha,
            "bytes": len(pdf),
            "seen_status": "previously_seen_development",
            "development_use_only": True,
            "development_pilot_use_allowed": True,
            "formal_unseen_holdout_use_forbidden": True,
            "doi": None,
            "doi_status": "not_verified_not_asserted",
            "publication_verified": False,
            "local_pdf_metadata_evidence": {
                "evidence_path": "fixtures/offline-metadata-evidence.json",
                "evidence_status": "locally_checked_frozen_pdf_only",
                "source_sha256": sha,
                "page": 1,
                "publication_verified": False,
                "doi_candidate_status": "not_verified_not_asserted",
                "independent_review": "pending",
            },
        },
        "paper_accounting": {
            "unique_seen_development_papers": 1,
            "tracks_on_same_paper": 2,
            "paper_level_denominator": 1,
            "track_level_denominator": 2,
            "dedupe_key": "sha256",
            "tracks_count_as_additional_papers": False,
        },
        "tracks": [
            {
                "id": "original_fulltext",
                "label": "original full-text autonomous track",
                "source_sha256": sha,
                "uses_operator_transcription": False,
                "counts_as_additional_paper": False,
            },
            {
                "id": "named_transcription_assisted",
                "label": "named transcription-assisted track",
                "source_sha256": sha,
                "uses_operator_transcription": True,
                "counts_as_additional_paper": False,
            },
        ],
        "isolation": {
            "private_numeric_reference": {"in_public_projection": False},
            "scoring_rubric": {"in_public_projection": False},
            "future_clarifications": {
                "post_freeze_policy": "quarantine",
                "usable_without_amendment": False,
            },
            "canary_test_required": True,
        },
        "execution_policy": {
            "unknown_effect_replay": False,
            "unknown_effect_retry": False,
            "failed_or_missing_runs_in_denominator": True,
            "session_recovery": {
                "durable_event_log": True,
                "budget_reset_on_resume": False,
                "lost_work_policy": "record_missing_and_retain_in_denominator",
            },
        },
        "denominator_policy": {
            "include": ["planned", "started", "failed", "timed_out", "missing", "unknown"],
            "post_hoc_exclusion": False,
        },
        "completion_claims": {
            "all_pipeline_tasks_complete": False,
            "self_signed_completion": False,
            "independent_review_signed": False,
        },
    }


def _load_adapter():
    name = "_real_paper_adapter_under_test"
    spec = importlib.util.spec_from_file_location(name, str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # The adapter uses future annotations; dataclasses resolves the defining
    # module through sys.modules during exec_module, so register it first.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


adapter = _load_adapter()


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _rewrite_plan(case, mutate):
    plan_path = case["out"] / adapter.PLAN_NAME
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    mutate(plan)
    adapter._write_json(plan_path, plan)
    manifest_path = case["out"] / adapter.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["plan"]["sha256"] = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    adapter._write_json(manifest_path, manifest)
    return manifest, plan


def _authorized_protocol(tmp_path, *, pdf=FIXTURE_PDF, authorized=False, mutate=None):
    protocol = _public_protocol_fixture(pdf)
    protocol["execution_authorized"] = authorized
    if authorized:
        protocol["status"] = "authorized_not_executed"
    if mutate is not None:
        mutate(protocol)
    path = tmp_path / "taskpack.json"
    _write_json(path, protocol)
    return protocol, path


def _prepare_case(tmp_path, *, authorized=False, public=None, canaries=(), mutate=None):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(FIXTURE_PDF)
    protocol, taskpack_path = _authorized_protocol(
        tmp_path, pdf=FIXTURE_PDF, authorized=authorized, mutate=mutate
    )
    public = public or {
        "scenario": "Offline engineering fixture for the frozen development article.",
        "constraints": ["Public scenario only; no private answer or rubric."],
    }
    public_path = tmp_path / "public-input.json"
    _write_json(public_path, public)
    canary_paths = []
    for index, canary in enumerate(canaries):
        canary_path = tmp_path / f"canary-{index}.txt"
        canary_path.write_text(canary + "\n", encoding="utf-8")
        canary_paths.append(canary_path)
    out = tmp_path / "prepared"
    manifest = adapter.prepare(
        taskpack_path=taskpack_path,
        public_input_path=public_path,
        pdf_path=pdf,
        out_dir=out,
        selected_model=MODEL,
        private_canary_files=canary_paths,
    )
    return {
        "protocol": protocol,
        "taskpack_path": taskpack_path,
        "pdf": pdf,
        "public_path": public_path,
        "out": out,
        "manifest": manifest,
        "plan": json.loads((out / "plan.json").read_text(encoding="utf-8")),
    }


class FakeRunner:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, argv, timeout_s):
        self.calls.append((list(argv), timeout_s))
        if not self.results:
            raise AssertionError("unexpected public CLI call")
        value = self.results.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def _ok(response):
    return adapter.RunnerResult(0, json.dumps(response, ensure_ascii=False))


def _success_results():
    ref = "research_project:offline-fixture"
    return [
        _ok({"ref": "source:offline-fixture"}),
        _ok({"project_ref": ref, "state": "ready"}),
        _ok({"project_ref": ref, "state": "ready"}),
        _ok({"project_ref": ref, "state": "stopped", "snapshot_ref": "snap:1"}),
        _ok({"project_ref": ref, "state": "stopped"}),
        _ok({"snapshot_ref": "research_project_snapshot:offline-fixture"}),
    ]


def _run(case, results):
    runner = FakeRunner(results)
    status = adapter.execute(
        case["out"], taskpack_path=case["taskpack_path"], runner=runner
    )
    return runner, status


def _forbidden_runner(calls):
    def runner(argv, timeout_s):
        calls.append((list(argv), timeout_s))
        raise AssertionError("public CLI must not be called")

    return runner


def test_cli_help_is_usable(capsys):
    with pytest.raises(SystemExit) as stopped:
        adapter.main(["--help"])
    assert stopped.value.code == 0
    output = capsys.readouterr().out
    assert "prepare" in output and "run" in output and "show" in output


def test_prepare_validates_frozen_inputs_without_any_external_call(tmp_path, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("offline preparation invoked a subprocess")

    monkeypatch.setattr(adapter.subprocess, "run", forbidden)
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(FIXTURE_PDF)
    _, taskpack_path = _authorized_protocol(tmp_path, pdf=FIXTURE_PDF, authorized=False)
    public_path = tmp_path / "public-input.json"
    _write_json(public_path, {
        "scenario": "Offline engineering fixture for the frozen development article.",
        "constraints": ["Public scenario only."],
    })
    out = tmp_path / "prepared"
    code = adapter.main([
        "prepare", "--taskpack", str(taskpack_path), "--public-input", str(public_path),
        "--pdf", str(pdf), "--out", str(out), "--model", MODEL,
    ])
    assert code == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["status"] == "prepared_not_executed"
    assert manifest["taskpack"]["execution_authorized"] is False
    assert manifest["claims"]["model_calls_made"] == 0
    assert manifest["claims"]["execution_attempted"] is False
    assert manifest["claims"]["scientific_evaluation_executed"] is False
    assert manifest["claims"]["scientific_support"] == "unknown"
    assert manifest["paper"]["sha256"] == hashlib.sha256(FIXTURE_PDF).hexdigest()
    assert manifest["paper"]["bytes"] == len(FIXTURE_PDF)
    assert manifest["paper"]["unique_papers"] == 1
    assert manifest["paper"]["tracks_on_same_paper"] == 2
    assert manifest["denominator"]["paper_level"] == 1
    assert manifest["denominator"]["track_level"] == 2
    assert manifest["model"]["allowed"] == ["deepseek-flash-2", "glm-5.3-flash"]
    assert manifest["model"]["requested_effort"] == "max"
    assert manifest["model"]["max_output_tokens_per_call"] == 8000
    assert manifest["model"]["provider_timeout_s"] == 900
    assert manifest["model"]["effort_transport"]["status"] == "forwarded_via_public_cli"
    assert manifest["model"]["effort_transport"]["forwarded"] == {
        "reasoning_effort": "max",
        "max_output_tokens": 8000,
        "provider_timeout_s": 900,
    }
    assert manifest["model"]["effort_transport"]["service_internal_effort_independently_verified"] is False
    tracks = {track["id"]: track for track in manifest["tracks"]}
    assert tracks["original_fulltext"]["dispatch_state"] == "planned"
    assert tracks["named_transcription_assisted"]["dispatch_state"] == "missing_pending_named_transcription"
    assert tracks["named_transcription_assisted"]["missing_reason"] == "named_human_transcription_not_supplied"
    assert tracks["named_transcription_assisted"]["actor"] is None
    assert tracks["named_transcription_assisted"]["transcription_policy"] == "named_attributed_transcription_only"
    assert tracks["named_transcription_assisted"]["operation_ids"] == []
    blockers = {item["id"]: item for item in manifest["interface_blockers"]}
    assert blockers["reasoning_effort_not_forwarded"]["status"] == "resolved_public_cli_forwarding_not_service_verified"
    assert blockers["reasoning_effort_not_forwarded"]["forwarded_value"] == "max"
    assert blockers["max_output_tokens_transport"]["forwarded_value"] == 8000
    assert blockers["provider_timeout_transport"]["forwarded_value"] == 900
    assert blockers["service_internal_effort_verification"]["status"] == "not_independently_verified"
    assert blockers["batch_paper_identifier_unavailable"]["status"] == "not_used"
    assert manifest["canary"]["status"] == "not_run_no_canaries_supplied"
    assert manifest["canary"]["general_semantic_nonleak_claim"] is False


def test_prepare_freezes_public_transport_watchdog_and_version_binding(tmp_path):
    from biomodel_agent.recovery import implementation_fingerprint

    case = _prepare_case(tmp_path)
    manifest = case["manifest"]
    plan = case["plan"]
    assert manifest["budget"]["max_output_tokens_per_call"] == 8000
    assert manifest["budget"]["provider_timeout_s"] == 900
    assert manifest["budget"]["project_run_watchdog_s"] == 960.0
    assert manifest["budget"]["project_run_watchdog_grace_s"] == 60.0
    assert manifest["budget"]["watchdog_grace_scope"] == "supervision_and_receipt_persistence_only"
    assert manifest["budget"]["product_policy_total_timeout_s"] == 900
    assert manifest["budget"]["product_policy_total_timeout_s_unchanged"] is True
    run_operation = next(
        operation for operation in plan["operations"] if operation["kind"] == "project_run"
    )
    assert run_operation["timeout_s"] == 960.0
    assert run_operation["product_deadline_s"] == 900.0
    assert run_operation["watchdog_grace_s"] == 60.0
    assert run_operation["watchdog_grace_scope"] == "supervision_and_receipt_persistence_only"
    assert "--reasoning-effort" in run_operation["argv_template"]
    assert "--max-output-tokens" in run_operation["argv_template"]
    assert "--provider-timeout" in run_operation["argv_template"]
    template = run_operation["argv_template"]
    assert template[template.index("--reasoning-effort") + 1] == "max"
    assert template[template.index("--max-output-tokens") + 1] == "8000"
    assert template[template.index("--provider-timeout") + 1] == "900"
    assert plan["execution_options"]["python_launcher"][1:4] == ["-X", "utf8", "-m"]
    assert plan["execution_options"]["product_policy_total_timeout_s"] == 900
    assert plan["execution_options"]["product_policy_total_timeout_s_unchanged"] is True
    assert manifest["diagnostics"]["directory"] == "diagnostics"
    assert manifest["diagnostics"]["receipt_binding"] == ["path", "sha256", "bytes"]
    binding = manifest["version_binding"]
    assert binding["adapter_sha256"] == hashlib.sha256(SCRIPT.read_bytes()).hexdigest()
    assert binding["implementation_fingerprint"] == implementation_fingerprint()
    assert binding["implementation_fingerprint_source"] == (
        "biomodel_agent.recovery.implementation_fingerprint"
    )
    assert plan["version_binding"] == binding
    from importlib.metadata import version as live_pdf_version

    pdf_engine = binding["pdf_engine"]
    assert pdf_engine["parser"] == "pypdf"
    assert pdf_engine["distribution"] == "pypdf"
    assert pdf_engine["version"] == live_pdf_version("pypdf")
    assert pdf_engine["version_source"] == "importlib.metadata.version"
    assert pdf_engine["query_policy"] == (
        "requery_live_environment_at_freeze_and_every_verification"
    )
    assert plan["execution_options"]["pdf_parser"] == pdf_engine["parser"]
    assert manifest["claims"]["public_cli_option_forwarding_prepared"] is True
    assert manifest["claims"]["service_internal_effort_independently_verified"] is False


def test_prepare_projection_contains_only_public_fields_and_no_canary(tmp_path):
    canary = "PRIVATE-CANARY-7f43-do-not-project"
    case = _prepare_case(tmp_path, canaries=[canary])
    manifest = case["manifest"]
    assert manifest["canary"]["status"] == "passed_literal_check"
    assert manifest["canary"]["checked_literals"] == 1
    assert manifest["canary"]["general_semantic_nonleak_claim"] is False
    projection = json.loads(
        (case["out"] / "public-input.json").read_text(encoding="utf-8")
    )
    assert projection["scenario"] == "Offline engineering fixture for the frozen development article."
    assert set(projection) == {"scenario", "constraints", "source_policy"}
    assert canary not in json.dumps(projection)
    for path in case["out"].rglob("*"):
        if path.is_file():
            assert canary not in path.read_text(encoding="utf-8")
    project = json.loads(
        (case["out"] / "projects/original_fulltext.json").read_text(encoding="utf-8")
    )
    assert set(project) == {"scenario", "mode", "policy", "constraints"}
    assert "source_refs" not in project
    assert project["mode"] == "reproduction"
    assert project["policy"]["planner_max_calls"] == 12
    assert project["policy"]["max_native_calls"] == 4
    assert project["policy"]["total_timeout_s"] == 900
    assert any(
        "do not substitute a summary, transcription" in constraint
        for constraint in project["constraints"]
    )
    assisted = json.loads(
        (case["out"] / "projects/named_transcription_assisted.json").read_text(encoding="utf-8")
    )
    assert set(assisted) == {"scenario", "mode", "policy", "constraints"}
    assert assisted["policy"]["total_timeout_s"] == 900
    assert any(
        "Named human transcription assistance is permitted" in constraint
        and "actor, timestamp, reason, original region and affected fields" in constraint
        and "Retain the complete original PDF unchanged" in constraint
        for constraint in assisted["constraints"]
    )
    assert not any(
        "do not substitute a summary, transcription" in constraint
        for constraint in assisted["constraints"]
    )


def test_prepare_rejects_hash_and_byte_mismatch(tmp_path):
    case = _prepare_case(tmp_path)
    case["pdf"].write_bytes(FIXTURE_PDF[:-1] + b"X")
    with pytest.raises(adapter.AdapterError, match="sha256"):
        adapter.prepare(
            taskpack_path=case["taskpack_path"],
            public_input_path=case["public_path"],
            pdf_path=case["pdf"],
            out_dir=tmp_path / "second",
            selected_model=MODEL,
        )
    protocol = deepcopy(case["protocol"])
    protocol["paper_candidate"]["bytes"] = len(FIXTURE_PDF) + 1
    bad_path = tmp_path / "bad-bytes.json"
    _write_json(bad_path, protocol)
    with pytest.raises(adapter.AdapterError, match="bytes"):
        adapter.prepare(
            taskpack_path=bad_path,
            public_input_path=case["public_path"],
            pdf_path=case["pdf"],
            out_dir=tmp_path / "third",
            selected_model=MODEL,
        )


@pytest.mark.parametrize("field,value", [
    ("private_reference", "PRIVATE-CANARY-unknown-field"),
    ("scoring_rubric", {"threshold": 0.5}),
    ("future_clarifications", ["later answer"]),
    ("summary", "a human answer-like summary"),
])
def test_public_input_rejects_unknown_or_private_fields(tmp_path, field, value):
    public = {
        "scenario": "Offline engineering fixture.",
        "constraints": [],
        field: value,
    }
    with pytest.raises(adapter.AdapterError, match="unknown public input"):
        _prepare_case(tmp_path, public=public)


def test_private_canary_projected_into_public_input_is_refused(tmp_path):
    canary = "PRIVATE-CANARY-IN-SCENARIO"
    with pytest.raises(adapter.AdapterError, match="canary"):
        _prepare_case(
            tmp_path,
            public={"scenario": f"Offline fixture {canary}", "constraints": []},
            canaries=[canary],
        )
    assert not (tmp_path / "prepared" / "preparation-manifest.json").exists()


@pytest.mark.parametrize("canary", JSON_METACHAR_CANARIES)
def test_canary_check_covers_nested_public_values(canary):
    payload = [{"outer": [{"inner": [canary]}]}]
    with pytest.raises(adapter.AdapterError, match="canary"):
        adapter._assert_canaries_absent([payload], [canary])


@pytest.mark.parametrize("canary", JSON_METACHAR_CANARIES)
def test_canary_check_covers_public_keys(canary):
    payload = {"outer": [{canary: "value"}]}
    with pytest.raises(adapter.AdapterError, match="canary"):
        adapter._assert_canaries_absent([payload], [canary])


@pytest.mark.parametrize("canary", JSON_METACHAR_CANARIES)
def test_escaped_literal_canary_from_json_list_input_is_refused_before_manifest(
    tmp_path, canary
):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(FIXTURE_PDF)
    _, taskpack_path = _authorized_protocol(tmp_path, pdf=FIXTURE_PDF)
    public_path = tmp_path / "public-input.json"
    _write_json(public_path, {
        "scenario": f"Offline fixture {canary}",
        "constraints": [],
    })
    canary_path = tmp_path / "private-canaries.json"
    _write_json(canary_path, [canary])
    out = tmp_path / "prepared"
    with pytest.raises(adapter.AdapterError, match="canary"):
        adapter.prepare(
            taskpack_path=taskpack_path,
            public_input_path=public_path,
            pdf_path=pdf,
            out_dir=out,
            selected_model=MODEL,
            private_canary_files=[canary_path],
        )
    assert not (out / adapter.MANIFEST_NAME).exists()
    assert not (out / adapter.PLAN_NAME).exists()
    assert not (out / adapter.PUBLIC_INPUT_NAME).exists()


@pytest.mark.parametrize("mutation,message", [
    (lambda p: p["allowed_models"].append("third-model"), "exactly two"),
    (lambda p: p.__setitem__("requested_effort", "high"), "requested_effort"),
    (lambda p: p["budget"].__setitem__("reset_on_restart", True), "reset on restart"),
    (lambda p: p["budget"].__setitem__("max_output_tokens_per_call", 4096),
     "max_output_tokens_per_call"),
    (lambda p: p["tracks"][1].__setitem__("source_sha256", "0" * 64), "same frozen source"),
    (lambda p: p["isolation"]["private_numeric_reference"].__setitem__("in_public_projection", True),
     "outside the public projection"),
    (lambda p: p["paper_candidate"]["local_pdf_metadata_evidence"].__setitem__(
        "publication_verified", True), "external publication verification"),
    (lambda p: p["paper_candidate"].__setitem__("doi", "10.0000/printed-candidate"),
     "DOI candidate"),
])
def test_prepare_rejects_protocol_gate_changes(tmp_path, mutation, message):
    with pytest.raises(adapter.ProtocolError, match=message):
        _prepare_case(tmp_path, mutate=mutation)


def test_forged_two_model_list_is_rejected(tmp_path):
    with pytest.raises(adapter.ProtocolError, match="frozen models"):
        _prepare_case(
            tmp_path,
            mutate=lambda p: p.__setitem__(
                "allowed_models", ["deepseek-flash-2", "forged-flash-9"]
            ),
        )


def test_selected_model_must_be_allowed(tmp_path):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(FIXTURE_PDF)
    _, taskpack_path = _authorized_protocol(tmp_path, pdf=FIXTURE_PDF)
    public_path = tmp_path / "public-input.json"
    _write_json(public_path, {"scenario": "Offline fixture.", "constraints": []})
    with pytest.raises(adapter.AdapterError, match="allowed_models"):
        adapter.prepare(
            taskpack_path=taskpack_path,
            public_input_path=public_path,
            pdf_path=pdf,
            out_dir=tmp_path / "prepared",
            selected_model="unlisted-model",
        )


def test_current_protocol_refuses_execution_before_any_public_command(tmp_path):
    case = _prepare_case(tmp_path, authorized=False)
    calls = []
    with pytest.raises(adapter.ExecutionNotAuthorized, match="execution_authorized=false"):
        adapter.execute(
            case["out"], taskpack_path=case["taskpack_path"],
            runner=_forbidden_runner(calls),
        )
    assert calls == []
    state = adapter.status(case["out"])
    assert state["adapter_status"] == "prepared_not_executed"
    assert state["receipt_count"] == 0
    assert not (case["out"] / "run.json").exists()
    assert state["denominator"]["track_level"] == 2
    assert state["denominator"]["excluded_tracks"] == 0


@pytest.mark.parametrize("bad_result", [
    TimeoutError("offline timeout"),
    adapter.RunnerResult(2, "{}", "non-zero"),
    adapter.RunnerResult(0, "not-json", ""),
])
def test_timeout_nonzero_and_bad_json_are_unknown_and_never_replayed(tmp_path, bad_result):
    case = _prepare_case(tmp_path, authorized=True)
    runner, state = _run(case, [bad_result])
    assert len(runner.calls) == 1
    assert "source-ingest" in runner.calls[0][0]
    assert state["adapter_status"] == "stopped_unknown"
    assert state["blocking_operation_ids"] == ["source-ingest"]
    tracks = {track["id"]: track for track in state["denominator"]["tracks"]}
    assert tracks["original_fulltext"]["dispatch_state"] == "unknown"
    assert tracks["named_transcription_assisted"]["dispatch_state"] == "missing_pending_named_transcription"
    assert state["denominator"]["retained_tracks"] == 2
    assert state["unresolved_reservation_ids"] == []
    calls = []
    resumed = adapter.execute(
        case["out"], taskpack_path=case["taskpack_path"], runner=_forbidden_runner(calls)
    )
    assert calls == []
    assert resumed["adapter_status"] == "stopped_unknown"
    assert resumed["receipt_count"] == 1


def test_reservation_without_receipt_is_reconciled_as_unknown_without_replay(tmp_path):
    case = _prepare_case(tmp_path, authorized=True)
    taskpack_sha = hashlib.sha256(case["taskpack_path"].read_bytes()).hexdigest()
    adapter._initialise_run(case["out"], case["manifest"], case["plan"], taskpack_sha)
    adapter._append_jsonl(case["out"] / adapter.JOURNAL_NAME, {
        "event": "reserve",
        "version": 1,
        "reservation_id": "source-ingest:simulated-crash",
        "op_id": "source-ingest",
        "track_scope": ["original_fulltext", "named_transcription_assisted"],
        "command": "source-ingest",
        "argv": ["python", "-m", "biomodel_agent.cli", "source-ingest", "fixture"],
        "timeout_s": 300.0,
        "started_at": "2026-11-10T00:00:00+00:00",
        "monotonic_started": 0.0,
        "state": "reserved",
    })
    calls = []
    state = adapter.execute(
        case["out"], taskpack_path=case["taskpack_path"], runner=_forbidden_runner(calls)
    )
    assert calls == []
    assert state["adapter_status"] == "stopped_unknown"
    assert state["denominator"]["tracks"][0]["dispatch_state"] == "unknown"
    assert state["denominator"]["tracks"][0]["operations"]["source-ingest"] == "unknown"
    assert state["unresolved_reservation_ids"] == []
    receipts = [
        json.loads(line)
        for line in (case["out"] / "receipts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(receipts) == 1
    assert receipts[0]["state"] == "unknown"
    assert receipts[0]["error_type"] == "reservation_without_receipt_after_interruption"


def test_recovery_binds_existing_diagnostics_without_overwrite_or_replay(tmp_path):
    case = _prepare_case(tmp_path, authorized=True)
    taskpack_sha = hashlib.sha256(case["taskpack_path"].read_bytes()).hexdigest()
    adapter._initialise_run(case["out"], case["manifest"], case["plan"], taskpack_sha)
    diagnostic_text = "partial stdout before interruption: 中文\n"
    diagnostic_path = case["out"] / "diagnostics" / "source-ingest.stdout.txt"
    diagnostic_path.parent.mkdir(parents=True)
    diagnostic_path.write_text(diagnostic_text, encoding="utf-8")
    # Bind to the bytes actually on disk.  write_text/read_text translate
    # newlines on Windows, so hashing the in-memory text would be wrong.
    diagnostic_bytes_before_execute = diagnostic_path.read_bytes()
    adapter._append_jsonl(case["out"] / adapter.JOURNAL_NAME, {
        "event": "reserve",
        "version": 1,
        "reservation_id": "source-ingest:simulated-crash",
        "op_id": "source-ingest",
        "track_scope": ["original_fulltext", "named_transcription_assisted"],
        "command": "source-ingest",
        "argv": ["python", "-X", "utf8", "-m", "biomodel_agent.cli", "source-ingest", "fixture"],
        "timeout_s": 300.0,
        "started_at": "2026-11-10T00:00:00+00:00",
        "monotonic_started": 0.0,
        "state": "reserved",
    })
    calls = []
    state = adapter.execute(
        case["out"], taskpack_path=case["taskpack_path"], runner=_forbidden_runner(calls)
    )
    assert calls == []
    assert state["adapter_status"] == "stopped_unknown"
    receipt = json.loads(
        (case["out"] / "receipts.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert receipt["state"] == "unknown"
    assert receipt["error_type"] == "reservation_without_receipt_after_interruption"
    binding = receipt["diagnostics"]["stdout"]
    assert binding["path"] == "diagnostics/source-ingest.stdout.txt"
    assert binding["sha256"] == hashlib.sha256(diagnostic_bytes_before_execute).hexdigest()
    assert binding["bytes"] == len(diagnostic_bytes_before_execute)
    assert diagnostic_path.read_bytes() == diagnostic_bytes_before_execute
    assert diagnostic_path.read_text(encoding="utf-8") == diagnostic_text


def test_resume_after_partial_success_does_not_recreate_or_reset_budget(tmp_path):
    case = _prepare_case(tmp_path, authorized=True)
    ref = "research_project:offline-fixture"
    runner = FakeRunner([
        _ok({"ref": "source:offline-fixture"}),
        _ok({"project_ref": ref, "state": "ready"}),
        _ok({"project_ref": ref, "state": "ready"}),
        KeyboardInterrupt(),
    ])
    with pytest.raises(KeyboardInterrupt):
        adapter.execute(case["out"], taskpack_path=case["taskpack_path"], runner=runner)
    first = adapter.status(case["out"])
    assert len(runner.calls) == 4
    assert sum("research-project-create" in call[0] for call in runner.calls) == 1
    assert first["adapter_status"] == "stopped_unknown"
    raw = first["denominator"]["tracks"][0]
    assert raw["dispatch_state"] == "unknown"
    assert raw["operations"]["source-ingest"] == "succeeded"
    assert raw["operations"]["original_fulltext.create"] == "succeeded"
    assert raw["operations"]["original_fulltext.show_after_create"] == "succeeded"
    assert raw["operations"]["original_fulltext.run"] == "unknown"
    assert raw["operations"]["original_fulltext.show_after_run"] == "missing"
    ledger_before = json.loads(
        (case["out"] / "budget-ledger.json").read_text(encoding="utf-8")
    )
    run_before = json.loads((case["out"] / "run.json").read_text(encoding="utf-8"))
    calls = []
    resumed = adapter.execute(
        case["out"], taskpack_path=case["taskpack_path"], runner=_forbidden_runner(calls)
    )
    assert calls == []
    assert resumed["adapter_status"] == "stopped_unknown"
    ledger_after = json.loads(
        (case["out"] / "budget-ledger.json").read_text(encoding="utf-8")
    )
    run_after = json.loads((case["out"] / "run.json").read_text(encoding="utf-8"))
    assert ledger_after["limits"] == ledger_before["limits"]
    assert ledger_after["reset_on_restart"] is False
    assert ledger_after["resume_count"] == ledger_before["resume_count"] + 1
    assert run_after["budget"] == run_before["budget"]
    assert resumed["denominator"]["retained_tracks"] == 2
    assert sum("research-project-create" in call[0] for call in calls) == 0


def test_product_failure_keeps_failed_and_missing_tracks_in_denominator(tmp_path):
    case = _prepare_case(tmp_path, authorized=True)
    ref = "research_project:offline-fixture"
    runner, state = _run(case, [
        _ok({"ref": "source:offline-fixture"}),
        _ok({"project_ref": ref, "state": "ready"}),
        _ok({"project_ref": ref, "state": "ready"}),
        _ok({"state": "failed"}),
    ])
    assert len(runner.calls) == 4
    assert state["adapter_status"] == "stopped_failed"
    assert state["blocking_operation_ids"] == ["original_fulltext.run"]
    tracks = {track["id"]: track for track in state["denominator"]["tracks"]}
    assert tracks["original_fulltext"]["dispatch_state"] == "failed"
    assert tracks["original_fulltext"]["operations"]["original_fulltext.show_after_run"] == "missing"
    assert tracks["named_transcription_assisted"]["dispatch_state"] == "missing_pending_named_transcription"
    assert state["denominator"]["retained_tracks"] == 2
    assert state["denominator"]["excluded_tracks"] == 0


def test_successful_public_cli_dispatch_uses_only_real_options_and_persists_receipts(tmp_path):
    case = _prepare_case(tmp_path, authorized=True)
    runner, state = _run(case, _success_results())
    assert len(runner.calls) == 6
    commands = [" ".join(call[0]) for call in runner.calls]
    assert "source-ingest" in commands[0]
    assert "research-project-create" in commands[1]
    assert "research-project-show" in commands[2]
    assert "research-project-run" in commands[3]
    assert "research-project-show" in commands[4]
    assert "research-project-snapshot" in commands[5]
    for argv, _timeout_s in runner.calls:
        assert argv[:5] == [sys.executable, "-X", "utf8", "-m", "biomodel_agent.cli"]
    run_argv = runner.calls[3][0]

    def option(name):
        assert name in run_argv
        return run_argv[run_argv.index(name) + 1]

    assert option("--model") == MODEL
    assert option("--reasoning-effort") == "max"
    assert option("--max-output-tokens") == "8000"
    assert option("--provider-timeout") == "900"
    assert runner.calls[3][1] == 960.0
    assert state["adapter_status"] == "dispatch_completed"
    assert state["receipt_count"] == 6
    assert state["version_binding"]["status"] == "matches_prepared"
    assert state["denominator"]["retained_tracks"] == 2
    assert state["denominator"]["excluded_tracks"] == 0
    raw = state["denominator"]["tracks"][0]
    assert raw["dispatch_state"] == "succeeded"
    assert raw["product_reported_state"] == "stopped"
    assert all(value == "succeeded" for value in raw["operations"].values())
    assert state["budget"]["limits"]["planner_calls_total"] == 24
    assert state["budget"]["limits"]["native_calls_total"] == 8
    assert state["budget"]["limits"]["max_output_tokens_per_call"] == 8000
    assert state["budget"]["limits"]["provider_timeout_s"] == 900
    assert state["budget"]["reset_on_restart"] is False
    receipts = [
        json.loads(line)
        for line in (case["out"] / "receipts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(receipts) == 6
    assert all(receipt["external_call"] is True for receipt in receipts)
    assert all(receipt["state"] == "succeeded" for receipt in receipts)
    for receipt in receipts:
        assert set(receipt["diagnostics"]) == {"stdout"}
        binding = receipt["diagnostics"]["stdout"]
        assert binding["path"] == f"diagnostics/{receipt['op_id']}.stdout.txt"
        data = (case["out"] / binding["path"]).read_bytes()
        assert binding["sha256"] == hashlib.sha256(data).hexdigest()
        assert binding["sha256"] == receipt["stdout_sha256"]
        assert binding["bytes"] == len(data)
    assert (case["out"] / "runtime" / "original_fulltext-project.json").is_file()
    calls = []
    resumed = adapter.execute(
        case["out"], taskpack_path=case["taskpack_path"], runner=_forbidden_runner(calls)
    )
    assert calls == []
    assert resumed["adapter_status"] == "dispatch_completed"
    assert resumed["receipt_count"] == 6


def test_concurrent_writer_to_same_run_directory_is_rejected(tmp_path):
    case = _prepare_case(tmp_path, authorized=True)
    calls = []
    with adapter.RunDirLock(case["out"]):
        with pytest.raises(adapter.ConcurrentRunError, match="another writer"):
            adapter.execute(
                case["out"], taskpack_path=case["taskpack_path"],
                runner=_forbidden_runner(calls),
            )
    assert calls == []


def test_actual_public_cli_creates_and_shows_an_empty_project(tmp_path, capsys):
    case = _prepare_case(tmp_path, authorized=False)
    from biomodel_agent.cli import main as public_main

    store = tmp_path / "public-store"
    config = case["out"] / "projects" / "original_fulltext.json"
    assert public_main([
        "research-project-create", "--store", str(store), "--config", str(config),
    ]) == 0
    created = json.loads(capsys.readouterr().out)
    ref = created["project_ref"]
    assert created["state"] == "ready"
    assert public_main([
        "research-project-show", ref, "--store", str(store),
    ]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["project_ref"] == ref
    assert shown["state"] == "ready"
    assert shown["scientific_support"] == "unknown"
    assert shown["budget"]["planner_max_calls"] == 12
    assert shown["budget"]["max_native_calls"] == 4


def test_actual_public_cli_ingests_txt_and_binds_source_ref_to_project(tmp_path, capsys):
    case = _prepare_case(tmp_path, authorized=False)
    from biomodel_agent.cli import main as public_main

    store = tmp_path / "public-store"
    material = tmp_path / "offline-material.txt"
    material.write_text(
        "Offline engineering material for the public CLI contract; no model call.\n",
        encoding="utf-8",
    )
    assert public_main(["source-ingest", str(material), "--store", str(store)]) == 0
    ingested = json.loads(capsys.readouterr().out)
    source_ref = ingested["ref"]
    assert isinstance(source_ref, str) and source_ref

    config = json.loads(
        (case["out"] / "projects" / "original_fulltext.json").read_text(encoding="utf-8")
    )
    config["source_refs"] = [source_ref]
    bound_config = tmp_path / "bound-project.json"
    _write_json(bound_config, config)
    assert public_main([
        "research-project-create", "--store", str(store), "--config", str(bound_config),
    ]) == 0
    created = json.loads(capsys.readouterr().out)
    project_ref = created["project_ref"]
    assert created["state"] == "ready"
    assert public_main([
        "research-project-show", project_ref, "--store", str(store),
    ]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["project_ref"] == project_ref
    assert shown["state"] == "ready"
    assert source_ref in json.dumps(shown, ensure_ascii=False)


@pytest.mark.parametrize("explicit_runner", [False, True])
def test_legacy_blocked_preparation_is_refused_before_any_command(
    tmp_path, monkeypatch, explicit_runner
):
    case = _prepare_case(tmp_path, authorized=True)

    def to_legacy_blocked(plan):
        plan["model"]["effort_transport"] = {
            "requested_effort": "max",
            "status": "blocked_not_transmitted",
            "interface": "research-project-run",
            "missing_option": "--reasoning-effort",
            "claim": "requested_effort_validated_not_transmitted",
        }

    _rewrite_plan(case, to_legacy_blocked)
    calls = []

    def forbidden_subprocess(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("actual public CLI must not be invoked")

    monkeypatch.setattr(adapter.subprocess, "run", forbidden_subprocess)
    runner = adapter.SubprocessRunner() if explicit_runner else _forbidden_runner(calls)
    with pytest.raises(adapter.InterfaceBlocked, match="blocked_not_transmitted"):
        adapter.execute(
            case["out"], taskpack_path=case["taskpack_path"], runner=runner
        )
    assert calls == []
    assert not (case["out"] / "run.json").exists()
    assert not (case["out"] / "budget-ledger.json").exists()
    shown = adapter.status(case["out"])
    assert shown["adapter_status"] == "prepared_not_executed"
    assert shown["receipt_count"] == 0
    assert shown["denominator"]["retained_tracks"] == 2
    assert shown["denominator"]["excluded_tracks"] == 0


@pytest.mark.parametrize("field,match", [
    ("adapter_sha256", "adapter file sha256 changed"),
    ("implementation_fingerprint", "public implementation fingerprint changed"),
])
def test_changed_version_binding_refuses_execution_but_show_remains_available(
    tmp_path, field, match
):
    case = _prepare_case(tmp_path, authorized=True)
    manifest_path = case["out"] / adapter.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version_binding"][field] = "0" * 64
    adapter._write_json(manifest_path, manifest)
    calls = []
    with pytest.raises(adapter.ProtocolError, match=match):
        adapter.execute(
            case["out"], taskpack_path=case["taskpack_path"],
            runner=_forbidden_runner(calls),
        )
    assert calls == []
    assert not (case["out"] / "run.json").exists()
    assert not (case["out"] / "budget-ledger.json").exists()
    shown = adapter.status(case["out"])
    assert shown["adapter_status"] == "prepared_not_executed"
    assert shown["receipt_count"] == 0
    assert shown["version_binding"]["status"] == "changed_since_preparation"
    assert shown["version_binding"]["readonly_history_available"] is True
    assert shown["denominator"]["retained_tracks"] == 2
    assert shown["denominator"]["excluded_tracks"] == 0


def test_legacy_missing_version_binding_is_explicit_and_show_is_readonly(tmp_path):
    case = _prepare_case(tmp_path, authorized=True)
    manifest_path = case["out"] / adapter.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["version_binding"]
    adapter._write_json(manifest_path, manifest)
    calls = []
    with pytest.raises(adapter.ProtocolError, match="legacy preparation artifacts"):
        adapter.execute(
            case["out"], taskpack_path=case["taskpack_path"],
            runner=_forbidden_runner(calls),
        )
    assert calls == []
    assert not (case["out"] / "run.json").exists()
    shown = adapter.status(case["out"])
    assert shown["version_binding"]["status"] == "legacy_missing_version_binding"
    assert shown["version_binding"]["execution_refused"] is True
    assert shown["version_binding"]["readonly_history_available"] is True
    assert shown["adapter_status"] == "prepared_not_executed"


def test_legacy_plan_missing_version_binding_is_explicit_and_show_is_readonly(tmp_path):
    case = _prepare_case(tmp_path, authorized=True)
    _rewrite_plan(case, lambda plan: plan.pop("version_binding"))
    calls = []
    with pytest.raises(adapter.ProtocolError, match="legacy execution plan"):
        adapter.execute(
            case["out"], taskpack_path=case["taskpack_path"],
            runner=_forbidden_runner(calls),
        )
    assert calls == []
    shown = adapter.status(case["out"])
    assert shown["version_binding"]["status"] == "legacy_missing_version_binding"
    assert shown["version_binding"]["execution_refused"] is True
    assert shown["adapter_status"] == "prepared_not_executed"


def _prepared_run_directory_snapshot(out):
    return {
        path.relative_to(out).as_posix(): path.read_bytes()
        for path in out.rglob("*") if path.is_file()
    }


def test_pdf_only_version_drift_refuses_execution_and_preserves_history(tmp_path, monkeypatch):
    import importlib.metadata
    from biomodel_agent.recovery import implementation_fingerprint

    case = _prepare_case(tmp_path, authorized=True)
    prepared_version = case["manifest"]["version_binding"]["pdf_engine"]["version"]
    drifted_version = prepared_version + "+version-drift-control"
    original_version = importlib.metadata.version
    original_fingerprint = implementation_fingerprint()

    def changed_pdf_version(distribution):
        if distribution == "pypdf":
            return drifted_version
        return original_version(distribution)

    monkeypatch.setattr(importlib.metadata, "version", changed_pdf_version)
    assert implementation_fingerprint() == original_fingerprint
    assert case["manifest"]["version_binding"]["implementation_fingerprint"] == (
        original_fingerprint
    )

    before = _prepared_run_directory_snapshot(case["out"])
    assert len(before) == 6
    calls = []
    with pytest.raises(adapter.ProtocolError, match="PDF engine version changed"):
        adapter.execute(
            case["out"], taskpack_path=case["taskpack_path"],
            runner=_forbidden_runner(calls),
        )
    assert calls == []
    assert _prepared_run_directory_snapshot(case["out"]) == before
    assert not (case["out"] / "run.json").exists()
    assert not (case["out"] / "budget-ledger.json").exists()
    assert not (case["out"] / adapter.JOURNAL_NAME).exists()
    shown = adapter.status(case["out"])
    assert _prepared_run_directory_snapshot(case["out"]) == before
    report = shown["version_binding"]
    assert report["status"] == "pdf_engine_changed_since_preparation"
    assert report["execution_refused"] is True
    assert report["readonly_history_available"] is True
    assert report["pdf_engine"]["prepared"]["version"] == prepared_version
    assert report["pdf_engine"]["current"]["version"] == drifted_version
    assert report["pdf_engine"]["execution_refused"] is True
    assert shown["adapter_status"] == "prepared_not_executed"
    assert shown["receipt_count"] == 0
    assert shown["denominator"]["retained_tracks"] == 2
    assert shown["denominator"]["excluded_tracks"] == 0


@pytest.mark.parametrize("mode,expected_status", [
    ("legacy_missing_pdf_binding", "legacy_missing_pdf_engine_binding"),
    ("pdf_engine_unavailable", "current_pdf_engine_unavailable"),
])
def test_missing_pdf_binding_or_unavailable_engine_blocks_execution_but_show_is_readable(
    tmp_path, monkeypatch, mode, expected_status
):
    from importlib.metadata import version as live_pdf_version

    case = _prepare_case(tmp_path, authorized=True)
    prepared_version = case["manifest"]["version_binding"]["pdf_engine"]["version"]
    if mode == "legacy_missing_pdf_binding":
        def drop_pdf_binding(plan):
            plan["version_binding"].pop("pdf_engine")

        _rewrite_plan(case, drop_pdf_binding)
        manifest_path = case["out"] / adapter.MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["version_binding"].pop("pdf_engine")
        adapter._write_json(manifest_path, manifest)
        expected_match = "no frozen default PDF engine binding"
    else:
        def unavailable(distribution=adapter.PDF_PARSER_DISTRIBUTION):
            raise adapter.PdfEngineUnavailable(
                f"the PDF parser distribution '{distribution}' is unavailable here"
            )

        monkeypatch.setattr(adapter, "_installed_pdf_parser_version", unavailable)
        expected_match = "cannot be queried"

    before = _prepared_run_directory_snapshot(case["out"])
    calls = []
    with pytest.raises(adapter.ProtocolError, match=expected_match):
        adapter.execute(
            case["out"], taskpack_path=case["taskpack_path"],
            runner=_forbidden_runner(calls),
        )
    assert calls == []
    assert _prepared_run_directory_snapshot(case["out"]) == before
    assert not (case["out"] / "run.json").exists()
    assert not (case["out"] / "budget-ledger.json").exists()
    assert not (case["out"] / adapter.JOURNAL_NAME).exists()
    assert not (case["out"] / "receipts.jsonl").exists()
    shown = adapter.status(case["out"])
    assert _prepared_run_directory_snapshot(case["out"]) == before
    assert shown["adapter_status"] == "prepared_not_executed"
    assert shown["receipt_count"] == 0
    assert shown["denominator"]["retained_tracks"] == 2
    report = shown["version_binding"]
    assert report["status"] == expected_status
    assert report["execution_refused"] is True
    assert report["readonly_history_available"] is True
    if mode == "legacy_missing_pdf_binding":
        # The historical pack is never silently re-bound to the current engine.
        assert report["pdf_engine"]["prepared"] is None
        assert report["pdf_engine"]["current"]["version"] == live_pdf_version("pypdf")
    else:
        assert report["prepared_pdf_engine"]["version"] == prepared_version
        assert report["prepared_pdf_engine"]["version"] == live_pdf_version("pypdf")


def test_prepare_refuses_to_freeze_without_a_queryable_pdf_engine_version(
    tmp_path, monkeypatch
):
    def unavailable(distribution=adapter.PDF_PARSER_DISTRIBUTION):
        raise adapter.PdfEngineUnavailable(
            f"the PDF parser distribution '{distribution}' is unavailable here"
        )

    monkeypatch.setattr(adapter, "_installed_pdf_parser_version", unavailable)
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(FIXTURE_PDF)
    _, taskpack_path = _authorized_protocol(tmp_path, pdf=FIXTURE_PDF, authorized=True)
    public_path = tmp_path / "public-input.json"
    _write_json(public_path, {"scenario": "Offline fixture.", "constraints": []})
    out = tmp_path / "prepared"
    with pytest.raises(adapter.PdfEngineUnavailable, match="unavailable here"):
        adapter.prepare(
            taskpack_path=taskpack_path,
            public_input_path=public_path,
            pdf_path=pdf,
            out_dir=out,
            selected_model=MODEL,
        )
    assert not out.exists()
    assert not (out / adapter.MANIFEST_NAME).exists()
    assert not (out / adapter.PLAN_NAME).exists()


def test_subprocess_runner_decodes_utf8_bytes_explicitly(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["kwargs"] = kwargs
        return adapter.subprocess.CompletedProcess(
            argv,
            0,
            stdout='{"ref":"来源:中文路径"}\n'.encode("utf-8"),
            stderr="pypdf 结构告警: 中文路径\n".encode("utf-8"),
        )

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    argv = [sys.executable, "-X", "utf8", "-m", "biomodel_agent.cli", "source-ingest"]
    result = adapter.SubprocessRunner()(argv, 12.0)
    assert captured["argv"] == argv
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["text"] is False
    assert captured["kwargs"]["timeout"] == 12.0
    assert result.stdout == '{"ref":"来源:中文路径"}\n'
    assert result.stderr == "pypdf 结构告警: 中文路径\n"


def test_subprocess_runner_timeout_carries_partial_utf8_output(monkeypatch):
    def fake_run(argv, **kwargs):
        raise adapter.subprocess.TimeoutExpired(
            argv,
            1.0,
            output="部分 stdout 中文".encode("utf-8"),
            stderr="pypdf 结构告警: 超时\n".encode("utf-8"),
        )

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    with pytest.raises(adapter.RunnerTimeout) as stopped:
        adapter.SubprocessRunner()(
            [sys.executable, "-X", "utf8", "-m", "biomodel_agent.cli"], 1.0
        )
    assert stopped.value.stdout == "部分 stdout 中文"
    assert stopped.value.stderr == "pypdf 结构告警: 超时\n"


@pytest.mark.parametrize("result", [
    adapter.RunnerResult(2, '{"state":"failed"}\n', "pypdf: 结构告警: 中文路径\n"),
    adapter.RunnerResult(0, "not-json", "解析失败: 坏 JSON\n"),
])
def test_full_stdout_and_stderr_diagnostics_are_bound_on_unknown(tmp_path, result):
    case = _prepare_case(tmp_path, authorized=True)
    runner, state = _run(case, [result])
    assert len(runner.calls) == 1
    assert state["adapter_status"] == "stopped_unknown"
    receipt = json.loads(
        (case["out"] / "receipts.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert receipt["state"] == "unknown"
    for stream, text in (("stdout", result.stdout), ("stderr", result.stderr)):
        binding = receipt["diagnostics"][stream]
        assert binding["path"] == f"diagnostics/source-ingest.{stream}.txt"
        data = (case["out"] / binding["path"]).read_bytes()
        assert data == text.encode("utf-8")
        assert binding["sha256"] == hashlib.sha256(data).hexdigest()
        assert binding["bytes"] == len(data)
        assert binding["sha256"] == receipt[f"{stream}_sha256"]
        assert receipt[f"{stream}_path"] == binding["path"]
        assert receipt[f"{stream}_bytes"] == binding["bytes"]
    assert state["diagnostics"]["receipts_with_local_streams"] == 1


def test_timeout_partial_utf8_output_stays_unknown_with_diagnostics(tmp_path):
    case = _prepare_case(tmp_path, authorized=True)
    partial_stdout = '{"state": "started", "中文": '
    partial_stderr = "pypdf 结构告警: 超时前输出\n"
    runner, state = _run(
        case, [adapter.RunnerTimeout("timeout", partial_stdout, partial_stderr)]
    )
    assert len(runner.calls) == 1
    assert state["adapter_status"] == "stopped_unknown"
    receipt = json.loads(
        (case["out"] / "receipts.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert receipt["state"] == "unknown"
    assert receipt["error_type"] == "timeout"
    for stream, text in (("stdout", partial_stdout), ("stderr", partial_stderr)):
        binding = receipt["diagnostics"][stream]
        assert (case["out"] / binding["path"]).read_text(encoding="utf-8") == text
        assert binding["sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert binding["bytes"] == len(text.encode("utf-8"))


def test_success_keeps_stderr_parse_warning_diagnostics(tmp_path):
    case = _prepare_case(tmp_path, authorized=True)
    results = _success_results()
    results[0] = adapter.RunnerResult(
        0,
        json.dumps({"ref": "source:offline-fixture"}),
        "pypdf 结构告警: 目录对象异常，但 stdout JSON 完整\n",
    )
    runner, state = _run(case, results)
    assert len(runner.calls) == 6
    assert state["adapter_status"] == "dispatch_completed"
    receipt = json.loads(
        (case["out"] / "receipts.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert receipt["state"] == "succeeded"
    assert receipt["stdout_present"] is True
    assert receipt["stderr_present"] is True
    for stream in ("stdout", "stderr"):
        binding = receipt["diagnostics"][stream]
        data = (case["out"] / binding["path"]).read_bytes()
        assert binding["sha256"] == hashlib.sha256(data).hexdigest()
        assert binding["bytes"] == len(data)
    assert (
        (case["out"] / receipt["diagnostics"]["stderr"]["path"]).read_text(encoding="utf-8")
        == "pypdf 结构告警: 目录对象异常，但 stdout JSON 完整\n"
    )


def test_prepare_rejects_concurrent_writer_in_same_output_directory(tmp_path):
    case = _prepare_case(tmp_path, authorized=True)
    fresh = tmp_path / "prepared-race"
    with adapter.RunDirLock(fresh):
        with pytest.raises(adapter.ConcurrentRunError, match="another writer"):
            adapter.prepare(
                taskpack_path=case["taskpack_path"],
                public_input_path=case["public_path"],
                pdf_path=case["pdf"],
                out_dir=fresh,
                selected_model=MODEL,
            )
        assert not (fresh / adapter.MANIFEST_NAME).exists()


def test_stale_lock_marker_is_inert_for_prepare_and_execute(tmp_path):
    case = _prepare_case(tmp_path, authorized=True)
    fresh = tmp_path / "stale-prepare"
    fresh.mkdir()
    (fresh / adapter.LOCK_NAME).write_text("pid=999999\n", encoding="utf-8")
    manifest = adapter.prepare(
        taskpack_path=case["taskpack_path"],
        public_input_path=case["public_path"],
        pdf_path=case["pdf"],
        out_dir=fresh,
        selected_model=MODEL,
    )
    assert manifest["kind"] == "real_paper_offline_preparation"
    assert (fresh / adapter.MANIFEST_NAME).is_file()

    (case["out"] / adapter.LOCK_NAME).write_text("pid=999999\n", encoding="utf-8")
    runner, state = _run(case, _success_results())
    assert len(runner.calls) == 6
    assert state["adapter_status"] == "dispatch_completed"


def test_relative_cli_inputs_bind_absolute_paths_across_cwd_change(
    tmp_path, monkeypatch, capsys
):
    work = tmp_path / "work"
    work.mkdir()
    pdf = work / "source.pdf"
    pdf.write_bytes(FIXTURE_PDF)
    _, taskpack_path = _authorized_protocol(work, pdf=FIXTURE_PDF, authorized=True)
    public_path = work / "public-input.json"
    _write_json(public_path, {
        "scenario": "Offline engineering fixture for relative CLI input paths.",
        "constraints": [],
    })
    out = work / "prepared"

    monkeypatch.chdir(work)
    assert adapter.main([
        "prepare", "--taskpack", "taskpack.json", "--public-input", "public-input.json",
        "--pdf", "source.pdf", "--out", "prepared", "--model", MODEL,
    ]) == 0
    manifest = json.loads(capsys.readouterr().out)
    assert Path(manifest["taskpack"]["path"]) == taskpack_path
    assert Path(manifest["public_input"]["path"]) == public_path
    assert Path(manifest["paper"]["source_path"]) == pdf
    plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    assert Path(plan["taskpack"]["path"]) == taskpack_path
    assert Path(plan["public_input"]["path"]) == public_path
    assert Path(plan["source"]["path"]) == pdf

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert adapter.main(["show", "--run-dir", str(out)]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["adapter_status"] == "prepared_not_executed"
    runner = FakeRunner(_success_results())
    state = adapter.execute(
        out, taskpack_path=manifest["taskpack"]["path"], runner=runner
    )
    assert len(runner.calls) == 6
    assert state["adapter_status"] == "dispatch_completed"


@pytest.mark.parametrize("product_state", ["needs_recovery", "unknown_effects"])
def test_product_reported_unknown_stops_and_is_not_replayed(tmp_path, product_state):
    case = _prepare_case(tmp_path, authorized=True)
    ref = "research_project:offline-fixture"
    runner = FakeRunner([
        _ok({"ref": "source:offline-fixture"}),
        _ok({"project_ref": ref, "state": "ready"}),
        _ok({"project_ref": ref, "state": "ready"}),
        _ok({"project_ref": ref, "state": product_state}),
    ])
    state = adapter.execute(
        case["out"], taskpack_path=case["taskpack_path"], runner=runner
    )
    assert len(runner.calls) == 4
    assert state["adapter_status"] == "stopped_unknown"
    raw = state["denominator"]["tracks"][0]
    assert raw["dispatch_state"] == "unknown"
    assert raw["product_reported_state"] == product_state
    assert raw["operations"]["original_fulltext.run"] == "unknown"
    assert raw["operations"]["original_fulltext.show_after_run"] == "missing"
    receipts = [
        json.loads(line)
        for line in (case["out"] / "receipts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(receipts) == 4
    assert receipts[3]["state"] == "unknown"
    assert receipts[3]["response"]["state"] == product_state
    assert receipts[3]["error_type"] == "product_reported_unknown_effect"

    calls = []
    resumed = adapter.execute(
        case["out"], taskpack_path=case["taskpack_path"], runner=_forbidden_runner(calls)
    )
    assert calls == []
    assert resumed["adapter_status"] == "stopped_unknown"
    assert resumed["receipt_count"] == 4


@pytest.mark.parametrize("product_state,expected_status,expected_receipt_state,expected_error_type", [
    ("timed_out", "stopped_failed", "failed", "product_reported_timed_out"),
    ("unknown", "stopped_unknown", "unknown", "product_reported_unknown_effect"),
    ("deadline_exhausted", "stopped_failed", "failed", "product_reported_deadline_exhausted"),
])
def test_product_reported_negative_state_stops_dispatch_without_replay(
    tmp_path, product_state, expected_status, expected_receipt_state, expected_error_type
):
    case = _prepare_case(tmp_path, authorized=True)
    ref = "research_project:offline-fixture"
    run_response = {"project_ref": ref, "state": product_state}
    run_stdout = json.dumps(run_response, ensure_ascii=False)
    run_stderr = f"product diagnostic before stop: {product_state} 中文\n"
    runner = FakeRunner([
        _ok({"ref": "source:offline-fixture"}),
        _ok({"project_ref": ref, "state": "ready"}),
        _ok({"project_ref": ref, "state": "ready"}),
        adapter.RunnerResult(0, run_stdout, run_stderr),
        _ok({"project_ref": ref, "state": "stopped", "snapshot_ref": "snap:must-not-run"}),
        _ok({"snapshot_ref": "snap:must-not-run"}),
    ])
    state = adapter.execute(
        case["out"], taskpack_path=case["taskpack_path"], runner=runner
    )
    assert len(runner.calls) == 4
    assert "research-project-run" in " ".join(runner.calls[3][0])
    assert state["adapter_status"] == expected_status
    assert state["blocking_operation_ids"] == ["original_fulltext.run"]
    raw = state["denominator"]["tracks"][0]
    assert raw["dispatch_state"] == expected_receipt_state
    assert raw["product_reported_state"] == product_state
    assert raw["operations"]["original_fulltext.run"] == expected_receipt_state
    assert raw["operations"]["original_fulltext.show_after_run"] == "missing"
    assert raw["operations"]["original_fulltext.snapshot"] == "missing"
    assert state["denominator"]["retained_tracks"] == 2
    assert state["denominator"]["excluded_tracks"] == 0

    receipts = [
        json.loads(line)
        for line in (case["out"] / "receipts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(receipts) == 4
    run_receipt = receipts[3]
    assert run_receipt["state"] == expected_receipt_state
    assert run_receipt["response"] == run_response
    assert run_receipt["error_type"] == expected_error_type
    for stream, expected_text in (("stdout", run_stdout), ("stderr", run_stderr)):
        binding = run_receipt["diagnostics"][stream]
        data = (case["out"] / binding["path"]).read_bytes()
        assert data == expected_text.encode("utf-8")
        assert binding["sha256"] == hashlib.sha256(data).hexdigest()
        assert binding["bytes"] == len(data)
        assert run_receipt[f"{stream}_sha256"] == binding["sha256"]

    ledger_before = json.loads(
        (case["out"] / "budget-ledger.json").read_text(encoding="utf-8")
    )
    run_before = json.loads((case["out"] / "run.json").read_text(encoding="utf-8"))
    calls = []
    resumed = adapter.execute(
        case["out"], taskpack_path=case["taskpack_path"], runner=_forbidden_runner(calls)
    )
    assert calls == []
    assert resumed["adapter_status"] == expected_status
    assert resumed["receipt_count"] == 4
    assert resumed["blocking_operation_ids"] == ["original_fulltext.run"]
    assert resumed["denominator"]["retained_tracks"] == 2
    assert resumed["denominator"]["excluded_tracks"] == 0
    ledger_after = json.loads(
        (case["out"] / "budget-ledger.json").read_text(encoding="utf-8")
    )
    run_after = json.loads((case["out"] / "run.json").read_text(encoding="utf-8"))
    assert ledger_after["limits"] == ledger_before["limits"]
    assert ledger_after["reset_on_restart"] is False
    assert ledger_after["resume_count"] == ledger_before["resume_count"] + 1
    assert run_after["budget"] == run_before["budget"]
