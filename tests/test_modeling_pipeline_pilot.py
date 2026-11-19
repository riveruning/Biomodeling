"""Offline audit tests for the fixed four-case modeling pipeline pilot.

No network access and no provider calls: live mode is refused without
BIOMODEL_API_KEY, httpx.Client.post is patched to fail loudly during offline
preparation, and every scored fixture below is a synthetic offline object.
"""
import argparse
import copy
import importlib.util
import json
import math
from pathlib import Path

import httpx
import pytest

_spec = importlib.util.spec_from_file_location(
    'run_modeling_pipeline_pilot',
    Path(__file__).parents[1] / 'benchmarks/development/run_modeling_pipeline_pilot.py')
pilot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pilot)

SECRET = 'synthetic-not-a-real-credential'


@pytest.fixture()
def prepared(tmp_path, monkeypatch):
    monkeypatch.delenv(pilot.KEY_ENV, raising=False)
    monkeypatch.setattr(httpx.Client, 'post',
                        lambda *a, **k: pytest.fail('offline preparation made a request'))
    out = tmp_path / 'pilot'
    manifest = pilot.prepare(out)
    return out, manifest


def live_args(out):
    manifest = pilot.verify_prepared(out)
    return argparse.Namespace(
        output=out, run_live=True,
        model=manifest['provider']['model'],
        planner_max_calls=manifest['budgets']['planner_max_calls'],
        max_iterations=manifest['budgets']['max_iterations'],
        max_native_calls=manifest['budgets']['max_native_calls'],
        native_calls_per_iteration=manifest['budgets']['native_calls_per_iteration'],
        max_literature_requests=manifest['budgets']['max_literature_requests'],
        context_max_chars=manifest['budgets']['context_max_chars'],
        provider_timeout_s=manifest['provider']['timeout_s'],
        max_tokens=manifest['provider']['max_tokens'])


def test_prepare_is_offline_freezes_four_cases_and_hashes(prepared):
    out, manifest = prepared
    assert [case['id'] for case in manifest['definitions']] == list(pilot.CASE_IDS)
    assert (out / 'manifest.sha256').read_text().strip() == pilot.sha256_file(out / 'manifest.json')
    assert manifest['implementation_sha256'] == pilot.recovery.implementation_fingerprint()
    assert manifest['runner_sha256'] == pilot.sha256_file(pilot.RUNNER_PATH)
    assert pilot.sha256_file(out / 'runner.py') == manifest['runner_sha256']
    for entry in manifest['materials'].values():
        assert pilot.sha256_file(out / entry['path']) == entry['sha256']
    assert pilot.verify_prepared(out) == manifest
    assert manifest['provider']['model'] in pilot.ALLOWED_MODELS
    assert manifest['provider']['reasoning_effort'] == 'max'
    assert manifest['provider']['base_url'] == pilot.BASE_URL
    assert manifest['provider']['api_key_env'] == pilot.KEY_ENV
    assert manifest['provider']['json_mode'] is True
    # events record the offline freeze only
    events = (out / 'events.jsonl').read_text(encoding='utf-8').strip().splitlines()
    assert json.loads(events[0])['event'] == 'frozen'


def test_existing_output_and_started_run_are_never_overwritten(tmp_path, monkeypatch, prepared):
    out, manifest = prepared
    monkeypatch.setattr(httpx.Client, 'post',
                        lambda *a, **k: pytest.fail('offline preparation made a request'))
    with pytest.raises(FileExistsError):
        pilot.prepare(out)
    monkeypatch.delenv(pilot.KEY_ENV, raising=False)
    with pytest.raises(ValueError, match='BIOMODEL_API_KEY'):
        pilot.run_live(live_args(out), out)
    assert not (out / 'live-started.json').exists()
    (out / 'live-started.json').write_text('{}', encoding='utf-8')
    monkeypatch.setenv(pilot.KEY_ENV, SECRET)
    with pytest.raises(FileExistsError):
        pilot.run_live(live_args(out), out)


def test_manifest_hash_binding_rejects_edits(prepared):
    out, manifest = prepared
    data = json.loads((out / 'manifest.json').read_text(encoding='utf-8'))
    data['budgets']['planner_max_calls'] = 99
    (out / 'manifest.json').write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    with pytest.raises(ValueError, match='manifest'):
        pilot.verify_prepared(out)


def test_implementation_or_runner_change_is_rejected(prepared, monkeypatch):
    out, manifest = prepared
    monkeypatch.setattr(pilot.recovery, 'implementation_fingerprint', lambda: '0' * 64)
    with pytest.raises(ValueError, match='implementation'):
        pilot.verify_prepared(out)
    monkeypatch.undo()
    manifest['runner_sha256'] = 'f' * 64
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    (out / 'manifest.sha256').write_text(pilot.sha256_file(out / 'manifest.json') + '\n', encoding='utf-8')
    with pytest.raises(ValueError, match='runner'):
        pilot.verify_prepared(out)


