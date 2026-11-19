"""History remains authoritative without repeatedly scanning each operation."""
import json
import pytest
from biomodel_agent import evidence,research_projects as p
from biomodel_agent.storage import ArtifactStore


def test_history_read_work_is_bounded_as_operations_accumulate(tmp_path,monkeypatch):
    store=ArtifactStore(tmp_path/'s');path=tmp_path/'source.txt';path.write_text('Explicit source data.')
    source=evidence.ingest_source(store,path);root=p.create(store,dict(scenario='Inspect the supplied source.',source_refs=[source]))
    for i in range(20):p.operate(store,root,'inspect_source',dict(source_ref=source,page=1,start=0,end=8),key='read-'+str(i))
    (store.root/'index.sqlite').unlink()
    reads=0;original=store._read_envelope
    def counted(ref):
        nonlocal reads
        reads+=1;return original(ref)
    monkeypatch.setattr(store,'_read_envelope',counted)
    attempts,returns,pending=p.history(store,root)
    assert len(attempts)==len(returns)==20 and not pending
    objects=len(list(store.objects_dir.glob('*.json')))
    assert reads<=8*objects+10*len(attempts)


def test_wrong_project_return_still_fails_binding_check(tmp_path):
    store=ArtifactStore(tmp_path/'s');root=p.create(store,dict(scenario='First project.'))
    other=p.create(store,dict(scenario='Second project.'))
    result=p.operate(store,root,'scenario_source',{})
    attempt=p.rows(store,p.STEP,root)[0][0]
    returned=p.rows(store,p.RETURN,root)[0][0]
    # Replace one result with a valid hash object that is wrongly attributed.
    obj=store.get(returned);payload=obj['payload'];payload['project_ref']=other
    store.put(p.RETURN,payload,refs=(other,attempt,result['source_ref']))
    with pytest.raises(ValueError,match='conflicting project returns'):p.history(store,root)
