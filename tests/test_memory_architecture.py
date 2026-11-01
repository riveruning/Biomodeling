"""Memory lifecycle contracts using synthetic tasks; no model-effect benchmark."""
from copy import deepcopy
import json

import pytest

from biomodel_agent import agent, bundles, cli, evidence, jats_sources, memory, memory_candidates
from biomodel_agent.domain.contracts import ExperimentSpec
from biomodel_agent.storage import ArtifactStore, ArtifactError


CONTEXT = dict(model_type='ode', task_kind='reproduction', condition='A')


@pytest.fixture
def env(tmp_path):
    store = ArtifactStore(tmp_path / 'store')
    path = tmp_path / 'source.txt'
    path.write_text('Initial x is not reported. Parameter k is unknown.')
    return store, evidence.ingest_source(store, path)


def add(store, source, **kwargs):
    fields = dict(project_id='p', source_refs=[source], category='experience',
                  author='Operator', content='Check initial x against the current sources.', conditions=CONTEXT)
    fields.update(kwargs)
    return memory.remember(store, **fields)


def scope(store, source, **kwargs):
    fields = dict(project_id='p', source_refs=[source], context=CONTEXT)
    fields.update(kwargs)
    return memory.snapshot(store, **fields)


class Scripted:
    last_usage = {}

    def __init__(self, *actions):
        self.actions = iter(actions or [dict(action='ask_researcher', args=dict(question='What is initial x?'))])
        self.calls = []

    def respond(self, messages):
        self.calls.append(deepcopy(messages))
        return next(self.actions)


def run(store, source, provider=None, **kwargs):
    fields = dict(memory_project='p', memory_context={'condition': 'A'}, experience_proposals=True)
    fields.update(kwargs)
    provider = provider or Scripted()
    ref = agent.run_agent(store, provider, [source], ExperimentSpec((0., 1.)), **fields)
    return ref, provider


def test_context_is_explicit_and_legacy_experience_does_not_gain_new_scope(env):
    store, source = env
    legacy = add(store, source, conditions=None)
    pref = add(store, source, category='preference', conditions=None)
    exact = add(store, source)
    assert set(scope(store, source, context=None)['memory_refs']) == {legacy, pref}
    assert set(scope(store, source)['memory_refs']) == {pref, exact}
    assert scope(store, source, context={**CONTEXT, 'condition': 'B'})['memory_refs'] == [pref]
    assert scope(store, source, context={'model_type': 'ode'})['memory_refs'] == [pref]
    result = memory.recall(store, scope(store, source), 'initial', source_refs=[source])
    hit = next(i for i in result['results'] if i['memory_ref'] == exact)
    assert hit['conditions'] == CONTEXT and hit['depends_on'] == []
    assert result['context'] == CONTEXT and hit['semantic_support'] == 'unknown'


@pytest.mark.parametrize('invalid', [{}, {'condition': ''}, {'condition': 37}, {'bad-key': 'A'},
                                   {'condition': 'A' * 129}, {f'k{i}': 'A' for i in range(17)}])
def test_invalid_conditions_do_not_create_memory(env, invalid):
    store, source = env
    with pytest.raises(ValueError):
        add(store, source, conditions=invalid)
    assert store.list(memory.KIND) == []


def test_dependencies_require_same_project_sources_and_declared_conditions(env):
    store, source = env
    parent = add(store, source)
    for changes in [dict(project_id='other'), dict(source_refs=[]), dict(conditions=None),
                    dict(conditions={**CONTEXT, 'condition': 'B'})]:
        with pytest.raises(ValueError, match='dependency'):
            add(store, source, depends_on=[parent], **changes)
    child = add(store, source, depends_on=[parent], conditions={**CONTEXT, 'organism': 'synthetic'})
    assert child in scope(store, source, context={**CONTEXT, 'organism': 'synthetic'})['memory_refs']
    assert child not in scope(store, source)['memory_refs']
    with pytest.raises(ValueError, match='supersedes'):
        add(store, source, depends_on=[parent], supersedes=parent, reason='Would create stale lineage.')