def test_pde_models_references_and_axes_are_contract_valid(prepared):
    out, manifest = prepared
    pde_cases = [case for case in manifest['definitions'] if case['kind'] == 'pde']
    assert len(pde_cases) == 2
    for case in pde_cases:
        # The registered reference must declare its family, or register_reference
        # would parse the PDE protocol as an ODE series and fail.
        assert case['reference']['register']['model_type'] == 'pde'
        pilot.pde_system.validate(case['model'])
        spec = case['reference']['register']['reference']
        pilot.typed_references.validate(spec)
        pilot.typed_references.compatible(spec, case['model'], case['settings'])
        # Exact float equality between the frozen reference axes and the axes
        # the backend recomputes from the model at run time.
        axes, _ = pilot.pde_system.grid(case['model'])
        for name, values in spec['axes'].items():
            assert axes[name].tolist() == values
        assert all(scale > 0 for scale in spec['scales'])
        assert len(spec['scales']) == len(spec['field_ids'])
        budget = case['threshold_budget']
        assert budget['threshold'] == spec['threshold']
        assert budget['fixed_before_any_call'] is True
        assert 'spatial' in budget and 'time' in budget and budget['margin']
    fourier = next(case for case in pde_cases if case['id'] == 'pde_fourier_decay')
    assert fourier['model']['boundary'] == 'periodic' and fourier['model']['grid']['shape'] == [96]
    logistic = next(case for case in pde_cases if case['id'] == 'pde_logistic_2d')
    assert logistic['model']['boundary'] == 'no_flux'
    assert logistic['model']['grid']['shape'] == [14, 9]  # non-square
    assert all(field['diffusion'] == 0.0 for field in logistic['model']['fields'])
    assert all('logistic' in field['reaction'] or '*' in field['reaction']
               for field in logistic['model']['fields'])


def test_ode_reference_grid_and_missing_rate_control(prepared):
    out, manifest = prepared
    by_id = {case['id']: case for case in manifest['definitions']}
    case = by_id['production_loss']
    register = case['reference']['register']
    ref = pilot.from_dict(pilot.SimulationResult, register['reference'])
    pilot.validation.validate_reference(ref)
    pilot.validation.validate_scales(register['scales'], ref.values)
    times = register['reference']['times']
    assert times == [i * 0.5 for i in range(21)]
    assert times[0] == 0.0 and times[-1] == 10.0
    values = register['reference']['values']['p']
    assert values[0] == 1.0
    assert abs(values[-1] - (2.0 - math.exp(-3.0))) < 1e-12
    assert register['scales'] == {'p': 1.0} and register['threshold'] == 0.001
    missing = by_id['missing_rate']
    assert missing['reference'] is None and missing['model_files'] == []
    assert missing['experiment_grid'] is None and missing['threshold_budget'] is None
    assert missing['expected_behavior'] == 'clarification_without_computation'
    assert missing['numerically_evaluable'] is False


def test_single_bad_field_is_not_masked_by_averaging(prepared):
    out, manifest = prepared
    case = next(case for case in manifest['definitions'] if case['id'] == 'pde_fourier_decay')
    spec = case['reference']['register']['reference']
    values = copy.deepcopy(spec['values'])
    offset = 0.003 * spec['scales'][1]  # field w: scaled RMSE 0.003 > threshold 0.002
    for row in values:
        row[1] = [value + offset for value in row[1]]
    result = dict(status='succeeded', data=dict(
        status='succeeded', times=spec['times'],
        axes={name: list(axis) for name, axis in spec['axes'].items()},
        field_ids=list(spec['field_ids']), values=values))
    metrics = pilot.score_pde_case(case, case['model'], case['settings'], True, result)
    assert metrics['status'] == 'fail'
    assert metrics['fields']['u']['status'] == 'pass'
    assert metrics['fields']['w']['status'] == 'fail'
    mean = (metrics['fields']['u']['rmse_scaled'] + metrics['fields']['w']['rmse_scaled']) / 2
    assert mean < spec['threshold']  # averaging across fields would have hidden the failure


def test_pde_binding_drift_fails_even_with_matching_numbers(prepared):
    out, manifest = prepared
    case = next(case for case in manifest['definitions'] if case['id'] == 'pde_fourier_decay')
    spec = case['reference']['register']['reference']
    result = dict(status='succeeded', data=dict(
        status='succeeded', times=spec['times'],
        axes={name: list(axis) for name, axis in spec['axes'].items()},
        field_ids=list(spec['field_ids']), values=copy.deepcopy(spec['values'])))
    drifted = dict(case['settings'])
    drifted['samples'] = 22
    metrics = pilot.score_pde_case(case, case['model'], drifted, True, result)
    assert metrics['status'] == 'fail'
    assert metrics['binding']['settings_match_frozen'] is False
    metrics = pilot.score_pde_case(case, case['model'], case['settings'], False, result)
    assert metrics['status'] == 'fail'
    assert metrics['binding']['reference_matches_frozen'] is False


