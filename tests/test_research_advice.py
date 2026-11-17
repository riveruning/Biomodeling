"""Native model scopes, explicit opt-in and invalidation without source leaks."""
import json
from pathlib import Path

import pytest

from biomodel_agent import evidence,memory,typed_models,fba,research_projects as projects,research_supervisor as supervisor,research_advice
from biomodel_agent.storage import ArtifactStore

ROOT=Path(__file__).parents[1]


@pytest.mark.parametrize('kind',['fba','boolean','stochastic','pde','individual'])
def test_native_decision_memory_keeps_exact_model_and_family_scope(tmp_path,kind):
    store=ArtifactStore(tmp_path/'s')
    path=tmp_path/'protocol.txt';path.write_text('Synthetic operator protocol: inspect seed and numerical checks.',encoding='utf-8')
    source=evidence.ingest_source(store,path)
    mr=fba.import_model(store,ROOT/'examples/fba-v1/chain.json') if kind=='fba' else typed_models.import_model(store,ROOT/f'examples/model-types/{kind}.json')
    condition=dict(model_type=kind)
    advice=memory.remember(store,project_id='explicit-project',content='Inspect seed and frozen numerical checks.',category='decision',
        author='reviewer',model_ref=mr,source_refs=[source],conditions=condition)
    scope=memory.snapshot(store,project_id='explicit-project',source_refs=[source],model_ref=mr,context=condition)
    assert scope['memory_refs']==[advice]
    assert not memory.snapshot(store,project_id='explicit-project',source_refs=[source],model_ref=mr,context=dict(model_type='different'))['memory_refs']
    root=projects.create(store,dict(scenario='Scoped advice.',source_refs=[source],model_refs=[mr],advice=dict(project_id='explicit-project',query='seed',model_ref=mr,conditions=condition)))
    assert advice in store.get(root)['refs']
    assert research_advice.view(store,projects.load(store,root))['results'][0]['memory_ref']==advice
    memory.invalidate_dependency(store,'explicit-project',mr,author='reviewer',reason='This model protocol was superseded.')
    assert projects.inspect(store,root)['state']=='ready'
    assert research_advice.view(store,projects.load(store,root))['status']=='stale_or_invalidated'


def test_memory_is_never_auto_enabled_or_sent_from_another_project(tmp_path):
    store=ArtifactStore(tmp_path/'s')
    memory.remember(store,project_id='private-other',content='Private calibration seed marker xyzzy.',category='experience',author='reviewer')
    root=projects.create(store,dict(scenario='No memory authorization.'))
    messages=json.dumps(supervisor._messages(store,root))
    assert 'xyzzy' not in messages
    assert research_advice.view(store,projects.load(store,root))['status']=='disabled'


def test_native_procedures_match_supported_families():
    from biomodel_agent.capabilities import TYPES
    assert set(research_advice.procedures())==set(TYPES)
