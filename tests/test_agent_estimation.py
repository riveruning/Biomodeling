"""Controlled Agent dispatch/review using synthetic providers and source fixtures."""
from copy import deepcopy
import json

import libsbml
import pytest

from biomodel_agent import agent, agent_tools, bindings, bundles, cli, estimation, estimation_workflow as workflow
from biomodel_agent import evidence, fitting, recovery, workstate
from biomodel_agent.domain.contracts import ExperimentSpec, ModelArtifact, ModelDraft, Parameter, RateRule, State
from biomodel_agent.domain.serialization import from_dict, to_dict
from biomodel_agent.storage import ArtifactStore
from test_estimation_architecture import setup as base_setup


class Scripted:
    last_usage = {}
    def __init__(self, *actions): self.actions=iter(actions); self.calls=[]
    def respond(self, messages): self.calls.append(deepcopy(messages)); return next(self.actions)


def act(action, **args): return dict(action=action, args=args)


@pytest.fixture
def setup(base_setup, tmp_path):
    store, source, c = base_setup
    path = tmp_path/'template.txt'
    path.write_text('Initial x = 1.\nParameter k = 0.3.\nEquation dx/dt = -k*x.\n')
    template_source = evidence.ingest_source(store, path)
    draft = ModelDraft('template', (State('x',1.),), (Parameter('k',.3),), rate_rules=(RateRule('x','-k*x'),))
    out = agent_tools.submit_draft(store, dict(draft=to_dict(draft), evidence=[
        dict(target=target, source_ref=template_source, page=1, quote=quote)
        for target,quote in [('x/initial','Initial x = 1.'),('k/value','Parameter k = 0.3.'),('rule:x/expression','Equation dx/dt = -k*x.')]]), [template_source])
    assert out['state']=='ready',out
    c['model_ref']=out['model_ref']; c['source_refs'].append(template_source)
    return store, c


def start(setup, provider, **kwargs):
    store,c=setup; plan=estimation.prepare_plan(store,c)
    fields=dict(estimation_plan_ref=plan); fields.update(kwargs)
    ref=agent.run_agent(store,provider,c['source_refs'],from_dict(ExperimentSpec,c['training'][0]['experiment']),**fields)
    return ref,plan


def result_of(store, run):
    return next(e['result_ref'] for e in store.get(run)['payload']['events'] if e.get('tool')=='run_estimation' and e.get('ok'))


def test_request_proposes_only_and_has_no_scientific_execution(setup):
    store,c=setup; provider=Scripted(act('request_estimation',targets=['k'],rationale='Need a separately reviewed estimate.'))
    ref=agent.run_agent(store,provider,c['source_refs'],ExperimentSpec((0.,1.)),estimation_requests=True)
    run=store.get(ref)['payload']; request=store.list(workflow.REQUEST)
    assert run['state']=='awaiting_input' and len(provider.calls)==1 and len(request)==1
    p=store.get(request[0])['payload']
    assert p['proposed_targets']==['k'] and p['authority']=='agent_proposal_only'
    assert p['task_ref']==run['task_ref'] and request[0] in store.get(ref)['refs']
    assert not store.list('parameter_estimation_execution') and not store.list(estimation.PLAN)
    assert store.get(run['checkpoint_ref'])['payload']['run_count']==0
    assert 'separate' in workstate.from_run(store,ref)['next_action']


@pytest.mark.parametrize('args,enabled',[(dict(targets=['k'],rationale='Need data.'),False),
    (dict(targets=['k'],rationale='Need data.',values={'k':.7}),True),
    (dict(targets=['k','k'],rationale='Need data.'),True), (dict(targets=['k'],rationale=''),True)])
def test_request_disabled_or_cannot_supply_values_protocol_or_bad_targets(setup,args,enabled):
    store,c=setup
    ref=agent.run_agent(store,Scripted(act('request_estimation',**args)),c['source_refs'],ExperimentSpec((0.,1.)),
                        estimation_requests=enabled,max_calls=1)
    assert store.get(ref)['payload']['state']!='completed'
    assert not store.list(workflow.REQUEST) and not store.list('parameter_estimation_execution')


