"""Read reuse must retain hashes, graph checks and fresh request boundaries."""
from collections import Counter
import os
from pathlib import Path

import pytest
from biomodel_agent.read_views import verified_read
from biomodel_agent.storage import ArtifactError,ArtifactStore,canonical_bytes,sha256_of


def graph(tmp_path):
    store=ArtifactStore(tmp_path/'store')
    blob=store.put_blob(b'source evidence')
    child=store.put('child',{'value':1},refs=[blob])
    root=store.put('root',{'name':'root'},refs=[child])
    return store,root,child,blob


def test_repeated_graph_reads_use_bounded_io_and_return_independent_data(tmp_path,monkeypatch):
    store,root,child,blob=graph(tmp_path);reads=Counter();original=Path.read_bytes
    def read(path):
        reads[path]+=1
        return original(path)
    monkeypatch.setattr(Path,'read_bytes',read)
    with verified_read(store) as view:
        for _ in range(12):
            value=view.get(root);value['payload']['name']='caller mutation'
            assert view.get(root)['payload']['name']=='root'
        assert view.get_blob(blob)==b'source evidence'
    paths=[store.objects_dir/(r+'.json') for r in (root,child)]+[store.blobs_dir/blob]
    assert all(reads[path]==2 for path in paths)
    with pytest.raises(ArtifactError,match='ended'):view.get(root)


@pytest.mark.parametrize('kind',['object','blob'])
def test_transitive_corruption_is_rejected_even_when_index_is_absent(tmp_path,kind):
    store,root,child,blob=graph(tmp_path);(store.root/'index.sqlite').unlink()
    target=store.objects_dir/(child+'.json') if kind=='object' else store.blobs_dir/blob
    target.write_bytes(b'bad data')
    with pytest.raises(ArtifactError,match='hash verification'):
        with verified_read(store) as view:view.get(root)
    assert not store.index_path.exists()


@pytest.mark.parametrize('change',['same_size_and_mtime','delete'])
def test_change_after_first_read_prevents_successful_query_return(tmp_path,change):
    store,root,child,_=graph(tmp_path);path=store.objects_dir/(child+'.json');before=path.stat()
    with pytest.raises(ArtifactError,match='during read query'):
        with verified_read(store) as view:
            view.get(root)
            if change=='delete':path.unlink()
            else:
                data=path.read_bytes();path.write_bytes(data.replace(b'"value":1',b'"value":2'))
                os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
            assert view.get(child)['payload']['value']==1


def test_evicted_and_oversized_data_are_still_rechecked(tmp_path):
    store=ArtifactStore(tmp_path/'store');large=store.put_blob(b'old'*100)
    with pytest.raises(ArtifactError,match='during read query'):
        with verified_read(store,max_bytes=8) as view:
            assert view.get_blob(large)==b'old'*100
            (store.blobs_dir/large).write_bytes(b'new'*100)


def test_new_query_observes_changes_and_read_view_cannot_write(tmp_path):
    store,root,child,_=graph(tmp_path);before=set(store.root.rglob('*'))
    with verified_read(store) as view:
        view.get(root)
        for action in (lambda:view.put('x',{}),lambda:view.put_blob(b'x'),view.recover,lambda:view.list()):
            with pytest.raises(ArtifactError,match='cannot write'):action()
    assert set(store.root.rglob('*'))==before
    (store.objects_dir/(child+'.json')).write_bytes(b'corrupt')
    with pytest.raises(ArtifactError):
        with verified_read(store) as view:view.get(root)


def test_ref_depth_checks_are_identical_to_the_base_store(tmp_path):
    store=ArtifactStore(tmp_path/'store');ref=None
    for _ in range(68):
        raw=canonical_bytes(dict(schema_version='1',kind='node',payload={},refs=[] if ref is None else [ref]))
        ref=sha256_of(raw);(store.objects_dir/(ref+'.json')).write_bytes(raw)
    with pytest.raises(ArtifactError,match='depth limit'):store.get(ref)
    with pytest.raises(ArtifactError,match='depth limit'):
        with verified_read(store) as view:view.get(ref)


def test_nested_read_view_keeps_outer_scope_open(tmp_path):
    store,root,_,_=graph(tmp_path)
    with verified_read(store) as outer:
        with verified_read(outer) as inner:
            assert inner is outer
            inner.get(root)
        outer.get(root)


def test_cache_eviction_does_not_drop_final_integrity_check(tmp_path):
    store=ArtifactStore(tmp_path/'store');first=store.put_blob(b'old');second=store.put_blob(b'new')
    with pytest.raises(ArtifactError,match='during read query'):
        with verified_read(store,max_bytes=3) as view:
            assert view.get_blob(first)==b'old'
            assert view.get_blob(second)==b'new'
            (store.blobs_dir/first).write_bytes(b'bad')
