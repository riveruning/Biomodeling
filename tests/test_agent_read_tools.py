"""Focused regressions for the extracted agent read/query actions.

All inference is scripted and all read tools are local fakes or real local
storage; no network or model service is used.
"""
import json

import pytest

from biomodel_agent import (
    agent, agent_read_tools, knowledge, memory, parameter_investigation,
    pdf_adapter, retrieval, skills,
)
from test_invocation_acceptance import Scripted, act, fixture, phase, run as run_agent


class StoreStub:
    def __init__(self, pages=()):
        self.pages = list(pages)

    def get(self, ref):
        return {'payload': {'pages': self.pages}}


class InvokeSpy:
    def __init__(self, error=None):
        self.error = error
        self.calls = []
        self.values = []

    def __call__(self, name, callback, **kwargs):
        self.calls.append((name, kwargs))
        if self.error is not None:
            raise self.error
        value = callback()
        self.values.append(value)
        return value


def content(outcome):
    return json.loads(outcome.message['content'])


def execute(action, args, *, invoke=None, store=None, source_refs=('src',),
            read_limit=8, knowledge_databases=(), memory_scope=None,
            skill_catalog=(), skill_protocol=None, inspect_progress=None,
            source_text=None, call_count=None):
    return agent_read_tools.execute_read_tool(
        action, args, store=store if store is not None else StoreStub(),
        source_refs=source_refs, retrieval_method='lexical_v1',
        knowledge_databases=knowledge_databases, memory_scope=memory_scope,
        skill_catalog=skill_catalog, skill_protocol=skill_protocol,
        read_limit=read_limit, invoke=invoke or InvokeSpy(),
        inspect_progress=inspect_progress or (lambda: {}),
        source_text=source_text or (lambda ref, page: None),
        call_count=call_count)


def test_inspect_progress_evaluates_callback_inside_invoke_with_checkpoint():
    feed = {'state': 'running'}
    seen = []

    def progress():
        seen.append(dict(feed))
        return {'state': feed['state'], 'calls': 1}

    class DelayedInvoke(InvokeSpy):
        def __call__(self, name, callback, **kwargs):
            feed['state'] = 'updated'
            return super().__call__(name, callback, **kwargs)

    invoke = DelayedInvoke()
    outcome = execute('inspect_progress', {}, invoke=invoke, inspect_progress=progress)
    assert seen == [{'state': 'updated'}]
    assert invoke.calls == [('inspect_progress', {})]
    assert outcome.event == {'type': 'tool_result', 'tool': 'inspect_progress',
                             'artifact_refs': []}
    assert content(outcome) == {'tool': 'inspect_progress', 'ok': True,
                                'state': 'updated', 'calls': 1}
    assert outcome.checkpoint is True and outcome.stop is False and outcome.state is None


def test_investigate_parameter_feedback_event_and_invoke_contract(monkeypatch):
    found = {'investigation_ref': 'investigation-1', 'retrieval': {'results': []},
             'source_review': {'issues': []}, 'next_action': 'provide value'}

    def investigate(store, sources, target, query, method=None):
        assert sources == ('src',) and target == 'k/value' and query == 'k'
        assert method == 'lexical_v1'
        return found

    monkeypatch.setattr(parameter_investigation, 'investigate', investigate)
    invoke = InvokeSpy()
    outcome = execute('investigate_parameter', {'target': 'k/value', 'query': 'k'},
                      invoke=invoke)
    name, kwargs = invoke.calls[0]
    assert name == 'investigate_parameter'
    assert kwargs['details'] == {'args': {'target': 'k/value', 'query': 'k'}}
    assert kwargs['result_refs'](found) == ['investigation-1']
    assert outcome.event == {'type': 'tool_result', 'tool': 'investigate_parameter',
                             'artifact_refs': ['investigation-1']}
    assert content(outcome) == {'tool': 'investigate_parameter', 'ok': True, **found}
    assert outcome.checkpoint is True and outcome.stop is False


def test_search_knowledge_feedback_event_and_artifact_ref(monkeypatch):
    found = {'search_ref': 'search-1', 'query': 'x', 'results': [{'title': 'hit'}]}
    monkeypatch.setattr(knowledge, 'search', lambda store, database, query: found)
    invoke = InvokeSpy()
    outcome = execute('search_knowledge', {'database': 'db', 'query': 'x'},
                      invoke=invoke, knowledge_databases=('db',))
    name, kwargs = invoke.calls[0]
    assert name == 'search_knowledge'
    assert kwargs['details'] == {'args': {'database': 'db', 'query': 'x'}}
    assert kwargs['result_refs'](found) == ('search-1',)
    assert outcome.event == {'type': 'tool_result', 'tool': 'search_knowledge',
                             'search_ref': 'search-1', 'artifact_refs': ['search-1']}
    assert content(outcome) == {'tool': 'search_knowledge', 'ok': True, **found}
    assert outcome.checkpoint is True and outcome.stop is False


