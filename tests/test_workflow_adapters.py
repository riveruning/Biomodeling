"""Scripted decisions with real tools; these are adapter acceptance controls."""
from copy import deepcopy
import importlib.util
import json
import math
import multiprocessing
import os
from pathlib import Path
import time

import pytest

from biomodel_agent.domain.contracts import ModelDraft, Parameter, RateRule, Reaction, State
from biomodel_agent.domain.serialization import to_dict
from biomodel_agent.storage import ArtifactStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def adapters():
    spec = importlib.util.spec_from_file_location('workflow_adapters_acceptance',ROOT/'benchmarks/development/workflow_adapters.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def projection(adapters,workflow='domain_agent',case='decay',phase='initial'):
    m = adapters.protocol.development_manifest(ROOT)
    t = next(t for t in adapters.protocol.schedule(m) if t['workflow']==workflow and t['case_id']==case and t['phase']==phase)
    return adapters.protocol.trial_input(m,t,ROOT)


def action(name,args=None):
    return {'action':name,'args':args or {}}


class EnvelopeTransport:
    evidence_kind = 'scripted_control'
    def __init__(self,p,actions,usage=None,model=None,delay=0):
        self.model_config=deepcopy(p['model_config']); self.actions=list(actions)
        self.usage=usage; self.returned_model=model; self.delay=delay
    def request(self,payload,timeout_s):
        if self.delay: time.sleep(self.delay)
        item=self.actions.pop(0)
        if isinstance(item,Exception): raise item
        if callable(item): item=item(payload['messages'])
        return {'model':self.returned_model or self.model_config['model'],
                'choices':[{'message':{'content':json.dumps(item)}}], 'usage':self.usage}


def refs(messages):
    main=reply=None; length=None
    for msg in messages:
        try: data=json.loads(msg['content'])
        except (ValueError,TypeError): continue
        if 'materials' in data and main is None:
            main=next(iter(data['materials'])); length=data['materials'][main]['pages'][0]['chars']
        if data.get('tool')=='researcher_reply': reply=data['source_ref']
    return main,reply,length


def read_action(messages):
    main,_,length=refs(messages)
    return action('inspect_source',{'source_ref':main,'page':1,'start':0,'end':length})


def decay_proposal(messages):
    main,_,_=refs(messages)
    d=ModelDraft('decay',(State('A',2.),),(Parameter('k',.4),),rate_rules=(RateRule('A','-k*A'),),time_unit='minute')
    evidence=[{'target':target,'source_ref':main,'page':1,'quote':quote} for target,quote in
              [('A/initial','Initial A = 2.0.'),('k/value','Parameter k = 0.4.'),('rule:A/expression','Equation dA/dt = -k*A.')]]
    return action('submit_draft',{'draft':to_dict(d),'evidence':evidence})


def translation_proposal(messages):
    main,reply,_=refs(messages)
    d=ModelDraft('translation',(State('M',1.),State('P',.2)),
        tuple(Parameter(k,v) for k,v in [('s',.8),('dm',.4),('kp',.6),('dp',.2)]),
        rate_rules=(RateRule('M','s-dm*M'),RateRule('P','kp*M-dp*P')),time_unit='minute')
    quotes=[('M/initial','Initial M = 1.0.'),('s/value','Parameter s = 0.8.'),('dm/value','Parameter dm = 0.4.'),
            ('kp/value','Parameter kp = 0.6.'),('dp/value','Parameter dp = 0.2.'),
            ('rule:M/expression','Equation dM/dt = s-dm*M.'),('rule:P/expression','Equation dP/dt = kp*M-dp*P.')]
    evidence=[{'target':t,'source_ref':main,'page':1,'quote':q} for t,q in quotes]
    evidence.append({'target':'P/initial','source_ref':reply,'page':1,'quote':'Initial P = 0.2.'})
    return action('submit_draft',{'draft':to_dict(d),'evidence':evidence})


def complete_actions(workflow,proposal=decay_proposal):
    return [proposal] if workflow=='fixed_workflow' else [read_action,proposal,action('run'),action('finish')]


@pytest.mark.parametrize('workflow',['fixed_workflow','generic_tool_agent','domain_agent'])
def test_each_workflow_runs_real_shared_scientific_backend(adapters,tmp_path,workflow):
    p=projection(adapters,workflow)
    result=adapters.run_trial(p,EnvelopeTransport(p,complete_actions(workflow)),tmp_path/'phase')
    r=result['record']
    assert r['status']=='finished' and r['observation']['state']=='completed'
    assert r['observation']['executed'] is True
    assert r['observation']['claimed_complete'] is True
    assert r['assessment']['source_support']==r['assessment']['numeric']=='unknown'
    assert r['telemetry']['tool_calls']==4
    store=ArtifactStore(result['store_root']); run=store.get(result['audit']['run_ref'])['payload']
    assert run['status']=='succeeded'
    assert abs(run['simulation']['values']['A'][-1]-2*math.exp(-4))<1e-6
    assert run['cached_from'] is None and run['scientific_status']=='not_verified'
    assert result['audit']['native_agent_run_ref'] is not None if workflow=='domain_agent' else result['audit']['native_agent_run_ref'] is None


@pytest.mark.parametrize('workflow',['fixed_workflow','generic_tool_agent','domain_agent'])
def test_actual_original_task_resumption_uses_new_source_and_remaining_budget(adapters,tmp_path,workflow):
    first=projection(adapters,workflow,'translation')
    ask=action('ask_researcher',{'question':'What is the initial P value?'})
    actions=[ask] if workflow=='fixed_workflow' else [read_action,ask]
    before=adapters.run_trial(first,EnvelopeTransport(first,actions),tmp_path/'initial')
    assert before['record']['observation']['state']=='awaiting_input'
    second=projection(adapters,workflow,'translation','clarified')
    follow=[translation_proposal] if workflow=='fixed_workflow' else [translation_proposal,action('run'),action('finish')]
    after=adapters.run_trial(second,EnvelopeTransport(second,follow),tmp_path/'clarified',
        store_root=before['store_root'],parent_session_ref=before['session_ref'])
    assert after['record']['status']=='finished' and after['record']['observation']['state']=='completed'
    assert after['record']['continuation']=='resumed'
    store=ArtifactStore(after['store_root']); state=store.get(after['session_ref'])['payload']
    assert state['meter_totals']['model_calls']==before['record']['telemetry']['model_calls']+after['record']['telemetry']['model_calls']
    assert len(state['source_refs'])==2
    if workflow=='domain_agent':
        native=store.get(after['audit']['native_agent_run_ref'])['payload']
        assert native['parent_run_ref']==before['audit']['native_agent_run_ref']


def test_scripted_projection_does_not_leak_rubric_or_later_reply_to_provider(adapters,tmp_path):
    p=projection(adapters)
    out=adapters.run_trial(p,EnvelopeTransport(p,complete_actions('domain_agent')),tmp_path/'run')
    store=ArtifactStore(out['store_root'])
    requests=[store.get(r)['payload']['request'] for r in out['audit']['request_refs']]
    assert len(requests)==4
    assert all(req['model']==p['model_config']['model'] and req['temperature']==0. and req['top_p']==1. and req['seed']==0 and req['max_tokens']==1000 for req in requests)
    assert 'private_rubric' not in json.dumps(requests) and 'Initial P = 0.2.' not in json.dumps(requests)


@pytest.mark.parametrize('workflow',['fixed_workflow','generic_tool_agent','domain_agent'])
def test_tool_budget_stops_before_scientific_execution(adapters,tmp_path,workflow):
    p=projection(adapters,workflow); p['budget']['max_tool_calls']=2
    out=adapters.run_trial(p,EnvelopeTransport(p,complete_actions(workflow)),tmp_path/'run')
    assert out['record']['observation']['executed'] is False
    assert out['record']['telemetry']['tool_calls']==2
    assert not ArtifactStore(out['store_root']).list('run')
    assert out['audit']['stop_reason']=='tool_budget_exhausted'


@pytest.mark.parametrize('workflow',['fixed_workflow','generic_tool_agent','domain_agent'])
def test_model_call_budget_has_no_unmetered_retry(adapters,tmp_path,workflow):
    p=projection(adapters,workflow); p['budget']['max_model_calls']=1
    ask=action('ask_researcher',{'question':'Please clarify the intended task.'})
    actions=[ask] if workflow=='fixed_workflow' else [read_action,ask]
    out=adapters.run_trial(p,EnvelopeTransport(p,actions),tmp_path/'run')
    assert out['record']['telemetry']['model_calls']==1
    assert out['record']['observation']['executed'] is False


@pytest.mark.parametrize('workflow',['fixed_workflow','generic_tool_agent','domain_agent'])
def test_provider_failure_is_sanitized_and_counted(adapters,tmp_path,workflow):
    p=projection(adapters,workflow)
    out=adapters.run_trial(p,EnvelopeTransport(p,[RuntimeError('SECRET-not-for-audit')]),tmp_path/'run')
    assert out['record']['status']=='provider_error'
    assert out['record']['telemetry']['model_calls']==1
    assert out['record']['telemetry']['input_tokens'] is None
    assert 'SECRET' not in json.dumps(out)
    for path in Path(out['store_root']).rglob('*.json'):
        assert 'SECRET' not in path.read_text()


def test_reported_token_and_model_identity_mismatches_stop_before_tool_effect(adapters,tmp_path):
    p=projection(adapters)
    out=adapters.run_trial(p,EnvelopeTransport(p,[decay_proposal],usage={'prompt_tokens':10,'completion_tokens':1001}),tmp_path/'tokens')
    assert out['record']['telemetry']['output_tokens']==1001
    assert out['record']['observation']['executed'] is False
    assert out['audit']['stop_reason']=='output_token_budget_exhausted'
    bad=adapters.run_trial(p,EnvelopeTransport(p,[decay_proposal],model='different-model'),tmp_path/'identity')
    assert bad['record']['status']=='invalid_output'
    assert bad['record']['observation']['executed'] is False


def test_hard_deadline_terminates_slow_provider(adapters,tmp_path):
    p=projection(adapters); p['budget']['max_elapsed_seconds']=.2
    start=time.monotonic()
    out=adapters.run_trial(p,EnvelopeTransport(p,[action('finish')],delay=5),tmp_path/'timeout')
    assert time.monotonic()-start<3
    assert out['record']['status']=='timeout'
    assert out['record']['telemetry']['model_calls']==1
    assert out['audit']['supervised'] is True


def test_unsafe_or_stale_input_refuses_before_output_directory(adapters,tmp_path):
    p=projection(adapters); p['private_rubric']={'secret':True}
    with pytest.raises(ValueError): adapters.run_trial(p,EnvelopeTransport(p,[]),tmp_path/'bad')
    assert not (tmp_path/'bad').exists()


def test_fixed_workflow_cannot_turn_into_an_unbounded_tool_agent(adapters,tmp_path):
    p=projection(adapters,'fixed_workflow')
    out=adapters.run_trial(p,EnvelopeTransport(p,[read_action]),tmp_path/'run')
    assert out['record']['status']=='invalid_output'
    assert out['record']['telemetry']['model_calls']==1
    assert out['record']['observation']['executed'] is False


@pytest.mark.parametrize('workflow',['fixed_workflow','generic_tool_agent','domain_agent'])
def test_shared_backend_keeps_nonnegative_scientific_failure(adapters,tmp_path,workflow):
    p=projection(adapters,workflow)
    text='Initial A = 2.0.\nParameter k = 0.4.\nReaction loss consumes A at constant rate k.'
    s=next(s for s in p['sources'] if s['role']=='source')
    s.update(text=text,sha256=adapters.protocol.hashlib.sha256(text.encode()).hexdigest())
    def negative(messages):
        main,_,_=refs(messages)
        d=ModelDraft('negative',(State('A',2.,quantity_kind='amount',nonnegative=True),),
            (Parameter('k',.4),),reactions=(Reaction('loss',{'A':1.},{},'k'),))
        return action('submit_draft',{'draft':to_dict(d),'evidence':[
            {'target':t,'source_ref':main,'page':1,'quote':q} for t,q in zip(
                ['A/initial','k/value','reaction:loss/rate'],text.splitlines())]})
    out=adapters.run_trial(p,EnvelopeTransport(p,complete_actions(workflow,negative)),tmp_path/'negative')
    run=ArtifactStore(out['store_root']).get(out['audit']['run_ref'])['payload']
    assert run['simulation']['values']['A'][-1]<-1.9
    assert run['scientific_status']=='failed'
    assert out['record']['observation']['state']=='failed'


def test_duplicate_trial_cannot_replay_with_a_different_output_path(adapters,tmp_path):
    p=projection(adapters)
    first=adapters.run_trial(p,EnvelopeTransport(p,[action('ask_researcher',{'question':'Please clarify.'})]),tmp_path/'first')
    with pytest.raises(ValueError,match='already reserved'):
        adapters.run_trial(p,EnvelopeTransport(p,complete_actions('domain_agent')),tmp_path/'retry',store_root=first['store_root'])
    assert not (tmp_path/'retry').exists()


@pytest.mark.parametrize('defect',['workflow','model','budget','source','toolchain'])
def test_parent_identity_and_scope_cannot_change_on_resume(adapters,tmp_path,defect):
    p=projection(adapters,'domain_agent','translation')
    first=adapters.run_trial(p,EnvelopeTransport(p,[action('ask_researcher',{'question':'What is initial P?'})]),tmp_path/'parent')
    second=projection(adapters,'generic_tool_agent' if defect=='workflow' else 'domain_agent','translation','clarified')
    if defect=='model': second['model_config']['model']='different'
    elif defect=='budget': second['budget']['max_model_calls']=9
    elif defect=='toolchain': second['trial']['toolchain_sha256']='f'*64
    elif defect=='source':
        s=next(s for s in second['sources'] if s['role']=='source'); text='changed original\n'+s['text']
        s.update(text=text,sha256=adapters.protocol.hashlib.sha256(text.encode()).hexdigest())
    with pytest.raises(ValueError):
        adapters.run_trial(second,EnvelopeTransport(second,[]),tmp_path/'refused',store_root=first['store_root'],parent_session_ref=first['session_ref'])
    assert not (tmp_path/'refused').exists()


def test_crashed_provider_attempt_is_retained_and_not_replayed(adapters,tmp_path):
    class ExitTransport(EnvelopeTransport):
        def request(self,payload,timeout_s): os._exit(7)
    p=projection(adapters)
    out=adapters.run_trial(p,ExitTransport(p,[]),tmp_path/'crash')
    assert out['record']['status']=='adapter_error'
    assert out['record']['telemetry']['model_calls']==1
    assert out['audit']['stop_reason']=='worker_exited_without_result'
    assert len(out['audit']['request_refs'])==1
    assert out['record']['telemetry']['input_tokens'] is None


def test_failed_parent_does_not_trigger_a_fresh_paid_task(adapters,tmp_path):
    p=projection(adapters,'domain_agent','translation')
    first=adapters.run_trial(p,EnvelopeTransport(p,[RuntimeError('failure')]),tmp_path/'initial')
    second=projection(adapters,'domain_agent','translation','clarified')
    out=adapters.run_trial(second,EnvelopeTransport(second,[translation_proposal]),tmp_path/'child',
        store_root=first['store_root'],parent_session_ref=first['session_ref'])
    assert out['record']['status']=='not_run' and out['record']['observation']['state']=='not_run'
    assert out['record']['telemetry']['model_calls']==out['record']['telemetry']['tool_calls']==0
    assert out['record']['continuation']=='fresh' and out['record']['parent_trial_id'] is None


@pytest.mark.parametrize('workflow',['fixed_workflow','generic_tool_agent','domain_agent'])
def test_hard_deadline_also_bounds_the_scientific_tool(adapters,tmp_path,workflow,monkeypatch):
    # Test scientific-call termination after admission, independently of how
    # quickly source parsing and the model/tool setup happen on this host.
    p=projection(adapters,workflow); p['budget']['max_elapsed_seconds']=30.
    admitted=tmp_path/'scientific-admitted.txt'
    real_monotonic=time.monotonic
    class AdmissionClock:
        @staticmethod
        def monotonic():
            try: float(admitted.read_text())
            except (OSError,ValueError): return real_monotonic()
            # Fault-inject exhaustion of the existing total budget after
            # the scientific effect starts. Do not patch the global clock.
            return real_monotonic()+p['budget']['max_elapsed_seconds']
    def slow(*args,**kwargs):
        admitted.write_text(str(real_monotonic()))
        time.sleep(5)
    monkeypatch.setattr(adapters,'time',AdmissionClock)
    monkeypatch.setattr(adapters.application,'run_reproduction',slow)
    out=adapters.run_trial(p,EnvelopeTransport(p,complete_actions(workflow)),tmp_path/'scientific-timeout')
    assert admitted.is_file(), 'scientific tool must actually be admitted'
    assert real_monotonic()-float(admitted.read_text())<3
    assert out['record']['status']=='timeout'
    assert out['audit']['stop_reason']=='wall_deadline'
    assert out['record']['observation']['executed'] is True
    assert out['record']['telemetry']['tool_calls']==4


def test_supervisor_reaps_known_multiprocessing_descendant(adapters,tmp_path):
    pidfile=tmp_path/'descendant.pid'
    class Descendant(EnvelopeTransport):
        def request(self,payload,timeout_s):
            child=multiprocessing.get_context('fork').Process(target=time.sleep,args=(5,)); child.start()
            pidfile.write_text(str(child.pid)); time.sleep(5)
    p=projection(adapters); p['budget']['max_elapsed_seconds']=.4
    out=adapters.run_trial(p,Descendant(p,[]),tmp_path/'timeout')
    assert out['record']['status']=='timeout' and pidfile.exists()
    pid=int(pidfile.read_text())
    with pytest.raises(ProcessLookupError): os.kill(pid,0)


@pytest.mark.parametrize('workflow',['fixed_workflow','generic_tool_agent','domain_agent'])
def test_shared_context_limit_precedes_provider_request(adapters,tmp_path,workflow):
    p=projection(adapters,workflow); p['budget']['max_request_chars']=8000; p['task']='T'*7900
    out=adapters.run_trial(p,EnvelopeTransport(p,complete_actions(workflow)),tmp_path/'context')
    assert out['record']['telemetry']['model_calls']==0
    assert out['record']['observation']['executed'] is False
    assert out['audit']['stop_reason']=='context_budget_exhausted'


@pytest.mark.parametrize('workflow',['fixed_workflow','generic_tool_agent','domain_agent'])
def test_unadvertised_product_tools_cannot_be_used_by_any_arm(adapters,tmp_path,workflow):
    p=projection(adapters,workflow)
    out=adapters.run_trial(p,EnvelopeTransport(p,[action('load_skill',{'name':'ode_reproduction'})]),tmp_path/'forbidden')
    assert out['record']['status']=='invalid_output'
    assert out['record']['telemetry']['model_calls']==1
    assert out['record']['observation']['executed'] is False


def test_duplicate_experiment_keys_refuse_before_creating_output(adapters,tmp_path):
    p=projection(adapters); s=next(s for s in p['sources'] if s['role']=='experiment')
    text='{"times":[0,1],"times":[0,2]}'
    s.update(text=text,sha256=adapters.protocol.hashlib.sha256(text.encode()).hexdigest())
    with pytest.raises(ValueError,match='experiment'):
        adapters.run_trial(p,EnvelopeTransport(p,[]),tmp_path/'bad')
    assert not (tmp_path/'bad').exists()


@pytest.fixture
def driver():
    spec=importlib.util.spec_from_file_location('comparison_driver_acceptance',ROOT/'benchmarks/development/run_agent_comparison.py')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_driver_normalizes_actual_execution_without_awarding_unreviewed_model_scores(driver,tmp_path):
    m=driver.protocol.development_manifest(ROOT); m['cases']=m['cases'][:1]; m['repeats']=1
    seen=[]
    def factory(p):
        seen.append(p)
        return EnvelopeTransport(p,complete_actions(p['trial']['workflow']))
    out=driver.run_comparison(m,factory,tmp_path/'comparison')
    assert len(out['records'])==3 and len(seen)==3
    assert all(set(p)=={'trial','model_config','budget','shared_tools','task','sources'} for p in seen)
    assert out['engineering']['scientific_execution_attempts']==3
    assert out['engineering']['online_inference_calls']==0
    assert out['engineering']['provider_requests']==9
    assert all(s['correct_completion']['numerator']==0 for s in out['results']['summary'].values())
    for r in out['records']:
        driver.protocol._validate_record(m,next(t for t in driver.protocol.schedule(m) if t['id']==r['trial_id']),r)


def test_driver_cli_refuses_live_manifest_without_creating_client_or_output(driver,tmp_path):
    m=driver.protocol.development_manifest(ROOT); m['evidence_kind']='live_model'
    driver.adapters._write(tmp_path/'manifest.json',m)
    driver.adapters._write(tmp_path/'scripts.json',{'evidence_kind':'scripted_control','trials':{}})
    assert driver.main(['--manifest',str(tmp_path/'manifest.json'),'--scripts',str(tmp_path/'scripts.json'),
                        '--output',str(tmp_path/'refused')])==2
    assert not (tmp_path/'refused').exists()


@pytest.mark.parametrize('workflow',['fixed_workflow','generic_tool_agent','domain_agent'])
@pytest.mark.parametrize('defect',['time_unit','value_binding'])
def test_each_arm_rejects_explicit_input_mismatch_before_simulation(adapters,tmp_path,workflow,defect):
    p=projection(adapters,workflow)
    if defect=='time_unit':
        s=next(s for s in p['sources'] if s['role']=='experiment')
        value=json.loads(s['text']); value['time_unit']='second'; text=json.dumps(value)
        s.update(text=text,sha256=adapters.protocol.hashlib.sha256(text.encode()).hexdigest())
    def proposal(messages):
        response=decay_proposal(messages)
        if defect=='value_binding': response['args']['draft']['parameters'][0]['value']=.9
        return response
    out=adapters.run_trial(p,EnvelopeTransport(p,complete_actions(workflow,proposal)),tmp_path/'mismatch')
    assert out['record']['observation']['executed'] is False
    assert out['record']['observation']['state']!='completed'
    assert not ArtifactStore(out['store_root']).list('run')
