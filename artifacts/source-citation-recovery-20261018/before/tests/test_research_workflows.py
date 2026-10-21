"""Independent acceptance of research traces, fitting, heldout protocols and agent gates."""
import json
import math
from pathlib import Path
import httpx
import numpy as np
import pytest

from biomodel_agent import agent, agent_tools, application, evidence, fitting, hypotheses, modeling, validation
from biomodel_agent.domain.contracts import *
from biomodel_agent.domain.serialization import to_dict
from biomodel_agent.storage import ArtifactStore
from biomodel_agent.llm import ChatProvider, ProviderError


def decay():
    return modeling.build_model(ModelDraft('decay',(State('x',1.),),(Parameter('d',.5),),rate_rules=(RateRule('x','-d*x'),)))


def test_frozen_run_cache_replay_and_comparison(tmp_path):
    store = ArtifactStore(tmp_path/'store')
    exp = ExperimentSpec((0.,.3,2.))
    ref = SimulationResult('succeeded',exp.times,{'x':tuple(math.exp(-.5*t) for t in exp.times)})
    r1 = application.run_reproduction(store,decay(),exp,reference=ref,scales={'x':1.})
    p1 = store.get(r1)['payload']
    assert p1['status']=='succeeded' and p1['comparison']['status']=='pass'
    assert p1['scientific_status']=='passed_structured_reference'
    r2 = application.run_reproduction(store,decay(),exp,reference=ref,scales={'x':1.})
    assert r2!=r1 and store.get(r2)['payload']['cached_from']==r1
    replay = store.get(application.replay_run(store,r1))['payload']
    assert replay['replay_of']==r1 and replay['cached_from'] is None
    assert replay['simulation']['values']==p1['simulation']['values']
    strict = store.get(application.run_reproduction(store,decay(),exp,reference=ref,scales={'x':1.},strict=True))['payload']
    assert strict['scientific_status']=='not_verified' and strict['source_validation']['missing']
    changed = store.get(application.run_reproduction(store,decay(),ExperimentSpec(exp.times,parameter_overrides={'d':.8})))['payload']
    assert changed['input_ref']!=p1['input_ref'] and changed['comparison']['status']=='not_run'


def test_failed_reference_is_not_success(tmp_path):
    s=ArtifactStore(tmp_path)
    e=ExperimentSpec((0.,1.))
    p=s.get(application.run_reproduction(s,decay(),e,reference=SimulationResult('succeeded',e.times,{'x':(100.,100.)}),scales={'x':1.}))['payload']
    assert p['comparison']['status']=='fail' and p['scientific_status']=='failed'
    with pytest.raises(ValueError):
        validation.compare_series({'times':[],'values':{}},{'times':[],'values':{}},{})


def test_numeric_failure_cli_and_agent_cannot_claim_completion(tmp_path, monkeypatch):
    from biomodel_agent.cli import _print_run_result
    store=ArtifactStore(tmp_path/'store')
    failed=store.put('run',{'status':'succeeded','scientific_status':'failed',
                            'comparison':{'status':'not_run'}})
    assert _print_run_result(failed,store,strict=False)==1
    monkeypatch.setattr(application,'run_reproduction',lambda *a,**kw:failed)
    _,ref,args=source_proposal(tmp_path,store)
    actions=[dict(action='submit_draft',args=args),dict(action='run',args={}),
             dict(action='finish',args={})]
    record=store.get(agent.run_agent(store,Scripted(actions),[ref],ExperimentSpec((0.,1.))))['payload']
    assert record['state']=='incomplete'


def test_report_uses_authoritative_sbml_math_and_hash(tmp_path):
    import hashlib
    from biomodel_agent.reporting import render_report
    store=ArtifactStore(tmp_path/'store');artifact=decay()
    ref=application.run_reproduction(store,artifact,ExperimentSpec((0.,1.)))
    report=render_report(store,ref)
    assert hashlib.sha256(artifact.sbml.encode()).hexdigest() in report
    assert 'd(x)/dt = -d * x' in report
    assert 'conservation' in report and 'not_run' in report


