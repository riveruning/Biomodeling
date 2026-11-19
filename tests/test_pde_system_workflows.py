"""End-to-end acceptance of the multi-field PDE system through ordinary entrances.

Ordinary entrances keep ``model_type='pde'``; the declared profile
``reaction_diffusion_cartesian_v1`` selects the new backend while the frozen
legacy linear profile keeps its semantics. All inputs are synthetic; numeric
controls use the zero-diffusion logistic analytic solution. No literature
pools, credentials, independent evaluation sets or external services are used.
"""
from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pytest

from biomodel_agent import agent, bundles, cli, pde, pde_system, reporting
from biomodel_agent import research_projects as projects, research_results, research_tasks as tasks
from biomodel_agent import typed_models as models, typed_references as references
from biomodel_agent.storage import ArtifactStore
from test_fba import Scripted, act
from test_research_projects import frame, step

ROOT=Path(__file__).resolve().parents[1]
EXAMPLES=ROOT/'examples/model-types'


def synthetic(shape=(6,),lengths=None):
    """Two-field zero-diffusion logistic system with independent analytic solutions."""
    lengths=[float(v) for v in (lengths if lengths is not None else [2.0]*len(shape))]
    size=1
    for value in shape:size*=value
    u0=np.linspace(0.2,0.95,size).reshape(shape)
    v0=(0.4+0.2*np.linspace(0.,1.,size)).reshape(shape)
    return dict(schema_version=1,model_type='pde',profile=pde_system.PROFILE,id='workflow_system',
        units={'time':'arbitrary time unit','space':'arbitrary length unit','state':'dimensionless synthetic concentration'},
        grid=dict(shape=list(shape),lengths=lengths),boundary='no_flux',
        parameters={'rate':0.8,'capacity':1.7,'rate_v':0.5,'capacity_v':2.0},
        fields=[
            dict(id='u',diffusion=0.0,initial=u0.tolist(),reaction='rate*u*(1 - u/capacity)',nonnegative=True),
            dict(id='v',diffusion=0.0,initial=v0.tolist(),reaction='rate_v*v*(1 - v/capacity_v)',nonnegative=True)],
        note='Synthetic zero-diffusion two-field logistic system; not a biological claim.')


def settings(**changes):
    values=dict(duration=1.,samples=5,method='RK45',rtol=1e-10,atol=1e-12)
    values.update(changes)
    return values


def exact(model,field_id,times):
    """Analytic logistic solution u(t)=K/(1+(K-u0)/u0*exp(-r t))."""
    suffix='' if field_id=='u' else '_v'
    rate=model['parameters']['rate'+suffix];capacity=model['parameters']['capacity'+suffix]
    initial=np.asarray(next(f['initial'] for f in model['fields'] if f['id']==field_id),dtype=float)
    decay=np.exp(-rate*np.asarray(times,dtype=float).reshape((-1,)+(1,)*initial.ndim))
    return capacity/(1.+((capacity-initial)/initial)*decay)


def system_reference(model,raw_settings,*,threshold=1e-6):
    cfg=pde_system.config(raw_settings)
    times=np.linspace(0.,cfg['duration'],cfg['samples'])
    shape=model['grid']['shape'];lengths=model['grid']['lengths']
    axes={}
    for index,name in enumerate(('x','y')):
        if index<len(shape):
            axes[name]=((np.arange(shape[index])+0.5)*(lengths[index]/shape[index])).tolist()
    field_ids=[f['id'] for f in model['fields']]
    values=[[exact(model,field_id,[t])[0].tolist() for field_id in field_ids] for t in times.tolist()]
    scales=[max(1.,float(np.max(np.abs(exact(model,field_id,times))))) for field_id in field_ids]
    return dict(schema_version=1,model_type='pde',profile=references.PDE_SYSTEM_PROFILE,
        units=deepcopy(model['units']),times=times.tolist(),axes=axes,field_ids=field_ids,
        values=values,scales=scales,threshold=threshold)


def _write(tmp_path,model):
    path=tmp_path/'model.json';path.write_text(json.dumps(model),encoding='utf-8')
    return path


