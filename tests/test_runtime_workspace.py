"""A runtime distribution has templates but no seeded sources or results."""
import json
from pathlib import Path
import shutil

from biomodel_agent import workspace_tools
from biomodel_agent.storage import ArtifactStore


def test_template_only_package_initializes_an_empty_workspace(tmp_path,monkeypatch):
    assets=tmp_path/'assets';assets.mkdir();root=Path(__file__).parents[1]/'src/biomodel_agent/workspace_assets'
    shutil.copytree(root/'templates',assets/'templates');shutil.copy2(root/'requirements.lock',assets/'requirements.lock')
    (assets/'AGENTS.md').write_text('Use your own material. Preserve unknown values.')
    monkeypatch.setattr(workspace_tools,'_assets',lambda:assets)
    result=workspace_tools.initialize(tmp_path/'workspace');store=ArtifactStore(tmp_path/'workspace/store')
    assert not store.list() and result['example_source_refs']=={} and result['provider_calls']==result['scientific_runs']==0
    manifest=json.loads((tmp_path/'workspace/workspace.json').read_text())
    assert 'Empty user workspace' in manifest['note'] and len(list((tmp_path/'workspace/templates').glob('*.json')))==6
def test_internal_records_are_utf8_with_a_non_utf8_system_default(tmp_path,monkeypatch):
    from pathlib import Path
    from biomodel_agent import typed_models
    from biomodel_agent.storage import ArtifactStore
    store=ArtifactStore(tmp_path/'store')
    ref=store.put('typed_execution',dict(input_ref='a'*64,note='中文记录 €'))
    original=Path.read_text
    def windows_default(path,encoding=None,errors=None):
        return original(path,encoding=encoding or 'gbk',errors=errors)
    monkeypatch.setattr(Path,'read_text',windows_default)
    rows=typed_models._records(store,'typed_execution','a'*64)
    assert len(rows)==1 and rows[0][0]==ref
    assert rows[0][1]['payload']['note']=='中文记录 €'