def test_search_sources_filters_payload_text_from_event_not_feedback(monkeypatch):
    found = {'query': 'k', 'results': [{
        'source_ref': 'src', 'page': 1, 'title': 'hit', 'score': 1.0,
        'text': 'private excerpt', 'content': 'private content'}]}
    monkeypatch.setattr(retrieval, 'search_sources',
                        lambda store, sources, query, method=None: found)
    invoke = InvokeSpy()
    outcome = execute('search_sources', {'query': 'k'}, invoke=invoke)
    name, kwargs = invoke.calls[0]
    assert name == 'search_sources'
    assert kwargs == {'details': {'args': {'query': 'k'}}}
    assert outcome.event == {
        'type': 'tool_result', 'tool': 'search_sources', 'ok': True, 'query': 'k',
        'results': [{'source_ref': 'src', 'page': 1, 'title': 'hit', 'score': 1.0}]}
    assert content(outcome) == {'tool': 'search_sources', 'ok': True, **found}
    assert outcome.checkpoint is True and outcome.stop is False


def test_recall_memory_filters_steps_and_payload_text(monkeypatch):
    found = {
        'results': [{'memory_ref': 'memory-1', 'title': 'hit',
                     'text': 'private excerpt', 'content': 'private content'}],
        'steps': [{'id': 'recall'}], 'memory_refs': ['memory-1']}
    monkeypatch.setattr(memory, 'recall',
                        lambda store, scope, query, source_refs=None: found)
    invoke = InvokeSpy()
    outcome = execute('recall_memory', {'query': 'x'}, invoke=invoke,
                      memory_scope={'project_id': 'p'})
    name, kwargs = invoke.calls[0]
    assert name == 'recall_memory'
    assert kwargs == {'details': {'args': {'query': 'x'}}}
    assert outcome.event == {
        'type': 'tool_result', 'tool': 'recall_memory', 'ok': True,
        'memory_refs': ['memory-1'],
        'results': [{'memory_ref': 'memory-1', 'title': 'hit'}]}
    assert content(outcome) == {'tool': 'recall_memory', 'ok': True, **found}
    assert outcome.checkpoint is True and outcome.stop is False


def test_load_skill_filters_steps_and_keeps_scientific_skill_ref(monkeypatch):
    found = {'name': 'ode_reproduction', 'version': '1',
             'steps': [{'id': 'read'}], 'scientific_skill_ref': 'skill-1'}
    monkeypatch.setattr(skills, 'load_skill', lambda name: found)
    invoke = InvokeSpy()
    outcome = execute('load_skill', {'name': 'ode_reproduction'}, invoke=invoke,
                      skill_catalog=[{'name': 'ode_reproduction', 'version': '1'}])
    name, kwargs = invoke.calls[0]
    assert name == 'load_skill'
    assert kwargs['details'] == {'args': {'name': 'ode_reproduction'}}
    assert kwargs['result_refs'](found) == ['skill-1']
    assert outcome.event == {
        'type': 'tool_result', 'tool': 'load_skill', 'ok': True,
        'name': 'ode_reproduction', 'version': '1',
        'scientific_skill_ref': 'skill-1', 'artifact_refs': ['skill-1']}
    assert content(outcome) == {'tool': 'load_skill', 'ok': True, **found}
    assert outcome.checkpoint is True and outcome.stop is False


def test_inspect_source_success_filters_text_and_skips_checkpoint(monkeypatch):
    locator = {'page': 1, 'start': 2, 'end': 5}
    monkeypatch.setattr(pdf_adapter, 'locate', lambda store, ref, page, start, end: locator)
    invoke = InvokeSpy()
    args = {'source_ref': 'src', 'page': 1, 'start': 2, 'end': 5}
    outcome = execute('inspect_source', args, invoke=invoke,
                      source_text=lambda ref, page: '0123456789')
    name, kwargs = invoke.calls[0]
    assert name == 'inspect_source'
    assert kwargs['details'] == {'args': args}
    assert kwargs['summarize']('0123456789') == {'page_found': True}
    assert kwargs['summarize'](None) == {'page_found': False}
    expected = {'tool': 'inspect_source', 'ok': True, 'source_ref': 'src',
                'page': 1, 'start': 2, 'end': 5, 'length': 3, 'text': '234',
                'locator': locator}
    assert content(outcome) == expected
    assert outcome.event == {
        'type': 'tool_result', 'tool': 'inspect_source', 'ok': True,
        'source_ref': 'src', 'page': 1, 'start': 2, 'end': 5, 'length': 3,
        'locator': locator}
    assert outcome.checkpoint is False and outcome.stop is False