def test_missing_or_failed_runs_return_unknown_not_pass(prepared):
    out, manifest = prepared
    ode = next(case for case in manifest['definitions'] if case['id'] == 'production_loss')
    assert pilot.score_ode_case(ode, None)['status'] == 'not_run'
    assert pilot.score_ode_case(ode, dict(status='failed'))['status'] == 'failed'
    assert pilot.score_ode_case(ode, dict(status='succeeded', times=[0.0, 0.5],
                                          values={'p': [1.0, 1.15]}))['status'] == 'fail'
    truncated = dict(status='succeeded', times=ode['reference']['register']['reference']['times'],
                     values={'p': [1.0] * 5})
    assert pilot.score_ode_case(ode, truncated)['status'] == 'fail'
    pde = next(case for case in manifest['definitions'] if case['id'] == 'pde_fourier_decay')
    assert pilot.score_pde_case(pde, pde['model'], pde['settings'], True, None)['status'] == 'not_run'
    assert pilot.score_pde_case(pde, pde['model'], pde['settings'], True,
                                dict(status='work_limit'))['status'] == 'failed'
    # one required field entirely absent from the saved rows: explicit per-field
    # fail, never an average (field_ids stay declared so the gap is visible)
    spec = pde['reference']['register']['reference']
    partial = dict(status='succeeded', data=dict(
        status='succeeded', times=spec['times'],
        axes={name: list(axis) for name, axis in spec['axes'].items()},
        field_ids=list(spec['field_ids']), values=[row[:1] for row in spec['values']]))
    metrics = pilot.score_pde_case(pde, pde['model'], pde['settings'], True, partial)
    assert metrics['status'] == 'fail'
    assert metrics['fields']['w']['status'] == 'fail'
    assert 'no averaging' in metrics['fields']['w']['reason']


def test_no_credential_material_is_written_or_leaked(tmp_path, monkeypatch):
    monkeypatch.setenv(pilot.KEY_ENV, SECRET)
    monkeypatch.setattr(httpx.Client, 'post',
                        lambda *a, **k: pytest.fail('offline preparation made a request'))
    out = tmp_path / 'pilot'
    manifest = pilot.prepare(out)
    for path in out.rglob('*'):
        if path.is_file():
            assert SECRET not in path.read_text(encoding='utf-8', errors='ignore')
    assert json.dumps(manifest).find(SECRET) == -1
    assert manifest['provider']['api_key_env'] == pilot.KEY_ENV
    assert not any(key in manifest['provider'] for key in ('key', 'api_key', 'token'))


def test_allowed_models_and_frozen_effort():
    assert pilot.check_model('deepseek-flash-2') == 'deepseek-flash-2'
    assert pilot.check_model('glm-5.3-flash') == 'glm-5.3-flash'
    with pytest.raises(ValueError):
        pilot.check_model('gpt-4o')
    with pytest.raises(ValueError):
        pilot.check_model('deepseek-flash-2 ')
    provider = pilot.make_provider(dict(base_url=pilot.BASE_URL, api_key_env=pilot.KEY_ENV,
                                        model='glm-5.3-flash', reasoning_effort='max',
                                        timeout_s=180.0, max_tokens=16000, json_mode=True))
    assert isinstance(provider, pilot.ChatProvider)
    assert provider.reasoning_effort == 'max'
    assert provider.api_key_env == pilot.KEY_ENV
    assert provider.base_url == pilot.BASE_URL
    assert provider.json_mode is True
    assert type(provider).__module__ == 'biomodel_agent.llm'
    # The factory enforces the whole frozen contract: allowed models, fixed USTC
    # endpoint, key environment name, max effort and the json_mode boolean.
    with pytest.raises(ValueError):
        pilot.make_provider(dict(base_url=pilot.BASE_URL, api_key_env=pilot.KEY_ENV,
                                 model='glm-5.3-flash', reasoning_effort='low',
                                 timeout_s=180.0, max_tokens=16000, json_mode=True))
    with pytest.raises(ValueError):
        pilot.make_provider(dict(base_url=pilot.BASE_URL, api_key_env=pilot.KEY_ENV,
                                 model='gpt-4o', reasoning_effort='max',
                                 timeout_s=180.0, max_tokens=16000, json_mode=True))
    with pytest.raises(ValueError):
        pilot.make_provider(dict(base_url='https://other.example.com/v1', api_key_env=pilot.KEY_ENV,
                                 model='glm-5.3-flash', reasoning_effort='max',
                                 timeout_s=180.0, max_tokens=16000, json_mode=True))
    with pytest.raises(ValueError):
        pilot.make_provider(dict(base_url=pilot.BASE_URL, api_key_env='OTHER_KEY_ENV',
                                 model='glm-5.3-flash', reasoning_effort='max',
                                 timeout_s=180.0, max_tokens=16000, json_mode=True))
    with pytest.raises(ValueError):
        pilot.make_provider(dict(base_url=pilot.BASE_URL, api_key_env=pilot.KEY_ENV,
                                 model='glm-5.3-flash', reasoning_effort='max',
                                 timeout_s=180.0, max_tokens=16000, json_mode=False))


def test_budget_defaults_frozen_and_bounds_enforced():
    assert pilot.DEFAULT_BUDGETS == dict(planner_max_calls=32, max_iterations=2,
                                         max_native_calls=12, native_calls_per_iteration=6,
                                         max_literature_requests=0, context_max_chars=120000)
    provider = dict(timeout_s=180.0, max_tokens=16000)
    pilot.check_budgets(dict(pilot.DEFAULT_BUDGETS), provider)
    for key, value in (('planner_max_calls', 200), ('max_iterations', 0), ('max_native_calls', 0),
                       ('native_calls_per_iteration', 50), ('max_literature_requests', -1),
                       ('context_max_chars', 1000)):
        with pytest.raises(ValueError):
            pilot.check_budgets({**pilot.DEFAULT_BUDGETS, key: value}, provider)
    with pytest.raises(ValueError):
        pilot.check_budgets(dict(pilot.DEFAULT_BUDGETS), dict(timeout_s=10.0, max_tokens=16000))
    with pytest.raises(ValueError):
        pilot.check_budgets(dict(pilot.DEFAULT_BUDGETS), dict(timeout_s=180.0, max_tokens=100))
    with pytest.raises(ValueError):
        pilot.check_budgets({**pilot.DEFAULT_BUDGETS, 'native_calls_per_iteration': 20,
                             'max_native_calls': 12}, provider)


