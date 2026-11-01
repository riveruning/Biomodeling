"""The Agent may select source data; literal copies cannot become independent truth."""
import json
import math
import pytest

from biomodel_agent import bundles,evidence,reporting,research_projects as p,research_supervisor as supervisor
from biomodel_agent.storage import ArtifactStore
from test_modeling_plan import setup as ode_setup
from test_research_projects import ROOT,frame,step,assess


def source(store,tmp_path,text):
    path=tmp_path/'reference.txt';path.write_text(text,encoding='utf-8',newline='\n');return evidence.ingest_source(store,path)


def extract(store,project,src,text,**extra):
    return step(store,project,'reference_from_source',source_ref=src,page=1,start=0,end=len(text),
        format='json_reference',note='Synthetic source data; no biological validation.',**extra)['reference_ref']


def test_agent_selects_exact_source_csv_freezes_reference_and_reproduces_ode(tmp_path):
    store,model_source,plan=ode_setup(tmp_path)
    text='time [second],measured_x\n0,1\n1,'+str(math.exp(-.4))+'\n'
    data=source(store,tmp_path,text)
    root=p.create(store,dict(scenario='Reproduce the declared decay and compare the supplied observations.',mode='reproduction',source_refs=[model_source,data]))
    ref=step(store,root,'reference_from_source',source_ref=data,page=1,start=0,end=len(text),format='csv_series',
        time_column='time [second]',columns={'x':'measured_x'},note='Literal synthetic CSV control.')['reference_ref']
    saved=p._reference(store,ref)
    assert saved['reference']['values']['x']==[1.,math.exp(-.4)] and saved['scales']=={'x':1.}
    assert not saved['independent'] and ref in p.inspect(store,root)['reference_refs']
    spec=frame('strict_reproduction');spec['assumptions']=[]
    step(store,root,'revise_frame',frame=spec,reason='The Agent chose the source data and model.')
    it=step(store,root,'open_iteration',reason='Freeze source model and source reference.',decision_plan=plan,reference_ref=ref,
        config=dict(model_type='ode',execution=dict(experiment=dict(times=[0.,1.],time_unit='second'))))['iteration_ref']
    step(store,root,'run_iteration',iteration_ref=it)
    assert p.facts(store,it)['comparison_status']=='pass'
    assert p.own(store,assess(store,root,it),p.ASSESSMENT,root)['mandatory_checks']['reference_comparison']=='pass'
    snap=p.snapshot(store,root);before=reporting.render_report(store,snap)
    bundles.export_bundle(store,snap,tmp_path/'bundle');detached=ArtifactStore(tmp_path/'bundle')
    assert p._reference(detached,ref)==saved and reporting.render_report(detached,snap)==before
    assert 'agent_source_literal_copy' in json.dumps(supervisor._messages(store,root))


@pytest.mark.parametrize('expected',[10.,9.])
def test_fba_source_objective_is_frozen_and_failed_comparison_cannot_be_omitted_from_criteria(tmp_path,expected):
    store=ArtifactStore(tmp_path/'s');text=json.dumps(dict(objective=expected,source='Synthetic LP analytic control.'))
    src=source(store,tmp_path,text);root=p.create(store,dict(scenario='Compare a hypothetical steady-state model.',source_refs=[src]))
    ref=extract(store,root,src,text,model_type='fba')
    step(store,root,'revise_frame',frame=frame(),reason='Explicit application hypothesis.')
    it=step(store,root,'open_iteration',reason='Freeze model and objective reference.',reference_ref=ref,hypothesis_ids=['H1'],
        native_draft=json.loads((ROOT/'examples/fba-v1/chain.json').read_text()),config=dict(model_type='fba'))['iteration_ref']
    step(store,root,'run_iteration',iteration_ref=it)
    if expected==10.:
        assert p.facts(store,it)['comparison_status']=='pass';assess(store,root,it)
    else:
        assert p.facts(store,it)['comparison_status']=='fail'
        with pytest.raises(ValueError,match='required frozen criteria'):assess(store,root,it)


@pytest.mark.parametrize('text',[
    '{"objective":10,"objective":9,"source":"ambiguous"}',
    '{"objective":NaN,"source":"invalid"}',
    '{"objective":Infinity,"source":"invalid"}',
    '{"objective":10,"source":"synthetic"} trailing prose',
])
def test_literal_reference_rejects_ambiguous_or_nonfinite_json_without_wedging_project(tmp_path,text):
    store=ArtifactStore(tmp_path/'s');src=source(store,tmp_path,text);root=p.create(store,dict(scenario='Inspect data.',source_refs=[src]))
    with pytest.raises(ValueError):extract(store,root,src,text,model_type='fba')
    assert not p.inspect(store,root)['reference_refs'] and p.inspect(store,root)['state']=='ready'


def test_agent_cannot_turn_its_scenario_or_an_invented_numeric_argument_into_reference_data(tmp_path):
    store=ArtifactStore(tmp_path/'s');text='{"objective":10,"source":"scenario hypothesis"}'
    root=p.create(store,dict(scenario=text));src=step(store,root,'scenario_source')['source_ref']
    with pytest.raises(ValueError,match='not scenario'):extract(store,root,src,text,model_type='fba')
    actual=source(store,tmp_path,text);other=p.create(store,dict(scenario='Another project.',source_refs=[actual]))
    with pytest.raises(ValueError,match='outside project'):extract(store,root,actual,text,model_type='fba')
    with pytest.raises(ValueError):step(store,other,'reference_from_source',source_ref=actual,page=1,start=0,end=len(text),
        format='json_reference',note='Cannot override copied values.',model_type='fba',reference=dict(objective=99,source='invented'))


def test_reference_copy_is_revalidated_against_source_on_read(tmp_path):
    store=ArtifactStore(tmp_path/'s');text='{"objective":10,"source":"synthetic"}'
    src=source(store,tmp_path,text);root=p.create(store,dict(scenario='Inspect data.',source_refs=[src]));ref=extract(store,root,src,text,model_type='fba')
    obj=store.get(ref);payload=obj['payload'];payload['reference']['objective']=9.
    forged=store.put(p.REFERENCE,payload,refs=obj['refs'])
    with pytest.raises(ValueError,match='differs from its source'):p._reference(store,forged)
