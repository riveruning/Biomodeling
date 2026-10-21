"""Codex-owned journal boundary checks; scripted inference, real scientific tools."""
from copy import deepcopy
import json
import multiprocessing as mp
import os

import pytest

from biomodel_agent import agent, agent_tools, application, bundles, studies
from biomodel_agent.domain.contracts import ExperimentSpec, ModelDraft, Parameter, RateRule, State
from biomodel_agent.domain.serialization import to_dict
from biomodel_agent.llm import ModelResponseError
from biomodel_agent.storage import ArtifactStore


class Scripted:
    last_usage = {'total_tokens': 3}
    api_key = 'private-key-never-journal-this'
    def __init__(self, actions):
        self.actions = iter(actions)
        self.calls = []
    def respond(self, messages):
        self.calls.append(deepcopy(messages))
        value = next(self.actions)
        if isinstance(value, BaseException):
            raise value
        return deepcopy(value)


def act(name, **args):
    return {'action': name, 'args': args}


def fixture(tmp_path):
    store = ArtifactStore(tmp_path / 'store')
    quotes = ('Initial x = 1.', 'Parameter k = 0.4.', 'Equation dx/dt = -k*x.')
    source = store.put('source', {'pages': [{'number': 1, 'text': '\n'.join(quotes)}]})
    draft = ModelDraft('decay', (State('x', 1.),), (Parameter('k', .4),),
                       rate_rules=(RateRule('x', '-k*x'),))
    args = {'draft': to_dict(draft), 'evidence': [
        {'target': target, 'source_ref': source, 'page': 1, 'quote': quote}
        for target, quote in zip(('x/initial', 'k/value', 'rule:x/expression'), quotes)]}
    return store, source, args


def records(store, name=None):
    rows = [(ref, store.get(ref)) for ref in store.list('agent_invocation')]
    return [(ref, env) for ref, env in rows if name is None or env['payload']['name'] == name]


def phase(store, name, state):
    return [(ref, env) for ref, env in records(store, name) if env['payload']['phase'] == state]


def run(store, source, provider, **kwargs):
    return agent.run_agent(store, provider, [source], ExperimentSpec((0., 1.)), **kwargs)


def test_crash_at_provider_entry_retains_attempt_reservation(tmp_path):
    store, source, _ = fixture(tmp_path)
    provider = Scripted([SystemExit('injected at provider entry')])
    with pytest.raises(SystemExit):
        run(store, source, provider, max_calls=1)
    reopened = ArtifactStore(tmp_path / 'store')
    reserved = phase(reopened, 'respond', 'reserved')
    assert len(provider.calls) == len(reserved) == 1
    data = reserved[0][1]['payload']
    assert data['call_count'] == 1 and data['run_count'] == 0
    assert data['details']['request_messages'] == provider.calls[0]
    interrupted = phase(reopened, 'respond', 'interrupted')
    assert len(interrupted) == 1
    assert interrupted[0][1]['payload']['operation_id'] == data['operation_id']
    assert not reopened.list('agent_run')


def test_provider_return_journal_boundary_precedes_action_handling(tmp_path, monkeypatch):
    store, source, _ = fixture(tmp_path)
    response = act('ask_researcher', question='Confirm initial x?')
    provider = Scripted([response])
    def crash(*args):
        raise SystemExit('after service return before action handling')
    monkeypatch.setattr(agent, '_assistant', crash)
    with pytest.raises(SystemExit):
        run(store, source, provider)
    returned = phase(store, 'respond', 'returned')
    assert len(returned) == 1 and len(provider.calls) == 1
    assert json.loads(returned[0][1]['payload']['details']['response_json']) == response
    assert not store.list('agent_run')


def test_post_run_boundary_preserves_result_ref_and_compute_reservation(tmp_path, monkeypatch):
    store, source, proposal = fixture(tmp_path)
    provider = Scripted([{'action':'submit_draft', 'args':proposal}, act('run')])
    def crash(*args):
        raise SystemExit('after result saved before feedback/checkpoint')
    monkeypatch.setattr(agent, '_run_facts', crash)
    with pytest.raises(SystemExit):
        run(store, source, provider)
    result_refs = store.list('run')
    assert len(result_refs) == 1 and len(provider.calls) == 2
    returned = phase(store, 'run', 'returned')
    assert len(returned) == 1
    env = returned[0][1]
    assert env['payload']['call_count'] == 2 and env['payload']['run_count'] == 1
    assert env['payload']['details']['result_ref'] == result_refs[0]
    assert result_refs[0] in env['refs']
    assert store.get(result_refs[0])['payload']['simulation']['values']['x'][-1] == pytest.approx(.670320046, abs=1e-6)
    assert not store.list('agent_run')


