"""Decision provenance, ambiguity and missing-input controls over real gates."""
from copy import deepcopy

import pytest

from biomodel_agent import evidence, modeling_plan as plans
from biomodel_agent.domain.contracts import ModelDraft, Parameter, RateRule, State
from biomodel_agent.domain.serialization import to_dict
from biomodel_agent.storage import ArtifactStore


TEXT = 'Initial x = 1.\nParameter k = 0.4.\nEquation dx/dt = -k*x.\nTime is measured in seconds.\n'


def setup(tmp_path, text=TEXT):
    store = ArtifactStore(tmp_path / 'store')
    # Evidence offsets refer to this exact LF fixture on every operating system.
    path = tmp_path / 'source.txt'; path.write_text(text,encoding='utf-8',newline='\n')
    source = evidence.ingest_source(store, path)
    draft = ModelDraft('decay', (State('x', 1.),), (Parameter('k', .4),),
                       rate_rules=(RateRule('x', '-k*x'),))
    quotes = ['Initial x = 1.', 'Parameter k = 0.4.', 'Equation dx/dt = -k*x.']
    targets = ['x/initial', 'k/value', 'rule:x/expression']
    anchors = [dict(id=f'E{i}', source_ref=source, page=1, start=text.index(q),
                    end=text.index(q)+len(q), quote=q, interpretation='Unverified reading of the supplied declaration.',
                    conditions={}) for i, q in enumerate(quotes) if q in text]
    by_quote = {a['quote']: a['id'] for a in anchors}
    decisions = [dict(target=t, origin='reported' if q in by_quote else 'unresolved',
        evidence_ids=[by_quote[q]] if q in by_quote else [], mechanism_ids=[],
        rationale='Use the supplied declaration.' if q in by_quote else 'Which value or rate law is intended?',
        alternatives=[]) for t, q in zip(targets, quotes)]
    spec = dict(schema_version=1, id='decay_task', model_type='ode', mode='reproduction', context={},
        entities=[dict(id='protein', label='Supplied normalized state')], evidence=anchors, mechanisms=[],
        candidates=[dict(id='direct', label='Explicit supplied equation', variable_map={'protein': 'x'},
                         draft=to_dict(draft), decisions=decisions)], selected_candidate='direct')
    return store, source, spec


def test_complete_plan_uses_existing_source_and_sbml_checks(tmp_path):
    store, source, spec = setup(tmp_path); original = deepcopy(spec)
    ref = plans.prepare(store, spec, [source])
    assert spec == original
    before = store.list()
    summary = plans.inspect(store, ref)
    assert summary['state'] == 'ready_to_compile' and summary['scientific_support'] == 'unknown'
    assert store.list() == before
    compiled = plans.compile_plan(store, ref)
    plan, receipt, artifact = plans.compiled_model(store, compiled)
    assert receipt['state'] == 'ready' and 'sbml' in artifact.format
    assert plan['spec']['candidates'][0]['draft'] == to_dict(from_draft(spec))
    assert not store.list('run')


def from_draft(spec):
    from biomodel_agent.domain.serialization import from_dict
    return from_dict(ModelDraft, spec['candidates'][0]['draft'])


def test_qualitative_relation_cannot_supply_rate_law(tmp_path):
    store, source, spec = setup(tmp_path)
    # Genuine quote, but it supports a qualitative relation, not this formula.
    path = tmp_path / 'qualitative.txt'; path.write_text('Protein x is degraded.')
    qualitative = evidence.ingest_source(store, path)
    e = spec['evidence'][2]
    e.update(source_ref=qualitative, start=0, end=22, quote='Protein x is degraded.')
    ref = plans.prepare(store, spec, [source, qualitative])
    summary = plans.inspect(store, ref)
    assert summary['state'] != 'ready_to_compile'
    assert any(i['code'].startswith('source_binding_') for i in summary['issues'])
    compiled = store.get(plans.compile_plan(store, ref))['payload']
    assert compiled['model_ref'] is None and not store.list('model')


def test_multiple_candidates_require_explicit_selection(tmp_path):
    store, source, spec = setup(tmp_path)
    other = deepcopy(spec['candidates'][0]); other['id'] = 'alternative'
    spec['candidates'].append(other); spec['selected_candidate'] = None
    ref = plans.prepare(store, spec, [source])
    assert plans.inspect(store, ref)['state'] == 'awaiting_selection'
    assert store.get(plans.compile_plan(store, ref))['payload']['model_ref'] is None
    spec['selected_candidate'] = 'direct'
    revised = plans.prepare(store, spec, [source], parent_ref=ref, reason='Explicitly select the supplied equation.')
    assert plans.inspect(store, revised)['state'] == 'ready_to_compile'
    assert plans.inspect(store, ref)['state'] == 'awaiting_selection'