def test_live_rejects_flag_drift_from_frozen_manifest(prepared):
    out, manifest = prepared
    args = live_args(out)
    pilot.check_frozen_args(args, manifest)
    args.model = 'deepseek-flash-2'
    with pytest.raises(ValueError, match='frozen manifest'):
        pilot.check_frozen_args(args, manifest)
    args = live_args(out)
    args.planner_max_calls = manifest['budgets']['planner_max_calls'] - 1
    with pytest.raises(ValueError, match='frozen manifest'):
        pilot.check_frozen_args(args, manifest)


def test_summary_reports_explicit_numerators_denominators(prepared):
    out, manifest = prepared
    results = []
    for case in manifest['definitions']:
        if not case['numerically_evaluable']:
            metrics = dict(status='not_applicable_numeric', numeric_evaluable=False)
            behavior = 'not_applicable_numeric'
        elif case['id'] == 'pde_logistic_2d':
            # A scorable failure: every frozen error was actually computed, but
            # the recomputed error exceeds the frozen threshold.
            metrics = dict(status='fail', numeric_evaluable=True)
            behavior = 'fail'
        else:
            metrics = dict(status='pass', numeric_evaluable=True)
            behavior = 'pass'
        results.append(dict(case_id=case['id'], behavior_status=behavior, metrics=metrics))
    summary = pilot.score_summary(manifest, results)
    assert summary['behavior_pass']['numerator'] == 2
    assert summary['behavior_pass']['denominator'] == 4
    assert summary['behavior_pass']['breakdown'] == {'fail': ['pde_logistic_2d'],
                                                     'not_applicable_numeric': ['missing_rate'],
                                                     'pass': ['pde_fourier_decay', 'production_loss']}
    # Coverage counts the scorable failure too: all three applicable cases were
    # actually evaluated to finite errors against correct frozen bindings.
    assert summary['numeric_coverage']['numerator'] == 3
    assert summary['numeric_coverage']['denominator'] == 3
    assert summary['numeric_coverage']['evaluated'] == ['pde_fourier_decay', 'pde_logistic_2d',
                                                        'production_loss']
    assert summary['numeric_coverage']['breakdown'] == {'evaluated': ['pde_fourier_decay',
                                                                      'pde_logistic_2d',
                                                                      'production_loss']}
    # Attainment counts only the within-threshold passes.
    assert summary['numeric_target']['numerator'] == 2
    assert summary['numeric_target']['denominator'] == 3
    assert summary['numeric_target']['breakdown'] == {'fail': ['pde_logistic_2d'],
                                                      'pass': ['pde_fourier_decay', 'production_loss']}
    assert summary['numeric_applicability']['numerator'] == 3
    assert summary['numeric_applicability']['denominator'] == 4
    assert summary['numeric_applicability']['by_case']['missing_rate'] is False
    assert summary['scientific_mm_a_runs'] == 0
    assert summary['expt_b_runs'] == 0
    assert summary['scientific_score'] is None
    assert summary['synthetic_controls_only'] is True
    # missing results degrade to not_run, never to an implicit pass
    degraded = pilot.score_summary(manifest, [])
    assert degraded['behavior_pass']['numerator'] == 0
    assert degraded['behavior_pass']['breakdown'] == {'not_run': sorted(pilot.CASE_IDS)}
    assert degraded['numeric_coverage']['denominator'] == 3
    assert degraded['numeric_coverage']['numerator'] == 0
    assert degraded['numeric_coverage']['breakdown'] == {'not_run': ['pde_fourier_decay',
                                                                     'pde_logistic_2d',
                                                                     'production_loss']}
    assert degraded['numeric_target']['numerator'] == 0
    assert degraded['numeric_target']['breakdown'] == {'not_run': ['pde_fourier_decay',
                                                                   'pde_logistic_2d',
                                                                   'production_loss']}
    assert degraded['numeric_applicability']['numerator'] == 3


def test_live_path_uses_only_the_real_provider_adapter(prepared):
    out, manifest = prepared
    source = pilot.RUNNER_PATH.read_text(encoding='utf-8')
    assert 'MockTransport' not in source
    assert 'Scripted' not in source
    provider = pilot.make_provider(manifest['provider'])
    assert type(provider).__module__ == 'biomodel_agent.llm'
    assert provider.json_mode is True


class _FakeStore:
    """Minimal offline stand-in exposing only the lookups the runner needs."""

    def __init__(self, objects=None, index=None):
        self._objects = objects or {}
        self._index = index or {}

    def get(self, ref):
        return self._objects[ref]

    def _index_row(self, ref):
        return self._index.get(ref)


