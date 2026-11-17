"""Capability descriptions agree across public entrypoints and actual adapters."""
import json
import pytest

from biomodel_agent import capabilities, cli, research_projects, research_supervisor, typed_models
from biomodel_agent.storage import ArtifactStore
from biomodel_agent.workbench import Workbench


def test_cli_workbench_and_research_prompt_share_catalog(tmp_path,capsys):
    expected=capabilities.catalog()
    assert cli.main(['model-types'])==0
    assert json.loads(capsys.readouterr().out)==expected
    app=Workbench(tmp_path/'s')
    try:
        assert app.overview()['capabilities']==expected
        root=research_projects.create(app.store,dict(scenario='Capability control.'))
        task=json.loads(research_supervisor._messages(app.store,root)[1]['content'])
        assert task['capabilities']==expected
    finally:app.close()


@pytest.mark.parametrize('kind',capabilities.TYPED_TYPES)
def test_typed_profiles_and_limits_come_from_real_backends(kind):
    row=capabilities.get(kind);backend=typed_models.backend(kind)
    assert row['profile']==backend.PROFILE and row['limitations']==backend.LIMITATIONS
    assert row['project_reference_types']==['typed_observations']
    assert row['research_modes']['strict_reproduction']==(kind in ('boolean','stochastic'))


def test_capability_records_are_independent_and_unknown_types_fail():
    values=capabilities.catalog();values[0]['tools']['native_knowledge'].clear()
    assert capabilities.get('ode')['tools']['native_knowledge']==['biomodels','sabiork']
    assert capabilities.get('fba')['project_reference_types']==['fba_objective']
    assert not capabilities.get('fba')['source_construction']['strict_supported']
    with pytest.raises(ValueError):capabilities.get('unsupported')


def test_pde_system_capability_template_and_agent_brief_agree(tmp_path):
    from pathlib import Path
    from biomodel_agent import pde_system, typed_agent_runtime

    row = capabilities.get('pde')
    assert pde_system.PROFILE in {item['profile'] for item in row['profiles']}
    store = ArtifactStore(tmp_path / 'pde-system')
    project = research_projects.create(store, dict(scenario='Inspect the PDE schema.'))
    prompt = json.loads(research_supervisor._messages(store, project)[1]['content'])
    example = prompt['native_model_schema_examples']['pde_system']
    assert example['profile'] == pde_system.PROFILE
    assert all(field['reaction'] == '0' for field in example['fields'])
    pde_system.validate(example)
    path = Path(tmp_path) / 'pde-system.json'
    path.write_text(json.dumps(example), encoding='utf-8')
    model_ref = typed_models.import_model(store, path)
    input_ref = typed_models.prepare(store, model_ref, dict(duration=0.1, samples=2))
    brief = typed_agent_runtime.brief(store, dict(
        input_ref=input_ref, model_ref=model_ref, model_type='pde'))
    assert brief['profile'] == pde_system.PROFILE
    assert brief['limitations'] == pde_system.LIMITATIONS
