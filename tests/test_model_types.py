"""Independent analytic/manual controls and native lifecycle acceptance.

Synthetic development controls only: these are not independent paper scores.
"""
from copy import deepcopy
import json
import math
from pathlib import Path
import time

import numpy as np
import pytest

from biomodel_agent import agent, boolean, bundles, cli, individual, pde, recovery, reporting, stochastic
from biomodel_agent import typed_models as models
from biomodel_agent.model_contracts import WorkLimit
from biomodel_agent.storage import ArtifactStore, canonical_bytes

EXAMPLES=Path(__file__).resolve().parents[1]/'examples/model-types'


def model(name):
    return json.loads((EXAMPLES/(name+'.json')).read_text())


def settings(name):
    return json.loads((EXAMPLES/(name+'-settings.json')).read_text())


def imported(tmp_path,name):
    store=ArtifactStore(tmp_path/'store')
    return store,models.import_model(store,EXAMPLES/(name+'.json'))


class Scripted:
    def __init__(self, names): self.actions=iter(names); self.calls=0
    def respond(self,messages):
        self.calls+=1; value=next(self.actions)
        if isinstance(value,BaseException): raise value
        return value if isinstance(value,dict) else dict(action=value,args={})


def sleepy_worker(m,s,connection): time.sleep(5.)


def crashing_worker(m,s,connection): connection.close()


def test_ssa_independent_birth_death_moments():
    m=model('stochastic'); s=stochastic.config(dict(duration=2.,samples=3,trajectories=512,seed=702))
    result=stochastic.execute(m,s)
    survival=math.exp(-.5*2)
    mean=4*survival+10*(1-survival)
    variance=4*survival*(1-survival)+10*(1-survival)
    assert abs(result['mean'][-1][0]-mean)<6*math.sqrt(variance/512)
    assert abs(result['variance'][-1][0]-variance)<.3*variance
    assert all(v['status']=='pass' for v in stochastic.checks(m,s,result))


def test_ssa_combinatorial_propensity_and_conserved_isomerization():
    m=model('stochastic'); m['reactions']=[dict(id='pair',reactants={'X':2},products={},rate=2.)]
    stochastic.validate(m)
    assert stochastic.propensities(m,{'X':5}).tolist()==[20.]
    assert stochastic.propensities(m,{'X':1}).tolist()==[0.]
    m['species']=[dict(id='X',initial=20),dict(id='Y',initial=0)]
    m['reactions']=[dict(id='convert',reactants={'X':1},products={'Y':1},rate=2.)]
    s=stochastic.config(dict(duration=3.,trajectories=10,samples=11))
    data=stochastic.execute(m,s)
    assert np.all(np.sum(data['trajectories'],axis=2)==20)
    assert np.all(np.diff(np.asarray(data['trajectories'])[:,:,0],axis=1)<=0)


def test_ssa_absorbing_state_and_seeded_replay():
    m=model('stochastic'); m['species'][0]['initial']=0; m['reactions']=m['reactions'][1:]
    s=stochastic.config(dict(seed=91,trajectories=3,samples=5))
    r=stochastic.execute(m,s)
    assert r['event_count']==0 and np.count_nonzero(r['trajectories'])==0
    m=model('stochastic')
    assert stochastic.execute(m,s)==stochastic.execute(m,s)
    assert stochastic.execute(m,s)!=stochastic.execute(m,{**s,'seed':92})


def test_ssa_event_budget_is_global_not_per_path():
    m=model('stochastic');m['reactions']=m['reactions'][:1];m['reactions'][0]['rate']=1000.
    with pytest.raises(WorkLimit): stochastic.execute(m,stochastic.config(dict(max_events=2,trajectories=3)))


@pytest.mark.parametrize('boundary',['dirichlet_zero','no_flux','periodic'])
def test_pde_independent_continuum_modes_and_second_order_grid_convergence(boundary):
    errors=[]
    for n in (17,33):
        m=model('pde');m['boundary']=boundary;m['initial']=[0.]*n
        x,_=pde.grid(m); k=math.pi if boundary!='periodic' else 2*math.pi
        initial=np.sin(k*x) if boundary=='dirichlet_zero' else 2+np.cos(k*x)
        if boundary=='dirichlet_zero':initial[[0,-1]]=0.
        m['initial']=initial.tolist();pde.validate(m)
        s=pde.config(dict(duration=.2,samples=3));r=pde.execute(m,s)
        reference=(np.sin(k*x)*math.exp(-(.25*k*k+.1)*.2) if boundary=='dirichlet_zero' else
                   2*math.exp(-.1*.2)+np.cos(k*x)*math.exp(-(.25*k*k+.1)*.2))
        errors.append(float(np.max(np.abs(np.array(r['values'][-1])-reference))))
        assert all(v['status']=='pass' for v in pde.checks(m,s,r)),r
    assert errors[1]<.3*errors[0]
    assert errors[1]<.002