def _project(store,reference_refs=()):
    config=dict(scenario='Study a synthetic multi-field reaction-diffusion hypothesis.',
        policy=dict(planner_max_calls=20,max_native_calls=18))
    if reference_refs:config['reference_refs']=list(reference_refs)
    project=projects.create(store,config)
    step(store,project,'revise_frame',frame=frame(),reason='Choose the research target and granularity.')
    return project


def _iteration(store,project,model,*,native_agent=False,reference_ref=None):
    args=dict(reason='Freeze an explicit multi-field hypothesis for development.',
        config=dict(model_type='pde',execution=dict(settings=settings())),
        native_draft=model,hypothesis_ids=['H1'])
    if native_agent:args['native_agent']=True
    if reference_ref:args['reference_ref']=reference_ref
    return step(store,project,'open_iteration',**args)['iteration_ref']


def test_backend_selection_uses_the_declared_profile():
    assert models.backend('pde',model=synthetic()) is pde_system
    assert models.backend('pde') is pde
    legacy=json.loads((EXAMPLES/'pde.json').read_text())
    assert models.backend('pde',model=legacy) is pde
    assert models.backend('pde',model=dict(model_type='pde',profile='reaction_diffusion_cartesian_v2')) is pde
    with pytest.raises(ValueError,match='model_type'):
        models.backend('pde',model=dict(model_type='boolean',profile=pde_system.PROFILE))


def test_forged_profile_is_rejected_by_the_real_validator(tmp_path):
    forged=dict(schema_version=1,model_type='pde',profile=pde_system.PROFILE,id='forged',
        units={'time':'t','space':'m','state':'c'},length=1.,diffusion=.1,decay=0.,
        boundary='periodic',initial=[1.,2.,3.])
    store=ArtifactStore(tmp_path/'store')
    with pytest.raises(ValueError):models.import_model(store,_write(tmp_path,forged))
    assert not store.list('typed_model')


def test_system_lifecycle_import_prepare_execute_status_report_export_replay(tmp_path):
    store=ArtifactStore(tmp_path/'store')
    ref=models.import_model(store,_write(tmp_path,synthetic()))
    stored=store.get(ref)['payload']
    assert stored['model_type']=='pde' and stored['profile']==pde_system.PROFILE
    loaded=models.load(store,ref)
    assert loaded==synthetic() and models.backend('pde',model=loaded) is pde_system
    raw=settings()
    plan=models.prepare(store,ref,raw)
    inp=store.get(plan)['payload']
    assert inp['profile']==pde_system.PROFILE and inp['settings']==pde_system.config(raw)
    assert models.status(store,plan)['state']=='ready'
    run=models.execute(store,plan)
    assert models.execute(store,plan)==run and len(store.list('typed_execution'))==1
    assert models.status(store,plan)['run_ref']==run
    out=models.summary(store,run)
    assert out['profile']==pde_system.PROFILE and out['status']=='succeeded' and out['completion_eligible']
    assert all(v['status']=='pass' for v in out['checks'])
    _,model,outcome=models.result(store,run)
    data=outcome['data']
    assert data['profile']==pde_system.PROFILE and data['field_ids']==['u','v']
    assert data['time_integration']=='method_of_lines_RK45' and data['grid_kind']=='cell_centered'
    times=np.asarray(data['times'])
    np.testing.assert_allclose(np.asarray(data['values'])[:,0],exact(model,'u',times),rtol=1e-7,atol=1e-9)
    np.testing.assert_allclose(np.asarray(data['values'])[:,1],exact(model,'v',times),rtol=1e-7,atol=1e-9)
    report=reporting.render_report(store,run)
    assert pde_system.PROFILE in report and '"u"' in report and 'method_of_lines_RK45' in report
    assert 'final_values_preview' in report and '"dx"' not in report
    assert pde_system.PROFILE in reporting.render_report(store,ref)
    rerun=models.replay(store,run)
    assert models.result(store,rerun)[0]['replay_of']==run
    assert models.result(store,rerun)[2]==models.result(store,run)[2]
    assert len(store.list('typed_execution'))==2
    bundles.export_bundle(store,run,tmp_path/'bundle')
    detached=ArtifactStore(tmp_path/'bundle')
    assert models.summary(detached,run)==out
    assert reporting.render_report(detached,run)==report


