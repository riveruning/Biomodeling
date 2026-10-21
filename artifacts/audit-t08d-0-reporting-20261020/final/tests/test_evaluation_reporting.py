"""Adjudication-aware development reports, never model capability evidence."""
from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SAVED = ROOT/'artifacts/audit-t08c-http-transport-20261019/http-mock-execution'


@pytest.fixture
def reporting():
    spec = importlib.util.spec_from_file_location('evaluation_reporting_test',
        ROOT/'benchmarks/development/evaluation_reporting.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def saved(reporting):
    return reporting.v1._read(SAVED/'manifest.json'), reporting.v1._read(SAVED/'records.json')


def test_actual_saved_unreviewed_completions_stay_pending(reporting):
    m, records = saved(reporting)
    before = deepcopy((m, records))
    result = reporting.report(m, records, ROOT)
    assert result['reporting_protocol'] == 'agent_evaluation_report_v2'
    for summary in result['summary'].values():
        metric = summary['metrics']['correct_completion']
        assert metric['denominator'] == 6
        assert metric['counts']['pending_adjudication'] == 6
        assert metric['true'] == metric['false'] == 0
        assert metric['unknown'] == 6 and metric['rate'] is None
        assert metric['rate_bounds'] == {'lower': 0.0, 'upper': 1.0}
        assert summary['metrics']['targeted_question']['denominator'] == 4
        assert summary['metrics']['targeted_question']['counts']['pending_adjudication'] == 4
    assert (m, records) == before


def test_actual_legacy_scores_stay_exact(reporting):
    m, records = saved(reporting)
    old = reporting.v1._read(SAVED/'results.json')
    assert reporting.v1.score(m, records, ROOT) == old
    assert reporting.report(m, records, ROOT)['legacy_result_sha256'] == reporting.v1.digest(old)


def controlled(reporting):
    """Explicit synthetic adjudications, never real reviewer decisions."""
    m, records = saved(reporting)
    trials = {t['id']: t for t in reporting.v1.schedule(m)}
    for r in records:
        complete = r['observation']['state'] == 'completed'
        r['assessment'].update(authority='scripted_fixture', reviewer='Unit fixture, not a domain reviewer',
            evidence_refs=['0'*64], numeric='pass' if complete else 'unknown',
            source_support='pass' if complete else 'unknown', behavior_labels_reviewed=True)
        r['telemetry']['max_response_tokens'] = 200
        if not complete:
            # Synthetic target attribution, not an adjudication of saved text.
            gaps = reporting.v1.rubric_for(m, trials[r['trial_id']])['required_gaps']
            r['observation'].update(identified_gaps=gaps[:], question_targets=gaps[:])
    return m, records


def row(result, ident):
    return next(r for r in result['rows'] if r['id'] == ident)


def stamp(reporting, m, records):
    for r in records:
        r.update(manifest_sha256=reporting.v1.digest(m), evidence_kind=m['evidence_kind'])


@pytest.mark.parametrize('numeric', ['pass', 'fail', 'unknown'])
@pytest.mark.parametrize('source', ['pass', 'fail', 'unknown'])
def test_partial_judgments_false_dominates_unknown(reporting, numeric, source):
    m, records = controlled(reporting)
    r = records[0]
    r['assessment'].update(numeric=numeric, source_support=source)
    result = row(reporting.report(m, records, ROOT), r['trial_id'])
    expected = False if 'fail' in (numeric, source) else None if 'unknown' in (numeric, source) else True
    assert result['metrics']['correct_completion']['value'] is expected
    assert result['metrics']['unsupported_completion']['value'] is (None if expected is None else not expected)
    assert result['metrics']['unsupported_execution']['value'] is (
        None if source == 'unknown' else source == 'fail')


def test_fixtures_and_unreviewed_records_are_distinct(reporting):
    m, records = controlled(reporting)
    result = reporting.report(m, records, ROOT)
    for s in result['summary'].values():
        assert s['metrics']['correct_completion']['true'] == 6
        assert s['metrics']['correct_completion']['rate'] == 1.0
        assert s['metrics']['clarification_recovery']['true'] == 4
        assert s['assessment_coverage']['authorities'] == {'scripted_fixture': 10}
        assert s['assessment_coverage']['independent_review_authenticated'] is False
    assert result['semantic_support'] == 'unknown'
    assert result['evidence_kind'] == 'scripted_control'


@pytest.mark.parametrize('field,value', [('numeric', 'pass'), ('source_support', 'pass'),
                                       ('behavior_labels_reviewed', True)])
def test_unreviewed_cannot_award_itself_a_verdict(reporting, field, value):
    m, records = saved(reporting)
    records[0]['assessment'][field] = value
    item = reporting.report(m, records, ROOT)['rows'][0]
    assert item['status'] == 'invalid_record'
    assert item['metrics']['correct_completion']['state'] == 'invalid_record'
    assert item['metrics']['correct_completion']['value'] is None


def test_empty_records_are_missing_not_confirmed_failures(reporting):
    m, _ = saved(reporting)
    result = reporting.report(m, [], ROOT)
    for s in result['summary'].values():
        assert s['planned_trials'] == 10
        metric = s['metrics']['correct_completion']
        assert metric['counts']['missing_record'] == metric['unknown'] == 6
        assert metric['false'] == 0 and metric['rate'] is None
        assert metric['rate_bounds'] == {'lower': 0.0, 'upper': 1.0}
        assert s['telemetry']['input_tokens']['total_if_complete'] is None


@pytest.mark.parametrize('value', [True, float('nan'), -1, '1'])
def test_invalid_telemetry_stays_unknown_in_denominator(reporting, value):
    m, records = controlled(reporting)
    records[0]['telemetry']['model_calls'] = value
    result = reporting.report(m, records, ROOT)
    metric = result['summary']['fixed_workflow']['metrics']['correct_completion']
    assert metric['denominator'] == 6 and metric['counts']['invalid_record'] == 1
    assert metric['true'] == 5 and metric['rate'] is None
    assert metric['rate_bounds'] == {'lower': 5/6, 'upper': 1.0}


@pytest.mark.parametrize('status', ['provider_error', 'timeout', 'adapter_error', 'invalid_output', 'not_run'])
def test_operational_outcomes_do_not_become_scientific_failures(reporting, status):
    m, records = controlled(reporting)
    records[0]['status'] = status
    result = reporting.report(m, records, ROOT)
    item = result['rows'][0]['metrics']['correct_completion']
    assert item['value'] is False
    assert item['state'] == ('not_run' if status == 'not_run' else 'operational_failure')
    metric = result['summary']['fixed_workflow']['metrics']['correct_completion']
    assert metric['counts']['not_met'] == 0
    assert metric['false'] == 1 and metric['true'] == 5 and metric['unknown'] == 0
    assert metric['rate'] == 5/6
    assert metric['rate_bounds'] == {'lower': 5/6, 'upper': 5/6}


def test_operational_error_does_not_hide_unsupported_execution(reporting):
    m, records = controlled(reporting)
    r = next(r for r in records if r['trial_id'] == 'fixed_workflow--translation--0--initial')
    r['status'] = 'provider_error'
    r['observation'].update(state='completed', executed=True, claimed_complete=True)
    item = row(reporting.report(m, records, ROOT), r['trial_id'])
    assert item['metrics']['unsupported_execution']['value'] is True
    assert item['metrics']['unsupported_completion']['value'] is True
    assert item['metrics']['gap_identification']['state'] == 'operational_failure'


def test_no_response_usage_is_pending_budget_never_free(reporting):
    m, records = controlled(reporting)
    records[0]['telemetry']['max_response_tokens'] = None
    item = reporting.report(m, records, ROOT)['rows'][0]
    assert item['metrics']['correct_completion']['value'] is True
    assert item['metrics']['within_budget_completion']['state'] == 'pending_budget'
    assert item['metrics']['within_budget_completion']['value'] is None


def test_exceeded_budget_settles_even_pending_completion(reporting):
    m, records = saved(reporting)
    records[0]['telemetry']['model_calls'] = m['budget']['max_model_calls']+1
    item = reporting.report(m, records, ROOT)['rows'][0]
    assert item['metrics']['correct_completion']['value'] is None
    assert item['metrics']['within_budget_completion']['value'] is False


def test_child_cannot_reset_the_cumulative_budget(reporting):
    m, records = controlled(reporting)
    for r in records:
        if r['trial_id'] in ('domain_agent--translation--0--initial', 'domain_agent--translation--0--clarified'):
            r['telemetry']['model_calls'] = 6
    item = row(reporting.report(m, records, ROOT), 'domain_agent--translation--0--clarified')
    assert item['metrics']['correct_completion']['value'] is True
    assert item['metrics']['within_budget_completion']['value'] is False
    assert item['budget_status'] == 'exceeded'


@pytest.mark.parametrize('change,expected', [('missing', None), ('invalid', None), ('unreviewed', None),
                                          ('wrong_targets', False), ('operational', False)])
def test_recovery_requires_parent_evidence(reporting, change, expected):
    m, records = controlled(reporting)
    parent_id = 'fixed_workflow--translation--0--initial'
    child_id = 'fixed_workflow--translation--0--clarified'
    parent = next(r for r in records if r['trial_id'] == parent_id)
    if change == 'missing':
        records.remove(parent)
    elif change == 'invalid':
        parent['observation']['executed'] = 'no'
    elif change == 'unreviewed':
        parent['assessment'].update(authority='unreviewed', behavior_labels_reviewed=False)
    elif change == 'wrong_targets':
        parent['observation']['question_targets'] = ['unrelated']
    else:
        parent['status'] = 'timeout'
    child = row(reporting.report(m, records, ROOT), child_id)
    assert child['metrics']['correct_completion']['value'] is True
    assert child['metrics']['clarification_recovery']['value'] is expected


def test_fresh_child_is_not_recovery_even_with_unknown_parent_labels(reporting):
    m, records = saved(reporting)
    child = next(r for r in records if r['parent_trial_id'])
    child.update(continuation='fresh', parent_trial_id=None)
    item = row(reporting.report(m, records, ROOT), child['trial_id'])
    assert item['metrics']['clarification_recovery']['value'] is False


def test_parent_without_gap_eligibility_cannot_earn_clarification_recovery(reporting):
    m, records = controlled(reporting)
    m['cases'][1]['phases'][0]['private_rubric'] = {'execution_expected': True, 'required_gaps': []}
    stamp(reporting, m, records)
    child = row(reporting.report(m, records, ROOT), 'domain_agent--translation--0--clarified')
    assert child['metrics']['clarification_recovery']['value'] is False


def test_observed_noncompletion_is_known_even_if_assessments_pending(reporting):
    m, records = saved(reporting)
    records[0]['observation'].update(state='awaiting_input', executed=False, claimed_complete=False)
    item = reporting.report(m, records, ROOT)['rows'][0]
    assert item['metrics']['correct_completion']['value'] is False
    assert item['metrics']['overblocking']['value'] is True
    assert item['metrics']['unsupported_execution']['value'] is False


def test_all_metric_counts_partition_eligibility_and_family_summaries(reporting):
    m, records = saved(reporting)
    result = reporting.report(m, records, ROOT)
    for w, s in result['summary'].items():
        assert list(result['by_family'][w].values()) == [s]
        for metric in s['metrics'].values():
            assert metric['true']+metric['false']+metric['unknown'] == metric['denominator']
            assert sum(metric['counts'].values()) == metric['denominator']
            assert metric['denominator']+metric['not_applicable'] == 10
        assert s['telemetry']['input_tokens']['total_if_complete'] is None


def test_changed_family_grouping_does_not_change_the_fixed_total(reporting):
    m, records = saved(reporting)
    m['cases'][0]['family'] = 'alternative-synthetic-group-for-unit-control'
    stamp(reporting, m, records)
    result = reporting.report(m, records, ROOT)
    for w, summary in result['summary'].items():
        assert len(result['by_family'][w]) == 2
        assert sum(s['planned_trials'] for s in result['by_family'][w].values()) == summary['planned_trials']
        for name, total in summary['metrics'].items():
            for field in ('denominator', 'true', 'false', 'unknown'):
                assert sum(s['metrics'][name][field] for s in result['by_family'][w].values()) == total[field]


def test_no_applicable_gap_metrics_have_null_rates(reporting):
    m, records = controlled(reporting)
    m['cases'] = [m['cases'][0]]
    selected = {t['id'] for t in reporting.v1.schedule(m)}
    records = [r for r in records if r['trial_id'] in selected]
    stamp(reporting, m, records)
    for s in reporting.report(m, records, ROOT)['summary'].values():
        for name in ('gap_identification', 'targeted_question', 'clarification_recovery'):
            metric = s['metrics'][name]
            assert metric['denominator'] == 0 and metric['rate'] is None
            assert metric['rate_bounds'] == {'lower': None, 'upper': None}


@pytest.mark.parametrize('change', ['duplicate', 'unscheduled', 'unseen_manifest', 'approved_manifest'])
def test_batch_and_manifest_guards_are_not_weakened(reporting, change):
    m, records = saved(reporting)
    if change == 'duplicate':
        records.append(deepcopy(records[0]))
    elif change == 'unscheduled':
        records[0]['trial_id'] = 'not-scheduled'
    elif change == 'unseen_manifest':
        m['exposure'] = 'unseen'
    else:
        m['annotation_review'] = 'approved'
    with pytest.raises(ValueError):
        reporting.report(m, records, ROOT)


def test_live_manifest_cannot_use_fixture_judgments(reporting):
    m, records = controlled(reporting)
    m['evidence_kind'] = 'live_model'
    stamp(reporting, m, records)
    result = reporting.report(m, records, ROOT)
    assert all(r['status'] == 'invalid_record' for r in result['rows'])
    assert all(s['metrics']['correct_completion']['true'] == 0 for s in result['summary'].values())


def test_declared_human_identity_does_not_authenticate_review(reporting):
    m, records = controlled(reporting)
    records[0]['assessment']['authority'] = 'human'
    result = reporting.report(m, records, ROOT)
    coverage = result['summary']['fixed_workflow']['assessment_coverage']
    assert coverage['authorities']['human'] == 1
    assert coverage['independent_review_authenticated'] is False
    assert result['audit']['independent_review'] == 'pending'
    assert result['status'] == 'development_report_pending_independent_review'


@pytest.mark.parametrize('raw', ['{"a":1,"a":2}', '[NaN]', '[1e999]'])
def test_cli_refuses_noncanonical_json_before_output(reporting, tmp_path, capsys, raw):
    path = tmp_path/'bad.json'
    path.write_text(raw)
    out = tmp_path/'never-created'
    assert reporting.main(['--manifest', str(SAVED/'manifest.json'), '--records', str(path),
                           '--output', str(out)]) == 2
    assert not out.exists()


def test_cli_keeps_legacy_report_and_provenance(reporting, tmp_path, capsys):
    out = tmp_path/'new'
    assert reporting.main(['--manifest', str(SAVED/'manifest.json'), '--records', str(SAVED/'records.json'),
                           '--output', str(out)]) == 0
    assert reporting.v1._read(out/'results-v1.json') == reporting.v1._read(SAVED/'results.json')
    report = reporting.v1._read(out/'results-v2.json')
    assert report['legacy_result_sha256'] == reporting.v1.digest(reporting.v1._read(out/'results-v1.json'))
    assert set(reporting.v1._read(out/'implementation.json')) == {
        'reporting_protocol', 'reporter_sha256', 'legacy_scorer_sha256'}
    old = {p.name: p.read_bytes() for p in out.iterdir()}
    assert reporting.main(['--manifest', str(SAVED/'manifest.json'), '--records', str(SAVED/'records.json'),
                           '--output', str(out)]) == 2
    assert old == {p.name: p.read_bytes() for p in out.iterdir()}


def test_cli_invalid_record_is_saved_but_bad_batch_creates_nothing(reporting, tmp_path, capsys):
    m, records = saved(reporting)
    records[0]['assessment']['source_support'] = 'pass'
    path = tmp_path/'records.json'
    reporting.v1._write(path, records)
    out = tmp_path/'invalid'
    assert reporting.main(['--manifest', str(SAVED/'manifest.json'), '--records', str(path),
                           '--output', str(out)]) == 1
    assert reporting.v1._read(out/'results-v2.json')['rows'][0]['status'] == 'invalid_record'
    reporting.v1._write(path, [records[0], records[0]])
    refused = tmp_path/'refused'
    assert reporting.main(['--manifest', str(SAVED/'manifest.json'), '--records', str(path),
                           '--output', str(refused)]) == 2
    assert not refused.exists()
