"""Codex-owned checks, outside DSH's writable scope; offline/scripted only."""
from copy import deepcopy
import math

import pytest

from biomodel_agent import agent, application, modeling
from biomodel_agent.cli import _print_run_result
from biomodel_agent.domain.contracts import (
    Check, ExperimentSpec, ModelDraft, Parameter, RateRule, Reaction, SimulationResult,
    State, ValidationReport,
)
from biomodel_agent.domain.serialization import to_dict
from biomodel_agent.storage import ArtifactStore


class Scripted:
    last_usage = {}
    def __init__(self, actions):
        self.actions = iter(actions)
    def respond(self, messages):
        return deepcopy(next(self.actions))


def proposal(store, *, negative=False):
    text = 'Initial x = 1.\nParameter k = 0.4.\n'
    if negative:
        text += 'Reaction loss consumes x at constant rate k.'
        draft = ModelDraft('negative', (State('x', 1., quantity_kind='amount', nonnegative=True),),
            (Parameter('k', .4),), reactions=(Reaction('loss', {'x':1.}, {}, 'k'),))
        target = 'reaction:loss/rate'
    else:
        text += 'Equation dx/dt = -k*x.'
        draft = ModelDraft('decay', (State('x', 1.),), (Parameter('k', .4),),
            rate_rules=(RateRule('x', '-k*x'),))
        target = 'rule:x/expression'
    source_ref = store.put('source', {'pages':[{'number':1, 'text':text}]})
    args = {'draft':to_dict(draft), 'evidence':[
        {'target':t, 'source_ref':source_ref, 'page':1, 'quote':q}
        for t,q in zip(('x/initial', 'k/value', target), text.splitlines())]}
    actions = [{'action':'submit_draft', 'args':args}, {'action':'run', 'args':{}},
               {'action':'finish', 'args':{}}]
    return source_ref, draft, actions


def test_real_negative_result_cannot_complete_or_lose_failure(tmp_path):
    store = ArtifactStore(tmp_path)
    source, _, actions = proposal(store, negative=True)
    ref = agent.run_agent(store, Scripted(actions), [source], ExperimentSpec((0.,2.,4.)))
    task = store.get(ref)['payload']
    run = store.get(task['run_ref'])['payload']
    assert run['simulation']['values']['x'][-1] < -.5
    assert any(c['status']=='fail' and c['severity']=='blocking' for c in run['numeric_validation']['checks'])
    assert run['scientific_status']=='failed'
    assert task['state']=='incomplete'
    assert _print_run_result(task['run_ref'], store, strict=True)==1


def test_real_strict_reference_failure_is_failed_even_with_unknown_sources(tmp_path):
    store = ArtifactStore(tmp_path)
    _, draft, _ = proposal(store)
    exp = ExperimentSpec((0.,1.))
    ref = application.run_reproduction(store, modeling.build_model(draft), exp,
        reference=SimulationResult('succeeded',exp.times,{'x':(100.,100.)}),
        scales={'x':1.}, strict=True)
    run = store.get(ref)['payload']
    assert run['comparison']['status']=='fail'
    assert run['source_validation']['semantic_support']=='unknown'
    assert run['scientific_status']=='failed'


def test_controlled_convergence_failure_wins_over_missing_source(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path)
    _, draft, _ = proposal(store)
    monkeypatch.setattr(application, '_convergence', lambda *a,**kw:
        (None, {'status':'fail','comparison':{'max_scaled_error':1.,'threshold':.001}}, None, 'fault injection'))
    ref = application.run_reproduction(store, modeling.build_model(draft), ExperimentSpec((0.,1.)), strict=True)
    run = store.get(ref)['payload']
    assert run['status']=='succeeded'
    assert run['scientific_status']=='failed'


def test_unknown_validation_is_not_a_failure_or_a_pass(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path)
    _, draft, _ = proposal(store)
    monkeypatch.setattr(application, '_numeric_report', lambda *a,**kw:
        ValidationReport((Check('finite','pass','finite'),
                          Check('convergence','unknown','not established',severity='blocking'))))
    ref = application.run_reproduction(store, modeling.build_model(draft), ExperimentSpec((0.,1.)),
                                        strict=False, use_cache=False)
    assert store.get(ref)['payload']['scientific_status']=='not_verified'