def test_system_cli_import_prepare_execute_status_report_export(tmp_path,capsys):
    root=str(tmp_path/'cli-store')
    config_path=tmp_path/'settings.json';config_path.write_text(json.dumps(settings()))
    model_path=_write(tmp_path,synthetic())

    def invoke(args,code=0):
        assert cli.main(args)==code
        return json.loads(capsys.readouterr().out)

    ref=invoke(['typed-import',str(model_path),'--store',root])['ref']
    assert invoke(['typed-import',str(model_path),'--store',root])['ref']==ref
    plan=invoke(['typed-prepare',ref,'--store',root,'--config',str(config_path)])['ref']
    run=invoke(['typed-execute',plan,'--store',root])['ref']
    assert invoke(['typed-status',plan,'--store',root])['run_ref']==run
    assert cli.main(['report',run,'--store',root,'--output',str(tmp_path/'report.md')])==0
    capsys.readouterr()
    text=(tmp_path/'report.md').read_text()
    assert pde_system.PROFILE in text and 'final_values_preview' in text
    assert cli.main(['export',run,'--store',root,'--output',str(tmp_path/'export')])==0
    capsys.readouterr()
    assert models.summary(ArtifactStore(tmp_path/'export'),run)['completion_eligible']


def test_system_agent_run_typed_and_finish(tmp_path):
    store=ArtifactStore(tmp_path/'store')
    ref=models.import_model(store,_write(tmp_path,synthetic()))
    provider=Scripted([act('inspect_typed'),act('run_typed'),act('finish')])
    ar=agent.run_model_agent(store,provider,model_type='pde',model_ref=ref,settings=settings())
    payload=store.get(ar)['payload']
    assert payload['state']=='completed' and payload['call_count']==3 and payload['run_count']==1
    out=models.summary(store,payload['run_ref'])
    assert out['profile']==pde_system.PROFILE and out['completion_eligible']
    _,model,outcome=models.result(store,payload['run_ref'])
    data=outcome['data']
    np.testing.assert_allclose(np.asarray(data['values'])[:,1],exact(model,'v',np.asarray(data['times'])),
                               rtol=1e-7,atol=1e-9)


def test_research_task_intake_and_execution_use_the_actual_backend(tmp_path):
    store=ArtifactStore(tmp_path/'store')
    ref=models.import_model(store,_write(tmp_path,synthetic()))
    config=dict(schema_version=1,goal='Execute the supplied multi-field reaction-diffusion system.',
        model_type='pde',model_ref=ref,execution={'settings':settings()})
    request=tasks.prepare(store,config)
    view=tasks.preflight(store,request)
    assert view['state']=='ready_to_run' and not view['gaps']
    wrapper=tasks.execute(store,Scripted([act('run_typed'),act('finish')]),request)
    result=tasks.result(store,wrapper)
    assert result['state']=='completed' and result['model_type']=='pde' and result['run_count']==1
    agent_run=store.get(result['native_run_ref'])['payload']
    typed_input=store.get(agent_run['input_ref'])['payload']
    assert typed_input['profile']==pde_system.PROFILE
    # The actual backend normalized the settings: new method/rtol keys survive.
    assert typed_input['settings']['method']=='RK45' and 'rtol' in typed_input['settings']
    assert 'max_rhs_evaluations' in typed_input['settings']
    out=models.summary(store,agent_run['run_ref'])
    assert out['profile']==pde_system.PROFILE and out['completion_eligible']
    assert tasks.status(store,request)['result_ref']==wrapper
    assert tasks.execute(store,None,request)==wrapper


def test_research_task_settings_are_validated_against_the_actual_backend(tmp_path):
    store=ArtifactStore(tmp_path/'store')
    new_ref=models.import_model(store,_write(tmp_path,synthetic()))
    legacy_ref=models.import_model(store,EXAMPLES/'pde.json')
    # New settings keys on the frozen legacy profile stay rejected by the legacy backend.
    legacy=dict(schema_version=1,goal='Legacy model with new settings keys.',model_type='pde',
        model_ref=legacy_ref,execution={'settings':{'method':'RK45'}})
    request=tasks.prepare(store,legacy)
    view=tasks.preflight(store,request)
    assert [g['code'] for g in view['gaps']]==['invalid_native_input']
    assert tasks.status(store,request)['state']=='awaiting_input'
    with pytest.raises(ValueError):tasks.execute(store,Scripted([]),request)
    assert not store.list(tasks.EXECUTION)
    # Settings the actual new backend rejects also stay an intake gap.
    bad=dict(schema_version=1,goal='Bad sample count.',model_type='pde',model_ref=new_ref,
        execution={'settings':{'samples':1}})
    assert 'invalid_native_input' in [g['code'] for g in tasks.preflight(store,tasks.prepare(store,bad))['gaps']]
    # Without a model the model_required gap is kept.
    empty=tasks.prepare(store,dict(schema_version=1,goal='No model yet.',model_type='pde',
        execution={'settings':{'method':'RK45'}}))
    empty_view=tasks.status(store,empty)
    assert empty_view['state']=='awaiting_input'
    assert 'model_required' in [g['code'] for g in empty_view['gaps']]