@pytest.mark.parametrize('change', ['retract', 'update', 'invalidate_memory', 'invalidate_source', 'invalidate_model'])
def test_dependency_changes_propagate_and_reject_frozen_snapshots(env, change):
    store, source = env
    model = store.put('model', {'synthetic_identity_only': True})
    parent = add(store, source, model_ref=model)
    child = add(store, source, model_ref=model, depends_on=[parent])
    grandchild = add(store, source, model_ref=model, depends_on=[child])
    frozen = scope(store, source, model_ref=model)
    old = store.get(parent)
    if change == 'retract':
        memory.retract(store, parent, author='Operator', reason='Advice withdrawn.')
    elif change == 'update':
        add(store, source, model_ref=model, supersedes=parent, reason='Correction.')
    else:
        target = {'invalidate_memory': parent, 'invalidate_source': source, 'invalidate_model': model}[change]
        memory.invalidate_dependency(store, 'p', target, author='Operator', reason='Explicit correction.')
    assert store.get(parent) == old
    active = scope(store, source, model_ref=model)['memory_refs']
    assert child not in active and grandchild not in active
    with pytest.raises(ValueError, match='stale'):
        memory.validate_snapshot(store, frozen, [source])
    with pytest.raises(ValueError, match='dependency'):
        add(store, source, model_ref=model, depends_on=[child])


def test_new_source_does_not_infer_a_correction_and_invalidation_is_project_local(env, tmp_path):
    store, source = env
    a = add(store, source)
    b = add(store, source, project_id='other')
    path = tmp_path / 'correction.txt'; path.write_text('A new document is not an implicit withdrawal.')
    evidence.ingest_source(store, path)
    assert a in scope(store, source)['memory_refs']
    receipt = memory.invalidate_dependency(store, 'p', source, author='Operator', reason='Source explicitly withdrawn for p.')
    assert scope(store, source)['memory_refs'] == []
    assert b in scope(store, source, project_id='other')['memory_refs']
    assert store.get(receipt)['refs'] == [source]
    with pytest.raises(ValueError, match='cross projects'):
        memory.invalidate_dependency(store, 'other', a, author='Operator', reason='Wrong project.')


def test_post_run_projection_has_no_provider_call_or_automatic_activation(env):
    store, source = env
    ref, provider = run(store, source)
    before = store.get(ref)
    receipts = memory_candidates.derivations(store, ref)
    assert len(receipts) == 1
    receipt = store.get(receipts[0]); candidate = receipt['payload']['candidate_ref']
    assert receipt['payload']['provider_calls'] == 0 and len(provider.calls) == 1
    assert receipt['payload']['status'] == 'candidate_pending'
    assert ref in receipt['refs'] and ref in store.get(candidate)['refs']
    assert candidate not in before['refs'] and candidate not in json.dumps(provider.calls)
    assert memory_candidates.derive_for_run(store, ref) == receipts[0]
    assert len(store.list('memory_candidate')) == 1
    assert not store.list('memory_candidate_review') and not scope(store, source)['memory_refs']
    review = memory_candidates.review(store, candidate, decision='approved', reviewer='Operator', reason='Workflow trace checked.')
    assert not scope(store, source)['memory_refs']
    activated = memory_candidates.activate(store, review)
    assert scope(store, source)['memory_refs'] == [activated]
    assert memory_candidates.inspect(store, candidate)['conditions'] == CONTEXT
    assert store.get(ref) == before


def test_actual_recalled_advice_is_a_candidate_dependency_and_can_expire_after_review(env):
    store, source = env
    parent = add(store, source)
    provider = Scripted(dict(action='recall_memory', args=dict(query='initial')),
                        dict(action='ask_researcher', args=dict(question='What is initial x?')))
    ref, _ = run(store, source, provider)
    receipt = store.get(memory_candidates.derivations(store, ref)[0])['payload']
    candidate = receipt['candidate_ref']
    assert memory_candidates.inspect(store, candidate)['depends_on'] == [parent]
    review = memory_candidates.review(store, candidate, decision='approved', reviewer='Operator', reason='Trace checked.')
    memory.retract(store, parent, author='Operator', reason='Incorrect advice.')
    assert memory_candidates.inspect(store, candidate)['dependency_status'] == 'stale_or_out_of_scope'
    with pytest.raises(ValueError, match='changed after review'):
        memory_candidates.activate(store, review)


