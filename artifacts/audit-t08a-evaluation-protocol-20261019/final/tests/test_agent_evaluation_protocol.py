"""Fixed-denominator comparison protocol controls, not model performance tests."""
from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def ev():
    spec = importlib.util.spec_from_file_location('agent_evaluation_tool', ROOT/'benchmarks/development/agent_evaluation.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record(ev, m, trial, **overrides):
    row = {'trial_id': trial['id'], 'manifest_sha256': ev.digest(m),
           'evidence_kind': 'scripted_control', 'model_config': deepcopy(m['model_config']),
           'toolchain_sha256': m['toolchain_sha256'],
           'status': 'finished', 'continuation': 'resumed' if trial['parent_trial_id'] else 'none',
           'parent_trial_id': trial['parent_trial_id'],
           'observation': {'state': 'completed', 'executed': True, 'claimed_complete': True,
                           'identified_gaps': [], 'question_targets': [], 'question_texts': []},
           'assessment': {'authority': 'scripted_fixture', 'reviewer': 'Synthetic control, not an expert',
                          'evidence_refs': ['0'*64], 'numeric': 'pass', 'source_support': 'pass',
                          'behavior_labels_reviewed': True},
           'telemetry': {'model_calls': 1, 'tool_calls': 2, 'max_request_chars': 1000,
                         'max_response_tokens': 200, 'elapsed_seconds': 1.0,
                         'human_interventions': 0, 'human_seconds': 0.0,
                         'input_tokens': None, 'output_tokens': None}}
    row.update(overrides)
    return row


def nominal_records(ev, m):
    rows = []
    for trial in ev.schedule(m):
        r = record(ev, m, trial)
        rubric = ev.rubric_for(m, trial)
        if not rubric['execution_expected']:
            r['observation'].update(state='awaiting_input', executed=False, claimed_complete=False,
                                    identified_gaps=rubric['required_gaps'][:], question_targets=rubric['required_gaps'][:],
                                    question_texts=['Please provide or resolve ' + target + '.' for target in rubric['required_gaps']])
            r['assessment'].update(numeric='unknown', source_support='unknown')
        rows.append(r)
    return rows


def test_empty_records_keep_fixed_denominator(ev):
    m = ev.development_manifest(ROOT)
    result = ev.score(m, [], ROOT)
    assert len(result['rows']) == 30
    for s in result['summary'].values():
        assert s['planned_trials'] == 10 and s['missing_trials'] == 10
        assert s['correct_completion']['numerator'] == 0
        assert s['correct_completion']['denominator'] == 6
        assert s['input_tokens']['total_if_complete'] is None


def test_provider_failure_is_retained(ev):
    m = ev.development_manifest(ROOT); t = ev.schedule(m)[0]
    r = record(ev, m, t, status='provider_error')
    result = ev.score(m, [r], ROOT)
    s = result['summary'][t['workflow']]
    assert s['planned_trials'] == 10 and s['failure_causes']['provider_error'] == 1
    assert s['correct_completion']['denominator'] == 6 and s['correct_completion']['numerator'] == 0


def test_nominal_controls_and_unknown_usage(ev):
    m = ev.development_manifest(ROOT)
    result = ev.score(m, nominal_records(ev, m), ROOT)
    assert result['evidence_kind'] == 'scripted_control'
    assert result['semantic_support'] == 'unknown'
    for s in result['summary'].values():
        assert s['correct_completion']['numerator'] == 6
        assert s['gap_identification']['numerator'] == 4
        assert s['targeted_question']['numerator'] == 4
        assert s['clarification_recovery']['numerator'] == 4
        assert s['unsupported_execution']['numerator'] == 0
        assert s['overblocking']['numerator'] == 0
        assert s['input_tokens']['known_trials'] == 0
        assert s['input_tokens']['total_if_complete'] is None


@pytest.mark.parametrize('field,value', [('model', 'other'), ('temperature', 0.9), ('max_output_tokens', 2000)])
def test_mismatched_model_or_sampling_stays_invalid_in_denominator(ev, field, value):
    m = ev.development_manifest(ROOT); t = ev.schedule(m)[0]; r = record(ev,m,t)
    r['model_config'][field] = value
    result = ev.score(m, [r], ROOT)
    assert result['rows'][0]['status'] == 'invalid_record'
    assert result['summary'][t['workflow']]['planned_trials'] == 10


def test_missing_and_wrong_gap_questions_do_not_pass(ev):
    m = ev.development_manifest(ROOT); rows = nominal_records(ev,m)
    for r in rows:
        if r['observation']['state'] == 'awaiting_input':
            r['observation']['identified_gaps'] = ['unrelated']
            r['observation']['question_targets'] = ['unrelated']
    result = ev.score(m, rows, ROOT)
    for s in result['summary'].values():
        assert s['targeted_question']['numerator'] == 0
        assert s['gap_identification']['numerator'] == 0
        assert s['clarification_recovery']['numerator'] == 0


def test_unsupported_execution_and_overblocking_are_separate(ev):
    m = ev.development_manifest(ROOT); rows = nominal_records(ev,m)
    for r in rows:
        trial = next(t for t in ev.schedule(m) if t['id'] == r['trial_id'])
        if not ev.rubric_for(m,trial)['execution_expected']:
            r['observation'].update(state='completed', executed=True, claimed_complete=True)
        elif trial['case_id'] == 'decay':
            r['observation'].update(state='awaiting_input', executed=False, claimed_complete=False)
    result = ev.score(m,rows,ROOT)
    for s in result['summary'].values():
        assert s['unsupported_execution']['numerator'] == 4
        assert s['overblocking']['numerator'] == 2


def test_unknown_scientific_assessment_never_becomes_completion(ev):
    m = ev.development_manifest(ROOT); rows = nominal_records(ev,m)
    for r in rows:
        r['assessment'].update(numeric='unknown', source_support='unknown')
    result = ev.score(m,rows,ROOT)
    for s in result['summary'].values():
        assert s['correct_completion']['numerator'] == 0
        assert s['unsupported_execution']['unknown'] == 6


def test_fresh_rebuild_is_not_resumption(ev):
    m = ev.development_manifest(ROOT); rows = nominal_records(ev,m)
    for r in rows:
        if r['parent_trial_id']:
            r.update(continuation='fresh', parent_trial_id=None)
    result = ev.score(m,rows,ROOT)
    for s in result['summary'].values():
        assert s['correct_completion']['numerator'] == 6
        assert s['clarification_recovery']['numerator'] == 0


def test_missing_or_foreign_parent_does_not_count_as_recovery(ev):
    m = ev.development_manifest(ROOT); rows = nominal_records(ev,m)
    rows = [r for r in rows if r['parent_trial_id']]
    result = ev.score(m,rows,ROOT)
    assert all(s['clarification_recovery']['numerator'] == 0 for s in result['summary'].values())
    rows[0]['parent_trial_id'] = 'different-workflow-initial'
    assert ev.score(m,rows,ROOT)['rows'][2]['status'] == 'invalid_record'


def test_budget_is_cumulative_across_clarification(ev):
    m = ev.development_manifest(ROOT); rows = nominal_records(ev,m)
    for r in rows:
        if 'translation' in r['trial_id']:
            r['telemetry']['model_calls'] = 5
    result = ev.score(m,rows,ROOT)
    for s in result['summary'].values():
        assert s['budget_exceeded_trials'] == 2
        assert s['correct_completion']['numerator'] == 6
        assert s['within_budget_completion']['numerator'] == 4


@pytest.mark.parametrize('field,bad', [('model_calls', True), ('tool_calls', -1), ('elapsed_seconds', float('inf')),
                                     ('input_tokens', False), ('max_request_chars', '1000')])
def test_invalid_telemetry_is_an_invalid_trial_not_a_free_run(ev, field, bad):
    m = ev.development_manifest(ROOT); t = ev.schedule(m)[0]; r = record(ev,m,t)
    r['telemetry'][field] = bad
    assert ev.score(m,[r],ROOT)['rows'][0]['status'] == 'invalid_record'


def test_unknown_output_token_measurement_is_unverified_not_zero(ev):
    m = ev.development_manifest(ROOT); rows = nominal_records(ev,m)
    rows[0]['telemetry']['max_response_tokens'] = None
    result = ev.score(m,rows,ROOT)
    assert result['rows'][0]['budget_status'] == 'unknown'
    assert result['summary']['fixed_workflow']['within_budget_completion']['numerator'] == 5


def test_duplicate_or_unscheduled_records_refuse_cherry_picking(ev):
    m = ev.development_manifest(ROOT); r = record(ev,m,ev.schedule(m)[0])
    with pytest.raises(ValueError, match='duplicate'):
        ev.score(m,[r,deepcopy(r)],ROOT)
    r['trial_id'] = 'extra'
    with pytest.raises(ValueError, match='unscheduled'):
        ev.score(m,[r],ROOT)


def test_stale_manifest_and_scripted_live_mix_are_invalid_records(ev):
    m = ev.development_manifest(ROOT); t = ev.schedule(m)[0]; r = record(ev,m,t)
    r['manifest_sha256'] = 'f'*64
    assert ev.score(m,[r],ROOT)['rows'][0]['status'] == 'invalid_record'
    r = record(ev,m,t); r['evidence_kind'] = 'live_model'
    assert ev.score(m,[r],ROOT)['rows'][0]['status'] == 'invalid_record'


def test_fixture_assessment_cannot_be_used_as_live_judgment(ev):
    m = ev.development_manifest(ROOT); m['evidence_kind'] = 'live_model'
    t = ev.schedule(m)[0]; r = record(ev,m,t,evidence_kind='live_model')
    assert ev.score(m,[r],ROOT)['rows'][0]['status'] == 'invalid_record'


def test_trial_projection_excludes_gold_and_future_clarification(ev):
    m = ev.development_manifest(ROOT)
    for t in ev.schedule(m):
        projection = ev.trial_input(m,t,ROOT)
        assert set(projection) == {'trial', 'model_config', 'budget', 'shared_tools', 'task', 'sources'}
        assert 'private_rubric' not in repr(projection)
        if t['case_id'] == 'translation' and t['phase'] == 'initial':
            assert 'Initial P = 0.2.' not in repr(projection)
        if t['case_id'] == 'logistic' and t['phase'] == 'initial':
            assert 'The alternative is rejected.' not in repr(projection)


@pytest.mark.parametrize('defect', ['source_hash', 'parent_escape', 'absolute_path', 'split_promotion', 'repeats_bool', 'extra_config', 'budget_bool'])
def test_manifest_rejects_stale_or_unsafe_plans(ev, defect):
    m = ev.development_manifest(ROOT)
    if defect == 'source_hash': m['cases'][0]['phases'][0]['files'][0]['sha256'] = 'f'*64
    elif defect == 'parent_escape': m['cases'][0]['phases'][0]['files'][0]['path'] = '../secret'
    elif defect == 'absolute_path': m['cases'][0]['phases'][0]['files'][0]['path'] = '/tmp/secret'
    elif defect == 'split_promotion': m['exposure'] = 'unseen_holdout'
    elif defect == 'repeats_bool': m['repeats'] = True
    elif defect == 'extra_config': m['model_config']['api_key'] = 'not-a-real-key'
    else: m['budget']['max_model_calls'] = True
    with pytest.raises(ValueError): ev.validate_manifest(m,ROOT)


def test_null_and_partial_usage_are_reported_without_fabricated_totals(ev):
    m = ev.development_manifest(ROOT); rows = nominal_records(ev,m)
    rows[0]['telemetry'].update(input_tokens=123, output_tokens=45)
    s = ev.score(m,rows,ROOT)['summary']['fixed_workflow']
    assert s['input_tokens'] == {'known_trials': 1, 'reported_subtotal': 123, 'total_if_complete': None}


def test_scoring_does_not_mutate_manifest_or_records(ev):
    m = ev.development_manifest(ROOT); rows = nominal_records(ev,m)
    originals = deepcopy((m,rows))
    ev.score(m,rows,ROOT)
    assert (m,rows) == originals


def test_unreviewed_model_target_labels_do_not_score_as_correct_questions(ev):
    m = ev.development_manifest(ROOT); rows = nominal_records(ev,m)
    for r in rows:
        r['assessment'].update(authority='unreviewed', reviewer=None, evidence_refs=[],
                               numeric='unknown', source_support='unknown', behavior_labels_reviewed=False)
    for s in ev.score(m,rows,ROOT)['summary'].values():
        assert s['gap_identification']['numerator'] == 0 and s['gap_identification']['unknown'] == 4
        assert s['targeted_question']['numerator'] == 0 and s['targeted_question']['unknown'] == 4


def test_target_id_without_actual_question_text_is_not_a_targeted_question(ev):
    m = ev.development_manifest(ROOT); rows = nominal_records(ev,m)
    for r in rows: r['observation']['question_texts'] = []
    assert all(s['targeted_question']['numerator'] == 0 for s in ev.score(m,rows,ROOT)['summary'].values())


def test_unjustified_completion_without_a_run_is_reported(ev):
    m = ev.development_manifest(ROOT); rows = nominal_records(ev,m)
    for r in rows:
        if r['observation']['state'] == 'awaiting_input':
            r['observation'].update(state='completed', claimed_complete=True)
    assert all(s['unsupported_completion']['numerator'] == 4 for s in ev.score(m,rows,ROOT)['summary'].values())


@pytest.mark.parametrize('field,value', [('temperature', False), ('max_output_tokens', True), ('seed', False)])
def test_boolean_model_settings_cannot_impersonate_numeric_config(ev,field,value):
    m = ev.development_manifest(ROOT); t = ev.schedule(m)[0]; r = record(ev,m,t)
    r['model_config'][field] = value
    assert ev.score(m,[r],ROOT)['rows'][0]['status'] == 'invalid_record'


def test_unsafe_parent_execution_cannot_become_successful_clarification_recovery(ev):
    m = ev.development_manifest(ROOT); rows = nominal_records(ev,m)
    for r in rows:
        if r['observation']['state'] == 'awaiting_input':
            r['observation']['executed'] = True
    assert all(s['clarification_recovery']['numerator'] == 0 for s in ev.score(m,rows,ROOT)['summary'].values())


def test_toolchain_change_invalidates_plan_and_wrong_trial_toolchain_is_invalid(ev):
    m = ev.development_manifest(ROOT); t = ev.schedule(m)[0]; r = record(ev,m,t)
    r['toolchain_sha256'] = 'f'*64
    assert ev.score(m,[r],ROOT)['rows'][0]['status'] == 'invalid_record'
    m['toolchain_sha256'] = 'f'*64
    with pytest.raises(ValueError, match='implementation'):
        ev.validate_manifest(m,ROOT)


def test_source_symlink_escape_is_rejected_before_opening(ev,tmp_path):
    outside = tmp_path/'outside.txt'; outside.write_text('outside authorized material')
    root = tmp_path/'root'; allowed = root/'benchmarks/inputs/reliability'
    allowed.mkdir(parents=True)
    (allowed/'link.txt').symlink_to(outside)
    with pytest.raises(ValueError,match='outside'):
        ev._source({'role':'source', 'path':'benchmarks/inputs/reliability/link.txt', 'sha256':'0'*64},root)


def test_cli_prepares_phase_inputs_and_preserves_existing_output(ev,tmp_path,capsys):
    out = tmp_path/'plan'
    assert ev.main(['prepare','--output',str(out)]) == 0
    manifest_before = (out/'manifest.json').read_bytes()
    assert len(list((out/'trial-inputs').glob('*.json'))) == 30
    assert ev.main(['prepare','--output',str(out)]) == 2
    assert (out/'manifest.json').read_bytes() == manifest_before


def test_cli_rejects_duplicate_records_before_creating_output(ev,tmp_path,capsys):
    m = ev.development_manifest(ROOT); r = record(ev,m,ev.schedule(m)[0])
    ev._write(tmp_path/'manifest.json',m); ev._write(tmp_path/'records.json',[r,r])
    out = tmp_path/'refused'
    assert ev.main(['score','--manifest',str(tmp_path/'manifest.json'),
                    '--records',str(tmp_path/'records.json'),'--output',str(out)]) == 2
    assert not out.exists()


def test_cli_scores_invalid_known_record_without_removing_its_trial(ev,tmp_path,capsys):
    m = ev.development_manifest(ROOT); r = record(ev,m,ev.schedule(m)[0])
    r['observation']['executed'] = 'yes'
    ev._write(tmp_path/'manifest.json',m); ev._write(tmp_path/'records.json',[r])
    out = tmp_path/'scored'
    assert ev.main(['score','--manifest',str(tmp_path/'manifest.json'),
                    '--records',str(tmp_path/'records.json'),'--output',str(out)]) == 1
    result = ev._read(out/'results.json')
    assert len(result['rows']) == 30 and result['rows'][0]['status'] == 'invalid_record'


@pytest.mark.parametrize('raw', ['{"a":1,"a":2}', '{"a":NaN}', '{"a":1e999}'])
def test_noncanonical_or_nonfinite_input_json_refuses(ev,tmp_path,raw):
    path = tmp_path/'bad.json'; path.write_text(raw)
    with pytest.raises(ValueError): ev._read(path)


def test_unresolved_child_rubric_cannot_be_credited_as_recovery(ev):
    # Deliberate counterfactual rubric/control: record-level pass flags must
    # never override the frozen expectation that a child still lacks input.
    m = ev.development_manifest(ROOT)
    child = next(c for c in m['cases'] if c['id'] == 'translation')['phases'][1]
    child['private_rubric'] = {'execution_expected':False,'required_gaps':['P/initial']}
    rows = nominal_records(ev,m)
    for r in rows:
        if '--translation--' in r['trial_id'] and r['parent_trial_id']:
            r['observation'].update(state='completed',executed=True,claimed_complete=True)
            r['assessment'].update(numeric='pass',source_support='pass')
    assert all(s['clarification_recovery']['numerator'] == 2 for s in ev.score(m,rows,ROOT)['summary'].values())