@pytest.mark.parametrize('boundary',['no_flux','periodic'])
def test_pde_closed_mass_and_zero_diffusion_decay(boundary):
    m=model('pde');m.update(boundary=boundary,initial=[1.,2.,3.,4.,5.],diffusion=0.,decay=.7)
    s=pde.config(dict(duration=2.,samples=5));r=pde.execute(m,s)
    assert np.allclose(r['values'][-1],np.array(m['initial'])*math.exp(-1.4),rtol=1e-12)
    assert r['mass'][-1]==pytest.approx(r['mass'][0]*math.exp(-1.4),rel=1e-12)
    assert all(v['status']=='pass' for v in pde.checks(m,s,r))


def test_boolean_manual_feedback_cycle_and_static_intervention():
    m=model('boolean');s=boolean.config(dict(steps=4));r=boolean.execute(m,s)
    assert r['states']==[[False,False],[True,False],[True,True],[False,True],[False,False]]
    assert r['synchronous_cycle']==dict(first_step=0,period=4) and not r['terminal_fixed_point']
    m['clamps']={'A':False};r=boolean.execute(m,s)
    assert r['states']==[[False,False]]*5 and r['terminal_fixed_point']
    assert all(v['status']=='pass' for v in boolean.checks(m,s,r))


def test_boolean_random_sequential_is_a_sweep_not_parallel():
    m=model('boolean');m['update']='random_sequential';s=boolean.config(dict(steps=8,seed=400))
    r=boolean.execute(m,s)
    assert r==boolean.execute(m,s)
    assert r['synchronous_cycle'] is None and r['asynchronous_attractor']=='not_assessed'
    for before,after,order in zip(r['states'],r['states'][1:],r['update_orders']):
        a,b=before
        for name in order:
            if name=='A':a=not b
            else:b=a
        assert after==[a,b]
    assert all(v['status']=='pass' for v in boolean.checks(m,s,r))


@pytest.mark.parametrize('text',['__import__("os")','A.__class__','A[0]','A + B','1','A == B','unknown','[A]','lambda: A','A if B else False'])
def test_boolean_rule_grammar_rejects_non_boolean_code(text):
    m=model('boolean');m['nodes'][0]['rule']=text
    with pytest.raises(ValueError):boolean.validate(m)


def test_individual_births_wait_and_lineage_is_explicit():
    m=model('individual');m.update(width=3,height=3,initial=[[1,1]],probabilities=dict(death=0.,move=0.,divide=1.))
    s=individual.config(dict(steps=2,seed=77));r=individual.execute(m,s)
    assert r['population']==[1,2,4]
    assert r['events'][0]['births'][0]['parent']==0
    assert r['events'][0]['activation_order']==[0]
    assert set(r['events'][1]['activation_order'])=={0,1}
    assert all(v['status']=='pass' for v in individual.checks(m,s,r))


def test_individual_saturated_space_inhibits_division():
    m=model('individual');m.update(width=2,height=2,initial=[[0,0],[0,1],[1,0],[1,1]],probabilities=dict(death=0.,move=1.,divide=1.))
    s=individual.config(dict(steps=5));r=individual.execute(m,s)
    assert r['population']==[4]*6 and all(not e['moves'] and not e['births'] for e in r['events'])


@pytest.mark.parametrize('boundary',['closed','periodic'])
def test_individual_migration_conserves_population_and_seed(boundary):
    m=model('individual');m['boundary']=boundary;m['probabilities']=dict(death=0.,move=1.,divide=0.)
    s=individual.config(dict(steps=30,seed=51));r=individual.execute(m,s)
    assert r['population']==[2]*31 and r==individual.execute(m,s)
    assert r!=individual.execute(m,{**s,'seed':52})
    assert all(v['status']=='pass' for v in individual.checks(m,s,r))


def test_individual_certain_death_extinction_and_empty_population():
    m=model('individual');m['probabilities']=dict(death=1.,move=1.,divide=1.)
    s=individual.config(dict(steps=3));r=individual.execute(m,s)
    assert r['population']==[2,0,0,0] and not any(e['births'] for e in r['events'])
    m['initial']=[];assert individual.execute(m,s)['population']==[0]*4


def test_individual_activation_budget_global():
    with pytest.raises(WorkLimit):individual.execute(model('individual'),individual.config(dict(max_activations=1)))