@pytest.mark.parametrize('args,error', [
    ({'page': 1, 'start': 0, 'end': 1},
     'inspect_source args must be exactly source_ref,page,start,end'),
    ({'source_ref': 'src', 'page': 1, 'start': 0, 'end': 1, 'extra': True},
     'inspect_source args must be exactly source_ref,page,start,end'),
    ({'source_ref': 'other', 'page': 1, 'start': 0, 'end': 1},
     'source/page/start/end outside task bounds'),
    pytest.param({'source_ref': 'src', 'page': True, 'start': 0, 'end': 1},
                 'source/page/start/end outside task bounds', id='bool-page'),
    pytest.param({'source_ref': 'src', 'page': 1, 'start': False, 'end': 1},
                 'source/page/start/end outside task bounds', id='bool-start'),
    pytest.param({'source_ref': 'src', 'page': 1, 'start': 0, 'end': True},
                 'source/page/start/end outside task bounds', id='bool-end'),
    ({'source_ref': 'src', 'page': 1, 'start': -1, 'end': 1},
     'source/page/start/end outside task bounds'),
    ({'source_ref': 'src', 'page': 1, 'start': 1, 'end': 1},
     'source/page/start/end outside task bounds'),
    ({'source_ref': 'src', 'page': 1, 'start': 0, 'end': 9},
     'source/page/start/end outside task bounds'),
])
def test_inspect_source_parameter_errors_stop_without_invoking(args, error):
    invoke = InvokeSpy()
    outcome = execute('inspect_source', args, invoke=invoke, read_limit=8)
    assert invoke.calls == []
    assert outcome.stop is True and outcome.state == 'failed'
    assert outcome.checkpoint is False
    assert outcome.event == {'type': 'tool_error', 'tool': 'inspect_source',
                             'error': error}
    assert content(outcome) == {'tool': 'inspect_source', 'ok': False, 'error': error}


def test_inspect_source_invalid_span_is_retryable_without_checkpoint():
    invoke = InvokeSpy()
    store = StoreStub([{'number': 1, 'text': 'abc'}])
    outcome = execute('inspect_source',
                      {'source_ref': 'src', 'page': 1, 'start': 0, 'end': 4},
                      invoke=invoke, store=store,
                      source_text=lambda ref, page: 'abc', call_count=lambda: 7)
    assert [call[0] for call in invoke.calls] == ['inspect_source']
    assert outcome.stop is False and outcome.checkpoint is False
    expected = {'type': 'tool_error', 'tool': 'inspect_source',
                'error': 'invalid source span',
                'available_pages': [{'page': 1, 'chars': 3}]}
    diagnostic=outcome.event['diagnostic']
    assert set(diagnostic)=={'error_type','what_engine_supports','page_characters',
                             'max_read_characters','suggested_fix','example'}
    assert diagnostic['error_type']=='invalid_source_span'
    assert diagnostic['page_characters']==3 and diagnostic['max_read_characters']==8
    assert diagnostic['example']==dict(source_ref='src',page=1,start=0,end=3)
    assert diagnostic['what_engine_supports'] and diagnostic['suggested_fix']
    expected['diagnostic']=diagnostic
    assert outcome.event == expected
    assert content(outcome) == {**expected, 'call': 7}


def test_inspect_source_nonpositive_page_is_checked_after_the_read():
    invoke = InvokeSpy()
    store = StoreStub([{'number': 1, 'text': 'abc'}])
    outcome = execute('inspect_source',
                      {'source_ref': 'src', 'page': 0, 'start': 0, 'end': 1},
                      invoke=invoke, store=store,
                      source_text=lambda ref, page: 'abc')
    assert [call[0] for call in invoke.calls] == ['inspect_source']
    assert outcome.stop is False and outcome.checkpoint is False
    assert outcome.event['type'] == 'tool_error'
    assert outcome.event['error'] == 'invalid source span'