def test_project_native_draft_direct_execution_keeps_the_new_profile(tmp_path):
    store=ArtifactStore(tmp_path/'store');project=_project(store)
    iteration=_iteration(store,project,synthetic())
    stored=store.get(projects.own(store,iteration,projects.ITERATION,project)['config']['model_ref'])['payload']
    assert stored['origin']=='agent_project_hypothesis' and stored['profile']==pde_system.PROFILE
    typed_run=step(store,project,'run_iteration',iteration_ref=iteration)['result_ref']
    assert store.get(typed_run)['kind']=='typed_run'
    out=models.summary(store,typed_run)
    assert out['profile']==pde_system.PROFILE and out['completion_eligible']
    _,model,outcome=models.result(store,typed_run)
    data=outcome['data']
    np.testing.assert_allclose(np.asarray(data['values'])[:,0],exact(model,'u',np.asarray(data['times'])),
                               rtol=1e-7,atol=1e-9)
    facts=projects.facts(store,iteration)
    assert facts['numeric_status']=='pass' and facts['comparison_status']=='not_requested'
    assert step(store,project,'run_iteration',iteration_ref=iteration)['result_ref']==typed_run
    assessment=step(store,project,'assess_iteration',iteration_ref=iteration,interpretation='consistent',
        rationale='Frozen native checks passed inside the declared scope.',evidence_ids=[])['assessment_ref']
    step(store,project,'conclude',outcome='completed_with_limits',
        summary='Numerically consistent hypothetical model only.',assessment_refs=[assessment],
        limitations=['No biological or independent validation.'])
    assert projects.inspect(store,project)['state']=='completed_with_limits'


def test_project_agent_execution_and_result_view_use_the_new_profile(tmp_path):
    store=ArtifactStore(tmp_path/'store');project=_project(store)
    iteration=_iteration(store,project,synthetic(),native_agent=True)
    result=projects.operate(store,project,'run_iteration',dict(iteration_ref=iteration),
        provider=Scripted([act('run_typed'),act('finish')]))
    payload=tasks.result(store,result['result_ref'])
    assert payload['state']=='completed' and payload['model_type']=='pde'
    agent_run=store.get(payload['native_run_ref'])['payload']
    assert agent_run['state']=='completed' and agent_run['run_count']==1
    out=models.summary(store,agent_run['run_ref'])
    assert out['profile']==pde_system.PROFILE and out['completion_eligible']
    facts=projects.facts(store,iteration)
    assert facts['numeric_status']=='pass'
    assert pde_system.PROFILE in reporting.render_report(store,result['result_ref'])
    snap=store.get(projects.snapshot(store,project))['payload']
    assert snap['view']['iteration_facts'][iteration]['numeric_status']=='pass'


def test_project_result_views_render_field_curves_and_2d_slices(tmp_path):
    store=ArtifactStore(tmp_path/'store')
    for shape,lengths in (((6,),(2.0,)),((3,4),(1.1,0.8))):
        project=_project(store)
        iteration=_iteration(store,project,synthetic(shape,lengths))
        typed_run=step(store,project,'run_iteration',iteration_ref=iteration)['result_ref']
        _,model,outcome=models.result(store,typed_run)
        data=outcome['data']
        snap=store.get(projects.snapshot(store,project))['payload']
        collected=research_results.collect(store,project,snap['view'])
        plot=collected['iterations'][0]['plots'][0]
        assert plot['x_label']=='position' and plot['x']==data['axes']['x']
        assert [s['name'] for s in plot['series']]==['u','v']
        if len(shape)==1:
            assert 'per declared field' in plot['note']
            for series in plot['series']:
                np.testing.assert_allclose(series['values'],
                    data['values'][-1][['u','v'].index(series['name'])])
        else:
            y_index=len(data['axes']['y'])//2
            assert 'y='+str(data['axes']['y'][y_index]) in plot['note']
            assert 'x-slice at fixed y' in plot['note']
            for series in plot['series']:
                expected=[row[y_index] for row in data['values'][-1][['u','v'].index(series['name'])]]
                np.testing.assert_allclose(series['values'],expected)
        # The receipt keeps the complete multi-field grid, not only the slice.
        assert np.asarray(data['values']).shape==(settings()['samples'],2,*shape)