def test_pde_cases_register_and_execute_through_the_real_sdk(prepared, tmp_path, monkeypatch):
    """Both synthetic PDE controls run the real register/import/prepare/execute/
    summary path (spawn worker, no network) and pass per-field scoring."""
    out, manifest = prepared
    monkeypatch.setattr(httpx.Client, 'post',
                        lambda *a, **k: pytest.fail('offline control made a request'))
    for case in (c for c in manifest['definitions'] if c['kind'] == 'pde'):
        store = pilot.ArtifactStore(tmp_path / ('store-' + case['id']))
        model_ref = pilot.typed_models.import_model(
            store, out / 'materials' / case['model_files'][0]['name'])
        reference_ref = pilot.projects.register_reference(
            store, pilot.deepcopy(case['reference']['register']), 'pipeline_pilot_operator')
        input_ref = pilot.typed_models.prepare(store, model_ref, case['settings'],
                                               reference_ref=reference_ref)
        run_ref = pilot.typed_models.execute(store, input_ref)
        verified = pilot.typed_models.summary(store, run_ref)
        assert verified['status'] == 'succeeded', verified['checks']
        assert verified['comparison']['status'] == 'pass'
        _, _, result = pilot.typed_models.result(store, run_ref)
        metrics = pilot.score_pde_case(case, case['model'], case['settings'], True, result)
        assert metrics['status'] == 'pass'
        assert metrics['numeric_evaluable'] is True
        assert all(field['status'] == 'pass' for field in metrics['fields'].values())
        json.dumps(metrics, allow_nan=False)  # every metric is finite JSON


def test_missing_rate_behavior_needs_one_precise_question_and_known_facts(prepared):
    out, manifest = prepared
    case = next(c for c in manifest['definitions'] if c['id'] == 'missing_rate')
    good = ('The source does not report the rate constant k. Please provide the numeric '
            'value and unit of k before reproduction can proceed.')
    known = {'iter1': dict(executed=False, scientific_attempt_reserved=False)}
    assert pilot.behavior_status(case, None, [dict(question_ref='q1', question=good)], known) == 'pass'
    # an unrelated pending question must not pass the control
    assert pilot.behavior_status(
        case, None, [dict(question_ref='q1', question='Which granularity should the model use?')],
        known) == 'fail'
    # a question naming k without asking for the missing value/unit is irrelevant
    assert pilot.behavior_status(
        case, None, [dict(question_ref='q1', question='Should k stay symbolic in the report?')],
        known) == 'fail'
    # the control requires exactly one pending question
    assert pilot.behavior_status(
        case, None, [dict(question_ref='q1', question=good), dict(question_ref='q2', question=good)],
        known) == 'fail'
    assert pilot.behavior_status(case, None, [], known) == 'fail'
    # unreadable question text degrades to unknown, never to a pass
    assert pilot.behavior_status(
        case, None, [dict(question_ref='q1', question='unknown', error_type='ValueError')],
        known) == 'unknown'
    # unreadable iteration facts are unknown and never silently treated as zero
    assert pilot.behavior_status(case, None, [dict(question_ref='q1', question=good)],
                                 dict(status='unknown', error_type='ValueError')) == 'unknown'
    assert pilot.behavior_status(case, None, [dict(question_ref='q1', question=good)],
                                 {'iter1': 'corrupt'}) == 'unknown'
    # any confirmed execution or reserved computation fails the control
    assert pilot.behavior_status(
        case, None, [dict(question_ref='q1', question=good)],
        {'iter1': dict(executed=True, scientific_attempt_reserved=False)}) == 'fail'
    assert pilot.behavior_status(
        case, None, [dict(question_ref='q1', question=good)],
        {'iter1': dict(executed=False, scientific_attempt_reserved=True)}) == 'fail'


def test_scores_reject_boolean_times_coordinates_and_missing_columns(prepared):
    out, manifest = prepared
    ode = next(c for c in manifest['definitions'] if c['id'] == 'production_loss')
    ref = ode['reference']['register']['reference']
    good_values = list(ref['values']['p'])
    # Python equality would coerce False to 0.0; strict validation must reject it.
    metrics = pilot.score_ode_case(ode, dict(status='succeeded', time_unit='second',
                                             times=[False] + ref['times'][1:],
                                             values={'p': good_values}))
    assert metrics['status'] == 'fail' and metrics['numeric_evaluable'] is False
    assert 'time grid' in metrics['reason']
    # a None column previously raised TypeError; it must become a failed metric
    metrics = pilot.score_ode_case(ode, dict(status='succeeded', time_unit='second',
                                             times=list(ref['times']), values={'p': None}))
    assert metrics['status'] == 'fail' and metrics['numeric_evaluable'] is False
    assert 'missing' in metrics['fields']['p']['reason']
    # the declared clock must match the frozen reference clock
    metrics = pilot.score_ode_case(ode, dict(status='succeeded', time_unit='minute',
                                             times=list(ref['times']), values={'p': good_values}))
    assert metrics['status'] == 'fail' and metrics['numeric_evaluable'] is False
    assert 'time_unit' in metrics['reason']
    pde = next(c for c in manifest['definitions'] if c['id'] == 'pde_fourier_decay')
    spec = pde['reference']['register']['reference']
    good = dict(status='succeeded', data=dict(
        status='succeeded', times=spec['times'],
        axes={name: list(axis) for name, axis in spec['axes'].items()},
        field_ids=list(spec['field_ids']), values=copy.deepcopy(spec['values'])))
    ok = pilot.score_pde_case(pde, pde['model'], pde['settings'], True, good)
    assert ok['status'] == 'pass' and ok['numeric_evaluable'] is True
    bool_axes = copy.deepcopy(good)
    bool_axes['data']['axes']['x'] = [False] + spec['axes']['x'][1:]
    metrics = pilot.score_pde_case(pde, pde['model'], pde['settings'], True, bool_axes)
    assert metrics['status'] == 'fail' and metrics['numeric_evaluable'] is False
    assert 'axis x' in metrics['reason']
    bool_times_pde = copy.deepcopy(good)
    bool_times_pde['data']['times'] = [False] + spec['times'][1:]
    metrics = pilot.score_pde_case(pde, pde['model'], pde['settings'], True, bool_times_pde)
    assert metrics['status'] == 'fail' and metrics['numeric_evaluable'] is False
    assert 'times' in metrics['reason']