def test_failed_reservation_never_calls_provider(tmp_path, monkeypatch):
    store, source, _ = fixture(tmp_path)
    provider = Scripted([act('ask_researcher', question='Initial x?')])
    original = store.put
    def fail(kind, payload, refs=None):
        if kind == 'agent_invocation' and payload['phase'] == 'reserved':
            raise OSError('reservation storage unavailable')
        return original(kind, payload, refs)
    monkeypatch.setattr(store, 'put', fail)
    with pytest.raises(OSError):
        run(store, source, provider)
    assert not provider.calls


def test_failed_response_record_does_not_execute_or_retry(tmp_path, monkeypatch):
    store, source, proposal = fixture(tmp_path)
    provider = Scripted([{'action':'submit_draft', 'args':proposal}])
    original = store.put
    def fail(kind, payload, refs=None):
        if kind == 'agent_invocation' and payload['phase'] == 'returned':
            raise OSError('response record write failed')
        return original(kind, payload, refs)
    monkeypatch.setattr(store, 'put', fail)
    with pytest.raises(OSError):
        run(store, source, provider)
    assert len(provider.calls) == 1 and not store.list('model')
    assert len(phase(store, 'respond', 'reserved')) == 1
    assert not phase(store, 'respond', 'returned')


def test_saved_run_with_failed_terminal_record_is_not_recomputed(tmp_path, monkeypatch):
    store, source, proposal = fixture(tmp_path)
    provider = Scripted([{'action':'submit_draft', 'args':proposal}, act('run'), act('finish')])
    original = store.put
    def fail(kind, payload, refs=None):
        if kind == 'agent_invocation' and payload['name'] == 'run' and payload['phase'] == 'returned':
            raise OSError('result journal write failed')
        return original(kind, payload, refs)
    monkeypatch.setattr(store, 'put', fail)
    result = store.get(run(store, source, provider))['payload']
    assert result['state'] == 'incomplete' and len(provider.calls) == 2
    assert len(store.list('run')) == 1
    assert len(phase(store, 'run', 'reserved')) == 1 and not phase(store, 'run', 'returned')
    assert phase(store, 'run', 'reserved')[0][1]['payload']['run_count'] == 1


def test_interrupt_inside_tool_with_partial_result_is_not_claimed_returned(tmp_path, monkeypatch):
    store, source, proposal = fixture(tmp_path)
    original = application.run_reproduction
    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise SystemExit('tool saved a result then interrupted before returning')
    monkeypatch.setattr(application, 'run_reproduction', interrupted)
    with pytest.raises(SystemExit):
        run(store, source, Scripted([{'action':'submit_draft', 'args':proposal},act('run')]))
    assert len(store.list('run')) == 1
    terminal = phase(store, 'run', 'interrupted')
    assert len(terminal) == 1 and terminal[0][1]['payload']['run_count'] == 1
    assert not phase(store, 'run', 'returned')


@pytest.mark.parametrize('name,args', [
    ('search_sources', {'query':'parameter k'}), ('inspect_source', {'page':1,'start':0,'end':10}),
    ('load_skill', {'name':'ode_reproduction'}), ('recall_memory', {'query':'initial x'})])
def test_actual_read_tools_have_observational_journal_entries(tmp_path, name, args):
    store, source, _ = fixture(tmp_path)
    args = dict(args)
    if name == 'inspect_source':
        args['source_ref'] = source
    provider = Scripted([{'action':name, 'args':args},act('ask_researcher',question='Continue?')])
    root = run(store, source, provider, memory_project='journal-read-test', max_calls=2)
    assert store.get(root)['payload']['state'] == 'awaiting_input'
    assert len(phase(store,name,'reserved')) == len(phase(store,name,'returned')) == 1
    row = phase(store,name,'returned')[0][1]['payload']
    assert row['replay_class'] == 'read_only' and row['run_count'] == 0