@pytest.mark.parametrize('name',models.TYPES)
def test_native_lifecycle_receipts_report_replay_and_detached_bundle(tmp_path,name):
    store,ref=imported(tmp_path,name);s=settings(name);plan=models.prepare(store,ref,s)
    assert models.status(store,plan)['state']=='ready'
    run=models.execute(store,plan);out=models.summary(store,run)
    assert out['completion_eligible'] and out['scientific_support']=='unknown',out
    assert models.execute(store,plan)==run and len(store.list('typed_execution'))==1
    assert models.status(store,plan)['run_ref']==run
    report=reporting.render_report(store,run)
    assert name in report and 'unknown' in report and 'not_requested' in report
    assert 'native' in reporting.render_report(store,ref).lower()
    rerun=models.replay(store,run)
    assert models.result(store,rerun)[2]==models.result(store,run)[2]
    assert models.result(store,rerun)[0]['replay_of']==run and len(store.list('typed_execution'))==2
    bundles.export_bundle(store,run,tmp_path/'bundle');detached=ArtifactStore(tmp_path/'bundle')
    assert reporting.render_report(detached,run)==report
    assert models.summary(detached,run)==out
    detached_replay=models.replay(detached,run)
    assert models.result(detached,detached_replay)[2]==models.result(store,run)[2]


@pytest.mark.parametrize('name',models.TYPES)
def test_typed_agent_routes_tools_budget_and_recovery_boundary(tmp_path,name):
    store,ref=imported(tmp_path,name);provider=Scripted(['inspect_typed','run_typed','finish'])
    ar=agent.run_model_agent(store,provider,model_type=name,model_ref=ref,settings=settings(name))
    p=store.get(ar)['payload']
    assert p['state']=='completed' and p['call_count']==3 and p['run_count']==1
    assert models.summary(store,p['run_ref'])['completion_eligible']
    assert name in reporting.render_report(store,ar)
    diagnostic=recovery.diagnose_execution(store,p['execution_ref'])
    assert diagnostic['status']=='blocked' and diagnostic['run_count'] is None
    assert 'native_typed_recovery_not_supported_inspect_receipts_no_retry' in diagnostic['reasons']


@pytest.mark.parametrize('actions',[['finish'],['run_typed','run_typed'],['run'],[dict(action='run_typed',args={'seed':999})],[RuntimeError('secret must not persist')]])
def test_typed_agent_invalid_actions_no_false_completion_or_extra_runs(tmp_path,actions):
    store,ref=imported(tmp_path,'boolean');provider=Scripted(actions)
    ar=agent.run_model_agent(store,provider,model_type='boolean',model_ref=ref,settings=dict(steps=2),max_calls=2)
    p=store.get(ar)['payload'];assert p['state']=='incomplete' and p['run_count']<=1
    assert len(store.list('typed_execution'))<=1
    assert 'secret must not persist' not in json.dumps(p)


def test_agent_rejects_mismatched_type_before_provider(tmp_path):
    store,ref=imported(tmp_path,'boolean');provider=Scripted(['run_typed'])
    with pytest.raises(ValueError,match='model_type'):
        agent.run_model_agent(store,provider,model_type='pde',model_ref=ref)
    assert provider.calls==0 and not store.list('agent_task')


def test_native_unknown_effect_blocks_retry_even_when_index_row_missing(tmp_path,monkeypatch):
    store,ref=imported(tmp_path,'boolean');inp=models.prepare(store,ref,dict(steps=1))
    reservation=store.put('typed_execution',dict(version=1,input_ref=inp,phase='reserved'),refs=(inp,))
    with store._db() as db:db.execute('DELETE FROM artifact_index WHERE ref = ?', (reservation,))
    assert models.status(store,inp)['state']=='unknown_effects'
    monkeypatch.setattr(models,'solve',lambda *a:pytest.fail('must never recompute unknown effect'))
    with pytest.raises(ValueError,match='unknown or partial'):models.execute(store,inp)


def test_native_owner_excludes_concurrent_execute(tmp_path):
    store,ref=imported(tmp_path,'boolean');inp=models.prepare(store,ref)
    with models._owner(store,inp):
        with pytest.raises(ValueError,match='active owner'):models.execute(store,inp)
    assert not store.list('typed_execution')


def test_native_environment_change_blocks_unexecuted_plan(tmp_path,monkeypatch):
    store,ref=imported(tmp_path,'boolean');inp=models.prepare(store,ref)
    from biomodel_agent import application
    monkeypatch.setattr(application,'environment_fingerprint',lambda:{'different':True})
    with pytest.raises(ValueError,match='environment changed'):models.execute(store,inp)
    assert not store.list('typed_execution')