def test_resume_rejects_transitive_invalidation_before_reply_write_and_call(env):
    store, source = env
    parent = add(store, source)
    add(store, source, depends_on=[parent])
    ref, _ = run(store, source)
    memory.invalidate_dependency(store, 'p', source, author='Operator', reason='Corrected source.')
    before = store.list(); provider = Scripted()
    with pytest.raises(ValueError, match='stale'):
        agent.resume_agent(store, provider, ref, 'Initial x = 1.', respondent='Operator')
    assert store.list() == before and not provider.calls


def test_resumed_task_keeps_opt_in_policy_and_derives_separate_receipt(env):
    store, source = env
    parent, _ = run(store, source)
    child = agent.resume_agent(store, Scripted(), parent, 'Still unknown.', respondent='Operator')
    result = store.get(child)['payload']
    policy = store.get(result['task_ref'])['payload']['experience_policy']
    assert policy['conditions'] == CONTEXT
    assert len(memory_candidates.derivations(store, parent)) == len(memory_candidates.derivations(store, child)) == 1
    assert parent != child


def test_context_cannot_relabel_actual_agent_task_and_opt_in_needs_project(env):
    store, source = env
    for kwargs in [dict(memory_project=None), dict(memory_context={'model_type': 'fba'}),
                   dict(memory_context={'task_kind': 'hypothesis'}), dict(mode='hypothesis')]:
        with pytest.raises(ValueError):
            run(store, source, **kwargs)
    assert not store.list('agent_task')


def test_default_run_does_not_derive_and_backfill_cannot_enable_frozen_task(env):
    store, source = env
    ref = agent.run_agent(store, Scripted(), [source], ExperimentSpec((0., 1.)))
    assert not memory_candidates.derivations(store, ref)
    with pytest.raises(ValueError, match='not enabled'):
        memory_candidates.derive_for_run(store, ref)


def test_no_issue_derivation_receipt_does_not_invent_experience(env):
    store, source = env
    ref, _ = run(store, source, Scripted(dict(action='finish', args={})))
    receipt = store.get(memory_candidates.derivations(store, ref)[0])['payload']
    assert receipt['status'] == 'not_applicable_no_recorded_issue'
    assert receipt['candidate_ref'] is None and not store.list('memory_candidate')


def test_missing_receipt_can_be_backfilled_without_rerunning_provider(env, monkeypatch):
    store, source = env
    derive = memory_candidates.derive_for_run
    monkeypatch.setattr(memory_candidates, 'derive_for_run', lambda *args: None)
    ref, provider = run(store, source)
    assert not memory_candidates.derivations(store, ref)
    monkeypatch.setattr(memory_candidates, 'derive_for_run', derive)
    receipt = derive(store, ref)
    assert receipt and len(provider.calls) == 1


def test_recovery_adopts_saved_response_and_keeps_proposal_out_of_original_calls(env, monkeypatch):
    store, source = env
    original = Scripted()
    with monkeypatch.context() as patch:
        patch.setattr(agent, '_assistant', lambda *args: (_ for _ in ()).throw(SystemExit('saved response')))
        with pytest.raises(SystemExit):
            run(store, source, original, max_calls=1)
    execution = store.list('agent_execution')[0]
    provider = Scripted()
    recovered = agent.recover_agent(store, provider, execution)
    result = store.get(recovered)['payload']
    assert result['state'] == 'awaiting_input' and result['call_count'] == 1
    assert len(original.calls) == 1 and not provider.calls
    assert len(memory_candidates.derivations(store, recovered)) == 1