def test_bound_agent_uses_plan_once_stops_for_review_and_does_not_publish_estimates(setup):
    store,c=setup; original=store.get(c['model_ref']); before_claims=store.list('claim')
    provider=Scripted(act('inspect_progress'),act('run_estimation'),act('finish'))
    run,plan=start(setup,provider); p=store.get(run)['payload']; result=result_of(store,run)
    assert p['state']=='awaiting_input' and p['model_ref'] is None and p['run_ref'] is None
    assert len(provider.calls)==2 and p['call_count']==2
    assert store.get(p['checkpoint_ref'])['payload']['run_count']==1
    assert workflow.check_result(store,result,plan_ref=plan)['execution_status']=='succeeded'
    assert not store.list(workflow.REVIEW) and not store.list(workflow.CANDIDATE)
    assert store.get(c['model_ref'])==original and store.list('claim')==before_claims
    brief=json.loads(provider.calls[0][1]['content'])['estimation']
    assert brief['validation_conditions']==1 and not {'values','validation','training'}&set(brief)
    progress=workstate.from_run(store,run)
    assert [s['id'] for s in progress['steps']]==['frozen_estimation','estimate','operator_review']
    assert progress['estimation']['adoption_status']=='not_adopted'


@pytest.mark.parametrize('action,args',[('submit_draft',{}),('run',{}),('finish',{}),
                                     ('run_estimation',{'bounds':{'k':[0.,100.]}}),('request_estimation',{'targets':['k'],'rationale':'change'})])
def test_frozen_branch_rejects_mutations_but_keeps_original_call_budget(setup,action,args):
    store,_=setup
    provider=Scripted(act(action,**args),act('run_estimation'))
    run,_=start(setup,provider,max_calls=2)
    assert store.get(run)['payload']['state']=='awaiting_input' and len(provider.calls)==2
    assert len(store.list('parameter_estimation_execution'))==1


@pytest.mark.parametrize('extra',[dict(memory_project='p'),dict(task_planning=True),dict(failure_handling=True),
                                 dict(scientific_skills=True),dict(mode='hypothesis'),dict(estimation_requests=True)])
def test_branch_settings_cannot_change_frozen_protocol_before_any_provider_call(setup,extra):
    store,c=setup; plan=estimation.prepare_plan(store,c); before=store.list(); provider=Scripted()
    with pytest.raises(ValueError):
        agent.run_agent(store,provider,c['source_refs'],from_dict(ExperimentSpec,c['training'][0]['experiment']),
                        estimation_plan_ref=plan,**extra)
    assert store.list()==before and not provider.calls


def test_scope_and_clock_mismatch_rejected_before_task_writes(setup):
    store,c=setup; plan=estimation.prepare_plan(store,c); before=store.list()
    for sources,exp in [(c['source_refs'][:1],from_dict(ExperimentSpec,c['training'][0]['experiment'])),
                        (c['source_refs'],ExperimentSpec((0.,1.),time_unit='minute'))]:
        with pytest.raises(ValueError): agent.run_agent(store,Scripted(),sources,exp,estimation_plan_ref=plan)
    assert store.list()==before


def test_review_branch_cannot_resume_by_turning_an_estimate_into_clarification_source(setup):
    store,_=setup; ref,_=start(setup,Scripted(act('run_estimation')))
    before=store.list(); provider=Scripted()
    with pytest.raises(ValueError,match='explicit result review'):
        agent.resume_agent(store,provider,ref,'Parameter k = 0.7.',respondent='Synthetic operator')
    assert store.list()==before and not provider.calls