def test_resume_and_alternative_branch_keep_distinct_executions_and_cumulative_calls(tmp_path):
    store, source, _ = fixture(tmp_path)
    first = run(store,source,Scripted([act('ask_researcher',question='Initial x?')]),max_calls=3)
    parent = deepcopy(store.get(first))
    children = [agent.resume_agent(store,Scripted([act('ask_researcher',question='Confirm?')]),
                first,'Initial x = 1.',respondent='A') for _ in range(2)]
    rows = [store.get(ref)['payload'] for ref in [first,*children]]
    assert len({row['execution_ref'] for row in rows}) == 3
    for row in rows[1:]:
        assert row['call_count'] == 2
        execution = store.get(row['execution_ref'])['payload']
        assert execution['base_call_count'] == 1 and execution['parent_run_ref'] == first
        assert all(store.get(r)['payload']['call_count'] == 2 for r in row['invocation_refs'])
    assert store.get(first) == parent


def test_retry_attempts_each_reserve_budget_and_keep_existing_limit(tmp_path):
    store, source, _ = fixture(tmp_path)
    provider = Scripted([ModelResponseError('bad response'),act('ask_researcher',question='Initial x?')])
    root = run(store,source,provider,max_calls=2)
    data = store.get(root)['payload']
    assert data['call_count'] == 2 and data['retry_count'] == 1
    reserved = phase(store,'respond','reserved')
    assert [row['payload']['call_count'] for _,row in reserved] == [1,2]
    assert len({row['payload']['operation_id'] for _,row in reserved}) == 2
    assert len(phase(store,'respond','error')) == 1


def test_normal_export_keeps_all_flat_journal_refs_and_no_credentials(tmp_path):
    store, source, proposal = fixture(tmp_path)
    provider = Scripted([{'action':'submit_draft','args':proposal},act('run'),act('finish')])
    root = run(store,source,provider)
    result = store.get(root)['payload']
    assert result['state'] == 'completed' and result['call_count'] == 3
    refs = result['invocation_refs']
    assert len(refs) == len(records(store)) == 10
    checkpoint = store.get(result['checkpoint_ref'])['payload']
    assert checkpoint['execution_ref'] == result['execution_ref']
    assert checkpoint['invocation_refs'] == refs
    manifest = bundles.export_bundle(store,root,tmp_path/'bundle')
    assert set(refs+[result['execution_ref']]).issubset(manifest['objects_and_blobs'])
    raw = json.dumps([store.get(ref) for ref in refs+[result['execution_ref']]])
    assert provider.api_key not in raw


def test_long_task_remains_under_storage_reference_depth_limit(tmp_path):
    store, source, _ = fixture(tmp_path)
    query = act('search_sources',query='parameter k')
    root = run(store,source,Scripted([query]*20+[act('ask_researcher',question='Continue?')]),max_calls=21)
    result = store.get(root)['payload']
    assert result['call_count'] == 21 and len(result['invocation_refs']) == 82
    assert store.verify(root)
    for ref,env in records(store):
        linked = [store.get(r)['kind'] for r in env['refs']]
        assert linked.count('agent_invocation') <= 1


def test_frozen_study_execution_is_reserved_before_entering_tool(tmp_path, monkeypatch):
    store,source,_ = fixture(tmp_path)
    config = {'training':[{'id':'train','role':'training','experiment':{'times':[0.,1.]},
              'values':{'response':[0.,1.]},'sigma':{'response':1.}}],
              'candidates':['cand_baseline'],'starts':1,'max_nfev':1,'timeout_s':1.}
    plan = studies.prepare_study(store,config)
    entered = []
    def interrupted(*args):
        entered.append(True)
        assert phase(store,'run_study','reserved')[0][1]['payload']['run_count'] == 1
        raise SystemExit('controlled study boundary, no fitting performed')
    monkeypatch.setattr(studies,'execute_study',interrupted)
    with pytest.raises(SystemExit):
        agent.run_agent(store,Scripted([act('run_study')]),[source],ExperimentSpec((0.,1.)),
                        mode='hypothesis',study_plan_ref=plan)
    assert entered == [True] and len(phase(store,'run_study','interrupted')) == 1