def test_native_read_recomputes_checks_instead_of_trusting_saved_pass(tmp_path):
    store,ref=imported(tmp_path,'boolean');run=models.run(store,ref,dict(steps=2))
    original=store.get(run);p=original['payload'];receipt=store.get(p['receipt_ref'])
    payload=deepcopy(receipt['payload']);payload['result']['data']['states'][1]=[False,False]
    changed_receipt=store.put('typed_execution_receipt',payload,refs=receipt['refs'])
    copied=deepcopy(p);copied['receipt_ref']=changed_receipt
    changed=store.put('typed_run',copied,refs=(p['input_ref'],p['execution_ref'],changed_receipt))
    assert not models.summary(store,changed)['completion_eligible']
    assert models.summary(store,changed)['status']=='numerical_failure'


def test_native_receipt_cross_binding_rejected(tmp_path):
    store,ref=imported(tmp_path,'boolean');a=models.run(store,ref,dict(steps=1));b=models.run(store,ref,dict(steps=2))
    pa=store.get(a)['payload'];pb=store.get(b)['payload'];changed={**pa,'receipt_ref':pb['receipt_ref']}
    x=store.put('typed_run',changed,refs=(pa['input_ref'],pa['execution_ref'],pb['receipt_ref']))
    with pytest.raises(ValueError,match='mismatch'):models.summary(store,x)


@pytest.mark.parametrize('worker,status',[(sleepy_worker,'time_limit'),(crashing_worker,'failed')])
def test_worker_timeout_and_crash_are_not_success(monkeypatch,worker,status):
    monkeypatch.setattr(models,'_worker',worker)
    s=boolean.config(dict(timeout_s=.2 if status=='time_limit' else 10.))
    start=time.monotonic();r=models.solve(model('boolean'),s)
    assert r['status']==status and time.monotonic()-start<5.
    assert not models.verification(model('boolean'),s,r)['completion_eligible']


@pytest.mark.parametrize('name,key,value',[('stochastic','seed',True),('stochastic','samples',1),('stochastic','duration',float('nan')),
    ('pde','seed',1),('pde','duration',-1),('boolean','steps',1.5),('boolean','seed',-1),('individual','steps',257),('individual','max_activations',False)])
def test_strict_settings_reject_wrong_types_unknowns_and_ranges(name,key,value):
    with pytest.raises(ValueError):models.backend(name).config({key:value})


@pytest.mark.parametrize('name',models.TYPES)
def test_strict_model_schema_rejects_unknown_fields_and_version_bool(name):
    m=model(name);m['garbage']=1
    with pytest.raises(ValueError):models.backend(name).validate(m)
    m=model(name);m['schema_version']=True
    with pytest.raises(ValueError):models.backend(name).validate(m)


@pytest.mark.parametrize('name',models.TYPES)
def test_native_cli_import_run_prepare_execute_status_report_export(tmp_path,capsys,name):
    root=str(tmp_path/'cli-store')
    def invoke(args,code=0):
        assert cli.main(args)==code
        return json.loads(capsys.readouterr().out)
    ref=invoke(['typed-import',str(EXAMPLES/(name+'.json')),'--store',root])['ref']
    plan=invoke(['typed-prepare',ref,'--store',root,'--config',str(EXAMPLES/(name+'-settings.json'))])['ref']
    run=invoke(['typed-execute',plan,'--store',root])['ref']
    assert invoke(['typed-status',plan,'--store',root])['run_ref']==run
    assert cli.main(['report',run,'--store',root,'--output',str(tmp_path/'report.md')])==0
    capsys.readouterr();assert name in (tmp_path/'report.md').read_text()
    assert cli.main(['export',run,'--store',root,'--output',str(tmp_path/'export')])==0
    capsys.readouterr();assert models.summary(ArtifactStore(tmp_path/'export'),run)['completion_eligible']


def test_capabilities_lists_six_explicit_bounded_profiles(capsys):
    assert cli.main(['model-types'])==0
    values=json.loads(capsys.readouterr().out)
    assert [v['model_type'] for v in values]==['ode','fba',*models.TYPES]
    assert all(v['scientific_support']=='unknown' for v in values[2:])


def test_native_import_duplicate_json_keys_rejected_before_artifact(tmp_path):
    path=tmp_path/'bad.json';path.write_text('{"model_type":"boolean","model_type":"pde"}')
    store=ArtifactStore(tmp_path/'store')
    with pytest.raises(ValueError,match='duplicate'):models.import_model(store,path)
    assert not store.list('typed_model')


