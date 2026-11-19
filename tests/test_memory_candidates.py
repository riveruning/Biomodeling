"""Explicit candidate review, activation, scope and retraction behavior."""
import json

import pytest

from biomodel_agent import agent, cli, evidence, memory, memory_candidates
from biomodel_agent.domain.contracts import ExperimentSpec
from biomodel_agent.storage import ArtifactStore


class Ask:
    last_usage = {}
    def respond(self, messages):
        return dict(action='ask_researcher', args=dict(question='What is initial x?'))


def setup(tmp_path):
    store = ArtifactStore(tmp_path / 'store')
    path = tmp_path / 'source.txt'
    path.write_text('Initial x is not reported.')
    source = evidence.ingest_source(store, path)
    run = agent.run_agent(store, Ask(), [source], ExperimentSpec((0., 1.)))
    candidate = memory_candidates.propose(store, run, project_id='project-a',
        author='operator', applicability='Deterministic decay models with missing initial conditions.')
    return store, source, run, candidate


def test_pending_and_approved_candidates_are_not_recalled_until_activation(tmp_path):
    store, source, run, candidate = setup(tmp_path)
    original = store.get(candidate)
    assert not memory.snapshot(store, project_id='project-a', source_refs=[source])['memory_refs']
    review = memory_candidates.review(store, candidate, decision='approved', reviewer='Reviewer A', reason='Reviewed the actual task trace.')
    assert not memory.snapshot(store, project_id='project-a', source_refs=[source])['memory_refs']
    ref = memory_candidates.activate(store, review)
    snapshot = memory.snapshot(store, project_id='project-a', source_refs=[source])
    assert snapshot['memory_refs'] == [ref]
    recalled = memory.recall(store, snapshot, 'initial', source_refs=[source])
    assert recalled['results'] and store.get(ref)['payload']['semantic_support'] == 'unknown'
    assert review in store.get(ref)['refs'] and run in original['refs']
    assert store.get(candidate) == original and 'Applicable when:' in store.get(ref)['payload']['content']
    # Project and source visibility remain enforced by existing snapshots.
    assert not memory.snapshot(store, project_id='project-b', source_refs=[source])['memory_refs']
    assert not memory.snapshot(store, project_id='project-a', source_refs=[])['memory_refs']
    with pytest.raises(ValueError, match='already activated'):
        memory_candidates.activate(store, review)
    memory.retract(store, ref, author='Reviewer A', reason='Experience no longer applies.')
    with pytest.raises(ValueError, match='stale'):
        memory.validate_snapshot(store, snapshot, [source])
    with pytest.raises(ValueError, match='already activated'):
        memory_candidates.activate(store, review)


def test_rejection_and_duplicate_review_cannot_activate_memory(tmp_path):
    store, source, run, candidate = setup(tmp_path)
    review = memory_candidates.review(store, candidate, decision='rejected', reviewer='Reviewer A', reason='Insufficient evidence.')
    assert memory_candidates.inspect(store, candidate)['review_status'] == 'rejected'
    with pytest.raises(ValueError, match='approved'):
        memory_candidates.activate(store, review)
    with pytest.raises(ValueError, match='already reviewed'):
        memory_candidates.review(store, candidate, decision='approved', reviewer='Reviewer B', reason='Try again.')
    assert not store.list(memory.KIND)


@pytest.mark.parametrize('field,value', [('applicability', ''), ('author', ''), ('project_id', '')])
def test_missing_attribution_and_conditions_rejected(tmp_path, field, value):
    store, source, run, candidate = setup(tmp_path)
    kwargs = dict(project_id='project-a', author='operator', applicability='Condition A')
    kwargs[field] = value
    before = store.list('memory_candidate')
    with pytest.raises(ValueError):
        memory_candidates.propose(store, run, **kwargs)
    assert store.list('memory_candidate') == before


def test_candidate_review_does_not_authorize_rewritten_memory(tmp_path):
    store, source, run, candidate = setup(tmp_path)
    review = memory_candidates.review(store, candidate, decision='approved', reviewer='Reviewer A', reason='Trace checked.')
    with pytest.raises(ValueError, match='does not authorize'):
        memory.remember(store, project_id='project-a', content='Invented parameter k = 99.',
                        category='experience', author='Reviewer A', source_refs=[source], candidate_review_ref=review)
    assert not store.list(memory.KIND)


def test_cli_candidate_and_progress_round_trip(tmp_path, capsys):
    store, source, run, candidate = setup(tmp_path)
    path = str(tmp_path / 'store')
    assert cli.main(['agent-progress', run, '--store', path]) == 0
    assert json.loads(capsys.readouterr().out)['state'] == 'awaiting_input'
    assert cli.main(['memory-candidate-inspect', candidate, '--store', path]) == 0
    assert json.loads(capsys.readouterr().out)['review_status'] == 'pending'
    assert cli.main(['memory-candidate-review', candidate, '--store', path, '--decision', 'approved',
                     '--reviewer', 'Reviewer A', '--reason', 'Reviewed trace.']) == 0
    review = json.loads(capsys.readouterr().out)['review_ref']
    assert cli.main(['memory-candidate-activate', review, '--store', path]) == 0
    assert json.loads(capsys.readouterr().out)['memory_ref']
