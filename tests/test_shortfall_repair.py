"""New working-state and investigation paths; synthetic sources, real solver."""
import json
import math
from copy import deepcopy

import pytest

from biomodel_agent import agent, context, diagnostics, evidence, reporting, workstate
from biomodel_agent.domain.contracts import ExperimentSpec, ModelDraft, Parameter, RateRule, State
from biomodel_agent.domain.serialization import to_dict
from biomodel_agent.parameter_investigation import investigate
from biomodel_agent.storage import ArtifactStore


class Scripted:
    last_usage = {}

    def __init__(self, actions):
        self.actions = iter(actions)
        self.requests = []

    def respond(self, messages):
        self.requests.append(deepcopy(messages))
        response = next(self.actions)
        return response(messages) if callable(response) else response


def action(name, **args):
    return dict(action=name, args=args)


def fixture(tmp_path, text='Initial x = 1.\nParameter k = 0.4.\nEquation dx/dt = -k*x.\n'):
    store = ArtifactStore(tmp_path / 'store')
    path = tmp_path / 'source.txt'
    path.write_text(text)
    source = evidence.ingest_source(store, path)
    return store, source


def proposal(source, initial_source=None, value=.4):
    draft = ModelDraft('decay', (State('x', 1.),), (Parameter('k', value),),
                       rate_rules=(RateRule('x', '-k*x'),))
    return dict(draft=to_dict(draft), evidence=[
        dict(target=target, source_ref=ref, page=1, quote=quote)
        for target, ref, quote in [('x/initial', initial_source or source, 'Initial x = 1.'),
                                  ('k/value', source, 'Parameter k = 0.4.'),
                                  ('rule:x/expression', source, 'Equation dx/dt = -k*x.')]])


def test_investigation_progress_and_parameter_provenance_real_execution(tmp_path):
    store, source = fixture(tmp_path)
    provider = Scripted([action('investigate_parameter', target='k/value', query='k'),
                         action('submit_draft', **proposal(source)), action('inspect_progress'),
                         action('run'), action('finish')])
    ref = agent.run_agent(store, provider, [source], ExperimentSpec((0., 1.)))
    result = store.get(ref)['payload']
    assert result['state'] == 'completed' and result['call_count'] == 5
    progress = workstate.from_run(store, ref)
    assert progress == result['progress']
    assert all(step['status'] == 'complete' for step in progress['steps'][1:])
    row = progress['parameters'][0]
    assert row['value'] == .4 and row['value_status'] == 'proposed'
    assert row['conditions_status'] == 'not_verified' and row['semantic_support'] == 'unknown'
    assert row['citations'][0]['source_ref'] == source
    assert row['citations'][0]['quote'] == 'Parameter k = 0.4.'
    dossier = store.get(store.list('parameter_investigation')[0])['payload']
    assert dossier['selected_value'] is None and dossier['retrieval']['results']
    assert store.get(result['run_ref'])['payload']['simulation']['values']['x'][-1] == pytest.approx(math.exp(-.4), rel=1e-6)
    assert 'Task progress' in reporting.render_report(store, ref)
    checkpoint = store.get(result['checkpoint_ref'])
    assert workstate.from_run(store, result['checkpoint_ref']) == checkpoint['payload']['progress']
    # Export closure contains both the investigation and parameter claims.
    investigation_ref = store.list('parameter_investigation')[0]
    assert investigation_ref in store.get(ref)['refs']


def test_feedback_repairs_target_without_silent_value_adoption(tmp_path):
    store, source = fixture(tmp_path)
    provider = Scripted([action('submit_draft', **proposal(source, value=.9)),
                         action('inspect_progress'), action('submit_draft', **proposal(source)),
                         action('run'), action('finish')])
    ref = agent.run_agent(store, provider, [source], ExperimentSpec((0., 1.)))
    result = store.get(ref)['payload']
    assert result['state'] == 'completed' and result['repair_count'] == 1
    feedback = json.loads(provider.requests[1][-1]['content'])['validation_feedback']['items']
    assert any(i['target'] == 'k/value' and i['category'] == 'binding' and i['route'] == 'repair' for i in feedback)
    assert result['progress']['proposal_revisions'] == 2 and not result['progress']['unresolved']