def test_missing_parameter_retained_and_explicit_source_revision_resolves(tmp_path):
    missing_text = TEXT.replace('Parameter k = 0.4.', 'Parameter k is not reported.')
    store, source, spec = setup(tmp_path, missing_text)
    spec['candidates'][0]['draft']['parameters'][0]['value'] = None
    ref = plans.prepare(store, spec, [source])
    summary = plans.inspect(store, ref)
    assert summary['state'] == 'awaiting_input'
    assert any(i['target'] == 'k/value' for i in summary['issues'])
    blocked = plans.compile_plan(store, ref)
    assert store.get(blocked)['payload']['model_ref'] is None
    # A new source snapshot and revision cannot mutate the original proposal.
    path = tmp_path / 'clarified.txt'; path.write_text(TEXT)
    clarified = evidence.ingest_source(store, path)
    _, _, corrected = setup(tmp_path / 'other')
    for e in corrected['evidence']:
        e['source_ref'] = clarified
    revised = plans.prepare(store, corrected, [clarified], parent_ref=ref, reason='Replace missing-input source with attributed complete specification.')
    assert plans.inspect(store, revised)['state'] == 'ready_to_compile'
    assert plans.inspect(store, ref)['state'] == 'awaiting_input'
    assert not store.list('run')


@pytest.mark.parametrize('mode,ready', [('reproduction', False), ('exploration', True)])
def test_assumptions_never_become_reported_facts(tmp_path, mode, ready):
    store, source, spec = setup(tmp_path)
    spec['mode'] = mode
    decision = spec['candidates'][0]['decisions'][2]
    decision.update(origin='assumed', evidence_ids=[], rationale='Exploratory first-order decay hypothesis.')
    ref = plans.prepare(store, spec, [source]); summary = plans.inspect(store, ref)
    assert (summary['state'] == 'ready_to_compile') == ready
    assert summary['assumptions'][0]['target'] == 'rule:x/expression'
    compiled = plans.compile_plan(store, ref)
    receipt = store.get(compiled)['payload']
    if ready:
        _, _, artifact = plans.compiled_model(store, compiled)
        assert 'rule:x/expression' not in artifact.element_map
        assert receipt['hypothesis_ref'] in store.get(receipt['draft_ref'])['payload']['hypothesis_refs']
        assert receipt['scientific_support'] == 'unknown'
    else:
        assert receipt['model_ref'] is None


@pytest.mark.parametrize('defect', ['version_bool', 'unknown_field', 'wrong_type', 'foreign_source',
    'bad_quote', 'bad_offset', 'duplicate_evidence', 'unknown_evidence', 'missing_decision',
    'duplicate_decision', 'reported_without_evidence', 'fake_lineage', 'bad_map', 'unknown_selection',
    'bad_origin', 'nonfinite', 'unknown_mechanism', 'compartment', 'reaction_profile'])
def test_invalid_contract_writes_no_plan(tmp_path, defect):
    store, source, spec = setup(tmp_path)
    c = spec['candidates'][0]; d = c['decisions'][0]
    if defect == 'version_bool': spec['schema_version'] = True
    elif defect == 'unknown_field': spec['approved'] = True
    elif defect == 'wrong_type': spec['model_type'] = 'pde'
    elif defect == 'foreign_source': spec['evidence'][0]['source_ref'] = '0'*64
    elif defect == 'bad_quote': spec['evidence'][0]['quote'] = 'Initial x = 2.'
    elif defect == 'bad_offset': spec['evidence'][0]['start'] = True
    elif defect == 'duplicate_evidence': spec['evidence'].append(deepcopy(spec['evidence'][0]))
    elif defect == 'unknown_evidence': d['evidence_ids'] = ['foreign']
    elif defect == 'missing_decision': c['decisions'].pop()
    elif defect == 'duplicate_decision': c['decisions'].append(deepcopy(d))
    elif defect == 'reported_without_evidence': d['evidence_ids'] = []
    elif defect == 'fake_lineage': c['draft']['parent_ref'] = source
    elif defect == 'bad_map': c['variable_map'] = {'protein': 'foreign'}
    elif defect == 'unknown_selection': spec['selected_candidate'] = 'other'
    elif defect == 'bad_origin': d['origin'] = 'verified'
    elif defect == 'nonfinite': c['draft']['parameters'][0]['value'] = float('inf')
    elif defect == 'unknown_mechanism': d['mechanism_ids'] = ['unknown']
    elif defect == 'compartment': c['draft']['compartment']['size'] = 10.
    elif defect == 'reaction_profile': c['draft']['reactions'] = [dict(id='r', reactants={}, products={'x': 1.}, rate='k')]
    with pytest.raises((ValueError, TypeError)):
        plans.prepare(store, spec, [source])
    assert not store.list(plans.PLAN)