@pytest.mark.parametrize('kind', ['numeric','static','comparison'])
def test_legacy_unknown_label_does_not_hide_explicit_failure(tmp_path, monkeypatch, kind):
    store = ArtifactStore(tmp_path)
    source, _, actions = proposal(store)
    payload = {'status':'succeeded', 'scientific_status':'not_verified',
               'comparison':{'status':'not_run'}}
    if kind=='comparison':
        payload['comparison']={'status':'fail'}
    else:
        payload[kind+'_validation']={'checks':[{'code':'injected','status':'fail','severity':'blocking'}]}
    legacy_ref = store.put('run', payload)
    monkeypatch.setattr(application, 'run_reproduction', lambda *a,**kw:legacy_ref)
    ref = agent.run_agent(store, Scripted(actions), [source], ExperimentSpec((0.,1.)))
    assert store.get(ref)['payload']['state']=='incomplete'
    assert _print_run_result(legacy_ref,store,strict=False)==1
    assert _print_run_result(legacy_ref,store,strict=True)==1
    assert store.get(legacy_ref)['payload']==payload


def test_valid_but_unreviewed_run_remains_technically_complete(tmp_path):
    store = ArtifactStore(tmp_path)
    source, _, actions = proposal(store)
    ref = agent.run_agent(store, Scripted(actions), [source], ExperimentSpec((0.,1.)))
    task = store.get(ref)['payload']
    run = store.get(task['run_ref'])['payload']
    assert task['state']=='completed'
    assert run['scientific_status']=='not_verified'
    assert _print_run_result(task['run_ref'],store,strict=True)==2
    assert _print_run_result(task['run_ref'],store,strict=False)==0


def test_valid_reference_pass_survives_cache_and_agent_summary(tmp_path):
    store = ArtifactStore(tmp_path)
    _, draft, _ = proposal(store)
    exp = ExperimentSpec((0.,1.))
    reference = SimulationResult('succeeded',exp.times,{'x':(1.,math.exp(-.4))})
    artifact = modeling.build_model(draft)
    original = application.run_reproduction(store, artifact, exp, reference=reference, scales={'x':1.})
    cached = application.run_reproduction(store, artifact, exp, reference=reference, scales={'x':1.})
    for ref in (original,cached):
        run = store.get(ref)['payload']
        assert run['scientific_status']=='passed_structured_reference'
        assert agent._run_facts(run)['scientific_status']=='passed_structured_reference'
    assert store.get(cached)['payload']['cached_from']==original


@pytest.mark.parametrize('checks', [(), (Check('finite','pass','finite'),
    Check('convergence','unknown','not established',severity='blocking'))])
def test_reference_pass_with_missing_or_unknown_numeric_validation_stays_unknown(tmp_path, monkeypatch, checks):
    store = ArtifactStore(tmp_path)
    _, draft, _ = proposal(store)
    exp = ExperimentSpec((0.,1.))
    monkeypatch.setattr(application, '_numeric_report', lambda *a,**kw:ValidationReport(checks))
    ref = application.run_reproduction(store, modeling.build_model(draft), exp,
        reference=SimulationResult('succeeded',exp.times,{'x':(1.,math.exp(-.4))}),scales={'x':1.},use_cache=False)
    assert store.get(ref)['payload']['comparison']['status']=='pass'
    assert store.get(ref)['payload']['scientific_status']=='not_verified'


@pytest.mark.parametrize('variant', ['missing_mode','unknown_mode','missing_convergence',
    'duplicate_finite_unknown','other_blocking_unknown','missing_unit_review'])
def test_incomplete_saved_facts_cannot_be_upgraded(variant):
    record = {'status':'succeeded','scientific_status':'not_verified','mode':'structured_execution',
        'comparison':{'status':'pass'},'numeric_validation':{'checks':[
            {'code':'finite','status':'pass','severity':'blocking'},
            {'code':'convergence','status':'pass','severity':'blocking'}]}}
    if variant=='missing_mode':
        del record['mode']
    elif variant=='unknown_mode':
        record['mode']='unrecognized_profile'
    elif variant=='missing_convergence':
        record['numeric_validation']['checks'].pop()
    elif variant=='duplicate_finite_unknown':
        record['numeric_validation']['checks'].append({'code':'finite','status':'unknown','severity':'warning'})
    elif variant=='other_blocking_unknown':
        record['numeric_validation']['checks'].append({'code':'required_extra','status':'unknown','severity':'blocking'})
    else:
        record['mode']='strict_reproduction'
        record['source_validation']={'status':'complete','semantic_support':'supported'}
    assert application.derive_scientific_status(record)=='not_verified'