def test_missing_parameter_remains_null_and_is_reported(tmp_path):
    store, source = fixture(tmp_path, 'Initial x = 1.\nParameter k is not reported.\nEquation dx/dt = -k*x.\n')
    provider = Scripted([action('investigate_parameter', target='k/value', query='k'),
                         action('submit_draft', **proposal(source, value=None))])
    ref = agent.run_agent(store, provider, [source], ExperimentSpec((0., 1.)))
    result = store.get(ref)['payload']
    assert result['state'] == 'awaiting_input' and not store.list('run')
    assert result['progress']['parameters'][0]['value'] is None
    assert any(i.get('category') == 'missing_information' for i in result['progress']['unresolved'])


@pytest.mark.parametrize('text,expected', [
    ('Parameter k = 0.4.\nParameter k = 0.9.\n', 'conflicting_declared_values'),
    ('Parameter k is not reported.\n', 'missing_source_value'),
    ('The table labels k per second, but the legend labels k per minute.\n', 'conflicting_time_units'),
])
def test_parameter_investigation_retains_original_conflicts(tmp_path, text, expected):
    store, source = fixture(tmp_path, text)
    result = investigate(store, [source], 'k/value', 'k')
    assert any(i['kind'] == expected for i in result['source_review']['issues'])
    assert result['selected_value'] is None and result['conditions_status'] == 'not_verified'
    assert store.get(result['investigation_ref'])['refs'] == [source]


def test_no_local_match_does_not_create_model_or_search_outside_scope(tmp_path):
    store, source = fixture(tmp_path, 'This source has no kinetic values.')
    other_path = tmp_path / 'other.txt'
    other_path.write_text('Parameter q = 10.')
    other = evidence.ingest_source(store, other_path)
    result = investigate(store, [source], 'q/value', 'q')
    assert not result['retrieval']['results'] and other not in result['source_refs']
    assert result['selected_value'] is None and not store.list('model')


@pytest.mark.parametrize('target', ['k', '../value', 'k/unit', True, 'k/value/extra'])
def test_bad_target_rejected_without_investigation_write(tmp_path, target):
    store, source = fixture(tmp_path)
    with pytest.raises(ValueError):
        investigate(store, [source], target, 'k')
    assert not store.list('parameter_investigation')


def test_progress_survives_reopen_and_attributed_resume(tmp_path):
    store, source = fixture(tmp_path, 'Initial x is not reported.\nParameter k = 0.4.\nEquation dx/dt = -k*x.\n')
    first = agent.run_agent(store, Scripted([action('investigate_parameter', target='x/initial', query='x'),
        action('ask_researcher', question='What is initial x?')]), [source], ExperimentSpec((0., 1.)))
    original = store.get(first)
    def submit(messages):
        reply_source = json.loads(messages[-1]['content'])['source_ref']
        return action('submit_draft', **proposal(source, initial_source=reply_source))
    store = ArtifactStore(tmp_path / 'store')
    resumed = agent.resume_agent(store, Scripted([submit, action('run'), action('finish')]),
                                 first, 'Initial x = 1.', respondent='Researcher A')
    result = store.get(resumed)['payload']
    assert result['state'] == 'completed' and result['call_count'] == 5
    assert result['progress']['remaining_calls'] == 3 and not result['progress']['unresolved']
    assert store.get(first) == original
    assert len(store.list('parameter_investigation')) == 1


def test_investigation_does_not_reset_call_budget(tmp_path):
    store, source = fixture(tmp_path)
    ref = agent.run_agent(store, Scripted([action('investigate_parameter', target='k/value', query='k')]),
                          [source], ExperimentSpec((0., 1.)), max_calls=1)
    result = store.get(ref)['payload']
    assert result['state'] == 'incomplete' and result['progress']['remaining_calls'] == 0
    assert not store.list('run')


def test_compaction_pins_latest_progress_and_investigation(tmp_path):
    history = [{'role': 'system', 'content': 'instructions'}, {'role': 'user', 'content': '{}'}]
    pinned = [{'role': 'user', 'content': json.dumps({'tool': tool, 'ok': True, 'marker': tool})}
              for tool in ('inspect_progress', 'investigate_parameter')]
    history += pinned
    history += [{'role': 'user', 'content': json.dumps({'tool': 'inspect_source', 'text': 'x'*1800})} for _ in range(10)]
    packet, audit = context.build_context(history, context.policy(8000))
    assert audit['compacted'] and all(m in packet for m in pinned)