def test_large_finite_errors_stay_json_writable_and_fail(prepared):
    out, manifest = prepared
    ode = next(c for c in manifest['definitions'] if c['id'] == 'production_loss')
    ref = ode['reference']['register']['reference']
    values = [v + 1e300 for v in ref['values']['p']]
    metrics = pilot.score_ode_case(ode, dict(status='succeeded', time_unit='second',
                                             times=list(ref['times']), values={'p': values}))
    assert metrics['status'] == 'fail' and metrics['numeric_evaluable'] is True
    field = metrics['fields']['p']
    assert field['status'] == 'fail'
    assert field['max_scaled_error'] == pytest.approx(1e300, rel=1e-9)
    assert field['rmse_scaled'] == pytest.approx(1e300, rel=1e-9)
    assert json.dumps(metrics, allow_nan=False)  # finite JSON, never Infinity/NaN
    # a finite but huge PDE error behaves the same and cannot mask the other field
    pde = next(c for c in manifest['definitions'] if c['id'] == 'pde_fourier_decay')
    spec = pde['reference']['register']['reference']
    pde_values = copy.deepcopy(spec['values'])
    for row in pde_values:
        row[0] = [v + 1e250 for v in row[0]]
    result = dict(status='succeeded', data=dict(
        status='succeeded', times=spec['times'],
        axes={name: list(axis) for name, axis in spec['axes'].items()},
        field_ids=list(spec['field_ids']), values=pde_values))
    metrics = pilot.score_pde_case(pde, pde['model'], pde['settings'], True, result)
    assert metrics['status'] == 'fail' and metrics['numeric_evaluable'] is True
    assert metrics['fields']['u']['status'] == 'fail'
    assert metrics['fields']['w']['status'] == 'pass'
    assert json.dumps(metrics, allow_nan=False)


def test_pde_grid_shape_follows_explicit_axis_names_not_dict_order(prepared):
    out, manifest = prepared
    case = next(c for c in manifest['definitions'] if c['id'] == 'pde_logistic_2d')
    spec = case['reference']['register']['reference']
    assert list(spec['axes']) == ['x', 'y']
    # Reversed axes dict insertion order must not change the (x, y) cell shape.
    result = dict(status='succeeded', data=dict(
        status='succeeded', times=spec['times'],
        axes={'y': list(spec['axes']['y']), 'x': list(spec['axes']['x'])},
        field_ids=list(spec['field_ids']), values=copy.deepcopy(spec['values'])))
    metrics = pilot.score_pde_case(case, case['model'], case['settings'], True, result)
    assert metrics['status'] == 'pass' and metrics['numeric_evaluable'] is True
    # A (y, x) shaped value grid is a real shape error and must fail explicitly.
    wrong = copy.deepcopy(spec['values'])
    nx, ny = len(spec['axes']['x']), len(spec['axes']['y'])
    for row in wrong:
        for index in (0, 1):
            grid = row[index]
            row[index] = [[grid[i][j] for i in range(nx)] for j in range(ny)]
    result_bad = dict(status='succeeded', data=dict(
        status='succeeded', times=spec['times'],
        axes={'x': list(spec['axes']['x']), 'y': list(spec['axes']['y'])},
        field_ids=list(spec['field_ids']), values=wrong))
    metrics = pilot.score_pde_case(case, case['model'], case['settings'], True, result_bad)
    assert metrics['status'] == 'fail' and metrics['numeric_evaluable'] is False
    assert 'ragged or non-finite' in metrics['fields']['u']['reason']


def _facts(**overrides):
    facts = dict(iteration_ref='iter1', state='completed', executed=True,
                 scientific_attempt_reserved=True, native_provider_calls=0,
                 numeric_status='pass', comparison_status='pass', source_binding_status='pass',
                 application_run_ref='run1', pending_question=None, scientific_support='unknown')
    facts.update(overrides)
    return facts