def test_returned_result_recovery_adopts_without_refitting_or_another_provider_call(setup,monkeypatch):
    store,c=setup; original=Scripted(act('run_estimation'))
    with monkeypatch.context() as patch:
        patch.setattr(workflow,'check_result',lambda *a,**k: (_ for _ in ()).throw(SystemExit('after journal return')))
        with pytest.raises(SystemExit): start(setup,original,max_calls=1)
    execution=store.list('agent_execution')[0]
    report=recovery.diagnose_execution(store,execution)
    assert report['status']=='ready',report
    assert report['run_count']==1 and report['adoptable_result_refs']==store.list(estimation.RESULT)
    monkeypatch.setattr(fitting,'fit_parameters',lambda *a,**k: pytest.fail('fit repeated'))
    monkeypatch.setattr(estimation,'execute',lambda *a,**k: pytest.fail('execution repeated'))
    provider=Scripted(); ref=agent.recover_agent(store,provider,execution)
    assert store.get(ref)['payload']['state']=='awaiting_input' and not provider.calls
    assert len(original.calls)==1 and len(store.list('parameter_estimation_execution'))==1


def test_unknown_effect_stops_recovery_even_when_core_result_exists(setup,monkeypatch):
    store,_=setup; execute=estimation.execute
    def crash(*args):
        execute(*args)
        raise SystemExit('core result exists but Agent return unconfirmed')
    with monkeypatch.context() as patch:
        patch.setattr(estimation,'execute',crash)
        with pytest.raises(SystemExit): start(setup,Scripted(act('run_estimation')))
    execution=store.list('agent_execution')[0]
    assert recovery.diagnose_execution(store,execution)['status']=='blocked'
    provider=Scripted()
    with pytest.raises(recovery.RecoveryBlocked): agent.recover_agent(store,provider,execution)
    assert not provider.calls and len(store.list(estimation.RESULT))==1


def test_request_return_can_recover_without_writing_a_duplicate_request(setup,monkeypatch):
    store,c=setup
    with monkeypatch.context() as patch:
        patch.setattr(agent,'_final',lambda *a,**k: (_ for _ in ()).throw(SystemExit('before final')))
        with pytest.raises(SystemExit):
            agent.run_agent(store,Scripted(act('request_estimation',targets=['k'],rationale='Need protocol.')),
                            c['source_refs'],ExperimentSpec((0.,1.)),estimation_requests=True,max_calls=1)
    execution=store.list('agent_execution')[0]
    assert recovery.diagnose_execution(store,execution)['status']=='ready'
    monkeypatch.setattr(workflow,'request',lambda *a,**k: pytest.fail('request written twice'))
    provider=Scripted(); ref=agent.recover_agent(store,provider,execution)
    assert store.get(ref)['payload']['state']=='awaiting_input' and not provider.calls
    assert len(store.list(workflow.REQUEST))==1


@pytest.mark.parametrize('status',['failed','timed_out','unsupported'])
def test_failed_scientific_outcome_is_recorded_without_becoming_reviewable_success(setup,monkeypatch,status):
    store,_=setup
    monkeypatch.setattr(fitting,'fit_parameters',lambda *a,**k:dict(status=status,best=None,starts=[],diagnostics=['Synthetic outcome control.']))
    ref,_=start(setup,Scripted(act('run_estimation'))); result=result_of(store,ref)
    assert store.get(ref)['payload']['state']=='incomplete'
    assert workflow.check_result(store,result)['execution_status']==status
    assert workstate.from_run(store,ref)['steps'][1]['status']=='failed'
    with pytest.raises(ValueError,match='succeeded computation'):
        approve(store,result)
    assert not store.list(workflow.REVIEW) and not store.list(workflow.CANDIDATE)


def compute(setup):
    store,c=setup; ref=estimation.execute(store,estimation.prepare_plan(store,c)); return store,c,ref


def approve(store,result):
    return workflow.review(store,result,decision='approved_for_exploration',reviewer='Synthetic test operator',
                           reason='Test workflow, not domain certification.',acknowledged_unresolved=store.get(result)['payload']['unresolved'])