def training_data():
    times=np.linspace(0,5,16)
    return [{'id':'decay','role':'training','experiment':to_dict(ExperimentSpec(tuple(times))),
             'values':{'signal':np.exp(-.7*times).tolist()},'sigma':{'signal':.01}}]


def test_fit_constants_not_state_initials_and_holdout_protected():
    obs=(ObservationSpec('signal','x'),)
    fit=fitting.fit_parameters(decay(),training_data(),obs,{'d':(.1,1.5)},starts=2,max_nfev=40)
    assert fit['status']=='succeeded',fit
    assert abs(fit['best']['parameters']['d']-.7)<1e-5
    assert len(fit['starts'])==2
    assert fit['uncertainty']['global_identifiability']=='unknown'
    assert fit['uncertainty']['confidence_intervals']=='not_run'
    row=training_data()[0];row['role']='holdout'
    with pytest.raises(ValueError,match='training'):
        fitting.fit_parameters(decay(),[row],obs,{'d':(.1,1.5)})
    with pytest.raises(ValueError):
        fitting.fit_parameters(decay(),training_data(),obs,{'x':(.1,1.5)})
    timed=fitting.fit_parameters(decay(),training_data(),obs,{'d':(.1,1.5)},timeout_s=.001)
    assert timed['status']=='timed_out' and timed['best'] is None


def test_equivalent_training_mechanisms_and_different_intervention():
    c=hypotheses.adaptation_candidates()
    fb=modeling.build_model(c['cand_feedback']);ff=modeling.build_model(c['cand_feedforward'])
    obs=[{'id':'response','expression':'x'}]
    e=ExperimentSpec((0.,1.,3.,10.,30.))
    p1=fitting.predict(fb,e,obs,{'k':2.,'d':1.2,'h':.2})['observables']['response']
    p2=fitting.predict(ff,e,obs,{'k':2.,'d':1.,'h':.2})['observables']['response']
    assert np.max(np.abs(np.array(p1)-p2))<1e-7
    intervention=ExperimentSpec(e.times,parameter_overrides={'b':0.})
    q1=fitting.predict(fb,intervention,obs,{'k':2.,'d':1.2,'h':.2})['observables']['response']
    q2=fitting.predict(ff,intervention,obs,{'k':2.,'d':1.,'h':.2})['observables']['response']
    assert np.max(np.abs(np.array(q1)-q2))>.25


class Scripted:
    last_usage={'total_tokens':0}
    def __init__(self,actions):self.actions=iter(actions)
    def respond(self,messages):return next(self.actions)


def source_proposal(tmp_path,store):
    text='x initial 1.0\nd value 0.5\ndx/dt = -d*x\n'
    p=tmp_path/'model.txt';p.write_text(text)
    ref=evidence.ingest_source(store,p)
    draft=ModelDraft('decay',(State('x',1.),),(Parameter('d',.5),),rate_rules=(RateRule('x','-d*x'),))
    items=[]
    for target,line in zip(['x/initial','d/value','rule:x/expression'],text.splitlines(True)):
        start=text.index(line);items.append(dict(target=target,source_ref=ref,page=1,start=start,end=start+len(line)))
    return text,ref,{'draft':to_dict(draft),'evidence':items}