@pytest.mark.parametrize('name,change',[
    ('stochastic',lambda m:m['species'][0].update(initial=True)),
    ('stochastic',lambda m:m.update(rate_convention='deterministic_concentration')),
    ('stochastic',lambda m:m['reactions'][0].update(products={'unknown':1})),
    ('stochastic',lambda m:m['reactions'][0].update(reactants={'X':3})),
    ('pde',lambda m:m.update(boundary='arbitrary')),
    ('pde',lambda m:m['initial'].__setitem__(0,1.)),
    ('pde',lambda m:m.update(diffusion=-1.)),
    ('boolean',lambda m:m['nodes'][0].update(initial=0)),
    ('boolean',lambda m:m.update(clamps={'B':True})),
    ('boolean',lambda m:m.update(update='unknown_async')),
    ('individual',lambda m:m['initial'].append(m['initial'][0])),
    ('individual',lambda m:m['probabilities'].update(death=1.01)),
    ('individual',lambda m:m['initial'].__setitem__(0,[8,0])),
])
def test_native_unsupported_or_inconsistent_models_fail_before_import(tmp_path,name,change):
    m=model(name);change(m);path=tmp_path/'invalid.json';path.write_text(json.dumps(m))
    store=ArtifactStore(tmp_path/'store')
    with pytest.raises(ValueError):models.import_model(store,path)
    assert not store.list('typed_model') and not store.list('typed_execution')


@pytest.mark.parametrize('name',models.TYPES)
def test_model_output_envelope_is_not_trusted_over_raw_input(tmp_path,name):
    store,ref=imported(tmp_path,name);obj=store.get(ref);p=deepcopy(obj['payload']);p['model']['id']='forged'
    altered=store.put('typed_model',p,refs=obj['refs'])
    with pytest.raises(ValueError,match='raw file'):models.load(store,altered)


@pytest.mark.parametrize('name,conf',[
    ('stochastic',dict(trajectories=512,samples=1001)),
    ('pde',dict(duration=1e6)),
    ('boolean',dict(steps=10000)),
    ('individual',dict(steps=256))])
def test_native_combined_work_and_output_compatibility_before_reservation(tmp_path,name,conf):
    m=model(name)
    if name=='boolean':
        m['nodes']=[dict(id='N'+str(i),initial=False,rule='False') for i in range(64)]
    elif name=='individual':
        m.update(width=32,height=32)
    path=tmp_path/'model.json';path.write_text(json.dumps(m));store=ArtifactStore(tmp_path/'store')
    ref=models.import_model(store,path)
    with pytest.raises(ValueError,match='budget|envelope'):models.prepare(store,ref,conf)
    assert not store.list('typed_input') and not store.list('typed_execution')


@pytest.mark.parametrize('name,conf',[('stochastic',dict(max_events=1,duration=100.)),('individual',dict(max_activations=1))])
def test_actual_worker_budget_failure_is_saved_and_agent_stays_incomplete(tmp_path,name,conf):
    store,ref=imported(tmp_path,name);ar=agent.run_model_agent(store,Scripted(['run_typed','finish']),model_type=name,model_ref=ref,settings=conf)
    p=store.get(ar)['payload'];out=models.summary(store,p['run_ref'])
    assert p['state']=='incomplete' and p['run_count']==1 and p['call_count']==1
    assert out['status']=='work_limit' and not out['completion_eligible']
    assert 'No complete trajectory' in reporting.render_report(store,p['run_ref'])
    inp=store.get(p['run_ref'])['payload']['input_ref']
    assert models.execute(store,inp)==p['run_ref']


@pytest.mark.parametrize('name',models.TYPES)
def test_type_specific_saved_result_damage_fails_checks(name):
    b=models.backend(name);m=model(name);s=b.config(settings(name));r=b.execute(m,s)
    if name=='stochastic':r['trajectories'][0][-1][0]=-1
    elif name=='pde':r['values'][-1][0]=1.
    elif name=='boolean':r['states'][1]=[False,False]
    else:r['frames'][-1].append(r['frames'][-1][0])
    verified=models.verification(m,s,dict(status='succeeded',data=r))
    assert not verified['completion_eligible'] and verified['status']=='numerical_failure'


def test_empty_success_and_unknown_status_cannot_qualify():
    m=model('boolean');s=boolean.config()
    assert models.verification(m,s,dict(status='succeeded',data=None))['status']=='numerical_failure'
    assert models.verification(m,s,dict(status='invented_success',data=None))['status']=='failed'