def test_review_requires_all_unknowns_and_rejection_cannot_be_adopted(setup):
    store,c,result=compute(setup)
    with pytest.raises(ValueError,match='acknowledgement'):
        workflow.review(store,result,decision='approved_for_exploration',reviewer='Fixture',reason='No acknowledgement.')
    assert not store.list(workflow.REVIEW)
    rejected=workflow.review(store,result,decision='rejected',reviewer='Fixture',reason='Insufficient evidence.')
    with pytest.raises(ValueError,match='approval'): workflow.prepare_candidate(store,rejected)
    with pytest.raises(ValueError,match='already reviewed'): approve(store,result)


@pytest.mark.parametrize('defect',['value','context','origin','diagnostics','unknowns','status','plan_ref','fit_ref'])
def test_forged_result_fields_do_not_gain_review_or_adoption_authority(setup,defect):
    store,c,ref=compute(setup); obj=store.get(ref);p=obj['payload']
    if defect=='value':p['estimates']['k']['value']=99.
    elif defect=='context':p['estimates']['k']['context']={'organism':'different'}
    elif defect=='origin':p['estimates']['k']['origin']='literature_reported'
    elif defect=='diagnostics':p['diagnostics']['global_identifiability']='proven'
    elif defect=='unknowns':p['unresolved']=[]
    elif defect=='status':p['scientific_status']='verified'
    elif defect=='plan_ref':p['plan_ref']=c['source_refs'][0]
    else:p['fit_ref']=None
    bad=store.put(estimation.RESULT,p,refs=obj['refs'])
    with pytest.raises((ValueError,KeyError)):
        workflow.review(store,bad,decision='approved_for_exploration',reviewer='Fixture',reason='Attempted promotion.',acknowledged_unresolved=p['unresolved'])
    assert not store.list(workflow.REVIEW)


def test_approved_candidate_is_separate_and_loses_changed_numerical_evidence(setup,tmp_path):
    store,c,result=compute(setup); original=store.get(c['model_ref']); claims=store.list('claim')
    review=approve(store,result); ref=workflow.prepare_candidate(store,review); candidate=store.get(ref)['payload']
    assert workflow.prepare_candidate(store,review)==ref
    artifact=from_dict(ModelArtifact,store.get(candidate['model_ref'])['payload'])
    model=libsbml.readSBMLFromString(artifact.sbml).getModel()
    assert model.getParameter('k').getValue()==pytest.approx(.7,abs=2e-5)
    assert artifact.parent_ref==c['model_ref'] and 'k/value' not in artifact.element_map
    assert original['payload']['element_map']['k/value']
    expected,_=bindings.sbml_targets(artifact.sbml)
    check=evidence.validate_links(store,artifact.element_map,list(expected))
    assert 'k/value' in check['missing'] and check['status']=='incomplete'
    assert candidate['scope']=='exploratory_model_only' and candidate['scientific_status']=='not_verified'
    assert store.get(c['model_ref'])==original and store.list('claim')==claims
    target=tmp_path/'bundle'; bundles.export_bundle(store,ref,target); reopened=ArtifactStore(target)
    assert workflow.prepare_candidate(reopened,review)==ref


def test_cli_review_candidate_and_agent_flags(setup,tmp_path,capsys):
    store,c,result=compute(setup); common=['--store',str(store.root)]
    args=['estimate-review',result,*common,'--decision','approved_for_exploration','--reviewer','Fixture','--reason','Workflow control.']
    for item in store.get(result)['payload']['unresolved']:args+=['--acknowledge',item]
    assert cli.main(args)==0
    review=json.loads(capsys.readouterr().out)['review_ref']
    assert cli.main(['estimate-candidate',review,*common])==0
    assert json.loads(capsys.readouterr().out)['scope']=='exploratory_model_only'
    with pytest.raises(SystemExit) as exc: cli.main(['agent','--help'])
    assert exc.value.code==0
    help_text=capsys.readouterr().out
    assert '--estimation-plan-ref' in help_text and '--estimation-requests' in help_text