def test_wrong_numeric_binding_and_wrong_conditions_block(tmp_path):
    store, source, spec = setup(tmp_path)
    spec['candidates'][0]['draft']['parameters'][0]['value'] = .8
    ref = plans.prepare(store, spec, [source])
    assert plans.inspect(store, ref)['state'] == 'invalid'
    spec['candidates'][0]['draft']['parameters'][0]['value'] = .4
    spec['evidence'][1]['conditions'] = {'organism': 'yeast'}
    spec['context'] = {'organism': 'human'}
    ref = plans.prepare(store, spec, [source])
    assert any(i['code'] == 'condition_mismatch' for i in plans.inspect(store, ref)['issues'])


def test_compilation_rejects_other_candidate_model_and_stale_implementation(tmp_path, monkeypatch):
    store, source, spec = setup(tmp_path)
    ref = plans.prepare(store, spec, [source]); compiled = plans.compile_plan(store, ref)
    obj = store.get(compiled); forged = deepcopy(obj['payload'])
    other = deepcopy(spec); other['candidates'][0]['draft']['id'] = 'different'
    other_ref = plans.prepare(store, other, [source]); other_compiled = plans.compile_plan(store, other_ref)
    foreign_model = store.get(other_compiled)['payload']['model_ref']
    forged['model_ref'] = foreign_model
    forged_ref = store.put(plans.COMPILATION, forged, refs=tuple(foreign_model if r == obj['payload']['model_ref'] else r for r in obj['refs']))
    with pytest.raises(ValueError): plans.compiled_model(store, forged_ref)
    monkeypatch.setattr('biomodel_agent.recovery.implementation_fingerprint', lambda: '0'*64)
    with pytest.raises(ValueError, match='stale'): plans.compiled_model(store, compiled)


def test_conflicting_source_is_not_hidden_by_one_correct_quote(tmp_path):
    store, source, spec = setup(tmp_path)
    path = tmp_path/'conflict.txt'; path.write_text('Parameter k = 0.8.\n')
    conflict = evidence.ingest_source(store, path)
    ref = plans.prepare(store, spec, [source, conflict])
    view = plans.inspect(store, ref)
    assert view['state'] == 'awaiting_input'
    assert any(i['code'] == 'source_review' and i['target'] == 'k/value' for i in view['issues'])
    receipt = store.get(plans.compile_plan(store, ref))['payload']
    assert receipt['model_ref'] is None


def test_used_mechanism_requires_mapped_entities_and_matching_conditions(tmp_path):
    store, source, spec = setup(tmp_path)
    spec['entities'].append(dict(id='other', label='Another entity not represented in this candidate'))
    spec['mechanisms'] = [dict(id='M1', subject='protein', relation='promotes', object='other',
        evidence_ids=['E2'], conditions={'organism': 'yeast'}, rationale='Unverified qualitative interpretation.')]
    spec['candidates'][0]['decisions'][2]['mechanism_ids'] = ['M1']
    spec['context'] = {'organism': 'human'}
    ref = plans.prepare(store, spec, [source])
    codes = {i['code'] for i in plans.inspect(store, ref)['issues']}
    assert {'unmapped_mechanism_entity', 'condition_mismatch'} <= codes


def test_compilation_cannot_omit_declared_assumptions(tmp_path):
    store, source, spec = setup(tmp_path)
    spec['mode'] = 'exploration'
    spec['candidates'][0]['decisions'][2].update(origin='assumed', evidence_ids=[])
    ref = plans.prepare(store, spec, [source]); compiled = plans.compile_plan(store, ref)
    obj = store.get(compiled); altered = deepcopy(obj['payload']); altered['assumptions'] = []
    forged = store.put(plans.COMPILATION, altered, refs=obj['refs'])
    with pytest.raises(ValueError): plans.compiled_model(store, forged)


def test_blocked_candidate_report_exposes_the_proposed_mathematics(tmp_path):
    from biomodel_agent import reporting
    store, source, spec = setup(tmp_path)
    spec['candidates'][0]['draft']['rate_rules'][0]['expression'] = '-3*k*x'
    spec['candidates'][0]['decisions'][-1].update(origin='unresolved', evidence_ids=[])
    ref = plans.prepare(store, spec, [source])
    assert plans.inspect(store, ref)['state'] == 'awaiting_input'
    report = reporting.render_report(store, ref)
    assert 'Proposed value / expression' in report and r'-3\*k\*x' in report
    assert not store.list('run')