def test_system_reference_passes_per_field_against_a_real_run():
    model=synthetic();cfg=pde_system.config(settings())
    outcome=dict(status='succeeded',data=pde_system.execute(model,cfg))
    spec=system_reference(model,settings())
    verdict=references.compare(spec,model,cfg,outcome)
    assert verdict['status']=='pass' and verdict['metric']=='per_field_scale_rmse'
    assert set(verdict['scores'])=={'u','v'}
    assert all(value<=spec['threshold'] for value in verdict['scores'].values())
    incompatible=deepcopy(spec);incompatible['units']=dict(incompatible['units'],time='different unit')
    with pytest.raises(ValueError,match='units mismatch'):references.compare(incompatible,model,cfg,outcome)


@pytest.mark.parametrize('defect',['field_order','coordinates','units','one_field','shape','forged_profile'])
def test_system_reference_negative_controls_are_rejected(defect):
    model=synthetic();cfg=pde_system.config(settings())
    outcome=dict(status='succeeded',data=pde_system.execute(model,cfg))
    spec=system_reference(model,settings())
    if defect=='field_order':
        spec['field_ids']=list(reversed(spec['field_ids']))
        spec['values']=[list(reversed(per_time)) for per_time in spec['values']]
        spec['scales']=list(reversed(spec['scales']))
    elif defect=='coordinates':
        spec['times']=[t+0.1 for t in spec['times']]
        spec['axes']['x']=[v+1e-6 for v in spec['axes']['x']]
    elif defect=='units':
        spec['units']=dict(spec['units'],time='different unit')
    elif defect=='one_field':
        spec['values'][-1][1]=[v+0.5 for v in spec['values'][-1][1]]
    elif defect=='shape':
        spec['values'][0][0]=list(spec['values'][0][0])+[1.0]
    else:
        spec['profile']='field_rmse_v1'
    if defect in ('units','shape','forged_profile'):
        with pytest.raises(ValueError):references.compare(spec,model,cfg,outcome)
    else:
        verdict=references.compare(spec,model,cfg,outcome)
        assert verdict['status']=='fail'
        if defect=='one_field':
            # A single wrong field fails even when the other field matches exactly.
            assert verdict['scores']['u']<=spec['threshold']<verdict['scores']['v']


def test_reference_profile_mismatch_is_rejected_in_both_directions():
    new_model=synthetic();new_cfg=pde_system.config(settings())
    legacy=json.loads((EXAMPLES/'pde.json').read_text());legacy.update(boundary='periodic',initial=[1.]*8)
    legacy_cfg=pde.config(dict(duration=2.,samples=3))
    crossed=system_reference(new_model,settings())
    crossed['units']=deepcopy(legacy['units'])
    with pytest.raises(ValueError,match='profile'):references.compatible(crossed,legacy,legacy_cfg)
    old_spec=dict(schema_version=1,model_type='pde',profile=references.PROFILES['pde'],
        units=deepcopy(new_model['units']),times=[0.,1.,2.],x=[(i+.5)/8 for i in range(8)],
        values=[[1.]*8,[float(np.exp(-.2))]*8,[float(np.exp(-.4))]*8],scale=1.,threshold=1e-8)
    with pytest.raises(ValueError,match='profile'):references.compatible(old_spec,new_model,new_cfg)