def test_agent_complete_trace_and_no_fabricated_sources(tmp_path):
    store=ArtifactStore(tmp_path/'store')
    text,ref,args=source_proposal(tmp_path,store)
    actions=[dict(action='inspect_source',args=dict(source_ref=ref,page=1,start=0,end=len(text))),
             dict(action='submit_draft',args=args),dict(action='run',args={}),dict(action='finish',args={})]
    record=store.get(agent.run_agent(store,Scripted(actions),[ref],ExperimentSpec((0.,1.))))['payload']
    assert record['state']=='completed',record
    assert record['call_count']==4
    run=store.get(record['run_ref'])['payload']
    assert run['mode']=='strict_reproduction' and run['scientific_status']=='not_verified'
    assert run['source_validation']['coverage']==1.
    assert len(record['events'][0]['requests'])==2  # actual call snapshot, not mutable final history
    missing={**args,'evidence':args['evidence'][1:]}
    outcome=agent_tools.submit_draft(store,missing,[ref])
    assert outcome['state']=='awaiting_input' and outcome['artifact'] is None
    budget=store.get(agent.run_agent(store,Scripted(actions),[ref],ExperimentSpec((0.,1.)),max_calls=1))['payload']
    assert budget['state']=='incomplete'


@pytest.mark.parametrize('action,args',[('shell',{'command':'echo unsafe'}),('inspect_source',{'source_ref':'0'*64,'page':1,'start':0,'end':1})])
def test_agent_tools_cannot_expand_scope(tmp_path,action,args):
    store=ArtifactStore(tmp_path/'store');_,ref,_=source_proposal(tmp_path,store)
    p=store.get(agent.run_agent(store,Scripted([dict(action=action,args=args)]),[ref],ExperimentSpec((0.,1.))))['payload']
    assert p['state']=='failed' and p['run_ref'] is None


def test_llm_real_wire_schema_and_redacted_errors(monkeypatch):
    monkeypatch.setenv('TEST_BIOMODEL_KEY','private-test-credential')
    requests=[]
    def handle(req):
        requests.append(json.loads(req.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"action":"finish","args":{}}'}}],'usage':{'total_tokens':20}})
    provider=ChatProvider('https://example.org/v1','test',api_key_env='TEST_BIOMODEL_KEY',transport=httpx.MockTransport(handle))
    assert provider.respond([{'role':'user','content':'data'}])['action']=='finish'
    assert requests[0]['messages'][0]['role']=='system' and provider.last_usage['total_tokens']==20
    bad=ChatProvider('https://example.org/v1','test',api_key_env='TEST_BIOMODEL_KEY',transport=httpx.MockTransport(lambda req:httpx.Response(403,text='private-test-credential')))
    with pytest.raises(ProviderError) as exc:bad.respond([])
    assert 'private-test-credential' not in str(exc.value)


def test_exact_quotes_resolve_offsets_without_llm_counting(tmp_path):
    store=ArtifactStore(tmp_path/'store');text,ref,args=source_proposal(tmp_path,store)
    for item in args['evidence']:
        item['quote']=text[item.pop('start'):item.pop('end')]
    result=agent_tools.submit_draft(store,args,[ref])
    assert result['state']=='ready',result['problems']
    for group in result['artifact'].element_map.values():
        for claim in group:
            p=store.get(claim)['payload']
            assert p['quote']==text[p['start']:p['end']]
    args['evidence'][0]['quote']='invented quotation'
    assert agent_tools.submit_draft(store,args,[ref])['state']=='invalid'
    assert [float(m.group()) for m in agent_tools.NUMBER.finditer('value 2.0. Next.')] == [2.]
    assert not list(agent_tools.NUMBER.finditer('version 1.2.3'))


def test_export_verified_closure_and_empty_report_is_not_proof(tmp_path):
    from biomodel_agent.bundles import export_bundle
    store=ArtifactStore(tmp_path/'store');text,ref,args=source_proposal(tmp_path,store)
    proposal=agent_tools.submit_draft(store,args,[ref])
    assert proposal['state']=='ready'
    manifest=export_bundle(store,proposal['model_ref'],tmp_path/'bundle')
    copied=ArtifactStore(tmp_path/'bundle')
    assert copied.get(proposal['model_ref'])==store.get(proposal['model_ref'])
    assert copied.get_blob(copied.get(ref)['payload']['blob_ref'])==text.encode()
    assert ref in manifest['objects_and_blobs']
    with pytest.raises(ValueError):export_bundle(store,proposal['model_ref'],tmp_path/'bundle')