def _hard_exit_child(store_path, source, proposal, point):
    store = ArtifactStore(store_path)
    if point == 'provider':
        class ExitProvider:
            def respond(self, messages):
                os._exit(23)
        run(store,source,ExitProvider())
    else:
        def exit_after_result(*args):
            os._exit(24)
        agent._run_facts = exit_after_result
        run(store,source,Scripted([{'action':'submit_draft','args':proposal},act('run')]))


@pytest.mark.parametrize('point,code',[('provider',23),('post_result',24)])
def test_abrupt_process_exit_leaves_durable_boundary(tmp_path, point, code):
    store,source,proposal = fixture(tmp_path)
    process=mp.get_context('spawn').Process(target=_hard_exit_child,
        args=(str(store.root),source,proposal,point))
    process.start()
    process.join(timeout=20)
    if process.is_alive():
        process.terminate();process.join(timeout=3)
        pytest.fail('fault-injection child exceeded bounded wait')
    assert process.exitcode==code
    reopened=ArtifactStore(store.root)
    assert len(reopened.list('agent_execution'))==1
    if point=='provider':
        assert len(phase(reopened,'respond','reserved'))==1
        assert len(records(reopened))==1  # os._exit cannot run the exception handler
    else:
        result=reopened.list('run')
        assert len(result)==1
        assert result[0] in phase(reopened,'run','returned')[0][1]['refs']
    assert not reopened.list('agent_run')


def test_legacy_pause_without_journal_fields_can_start_a_new_recorded_execution(tmp_path):
    store,source,_=fixture(tmp_path)
    parent=run(store,source,Scripted([act('ask_researcher',question='Initial x?')]))
    previous=store.get(parent)
    cp=store.get(previous['payload']['checkpoint_ref'])
    for payload in (previous['payload'],cp['payload']):
        payload.pop('execution_ref',None);payload.pop('invocation_refs',None)
    cp_refs=[r for r in cp['refs'] if store.get(r)['kind'] not in ('agent_execution','agent_invocation')]
    legacy_cp=store.put('agent_checkpoint',cp['payload'],refs=cp_refs)
    previous['payload']['checkpoint_ref']=legacy_cp
    previous['payload']['checkpoint_refs']=[legacy_cp]
    run_refs=[r for r in previous['refs'] if store.get(r)['kind'] not in
              ('agent_execution','agent_invocation','agent_checkpoint')]
    legacy=store.put('agent_run',previous['payload'],refs=[*run_refs,legacy_cp])
    original=store.get(legacy)
    result=store.get(agent.resume_agent(store,Scripted([act('ask_researcher',question='Confirm?')]),
                legacy,'Initial x = 1.',respondent='A'))['payload']
    assert result['call_count']==2 and result['execution_ref']
    assert store.get(result['execution_ref'])['payload']['base_call_count']==1
    assert len(result['invocation_refs'])==2  # no invented records for the historical call
    assert store.get(legacy)==original


def test_journal_metadata_mismatch_blocks_resume_before_reply_or_provider(tmp_path):
    store,source,_=fixture(tmp_path)
    parent=run(store,source,Scripted([act('ask_researcher',question='Initial x?')]))
    record=store.get(parent)
    record['payload']['invocation_refs']=[]
    inconsistent=store.put('agent_run',record['payload'],refs=record['refs'])
    before=store.list()
    provider=Scripted([])
    with pytest.raises(ValueError,match='history'):
        agent.resume_agent(store,provider,inconsistent,'Initial x = 1.',respondent='A')
    assert not provider.calls and store.list()==before


def test_consumed_scientific_budget_is_inherited_after_pause(tmp_path):
    store,source,proposal=fixture(tmp_path)
    parent=run(store,source,Scripted([{'action':'submit_draft','args':proposal},act('run'),
                                      act('ask_researcher',question='Confirm result?')]))
    assert len(store.list('run'))==1
    result=store.get(agent.resume_agent(store,Scripted([act('run')]),parent,
                    'Initial x = 1.',respondent='A'))['payload']
    assert result['state']=='incomplete' and result['call_count']==4
    assert len(store.list('run'))==1 and len(phase(store,'run','reserved'))==1
    assert store.get(result['execution_ref'])['payload']['base_run_count']==1