def test_value_error_feedback_differs_from_inspect_source_exception(monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError('lookup failed')

    monkeypatch.setattr(retrieval, 'search_sources', fail)
    outcome = execute('search_sources', {'query': 'x'}, invoke=InvokeSpy())
    assert outcome.checkpoint is True and outcome.stop is False
    assert outcome.event == {'type': 'tool_error', 'tool': 'search_sources',
                             'ok': False, 'error': 'lookup failed'}
    assert content(outcome) == {'tool': 'search_sources', 'ok': False,
                                'error': 'lookup failed'}

    def source_text(ref, page):
        raise ValueError('source read failed')

    with pytest.raises(ValueError, match='source read failed'):
        execute('inspect_source',
                {'source_ref': 'src', 'page': 1, 'start': 0, 'end': 1},
                source_text=source_text)


def test_inspect_progress_callback_sees_live_loop_counts_and_events(tmp_path, monkeypatch):
    store, source, _ = fixture(tmp_path)
    snapshots = []

    def project(_store, **kwargs):
        if not snapshots:
            snapshots.append({
                'calls': kwargs['calls'], 'state': kwargs['state'],
                'event_types': [event['type'] for event in kwargs['events']]})
        return {'state': kwargs.get('state'), 'calls': kwargs.get('calls')}

    monkeypatch.setattr(agent.workstate, 'project', project)
    provider = Scripted([act('inspect_progress'),
                         act('ask_researcher', question='Continue?')])
    root = run_agent(store, source, provider, max_calls=2)
    result = store.get(root)['payload']
    assert result['state'] == 'awaiting_input'
    assert snapshots == [{'calls': 1, 'state': 'running',
                          'event_types': ['llm_call']}]


def test_inspect_source_success_writes_only_final_checkpoint(tmp_path):
    store, source, _ = fixture(tmp_path)
    provider = Scripted([
        act('inspect_source', source_ref=source, page=1, start=0, end=10),
        act('ask_researcher', question='Continue?')])
    root = run_agent(store, source, provider, max_calls=2)
    result = store.get(root)['payload']
    assert result['state'] == 'awaiting_input' and result['call_count'] == 2
    read_event = next(event for event in result['events']
                      if event.get('tool') == 'inspect_source')
    assert read_event['type'] == 'tool_result' and read_event['call'] == 1
    assert len(store.list('agent_checkpoint')) == 1
    feedback = json.loads(provider.calls[1][-1]['content'])
    assert feedback['tool'] == 'inspect_source' and feedback['text'] == 'Initial x '


def test_inspect_source_invalid_span_invokes_once_without_branch_checkpoint(tmp_path):
    store, source, _ = fixture(tmp_path)
    provider = Scripted([
        act('inspect_source', source_ref=source, page=1, start=0, end=100),
        act('ask_researcher', question='Continue?')])
    root = run_agent(store, source, provider, max_calls=2)
    result = store.get(root)['payload']
    assert result['state'] == 'awaiting_input' and result['call_count'] == 2
    error = next(event for event in result['events']
                 if event.get('tool') == 'inspect_source')
    assert error['type'] == 'tool_error' and error['call'] == 1
    assert error['error'] == 'invalid source span'
    feedback = json.loads(provider.calls[1][-1]['content'])
    assert feedback['call'] == 1 and feedback['error'] == 'invalid source span'
    assert len(store.list('agent_checkpoint')) == 1
    assert len(phase(store, 'inspect_source', 'reserved')) == 1
    assert len(phase(store, 'inspect_source', 'returned')) == 1


def test_inspect_source_parameter_error_stops_without_invoking_or_branch_checkpoint(tmp_path):
    store, source, _ = fixture(tmp_path)
    provider = Scripted([act('inspect_source', page=1, start=0, end=1)])
    root = run_agent(store, source, provider)
    result = store.get(root)['payload']
    assert result['state'] == 'failed' and result['call_count'] == 1
    assert result['events'][-1]['type'] == 'tool_error'
    assert result['events'][-1]['call'] == 1
    assert not phase(store, 'inspect_source', 'reserved')
    assert len(store.list('agent_checkpoint')) == 1


def test_inspect_source_bool_coordinate_is_rejected_before_read(tmp_path):
    store, source, _ = fixture(tmp_path)
    provider = Scripted([
        act('inspect_source', source_ref=source, page=True, start=0, end=1)])
    root = run_agent(store, source, provider)
    result = store.get(root)['payload']
    assert result['state'] == 'failed' and result['call_count'] == 1
    assert result['events'][-1]['error'] == 'source/page/start/end outside task bounds'
    assert not phase(store, 'inspect_source', 'reserved')
    assert len(store.list('agent_checkpoint')) == 1


def test_checkpointed_read_action_saves_branch_checkpoint_before_next_turn(tmp_path):
    store, source, _ = fixture(tmp_path)
    provider = Scripted([
        act('search_sources', query='parameter k'),
        act('ask_researcher', question='Continue?')])
    root = run_agent(store, source, provider, max_calls=2)
    result = store.get(root)['payload']
    checkpoints = [store.get(ref)['payload'] for ref in store.list('agent_checkpoint')]
    assert result['state'] == 'awaiting_input' and len(checkpoints) == 2
    branch = next(row for row in checkpoints if row['call_count'] == 1)
    assert branch['state'] == 'running'
    assert branch['events'][-1]['type'] == 'tool_result'
    assert branch['events'][-1]['tool'] == 'search_sources'
    assert branch['events'][-1]['call'] == 1