def test_reference_size_bounds_keep_legacy_cap_and_allow_bounded_system_references():
    legacy=dict(schema_version=1,model_type='pde',profile=references.PROFILES['pde'],
        units={'time':'second','space':'millimeter','state':'arbitrary_concentration'},
        times=[0.01*i for i in range(100)],x=[0.001*i for i in range(100)],
        values=[[0.12345678901234568]*100 for _ in range(100)],scale=1.,threshold=1.)
    with pytest.raises(ValueError,match='bounded reference'):references.validate(legacy)
    axis=[i/256 for i in range(1,256,2)]
    large=dict(schema_version=1,model_type='pde',profile=references.PDE_SYSTEM_PROFILE,
        units={'time':'t','space':'m','state':'c'},times=[0.,0.5,1.],
        axes={'x':axis,'y':axis},field_ids=['u'],
        values=[[[[0.12345678901234568]*128 for _ in range(128)]] for _ in range(3)],
        scales=[1.],threshold=1.)
    assert references.validate(large)==large
    huge=dict(schema_version=1,model_type='pde',profile=references.PDE_SYSTEM_PROFILE,
        units={'time':'t','space':'m','state':'c'},times=[0.001*i for i in range(1001)],
        axes={'x':axis},field_ids=['u','v'],
        values=[[[0.12345678901234568]*128 for _ in range(2)] for _ in range(1001)],
        scales=[1.,1.],threshold=1.)
    with pytest.raises(ValueError,match='bounded reference'):references.validate(huge)


def test_project_freezes_reference_before_execution_and_passes_per_field(tmp_path):
    model=synthetic();raw=settings()
    spec=system_reference(model,raw)
    store=ArtifactStore(tmp_path/'store')
    rr=projects.register_reference(store,dict(model_type='pde',reference=spec,
        note='Independent synthetic analytic control.'),'tester')
    assert store.get(rr)['payload']['reference_type']=='typed_observations'
    project=_project(store,reference_refs=[rr])
    iteration=_iteration(store,project,model,reference_ref=rr)
    task=projects.own(store,iteration,projects.ITERATION,project)['task_ref']
    assert store.get(task)['kind']=='typed_input' and store.get(task)['payload']['reference_ref']==rr
    assert rr in store.get(task)['refs'] and not store.list('typed_run')
    step(store,project,'run_iteration',iteration_ref=iteration)
    facts=projects.facts(store,iteration)
    assert facts['numeric_status']=='pass' and facts['comparison_status']=='pass' and facts['state']=='completed'
    out=models.summary(store,facts['application_run_ref'])
    assert out['completion_eligible'] and out['comparison']['status']=='pass'
    assert set(out['comparison']['scores'])=={'u','v'}


def test_project_reference_failure_blocks_completion_eligibility(tmp_path):
    model=synthetic()
    spec=system_reference(model,settings())
    spec['values']=[[[value+0.5 for value in field_values] for field_values in per_time]
                    for per_time in spec['values']]
    store=ArtifactStore(tmp_path/'store')
    rr=projects.register_reference(store,dict(model_type='pde',reference=spec,
        note='Deliberately wrong synthetic reference.'),'tester')
    project=_project(store,reference_refs=[rr])
    iteration=_iteration(store,project,model,reference_ref=rr)
    step(store,project,'run_iteration',iteration_ref=iteration)
    facts=projects.facts(store,iteration)
    assert facts['numeric_status']=='pass' and facts['comparison_status']=='fail' and facts['state']=='failed'
    out=models.summary(store,facts['application_run_ref'])
    assert out['status']=='succeeded' and out['comparison']['status']=='fail' and not out['completion_eligible']


def test_legacy_pde_frozen_payload_defaults_and_layout_are_preserved(tmp_path):
    store=ArtifactStore(tmp_path/'store')
    ref=models.import_model(store,EXAMPLES/'pde.json')
    stored=store.get(ref)['payload']
    assert stored['profile']==pde.PROFILE=='linear_reaction_diffusion_1d_v1'
    loaded=models.load(store,ref)
    assert 'profile' not in loaded and models.backend('pde',model=loaded) is pde
    assert pde.config()=={'timeout_s':15.,'duration':1.,'samples':21}
    plan=models.prepare(store,ref,dict(duration=.2,samples=5))
    assert store.get(plan)['payload']['profile']==pde.PROFILE
    assert store.get(plan)['payload']['settings']=={'timeout_s':15.,'duration':.2,'samples':5}
    run=models.execute(store,plan)
    out=models.summary(store,run)
    assert out['profile']==pde.PROFILE and out['status']=='succeeded' and out['completion_eligible']
    _,model,outcome=models.result(store,run)
    data=outcome['data']
    assert set(data)=={'times','x','values','mass','grid_kind','dx','time_integration'}
    assert data['grid_kind']=='nodal' and len(data['x'])==33
    report=reporting.render_report(store,run)
    assert '"dx"' in report and 'final_values_preview' not in report
    rerun=models.replay(store,run)
    assert models.result(store,rerun)[0]['replay_of']==run
    assert models.result(store,rerun)[2]==models.result(store,run)[2]


