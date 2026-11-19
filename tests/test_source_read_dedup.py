"""Repeated source reads are reported, not silently repeated."""
from biomodel_agent import evidence, research_projects as projects
from biomodel_agent.storage import ArtifactStore


def _project(tmp_path):
    source_text = 'Equation (4): dX/dt = k1 - k2*X. ' * 20
    path = tmp_path / 'paper.txt'
    path.write_text(source_text, encoding='utf-8')
    store = ArtifactStore(tmp_path / 'store')
    source = evidence.ingest_source(store, path)
    project = projects.create(store, dict(mode='reproduction', source_refs=[source],
                                          scenario='Read the equation.',
                                          policy=dict(planner_max_calls=0)))
    return store, project, source


def test_identical_read_reports_repeat_and_no_new_characters(tmp_path):
    store, project, source = _project(tmp_path)
    args = dict(source_ref=source, page=1, start=10, end=60)
    first = projects.operate(store, project, 'inspect_source', args)
    second = projects.operate(store, project, 'inspect_source', args)
    assert first['read_ledger']['repeat_read'] is False
    assert second['read_ledger']['repeat_read'] is True
    assert second['read_ledger']['prior_span_occurrences'] == 1
    assert second['read_ledger']['new_characters'] == 0
    assert 'Do not call inspect_source for this span again' in second['diagnostic']
    assert second['text'] == first['text']


def test_new_span_is_not_flagged(tmp_path):
    store, project, source = _project(tmp_path)
    projects.operate(store, project, 'inspect_source',
                     dict(source_ref=source, page=1, start=0, end=40))
    result = projects.operate(store, project, 'inspect_source',
                              dict(source_ref=source, page=1, start=40, end=80))
    assert result['read_ledger']['repeat_read'] is False
    assert result['read_ledger']['new_characters'] == 40
    assert 'diagnostic' not in result
