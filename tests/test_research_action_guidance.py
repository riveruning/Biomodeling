"""Guidance and rejection feedback address failures seen in the live pilot."""
from copy import deepcopy
import json

import pytest

from biomodel_agent import evidence,research_projects as p,research_supervisor as supervisor
from biomodel_agent.storage import ArtifactStore
from test_research_projects import frame,opened,step


def test_source_anchor_error_reports_candidates_but_never_rebinds(tmp_path):
    store=ArtifactStore(tmp_path/'s');path=tmp_path/'source.txt';path.write_text('Header.\nInitial A = False.\n',encoding='utf-8')
    source=evidence.ingest_source(store,path)
    root=p.create(store,dict(scenario='Inspect declarations.',source_refs=[source]))
    f=frame();f['evidence']=[dict(id='initial',kind='source',source_ref=source,page=1,quote='Initial A = False.',start=0,end=17)]
    with pytest.raises(ValueError,match='initial'):
        step(store,root,'revise_frame',frame=f,reason='Wrong explicit offsets.')
    view=p.inspect(store,root);assert view['frame_ref'] is None
    ret=p.rows(store,p.RETURN,root)[0][1]
    assert ret['diagnostic']['citation']['matches']==[dict(start=8,end=26)]
    del f['evidence'][0]['start'];del f['evidence'][0]['end']
    step(store,root,'revise_frame',frame=f,reason='Use a literal unique quote instead.')
    anchor=p.inspect(store,root)['frame']['spec']['evidence'][0]
    assert anchor['start']==8 and anchor['end']==26
    assert path.read_text(encoding='utf-8')=='Header.\nInitial A = False.\n'


def test_repeated_source_quote_still_requires_operator_or_agent_selection(tmp_path):
    store=ArtifactStore(tmp_path/'s');path=tmp_path/'source.txt';path.write_text('same\nsame\n')
    source=evidence.ingest_source(store,path);root=p.create(store,dict(scenario='Ambiguous quote.',source_refs=[source]))
    f=frame();f['evidence']=[dict(id='repeat',kind='source',source_ref=source,page=1,quote='same')]
    with pytest.raises(ValueError,match='repeated'):step(store,root,'revise_frame',frame=f,reason='Ambiguous.')
    assert p.inspect(store,root)['frame_ref'] is None
    citation=p.rows(store,p.RETURN,root)[0][1]['diagnostic']['citation']
    assert citation['matches']==[dict(start=0,end=4),dict(start=5,end=9)]


def test_missing_native_type_has_actionable_field_path_before_model_write(tmp_path):
    store,root,_=opened(tmp_path,native_agent=False)
    raw=store.get(p.own(store,p.inspect(store,root)['iteration_refs'][0],p.ITERATION,root)['config']['model_ref'])['payload']['model']
    raw=deepcopy(raw);del raw['model_type']
    before=store.list('typed_model')
    with pytest.raises(ValueError,match=r'native_draft.model_type.*boolean'):
        step(store,root,'open_iteration',reason='Missing type.',native_draft=raw,hypothesis_ids=['H1'],
             config=dict(model_type='boolean',execution=dict(settings=dict(steps=4,seed=123))))
    assert store.list('typed_model')==before


def test_misplaced_native_execution_keys_rejected_before_model_write(tmp_path):
    store,root,it=opened(tmp_path,native_agent=False)
    raw=store.get(p.own(store,it,p.ITERATION,root)['config']['model_ref'])['payload']['model']
    before=store.list('typed_model')
    with pytest.raises(ValueError,match=r'config.execution.settings'):
        step(store,root,'open_iteration',reason='Misplaced seed/sweeps.',native_draft=raw,hypothesis_ids=['H1'],
             config=dict(model_type='boolean',execution=dict(sweeps=4,seed=123)))
    assert store.list('typed_model')==before


def test_current_action_schemas_are_stage_specific_and_bound_to_sources(tmp_path):
    from biomodel_agent import research_schema
    store=ArtifactStore(tmp_path/'s');path=tmp_path/'source.txt';path.write_text('Source control.')
    source=evidence.ingest_source(store,path);root=p.create(store,dict(scenario='Schema control.',source_refs=[source]))
    state=json.loads(supervisor._messages(store,root)[-1]['content'])
    schemas=state['action_schemas']
    assert set(schemas)=={'revise_frame'}
    spec=schemas['revise_frame']['properties']['args']['properties']['frame']
    assert spec['properties']['mode']['enum']==list(p.MODES)
    assert spec['properties']['evidence_needs']['items']['properties']['queries']['maxItems']==research_schema.LIMITS['queries']
    source_anchor=spec['properties']['evidence']['items']['oneOf'][0]
    assert source_anchor['properties']['source_ref']['enum']==[source]
    step(store,root,'revise_frame',frame=frame(),reason='Choose a scope.')
    state=json.loads(supervisor._messages(store,root)[-1]['content'])
    assert 'open_iteration' in state['action_schemas']
    assert 'revise_frame' in state['action_schemas']


@pytest.mark.parametrize('kind,target',[('boolean','A/rule'),('stochastic','birth/rate_unit')])
def test_strict_source_binding_feedback_preserves_missing_targets(tmp_path,kind,target):
    from pathlib import Path
    root_dir=Path(__file__).parents[1]
    source_path='examples/task-proposals/boolean-source.txt' if kind=='boolean' else 'examples/source-stochastic/birth-death.txt'
    proposal_path='examples/task-proposals/boolean-proposal.template.json' if kind=='boolean' else 'examples/source-stochastic/proposal.template.json'
    store=ArtifactStore(tmp_path/'s');source=evidence.ingest_source(store,root_dir/source_path)
    root=p.create(store,dict(scenario='Check explicit SSA declarations.',mode='reproduction',source_refs=[source]))
    spec=json.loads((root_dir/proposal_path).read_text().replace('SOURCE_REF',source))
    f=frame('strict_reproduction');f['assumptions']=[];f['evidence']=spec['evidence']
    step(store,root,'revise_frame',frame=f,reason='Exact source declarations.')
    candidate=spec['candidates'][0];mapping=deepcopy(candidate['evidence_map']);del mapping[target]
    draft=candidate['boolean_draft' if kind=='boolean' else 'stochastic_draft']
    settings=dict(steps=4) if kind=='boolean' else dict(duration=1.,samples=3,trajectories=2)
    with pytest.raises(ValueError,match='strict draft lacks complete source bindings'):
        step(store,root,'open_iteration',reason='Missing one binding.',native_draft=draft,
            source_evidence_map=mapping,stochastic_rate_units=candidate.get('stochastic_rate_units',{}),
            config=dict(model_type=kind,execution=dict(settings=settings)))
    rejected=[r for _,r in p.rows(store,p.RETURN,root) if r['state']=='rejected'][0]
    issues=rejected['diagnostic']['source_binding']['issues']
    assert issues[0]['missing_targets']==[target]
    assert not p.inspect(store,root)['iteration_refs'] and not store.list('typed_model')