def test_two_time_samples_support_a_frozen_external_reference(tmp_path):
    model = synthetic()
    raw_settings = settings(samples=2)
    spec = system_reference(model, raw_settings)
    store = ArtifactStore(tmp_path / 'two-sample-store')
    model_ref = models.import_model(store, _write(tmp_path, model))
    reference_ref = projects.register_reference(store, dict(
        model_type='pde', reference=spec, note='Synthetic two-sample analytic control.'), 'tester')
    input_ref = models.prepare(store, model_ref, raw_settings, reference_ref=reference_ref)
    run_ref = models.execute(store, input_ref)
    assert models.summary(store, run_ref)['comparison']['status'] == 'pass'


def test_system_reference_time_coordinates_are_nonnegative():
    spec = system_reference(synthetic(), settings(samples=3))
    spec['times'][0] = -0.1
    with pytest.raises(ValueError, match='nonnegative'):
        references.validate(spec)


@pytest.mark.parametrize('target', ['values', 'times', 'axes'])
def test_system_comparison_rejects_mixed_boolean_arrays(target):
    model = synthetic()
    model['grid']['lengths'] = [4.0]
    model['parameters']['rate'] = model['parameters']['rate_v'] = 0.0
    for field in model['fields']:
        field['initial'] = [1.0] * 6
    raw_settings = settings(samples=2)
    cfg = pde_system.config(raw_settings)
    outcome = dict(status='succeeded', data=pde_system.execute(model, cfg))
    spec = system_reference(model, raw_settings)
    assert references.compare(spec, model, cfg, outcome)['status'] == 'pass'
    if target == 'values':
        outcome['data']['values'][-1][0][0] = True
    elif target == 'times':
        outcome['data']['times'][-1] = True
    else:
        outcome['data']['axes']['x'][1] = True
    assert references.compare(spec, model, cfg, outcome)['status'] == 'fail'


@pytest.mark.parametrize('kind', ['boolean', 'stochastic', 'individual'])
def test_backend_selection_rejects_other_family_context(kind):
    with pytest.raises(ValueError, match='model_type'):
        models.backend(kind, model=synthetic())


@pytest.mark.parametrize('shape', [(6,), (4, 3)])
def test_system_reference_reaches_the_normal_project_planner(tmp_path, shape):
    from biomodel_agent import research_references, research_supervisor

    store = ArtifactStore(tmp_path / 'planner-store')
    model = synthetic(shape)
    spec = system_reference(model, settings())
    reference_ref = projects.register_reference(store, dict(
        model_type='pde', reference=spec, note='Synthetic planner-entry control.'), 'tester')
    model_ref = models.import_model(store, _write(tmp_path, model))
    project = projects.create(store, dict(
        scenario='Inspect the supplied synthetic multi-field PDE reference.',
        model_refs=[model_ref], reference_refs=[reference_ref],
        policy=dict(planner_max_calls=1)))
    provider = Scripted([act('ask_alignment', category='objective',
                             question='Which synthetic condition should be studied?',
                             affected_fields=['objective'])])
    research_supervisor.run(store, provider, project)
    assert projects.inspect(store, project)['state'] == 'awaiting_alignment'
    prompts = store.list('research_project_prompt')
    assert len(prompts) == 1
    text = json.dumps(store.get(prompts[0])['payload']['messages'])
    assert references.PDE_SYSTEM_PROFILE in text
    metadata = research_references.metadata(store, reference_ref)
    assert metadata['axes'] == spec['axes']
    assert metadata['field_ids'] == metadata['observables'] == spec['field_ids']
    assert metadata['scales'] == spec['scales']
    assert metadata['threshold'] == spec['threshold']
    assert not store.list('typed_run')