def test_case_metrics_keeps_sdk_gates_and_native_failures(prepared, monkeypatch):
    out, manifest = prepared
    case = next(c for c in manifest['definitions'] if c['id'] == 'production_loss')
    ref = case['reference']['register']['reference']
    simulation = dict(status='succeeded', time_unit='second', times=list(ref['times']),
                      values={k: list(v) for k, v in ref['values'].items()})
    store = _FakeStore({'run1': dict(kind='run', payload=dict(simulation=simulation, input_ref='inp1')),
                        'inp1': dict(kind='simulation_input', payload=dict(solver='BDF'))})
    view = dict(iteration_refs=['iter1'])
    monkeypatch.setattr(pilot.projects, 'facts', lambda store, ref: _facts())
    metrics = pilot.case_metrics(case, store, view)
    assert metrics['status'] == 'pass'
    assert metrics['numeric_evaluable'] is True
    assert metrics['gates'] == dict(confirmed_execution='pass', native_numeric_checks='pass',
                                    reference_comparison='pass', source_binding='pass')
    # A real native numeric check failure must not be overridden by the runner's
    # own trajectory recomputation.
    monkeypatch.setattr(pilot.projects, 'facts', lambda store, ref: _facts(numeric_status='fail'))
    metrics = pilot.case_metrics(case, store, view)
    assert metrics['status'] == 'fail'
    assert metrics['numeric_evaluable'] is True  # errors were computable; the gate fails the case
    assert metrics['gates']['native_numeric_checks'] == 'fail'
    assert 'gates did not pass' in metrics['reason']
    # A missing reference binding (comparison never run) can never pass.
    monkeypatch.setattr(pilot.projects, 'facts',
                        lambda store, ref: _facts(comparison_status='not_run'))
    metrics = pilot.case_metrics(case, store, view)
    assert metrics['status'] == 'fail'
    assert metrics['gates']['reference_comparison'] == 'not_run'
    # The complete ODE also requires a passed source binding.
    monkeypatch.setattr(pilot.projects, 'facts',
                        lambda store, ref: _facts(source_binding_status='unknown'))
    metrics = pilot.case_metrics(case, store, view)
    assert metrics['status'] == 'fail'
    assert metrics['gates']['source_binding'] == 'unknown'
    # Unreadable iteration facts degrade to unknown, never to success.
    def boom(store, ref):
        raise ValueError('corrupt')
    monkeypatch.setattr(pilot.projects, 'facts', boom)
    metrics = pilot.case_metrics(case, store, view)
    assert metrics['status'] == 'unknown'
    assert 'never treated as zero' in metrics['reason']


