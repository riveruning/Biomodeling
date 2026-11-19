"""Real kernel contention and abrupt-exit release on POSIX and Windows."""
import multiprocessing as mp
import os
from pathlib import Path
import subprocess
import sys

import pytest
from biomodel_agent import local_ownership as owners


def hold(path,pipe):
    with owners.owned(path):
        pipe.send('held');pipe.recv()


def test_second_descriptor_is_denied_and_baseexception_releases(tmp_path):
    path=tmp_path/'owner.lock'
    with pytest.raises(KeyboardInterrupt):
        with owners.owned(path):
            with pytest.raises(owners.OwnerBusy):owners.acquire(path)
            raise KeyboardInterrupt
    with owners.owned(path):pass
    assert path.is_file() and path.stat().st_size==0


def test_spawned_process_owner_is_exclusive_and_death_releases(tmp_path):
    path=tmp_path/'owner.lock';ctx=mp.get_context('spawn');parent,child=ctx.Pipe()
    process=ctx.Process(target=hold,args=(str(path),child));process.start();child.close()
    try:
        assert parent.poll(20) and parent.recv()=='held'
        with pytest.raises(owners.OwnerBusy):owners.acquire(path)
    finally:
        process.terminate();process.join(15);parent.close()
    assert not process.is_alive()
    with owners.owned(path):pass


def test_unicode_and_space_paths_are_owned_and_handles_not_inherited(tmp_path):
    directory=owners.directory(tmp_path/'研究 空间');path=directory/'owner.lock'
    fd=owners.acquire(path)
    try:assert not os.get_inheritable(fd)
    finally:os.close(fd)
    with owners.owned(path):pass


def test_final_symlink_is_rejected_and_target_unchanged(tmp_path):
    target=tmp_path/'target';target.write_text('keep');link=tmp_path/'owner.lock'
    try:link.symlink_to(target)
    except OSError as error:
        if os.name=='nt':pytest.skip('Windows symlink privilege unavailable; junction tested separately')
        raise error
    with pytest.raises(ValueError,match='reparse|symlink'):owners.acquire(link)
    assert target.read_text()=='keep'


@pytest.mark.skipif(os.name!='nt',reason='Windows junction semantics')
def test_windows_owner_directory_rejects_junction(tmp_path):
    target=tmp_path/'target';target.mkdir();link=tmp_path/'linked'
    result=subprocess.run(['cmd.exe','/d','/c','mklink','/J',str(link),str(target)],capture_output=True)
    assert result.returncode==0,result.stderr.decode(errors='replace')
    try:
        assert owners.is_redirect(link)
        with pytest.raises(ValueError,match='reparse'):owners.directory(link)
    finally:link.rmdir()


@pytest.mark.skipif(os.name!='posix',reason='POSIX FIFO semantics')
def test_special_file_is_rejected_without_blocking(tmp_path):
    path=tmp_path/'owner.lock';os.mkfifo(path)
    with pytest.raises(ValueError,match='regular file'):owners.acquire(path)
