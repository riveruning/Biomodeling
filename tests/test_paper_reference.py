"""Deterministic controls for image features and frozen execution bindings.

Pixel extraction uses synthetic RGB arrays; real PNG decoding is checked in the
separate public Goodwin demonstration with the optional figures runtime.
"""
from copy import deepcopy
from hashlib import sha256
import numpy as np
import pytest
from biomodel_agent import paper_reference as paper
from biomodel_agent.storage import ArtifactStore
from biomodel_agent.domain.contracts import ExperimentSpec,SolverConfig
from biomodel_agent.domain.serialization import to_dict


@pytest.fixture
def setup(tmp_path,monkeypatch):
    raw=b'synthetic-rgb-fixture'
    rgb=np.full((121,1001,3),255,dtype=np.uint8)
    for x in range(1,1000):
        v=.5+.3*np.cos(2*np.pi*x/200)
        y=round(110-100*v)
        rgb[y-1:y+2,x]=[0,180,0]
    cal=dict(version=1,image_sha256=sha256(raw).hexdigest(),image_size=[1001,121],x_pixels=[0,1000],time_range=[0,200],y_pixels=[10,110],value_range=[0,1],sample_x=[1,999],legend_mask=[800,1,990,15],pixel_uncertainty=1.5,series={'Y':dict(channel=1,minimum=70,margins=[25,0,15],value_roi=[0,1],prominence=.1,minimum_separation=20)},excluded={'X':'resolution limited'},source={'doi':'synthetic control; not a paper'},review='developer_only_independent_review_pending')
    def decode(data,p):
        if sha256(data).hexdigest()!=p['image_sha256']:raise ValueError('image hash')
        return paper.digitize_rgb(rgb,p)
    monkeypatch.setattr(paper,'_decode',decode)
    image=tmp_path/'raw';image.write_bytes(raw)
    store=ArtifactStore(tmp_path/'store');reference=paper.prepare_reference(store,image,cal)
    model={'sbml':'synthetic_control_only','id':'control'};model_ref=store.put('model',model)
    times=np.arange(0,400.01,.2)
    spec=dict(version=1,registration='retrospective_development',prior_exposure='synthetic development control',initials_origin='analyst_exploration',pointwise='not_run_unknown_initials_and_phase',phase_alignment='none',windows=[[0,200],[200,400]],stability_relative_tolerance=.01,experiment=to_dict(ExperimentSpec(tuple(times))),solver=to_dict(SolverConfig()),nonnegative=['Y'])
    protocol=paper.protocol(store,reference,model_ref,spec)
    inputs=dict(artifact=model,experiment=spec['experiment'],solver=spec['solver'],reference=None,scales=None,observations=[],strict=True,threshold=.001,nonnegative=['Y'])
    inp=store.put('simulation_input',inputs)
    run=dict(status='succeeded',input_ref=inp,simulation=dict(times=times.tolist(),values={'Y':(.5+.3*np.cos(2*np.pi*times/40)).tolist()}),static_validation={'checks':[]},numeric_validation={'checks':[dict(code=c,status='pass') for c in ('finite','convergence')]})
    return dict(store=store,cal=cal,rgb=rgb,reference=reference,model_ref=model_ref,spec=spec,protocol=protocol,run=run,inputs=inputs)


def run_compare(s):
    r=s['store'].put('run',s['run'],refs=(s['run']['input_ref'],))
    return s['store'].get(paper.compare(s['store'],s['protocol'],r))['payload']


def test_synthetic_agreement_preserves_unknown(setup):
    p=run_compare(setup)
    assert p['status']=='feature_band_agreement'
    assert p['scientific_support']=='unknown' and p['independent_review']=='pending'
    assert p['phase_alignment']=='none' and p['pointwise']=='not_run_unknown_initials_and_phase'
    assert len(p['comparisons'])==3


@pytest.mark.parametrize('factor',[.8,1.2])
def test_disagreement_is_retained(setup,factor):
    setup['run']['simulation']['values']['Y']=[x*factor for x in setup['run']['simulation']['values']['Y']]
    assert run_compare(setup)['status']=='feature_band_disagreement'


def test_nonstationary_windows_not_comparable(setup):
    setup['run']['simulation']['values']['Y']=[x*(1.2 if t>200 else 1) for t,x in zip(setup['run']['simulation']['times'],setup['run']['simulation']['values']['Y'])]
    assert run_compare(setup)['status']=='not_comparable'


@pytest.mark.parametrize('field,value',[
    ('phase_alignment','fitted'),('pointwise','pass'),('initials_origin','paper'),
    ('registration','preregistered'),('prior_exposure',''),('stability_relative_tolerance',.5),
    ('windows',[[0,100],[101,200]]),('nonnegative',True)])
def test_protocol_rejects_claims_and_tuning(setup,field,value):
    spec=deepcopy(setup['spec']);spec[field]=value
    with pytest.raises(ValueError):paper.protocol(setup['store'],setup['reference'],setup['model_ref'],spec)


