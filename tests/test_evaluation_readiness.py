"""Readiness inventory integrity and legacy scorer composition, no live calls."""
from collections import Counter
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def audit():
    spec = importlib.util.spec_from_file_location('evaluation_readiness_test', ROOT / 'benchmarks/development/evaluation_readiness.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def built(audit):
    return audit.build(ROOT)


def copied_root(tmp_path, packet):
    root = tmp_path / 'project'
    for name in packet['files']:
        dest = root / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((ROOT / name).read_bytes())
    return root


def test_inventory_counts_keep_units_separate(audit, built):
    packet, manifest, retrieval, report = built
    assert Counter(c['kind'] for c in packet['cards']) == dict(retrieval_control=18, staged_ode_task=3,
        publisher_figure_diagnostic=1, lp_numeric_control=1, public_lp_diagnostic=1)
    assert packet['workflow_plan']['base_cases'] == 3
    assert packet['workflow_plan']['stage_variants'] == 5
    assert packet['workflow_plan']['planned_stage_records'] == 30
    assert packet['evidence']['formal_eligible_in_audited_subset'] == 0
    assert packet['out_of_scope']['independent_evalset'] == 'not_inspected'
    assert len(retrieval['cases']) == 18 and len(manifest['cases']) == 3


def test_no_reviews_or_fake_gold_are_created(built):
    packet = built[0]
    for card in packet['cards']:
        assert card['review']['status'] == 'pending_independent'
        assert card['review']['reviewer'] is card['review']['reviewed_at'] is None
        assert all(v is None for v in card['review']['checks'].values())
        assert card['usage'] == 'development_only'
    agent = next(c for c in packet['cards'] if c['id'] == 'agent--decay')
    assert agent['private_rubric']['numeric_reference']['independent_review'] == 'pending'
    assert packet['evidence']['live_model_calls_this_preparation'] == 0


def test_empty_records_preserve_all_existing_denominators(built):
    packet, manifest, _, report = built
    for workflow, summary in report['summary'].items():
        assert summary['planned_trials'] == 10
        correct = summary['metrics']['correct_completion']
        assert correct['denominator'] == 6 and correct['unknown'] == 6
        assert correct['rate'] is None and correct['true'] == 0
        assert summary['metrics']['targeted_question']['denominator'] == 4
        assert summary['metrics']['clarification_recovery']['denominator'] == 4
        assert packet['workflow_plan']['applicable_denominators_per_workflow'][workflow]['unsupported_execution'] == 10


def test_existing_pre_review_candidate_not_silently_adopted(built):
    card = next(c for c in built[0]['cards'] if c['id'] == 'retrieval--clarification_scope')
    assert card['private_rubric']['expectation'] == 'conflicting'
    assert any('unadopted AI revision' in note for note in card['limits'])


def test_reference_types_and_unknown_initials_remain_explicit(built):
    packet = built[0]
    goodwin = next(c for c in packet['cards'] if c['id'] == 'goodwin--figure2')
    assert goodwin['private_rubric']['original_initials'] == 'unknown'
    assert goodwin['private_rubric']['pointwise'] == 'not_run'
    assert 'SciPy' in goodwin['private_rubric']['reference_type']
    textbook = next(c for c in packet['cards'] if c['id'] == 'fba--textbook')
    assert textbook['private_rubric']['reference']['objective'] == pytest.approx(.8739215069684307)


@pytest.mark.parametrize('mutation', ['review', 'split', 'exposure', 'rubric', 'family', 'card_count', 'files', 'plan', 'formal_count'])
def test_changed_packet_never_upgrades_or_silently_replans(audit, built, mutation):
    packet = deepcopy(built[0]); card = packet['cards'][0]
    if mutation == 'review': card['review']['status'] = 'approved'
    elif mutation == 'split': card['split'] = 'holdout'
    elif mutation == 'exposure': card['exposure'] = 'unseen'
    elif mutation == 'rubric': card['private_rubric']['expectation'] = 'different'
    elif mutation == 'family': card['family'] = 'invented-independent-paper'
    elif mutation == 'card_count': packet['cards'].pop()
    elif mutation == 'files': packet['files']['../../biomodel_agent_evalset/never-read'] = {'sha256': '0' * 64, 'bytes': 1}
    elif mutation == 'plan': packet['workflow_plan']['planned_stage_records'] = 1
    else: packet['evidence']['formal_eligible_in_audited_subset'] = 24
    with pytest.raises(ValueError, match='stale or altered'): audit.check(packet)


def test_valid_packet_still_refuses_formal_evaluation(audit, built):
    assert audit.check(built[0])['status'] == 'development_preparation_valid'
    with pytest.raises(ValueError, match='formal evaluation refused'): audit.check(built[0], require_formal=True)


@pytest.mark.parametrize('name', [
    'benchmarks/inputs/retrieval-development/cases.json',
    'benchmarks/inputs/reliability/translation/initial/source.txt',
    'benchmarks/inputs/reliability/translation/clarified/source.txt',
    'examples/goodwin/reference.json',
    'benchmarks/development/agent_evaluation.py',
])
def test_stale_material_and_scorer_hashes_invalidate_packet(audit, built, tmp_path, name):
    root = copied_root(tmp_path, built[0])
    target = root / name
    target.write_bytes(target.read_bytes() + b'\n')
    with pytest.raises(ValueError): audit.check(built[0], root)


@pytest.mark.parametrize('name', ['tests/fixtures/fba/textbook.xml', 'examples/goodwin/figure2-reference/publisher-figure-2.png'])
def test_public_reference_receipts_reject_changed_bytes(audit, built, tmp_path, name):
    root = copied_root(tmp_path, built[0]); target = root / name
    target.write_bytes(target.read_bytes() + b'changed')
    with pytest.raises(ValueError, match='changed|hash differs'): audit.build(root)


def test_symlink_rejected_before_read_even_if_pointing_inside_scope(audit, built, tmp_path):
    root = copied_root(tmp_path, built[0]); name = 'benchmarks/inputs/reliability/translation/initial/source.txt'
    target = root / name; target.unlink(); target.symlink_to(root / 'benchmarks/inputs/reliability/decay/initial/source.txt')
    with pytest.raises(ValueError, match='symlink'): audit.build(root)


def test_invalid_path_never_opened(audit, tmp_path):
    for name in ('../outside', '/absolute/path'):
        with pytest.raises(ValueError, match='project-relative'): audit.read_file(tmp_path, name)


def test_invalid_material_does_not_create_output(audit, built, tmp_path):
    root = copied_root(tmp_path, built[0]); (root / 'examples/goodwin/model.xml').write_text('changed')
    output = tmp_path / 'output'
    with pytest.raises(ValueError): audit.write(output, root)
    assert not output.exists()


def test_generated_private_and_stage_projection_boundaries(audit, tmp_path):
    output = tmp_path / 'pack'
    result = audit.write(output)
    assert result['cards'] == 24
    private = json.loads((output / 'private/readiness-packet.json').read_text())
    forms = json.loads((output / 'private/review-forms.json').read_text())
    assert forms['packet_sha256'] == audit.digest(private)
    assert len(forms['forms']) == 24
    for form, card in zip(forms['forms'], private['cards']):
        assert form['content_sha256'] == audit.digest(card)
        assert form['reviewer'] is None
    paths = list((output / 'stage-projections').glob('*.json'))
    assert len(paths) == 30
    for path in paths:
        text = path.read_text(); projection = json.loads(text)
        assert 'private_rubric' not in text and 'required_gaps' not in text
        assert set(projection) == {'trial', 'model_config', 'budget', 'shared_tools', 'task', 'sources'}
    initial = json.loads((output / 'stage-projections/domain_agent--translation--0--initial.json').read_text())
    future = json.loads((output / 'stage-projections/domain_agent--translation--0--clarified.json').read_text())
    assert initial['sources'][0]['text'] != future['sources'][0]['text']
    with pytest.raises(FileExistsError): audit.write(output)


def test_cli_checks_development_but_refuses_formal(audit, built, tmp_path, capsys):
    path = tmp_path / 'packet.json'; path.write_text(json.dumps(built[0]))
    assert audit.main(['check', '--packet', str(path)]) == 0
    assert audit.main(['check', '--packet', str(path), '--require-formal']) == 2
    assert 'formal evaluation refused' in capsys.readouterr().out


@pytest.mark.parametrize('case', ['decay', 'translation', 'logistic'])
def test_closed_forms_against_independently_coded_dop853(audit, case):
    import numpy as np
    from scipy.integrate import solve_ivp
    reference = audit.analytic_reference(case)
    if case == 'decay':
        rhs = lambda t, y: [-.4*y[0]]
        initial = [2.]; names = ['A']
    elif case == 'translation':
        rhs = lambda t, y: [.8-.4*y[0], .6*y[0]-.2*y[1]]
        initial = [1., .2]; names = ['M', 'P']
    else:
        rhs = lambda t, y: [.8*y[0]*(1-y[0]/2)]
        initial = [.2]; names = ['X']
    times = reference['series']['times']
    solution = solve_ivp(rhs, (times[0], times[-1]), initial, t_eval=times, method='DOP853', rtol=1e-11, atol=1e-13)
    assert solution.success
    actual = dict(times=times, values={name: solution.y[i].tolist() for i, name in enumerate(names)}, time_unit='minute')
    result = audit.numeric_diagnostic(case, reference['phase'], actual)
    assert result['comparison']['status'] == 'pass'
    assert result['comparison']['max_scaled_error'] < 1e-9
    assert result['formal_task_score'] == 'not_assigned' and result['source_support'] == 'unknown'
    source = (ROOT / reference['source_spans'][0]['path']).read_text()
    for span in reference['source_spans']:
        assert source[span['start']:span['end']] == span['quote']
    for name, value in zip(names, initial):
        assert reference['series']['values'][name][0] == pytest.approx(value)


@pytest.mark.parametrize('case', ['translation', 'logistic'])
def test_numeric_check_cannot_grade_unresolved_initial_stage(audit, case):
    ref = audit.analytic_reference(case)
    with pytest.raises(ValueError, match='unresolved'): audit.numeric_diagnostic(case, 'initial', ref['series'])


@pytest.mark.parametrize('change', ['wrong_values', 'time_unit', 'grid', 'columns', 'nan'])
def test_numeric_adverse_controls(audit, change):
    ref = audit.analytic_reference('decay'); actual = deepcopy(ref['series'])
    if change == 'wrong_values':
        actual['values']['A'] = [0.] * len(actual['times'])
        result = audit.numeric_diagnostic('decay', 'initial', actual)
        assert result['comparison']['status'] == 'fail'; return
    if change == 'time_unit': actual['time_unit'] = 'second'
    elif change == 'grid': actual['times'][1] += .01
    elif change == 'columns': actual['values']['extra'] = actual['values']['A'][:]
    else: actual['values']['A'][0] = float('nan')
    with pytest.raises(ValueError): audit.numeric_diagnostic('decay', 'initial', actual)


def test_numeric_cli_never_assigns_formal_score(audit, tmp_path, capsys):
    ref = audit.analytic_reference('decay'); file = tmp_path / 'series.json'
    file.write_text(json.dumps(ref['series']))
    args = ['numeric-check', '--case', 'decay', '--phase', 'initial', '--trajectory', str(file)]
    assert audit.main(args) == 0
    assert 'not_assigned' in capsys.readouterr().out
    ref['series']['values']['A'] = [0.] * len(ref['series']['times'])
    file.write_text(json.dumps(ref['series']))
    assert audit.main(args) == 1


@pytest.mark.parametrize('change', ['initial_time', 'override', 'source_clock'])
def test_closed_form_refuses_changed_experiment_semantics(audit, built, tmp_path, change):
    root = copied_root(tmp_path, built[0])
    file = root / 'benchmarks/inputs/reliability/decay/initial/experiment.json'
    exp = json.loads(file.read_text())
    if change == 'initial_time': exp['times'] = [x+1 for x in exp['times']]
    elif change == 'override': exp['parameter_overrides'] = {'k': .8}
    else: exp['time_unit'] = 'second'
    file.write_text(json.dumps(exp))
    with pytest.raises(ValueError, match='closed-form controls'): audit.analytic_reference('decay', root)