def test_call_records_read_usage_from_failure_receipts(prepared, monkeypatch):
    out, manifest = prepared
    attempts = [('a1', dict(sequence=0, operation='respond',
                            args=dict(request_ref='p1'), response_ref=None, native_allocation=0)),
                ('a2', dict(sequence=1, operation='inspect_progress', args={}, response_ref='resp9',
                            native_allocation=0))]
    returns = {'a1': ('r1', dict(state='rejected', elapsed_s=1.5, error_type='ProviderError',
                                 diagnostic=dict(stage='respond', code='http'),
                                 provider_failure_ref='f1')),
               'a2': ('r2', dict(state='returned', elapsed_s=0.1, result=dict(ok=True)))}
    failure = dict(version=1, project_ref='proj', attempt_ref='a1', request_ref='p1', provider='p',
                   error_kind='http',
                   usage=dict(prompt_tokens=10, completion_tokens=5, total_tokens=15),
                   elapsed_s=1.25, response_diagnostic=dict(finish_reason='stop'),
                   model_content_json_blob_ref='blob1',
                   model_content_authority='untrusted_model_output_not_an_action')
    monkeypatch.setattr(pilot.projects, 'history', lambda store, ref: (attempts, returns, []))
    monkeypatch.setattr(pilot.projects, 'own',
                        lambda store, ref, kind, project: failure if ref == 'f1'
                        else (_ for _ in ()).throw(ValueError('foreign')))
    records = pilot.call_records(_FakeStore(), 'proj')
    assert len(records) == 1
    record = records[0]
    assert record['usage'] == dict(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    assert record['elapsed_s'] == 1.25
    assert record['model_content_blob_ref'] == 'blob1'
    assert record['provider_failure_ref'] == 'f1'
    # a receipt without reported usage stays explicitly unknown
    monkeypatch.setattr(pilot.projects, 'own',
                        lambda store, ref, kind, project: dict(failure, usage={},
                                                               model_content_json_blob_ref=None))
    record = pilot.call_records(_FakeStore(), 'proj')[0]
    assert record['usage'] == 'unknown'
    assert record['model_content_blob_ref'] is None
    # an unbound failure receipt is never trusted
    monkeypatch.setattr(pilot.projects, 'own',
                        lambda store, ref, kind, project: dict(failure, attempt_ref='other'))
    record = pilot.call_records(_FakeStore(), 'proj')[0]
    assert record['usage'] == 'unknown'
    assert record['failure_receipt'] == 'unreadable_or_unbound'


def test_operation_events_export_sdk_reservations_and_returns(prepared, monkeypatch):
    out, manifest = prepared
    attempts = [('a1', dict(sequence=0, operation='respond',
                            args=dict(request_ref='p1'), response_ref=None, actor='agent',
                            native_allocation=0)),
                ('a2', dict(sequence=1, operation='inspect_progress', args={}, response_ref=None,
                            actor='agent', native_allocation=0))]
    returns = {'a1': ('r1', dict(state='rejected', elapsed_s=1.5, error_type='ProviderError',
                                 diagnostic=dict(stage='respond', code='http'),
                                 provider_failure_ref='f1')),
               'a2': ('r2', dict(state='returned', elapsed_s=0.1, result=dict(ok=True)))}
    index = {'a1': dict(ref='a1', kind='research_project_operation', id=None,
                        created_at='2026-11-12T00:00:00+00:00')}
    monkeypatch.setattr(pilot.projects, 'history', lambda store, ref: (attempts, returns, []))
    events = pilot.operation_events(_FakeStore(index=index), 'proj')
    assert [event['sequence'] for event in events] == [0, 1]
    assert events[0]['attempt_ref'] == 'a1'
    assert events[0]['index_registered_at'] == '2026-11-12T00:00:00+00:00'
    assert events[1]['index_registered_at'] is None  # missing index row stays None
    assert events[0]['state'] == 'rejected' and events[0]['error_stage'] == 'respond'
    assert events[0]['prompt_ref'] == 'p1' and events[0]['return_ref'] == 'r1'
    assert events[1]['state'] == 'returned'
    assert json.dumps(events, allow_nan=False)
    assert 'never writes it before' in pilot.OPERATION_EVENT_SEMANTICS


def test_fourier_threshold_budget_matches_analytic_bound(prepared):
    out, manifest = prepared
    case = next(c for c in manifest['definitions'] if c['id'] == 'pde_fourier_decay')
    budget = case['threshold_budget']
    N, T = 96, case['settings']['duration']
    lambda_d = (N * math.sin(math.pi / N) / math.pi) ** 2
    bounds = budget['spatial_bounds']
    assert bounds['lambda_d'] == pytest.approx(lambda_d, rel=1e-12)
    assert bounds['lambda_d'] == pytest.approx(0.999643, abs=5e-7)
    assert bounds['relative_eigenvalue_error'] == pytest.approx(1 - lambda_d, rel=1e-12)
    for field, (diffusion, decay) in (('u', (0.35, 0.4)), ('w', (0.12, 0.05))):
        rate = min(diffusion * lambda_d + decay, diffusion + decay)
        t_star = min(T, 1.0 / rate)
        expected = diffusion * (1 - lambda_d) * t_star * math.exp(-rate * t_star)
        assert bounds['per_field_scaled_bound'][field] == pytest.approx(expected, rel=1e-12)
        assert 0 < bounds['per_field_scaled_bound'][field] < budget['threshold']
    assert budget['threshold'] == 0.002
    assert budget['fixed_before_any_call'] is True
    # the previously wrong hardcoded constants are gone
    assert '0.9996053' not in budget['spatial'] and '7e-5' not in budget['spatial']
    assert '3.95e-4' not in budget['spatial']


def test_error_messages_redact_the_environment_key(monkeypatch):
    monkeypatch.setenv(pilot.KEY_ENV, SECRET)

    class Boom(Exception):
        pass

    message = pilot._safe_error_message(Boom('POST failed for key ' + SECRET))
    assert SECRET not in message
    assert '[redacted]' in message
    assert pilot._safe_error_message(Boom('Authorization: Bearer tok123')).count('[redacted]') == 1
    # offline preparation never needs the key at all
    monkeypatch.delenv(pilot.KEY_ENV, raising=False)
    assert pilot._environment_secrets() == []


def test_json_snapshot_preserves_previous_result_when_publication_fails(tmp_path, monkeypatch):
    target = tmp_path / 'summary.json'
    previous = b'{"completed_cases": 1}\n'
    target.write_bytes(previous)

    def fail_publication(source, destination):
        raise OSError('publication failed')

    monkeypatch.setattr(pilot.os, 'replace', fail_publication)
    with pytest.raises(OSError, match='publication failed'):
        pilot._write_json(target, {'completed_cases': 2})
    assert target.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [target]


def test_json_snapshot_rejects_nonfinite_values_before_replacing_results(tmp_path):
    target = tmp_path / 'summary.json'
    previous = b'{"completed_cases": 1}\n'
    target.write_bytes(previous)
    with pytest.raises(ValueError):
        pilot._write_json(target, {'error': float('inf')})
    assert target.read_bytes() == previous


def test_pde_score_rejects_extra_field_values(prepared):
    _, manifest = prepared
    case = next(c for c in manifest['definitions'] if c['id'] == 'pde_fourier_decay')
    data = pilot.deepcopy(case['reference']['register']['reference'])
    for row in data['values']:
        row.append(list(row[0]))
    metrics = pilot.score_pde_case(case, case['model'], case['settings'], True,
                                   dict(status='succeeded', data=data))
    assert metrics['status'] == 'fail'
    assert metrics['numeric_evaluable'] is False


def test_operation_export_links_successful_model_response(monkeypatch):
    attempts = [('a1', dict(sequence=0, operation='respond', args=dict(request_ref='p1')))]
    returns = {'a1': ('r1', dict(state='returned', elapsed_s=0.5,
                                result=dict(response_ref='response1')))}
    index = {'a1': dict(created_at='2026-11-12T00:00:00+00:00'),
             'r1': dict(created_at='2026-11-12T00:00:01+00:00')}
    monkeypatch.setattr(pilot.projects, 'history', lambda store, ref: (attempts, returns, []))
    event = pilot.operation_events(_FakeStore(index=index), 'project1')[0]
    assert event['response_ref'] == 'response1'
    assert event['return_index_registered_at'] == index['r1']['created_at']
