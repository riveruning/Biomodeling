from copy import deepcopy

import pytest

from biomodel_agent import timeline
from biomodel_agent.storage import ArtifactStore


def test_saved_failures_unknown_and_proposals_are_not_promoted():
    payload = dict(state='completed', call_count=6, run_count=1, events=[
        dict(type='llm_call', response=dict(action='finish', reason='I am done')),
        dict(type='tool_result', tool='run', ok=True, checks=[dict(status='fail')]),
        dict(type='tool_result', tool='run', ok=True, outcome=dict(checks=[dict(status='unknown')])),
        dict(type='tool_result', tool='run', ok=True, outcome=dict(completion_eligible=False)),
        dict(type='awaiting_input', question='Which parameter?'),
        dict(type='custom'), None])
    before = deepcopy(payload)
    view = timeline.project(payload, root_ref='a'*64, kind='agent_run')
    assert [r['status'] for r in view['rows']] == ['proposed', 'failed', 'unknown', 'blocked', 'waiting', 'unknown', 'unknown']
    assert view['scientific_support'] == 'unknown' and payload == before
    assert len(view['rows']) == len(payload['events'])


@pytest.mark.parametrize('kind', sorted(timeline.KINDS))
def test_all_supported_run_kinds_read_without_new_records(tmp_path, kind):
    store = ArtifactStore(tmp_path / 'store')
    ref = store.put(kind, dict(state='incomplete', events=[]))
    before = store.list()
    view = timeline.from_run(store, ref)
    assert view['kind'] == kind and view['call_count'] is None and view['run_count'] is None
    assert store.list() == before


def test_refs_filtered_and_full_model_response_not_echoed():
    ref = 'a'*64
    view = timeline.project(dict(state='incomplete', call_count=True, events=[
        dict(type='tool_result', artifact_refs=[ref, ref, '../outside', 123]),
        dict(type='llm_call', response=dict(action='inspect', args={'secret': 'DO_NOT_ECHO'}))]),
        root_ref=ref, kind='typed_agent_run')
    assert view['call_count'] is None
    assert view['rows'][0]['artifact_refs'] == [ref]
    assert 'DO_NOT_ECHO' not in timeline.render_markdown(view)


def test_markdown_preserves_bounded_error_and_escapes_content():
    view = timeline.project(dict(state='incomplete', events=[
        dict(type='tool_error', error='a|b\n<script>\x00' + 'x'*1000)]), root_ref='a'*64, kind='agent_checkpoint')
    assert len(view['rows'][0]['summary']) == 500
    text = timeline.render_markdown(view)
    assert 'a\\|b<br>&lt;script&gt;' in text and '\x00' not in text
    assert text.endswith('\n') and 'not timestamps' in text


@pytest.mark.parametrize('events', [None, {}, 'events'])
def test_invalid_saved_events_are_not_invented(events):
    with pytest.raises(ValueError):
        timeline.project(dict(events=events), root_ref='a'*64, kind='agent_run')


def test_unknown_record_kind_rejected(tmp_path):
    store = ArtifactStore(tmp_path / 'store'); ref = store.put('claim', dict(events=[]))
    with pytest.raises(ValueError): timeline.from_run(store, ref)


def test_report_cells_disclose_truncation_and_preserve_markdown_symbols():
    text = timeline.cell('a'*4000)
    assert 'truncated' in text and len(text) < 3100
    assert timeline.cell(r'a\|b') == r'a\\\|b'
    assert timeline.cell('-k*x + k*y') == r'-k\*x + k\*y'
    assert timeline.cell('`claim` [link]') == r'\`claim\` \[link\]'