@pytest.mark.parametrize('field,value',[('strict',False),('threshold',1.),('reference',{}),('scales',{'Y':10}),('solver',{}),('experiment',{}),('artifact',{'sbml':'changed'})])
def test_changed_run_input_rejected(setup,field,value):
    inputs=deepcopy(setup['inputs']);inputs[field]=value
    setup['run']['input_ref']=setup['store'].put('simulation_input',inputs)
    with pytest.raises(ValueError,match='run differs'):run_compare(setup)


@pytest.mark.parametrize('status',['fail','unknown','not_run'])
def test_convergence_must_actually_pass(setup,status):
    setup['run']['numeric_validation']['checks'][1]['status']=status
    with pytest.raises(ValueError):run_compare(setup)


def test_altered_digitized_rows_rejected_even_with_recomputed_features(setup):
    store=setup['store'];p=store.get(setup['reference'])['payload']
    for row in p['curves']['Y']:
        for k in ('value','low','high'):row[k]+=.01
    settings=p['calibration']['series']['Y']
    p['features']['Y']=paper.features(p['curves']['Y'],prominence=settings['prominence'],minimum_separation=settings['minimum_separation'])
    ref=store.put('paper_figure_reference',p,refs=(p['image_blob_ref'],))
    with pytest.raises(ValueError,match='original image pixels'):paper.validate_reference(store,ref)


def test_periods_never_bridge_a_missing_segment(setup):
    rows=paper.digitize_rgb(setup['rgb'],setup['cal'])['Y']
    for row in rows:
        if 65<row['time']<115:row['value']=row['low']=row['high']=None
    f=paper.features(rows,prominence=.1,minimum_separation=20)
    assert f['missing']>0
    assert f['period']['value']==pytest.approx(40,abs=.4)
    assert all(r['value']<41 for r in f['period_intervals'])


def test_all_missing_not_fabricated_zero(setup):
    rows=paper.digitize_rgb(np.full_like(setup['rgb'],255),setup['cal'])['Y']
    f=paper.features(rows,prominence=.1,minimum_separation=20)
    assert f['observed']==0 and f['minimum'] is f['maximum'] is f['period'] is None


def test_masked_curve_stays_missing(setup):
    cal=deepcopy(setup['cal']);cal['legend_mask']=[400,10,550,110]
    rows=paper.digitize_rgb(setup['rgb'],cal)['Y']
    assert all(row['value'] is None for row in rows if 400<=row['pixel_x']<550)


@pytest.mark.parametrize('key,value',[('image_size',[999999,10]),('pixel_uncertainty',0),('x_pixels',[1000,0]),('excluded',[]),('source','not metadata')])
def test_invalid_calibration_rejected(setup,key,value):
    cal=deepcopy(setup['cal']);cal[key]=value
    with pytest.raises(ValueError):paper.calibration(cal)


def test_no_period_when_only_one_trough(setup):
    rows=paper.digitize_rgb(setup['rgb'],setup['cal'])['Y'][:160]
    f=paper.features(rows,prominence=.1,minimum_separation=20)
    assert f['period'] is None


def test_report_labels_uncertainty_and_unknown(setup):
    from biomodel_agent.reporting import render_report
    store=setup['store'];run=store.put('run',setup['run'],refs=(setup['run']['input_ref'],))
    ref=paper.compare(store,setup['protocol'],run)
    report=render_report(store,ref)
    assert 'not confidence intervals' in report and 'Scientific support: **unknown**' in report


def test_portable_bundle_recomputes_comparison(setup,tmp_path):
    from biomodel_agent.bundles import export_bundle
    store=setup['store'];run=store.put('run',setup['run'],refs=(setup['run']['input_ref'],))
    ref=paper.compare(store,setup['protocol'],run)
    export_bundle(store,ref,tmp_path/'detached')
    detached=ArtifactStore(tmp_path/'detached')
    assert paper.compare(detached,setup['protocol'],run)==ref


def test_numeric_reference_is_not_a_publisher_reference(setup):
    ref=setup['store'].put('numerical_reference',dict(times=[0,1],values={'Y':[0,1]}))
    with pytest.raises(ValueError,match='figure reference'):paper.protocol(setup['store'],ref,setup['model_ref'],setup['spec'])


@pytest.mark.parametrize('field',['initial_overrides','parameter_overrides'])
def test_experiment_overrides_cannot_fill_unknown_initials(setup,field):
    spec=deepcopy(setup['spec']);spec['experiment'][field]={'Y':.1}
    with pytest.raises(ValueError,match='overrides'):paper.protocol(setup['store'],setup['reference'],setup['model_ref'],spec)


def test_cli_compares_and_reports_ref(setup,capsys):
    from biomodel_agent.cli import main
    store=setup['store'];run=store.put('run',setup['run'],refs=(setup['run']['input_ref'],))
    assert main(['paper-compare','--store',str(store.root),'--protocol-ref',setup['protocol'],'--run-ref',run])==0
    ref=capsys.readouterr().out.strip()
    assert store.get(ref)['kind']=='paper_comparison'