def test_failure_routes_do_not_invent_retry_authority():
    failure = diagnostics.event_failure(dict(type='provider_error', kind='authentication', retryable=False))
    assert failure['route'] == 'stop_or_inspect'
    unknown = diagnostics.event_failure(dict(type='tool_error', tool='search_sources', error='unexpected'))
    assert unknown['category'] == 'unknown' and unknown['route'] == 'stop_or_inspect'
    numeric = diagnostics.event_failure(dict(type='tool_error', tool='run', error='timeout'))
    assert numeric['category'] == 'numerical' and numeric['route'] == 'diagnose'


@pytest.mark.parametrize('tool,args', [('investigate_parameter', dict(target='k/value', query='k')),
                                      ('inspect_progress', {})])
def test_returned_new_tool_result_is_recovered_without_repeating_it(tmp_path, monkeypatch, tool, args):
    from biomodel_agent import parameter_investigation, recovery
    store, source = fixture(tmp_path)
    original_put = store.put
    def fault(kind, payload, refs=None):
        ref = original_put(kind, payload, refs)
        if kind == 'agent_invocation' and payload['name'] == tool and payload['phase'] == 'returned':
            raise SystemExit('tool receipt was persisted')
        return ref
    with monkeypatch.context() as patch:
        patch.setattr(store, 'put', fault)
        with pytest.raises(SystemExit):
            agent.run_agent(store, Scripted([action(tool, **args)]), [source], ExperimentSpec((0., 1.)))
    execution = store.list('agent_execution')[0]
    assert recovery.diagnose_execution(store, execution)['status'] == 'ready'
    monkeypatch.setattr(parameter_investigation, 'investigate', lambda *a, **k: pytest.fail('investigation repeated'))
    result = store.get(agent.recover_agent(store, Scripted([action('ask_researcher', question='Confirm condition?')]), execution))['payload']
    assert result['call_count'] == 2 and result['state'] == 'awaiting_input'
    assert len(store.list('parameter_investigation')) == (1 if tool == 'investigate_parameter' else 0)


def test_progress_legacy_run_uses_archived_checkpoint_draft(tmp_path):
    store, source = fixture(tmp_path)
    ref = agent.run_agent(store, Scripted([action('submit_draft', **proposal(source)),
                                         action('ask_researcher', question='Confirm condition?')]),
                          [source], ExperimentSpec((0., 1.)))
    record = store.get(ref)
    payload = dict(record['payload'])
    payload.pop('draft_ref')
    payload.pop('progress')
    legacy = store.put('agent_run', payload, refs=record['refs'])
    assert workstate.from_run(store, legacy)['parameters'][0]['value'] == .4


def test_parameter_investigation_cannot_extend_frozen_study(tmp_path):
    from test_study_execution_acceptance import prepared
    store, settings, plan, source, experiment = prepared(tmp_path)
    provider = Scripted([action('investigate_parameter', target='k/value', query='k'),
                         action('inspect_progress'), action('ask_researcher', question='Confirm the frozen protocol?')])
    ref = agent.run_agent(store, provider, [source], experiment, mode='hypothesis', study_plan_ref=plan)
    result = store.get(ref)['payload']
    assert result['state'] == 'awaiting_input' and not store.list('parameter_investigation')
    assert any(e.get('type') == 'tool_error' and e.get('tool') == 'investigate_parameter' for e in result['events'])
    assert result['progress']['steps'][0]['artifact_refs'] == [plan]


def test_unknown_parameter_write_is_not_retried_on_recovery(tmp_path, monkeypatch):
    from biomodel_agent import recovery
    store, source = fixture(tmp_path)
    original_put = store.put
    def fault(kind, payload, refs=None):
        ref = original_put(kind, payload, refs)
        if kind == 'parameter_investigation':
            raise SystemExit('artifact created but no returned receipt')
        return ref
    with monkeypatch.context() as patch:
        patch.setattr(store, 'put', fault)
        with pytest.raises(SystemExit):
            agent.run_agent(store, Scripted([action('investigate_parameter', target='k/value', query='k')]),
                            [source], ExperimentSpec((0., 1.)))
    execution = store.list('agent_execution')[0]
    report = recovery.diagnose_execution(store, execution)
    assert report['status'] == 'blocked'
    before = store.list()
    with pytest.raises(recovery.RecoveryBlocked):
        agent.recover_agent(store, Scripted([]), execution)
    assert store.list() == before
