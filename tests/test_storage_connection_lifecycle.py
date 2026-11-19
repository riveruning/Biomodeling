"""Completed writes must not checkpoint later during an unrelated readonly view."""
import gc
import sqlite3

import pytest

from biomodel_agent import recovery
from biomodel_agent.storage import ArtifactStore


def test_completed_writes_settle_index_before_gc_in_readonly_view(tmp_path):
    gc.collect();was_enabled=gc.isenabled();gc.disable()
    try:
        store=ArtifactStore(tmp_path/'store');ref=store.put('note',{'text':'synthetic resource-lifecycle control'})
        before=store.index_path.read_bytes();readonly=recovery.open_readonly_store(store.root)
        gc.collect()
        assert readonly.get(ref)['payload']=={'text':'synthetic resource-lifecycle control'}
        assert store.index_path.read_bytes()==before
    finally:
        if was_enabled:gc.enable()


@pytest.mark.parametrize('failure',[ValueError,KeyboardInterrupt])
def test_index_transaction_rolls_back_and_closes_on_exception(tmp_path,failure):
    store=ArtifactStore(tmp_path/'store')
    with store._db() as db:db.execute('CREATE TABLE lifecycle_control (value INTEGER)')
    with pytest.raises(sqlite3.ProgrammingError):db.execute('SELECT 1')
    with pytest.raises(failure):
        with store._db() as transaction:
            transaction.execute('INSERT INTO lifecycle_control VALUES (1)')
            raise failure()
    with pytest.raises(sqlite3.ProgrammingError):transaction.execute('SELECT 1')
    with store._db() as db:assert db.execute('SELECT COUNT(*) FROM lifecycle_control').fetchone()[0]==0