def test_projection_error_does_not_erase_committed_run_or_activate_advice(env, monkeypatch):
    store, source = env
    def fail(*args):
        raise ValueError('synthetic derivation fault')
    monkeypatch.setattr(memory_candidates, 'derive_for_run', fail)
    ref, provider = run(store, source)
    assert store.get(ref)['payload']['state'] == 'awaiting_input' and len(provider.calls) == 1
    receipt = store.get(memory_candidates.derivations(store, ref)[0])['payload']
    assert receipt['status'] == 'derivation_failed' and 'synthetic' in receipt['error']
    assert not store.list('memory_candidate') and not store.list(memory.KIND)


def test_export_derivation_keeps_links_and_pending_state(env, tmp_path):
    store, source = env
    ref, _ = run(store, source)
    receipt = memory_candidates.derivations(store, ref)[0]
    target = tmp_path / 'bundle'
    manifest = bundles.export_bundle(store, receipt, target)
    assert ref in manifest['objects_and_blobs']
    reopened = ArtifactStore(target)
    assert memory_candidates.derivations(reopened, ref) == [receipt]
    candidate = reopened.get(receipt)['payload']['candidate_ref']
    assert memory_candidates.inspect(reopened, candidate)['review_status'] == 'pending'


def test_cli_context_invalidation_and_receipt_discovery(env, tmp_path, capsys):
    store, source = env
    ref, _ = run(store, source)
    common = ['--store', str(tmp_path / 'store')]
    assert cli.main(['experience-list', '--run-ref', ref, *common]) == 0
    first = json.loads(capsys.readouterr().out)['derivations'][0]['ref']
    assert cli.main(['experience-derive', '--run-ref', ref, *common]) == 0
    assert json.loads(capsys.readouterr().out)['derivation_ref'] == first
    path = tmp_path / 'context.json'; path.write_text(json.dumps(CONTEXT))
    text = tmp_path / 'advice.txt'; text.write_text('Check initial x in sources.')
    assert cli.main(['memory-add', *common, '--project', 'p', '--content-file', str(text),
                     '--category', 'experience', '--author', 'Operator', '--source-ref', source,
                     '--conditions', str(path)]) == 0
    added = json.loads(capsys.readouterr().out)['memory_ref']
    assert cli.main(['memory-search', *common, '--project', 'p', '--source-ref', source,
                     '--context', str(path), '--query', 'initial']) == 0
    assert json.loads(capsys.readouterr().out)['results'][0]['memory_ref'] == added
    assert cli.main(['memory-invalidate', source, *common, '--project', 'p', '--author', 'Operator',
                     '--reason', 'Source withdrawn.']) == 0
    assert json.loads(capsys.readouterr().out)['invalidation_ref']
    assert not scope(store, source)['memory_refs']


def test_agent_source_summary_accepts_jats_without_pdf_page_fields(env, tmp_path):
    store, _ = env
    path = tmp_path / 'source.xml'
    path.write_text('<article><sec id="selected"><p>Initial x is not reported.</p></sec></article>')
    source = jats_sources.ingest(store, path, 'selected', {})
    assert 'status' not in store.get(source)['payload']['pages'][0]
    summary = evidence.source_summary(store, source)
    assert summary['parser'].startswith('jats')
    assert summary['page_status'][0]['status'] == 'unknown'
    ref, provider = run(store, source)
    assert store.get(ref)['payload']['state'] == 'awaiting_input' and len(provider.calls) == 1


def test_unknown_structured_parser_cannot_borrow_pdf_locator(env):
    store, source = env
    obj = store.get(source)
    extension = store.put('parser_output', {'synthetic': True})
    obj['payload'].update(parser='future_adapter', parser_output_ref=extension)
    ref = store.put('source', obj['payload'], refs=[*obj['refs'], extension])
    assert evidence.source_summary(store, ref)['parser'] == 'future_adapter'
    with pytest.raises(ArtifactError, match='no registered citation locator'):
        evidence.add_claim(store, ref, 1, 0, 7, 'initial')
