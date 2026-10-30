"""Real trusted subprocess controls; no provider or submitted code execution."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

import pytest

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def runtime():
    spec=importlib.util.spec_from_file_location('runtime_test',ROOT/'benchmarks/development/comparison_runtime.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def command(code):return [str(Path(sys.executable).resolve()),'-I','-S','-c',code]


def test_real_stdin_eof_separate_stderr_and_environment(runtime,tmp_path,monkeypatch):
    monkeypatch.setenv('BIOMODEL_PRIVATE_CANARY','must-not-be-inherited')
    code='import os,sys; data=sys.stdin.buffer.read(); assert "BIOMODEL_PRIVATE_CANARY" not in os.environ;sys.stdout.buffer.write(data);sys.stderr.write("separate")'
    p=tmp_path/'capture';receipt=runtime.supervise(command(code),tmp_path,b'input\x00bytes',p)
    assert receipt['status']=='succeeded' and receipt['exit_code']==0
    assert (p/'stdout.bin').read_bytes()==b'input\x00bytes'
    assert (p/'stderr.bin').read_bytes()==b'separate'
    assert receipt['stdin_bytes_written']==11
    assert runtime.verify_capture(p)==receipt
    assert receipt['usage'] is None and receipt['isolation']=='not_provided'


def test_nonzero_process_keeps_partial_output(runtime,tmp_path):
    p=tmp_path/'capture';r=runtime.supervise(command('import sys;print("partial",flush=True);sys.exit(7)'),tmp_path,b'',p)
    assert r['status']=='process_error' and r['exit_code']==7
    assert (p/'stdout.bin').read_text()=='partial\n'


def test_actual_timeout_kills_process_and_keeps_prefix(runtime,tmp_path):
    p=tmp_path/'capture';r=runtime.supervise(command('import time;print("started",flush=True);time.sleep(10)'),tmp_path,b'',p,timeout_s=.3)
    assert r['status']=='timeout' and r['elapsed_seconds']<3
    assert r['exit_code']<0 and b'started' in (p/'stdout.bin').read_bytes()


@pytest.mark.parametrize('channel',['stdout','stderr'])
def test_pipe_flood_is_bounded_and_stopped(runtime,tmp_path,channel):
    p=tmp_path/'capture';r=runtime.supervise(command(f'import sys,time;sys.{channel}.write("x"*100000);sys.{channel}.flush();time.sleep(10)'),tmp_path,b'',p,max_bytes=512)
    assert r['status']=='output_limit'
    assert sum(v['bytes'] for v in r['logs'].values())==512
    assert runtime.verify_capture(p)==r


def test_shared_pipe_cap_is_not_two_full_budgets(runtime,tmp_path):
    p=tmp_path/'capture';r=runtime.supervise(command('import sys;sys.stdout.write("a"*400);sys.stdout.flush();sys.stderr.write("b"*400);sys.stderr.flush()'),tmp_path,b'',p,max_bytes=512)
    assert r['status']=='output_limit' and sum(v['bytes'] for v in r['logs'].values())==512


def test_launch_error_is_a_retained_receipt(runtime,tmp_path):
    p=tmp_path/'capture';r=runtime.supervise(['/nonexistent/explicit-control'],tmp_path,b'',p)
    assert r['status']=='launch_error' and r['exit_code'] is None and r['error']
    assert runtime.verify_capture(p)==r


@pytest.mark.parametrize('kwargs',[{'timeout_s':True},{'timeout_s':float('nan')},{'timeout_s':0},{'max_bytes':True},{'max_bytes':8_000_001}])
def test_invalid_limits_do_not_reserve_or_launch(runtime,tmp_path,kwargs):
    p=tmp_path/'capture'
    with pytest.raises(ValueError):runtime.supervise(command('print(1)'),tmp_path,b'',p,**kwargs)
    assert not p.exists()


def test_shell_string_and_relative_executable_refused(runtime,tmp_path):
    for argv in ('echo hello',['python','-c','print(1)']):
        with pytest.raises(ValueError):runtime.supervise(argv,tmp_path,b'',tmp_path/'capture')


def test_one_shot_unknown_reservation_is_never_retried(runtime,tmp_path):
    p=tmp_path/'capture';p.mkdir();(p/'reservation.json').write_text('{"outcome":"unknown_until_receipt"}')
    with pytest.raises(FileExistsError):runtime.supervise(command('print(1)'),tmp_path,b'',p)
    assert not (p/'receipt.json').exists()


@pytest.mark.parametrize('mutation',['log','reservation','exit_code','status','elapsed','stdin'])
def test_capture_changes_detected(runtime,tmp_path,mutation):
    p=tmp_path/'capture';runtime.supervise(command('print(1)'),tmp_path,b'',p)
    if mutation=='log':(p/'stdout.bin').write_bytes(b'changed')
    elif mutation=='reservation':
        r=json.loads((p/'reservation.json').read_text());r['timeout_s']=7;runtime.save(p/'reservation.json',r)
    else:
        r=json.loads((p/'receipt.json').read_text())
        field,value={'exit_code':('exit_code',7),'status':('status','invented'),'elapsed':('elapsed_seconds',-1),'stdin':('stdin_bytes_written',1)}[mutation]
        r[field]=value;runtime.save(p/'receipt.json',r)
    with pytest.raises(ValueError):runtime.verify_capture(p)


@pytest.fixture
def task(runtime,tmp_path):
    m=runtime.system.development_manifest();p=tmp_path/'task'
    runtime.system.prepare_task(m,'codex-sol--decay--0--initial',p)
    return p


@pytest.mark.parametrize('scenario,status',[('success','succeeded'),('process_error','process_error'),('timeout','timeout'),('output_limit','output_limit')])
def test_process_capture_import_uses_measured_exit_and_time(runtime,task,tmp_path,scenario,status):
    p=tmp_path/'run';result=runtime.run_fixture(task,p,scenario)
    assert result['status']==status and result['actual_model_calls']==0
    process=runtime.verify_capture(p/'process');record=runtime.system._read(p/'imported/record.json')
    assert record['telemetry']['elapsed_seconds']==process['elapsed_seconds']
    assert record['assessment']['numeric']=='unknown' and record['evidence_kind']=='scripted_control'
    assert record['observation']['execution_observed'] is None
    if status=='succeeded':assert record['status']=='finished' and record['observation']['state']=='incomplete'
    elif status=='timeout':assert record['status']=='timeout'
    else:assert record['status']=='process_error'


def make_capture(runtime,root,series):
    root.mkdir();raw=json.dumps(series).encode();(root/'trajectory.json').write_bytes(raw)
    runtime.save(root/'delivery.json',dict(state='completed',execution_declared=True,question_texts=[],artifacts=[dict(path='trajectory.json',role='trajectory',sha256=runtime.sha(raw))]))


def test_hash_bound_trajectory_bridge_does_not_assert_execution_or_task_score(runtime,task,tmp_path):
    ref=runtime.readiness.analytic_reference('decay');capture=tmp_path/'delivery';make_capture(runtime,capture,ref['series'])
    result=runtime.trajectory_diagnostic(task,capture,'trajectory.json',tmp_path/'diagnostic')
    assert result['numeric']['comparison']['status']=='pass'
    assert result['formal_task_score']=='not_assigned' and result['execution_observed'] is None
    assert result['independent_review']=='pending'


def test_unresolved_stage_not_given_clarified_answer_score(runtime,tmp_path):
    m=runtime.system.development_manifest();task=tmp_path/'task'
    runtime.system.prepare_task(m,'codex-sol--translation--0--initial',task)
    capture=tmp_path/'delivery';make_capture(runtime,capture,runtime.readiness.analytic_reference('translation')['series'])
    result=runtime.trajectory_diagnostic(task,capture,'trajectory.json',tmp_path/'diagnostic')
    assert result['status']=='not_comparable_unresolved_phase' and result['numeric'] is None


def test_changed_delivery_refused_without_diagnostic_output(runtime,task,tmp_path):
    capture=tmp_path/'delivery';make_capture(runtime,capture,runtime.readiness.analytic_reference('decay')['series'])
    (capture/'trajectory.json').write_text('{}');output=tmp_path/'diagnostic'
    with pytest.raises(ValueError):runtime.trajectory_diagnostic(task,capture,'trajectory.json',output)
    assert not output.exists()


def test_nontrajectory_role_cannot_be_scored(runtime,task,tmp_path):
    capture=tmp_path/'delivery';make_capture(runtime,capture,runtime.readiness.analytic_reference('decay')['series'])
    delivery=json.loads((capture/'delivery.json').read_text());delivery['artifacts'][0]['role']='program';runtime.save(capture/'delivery.json',delivery)
    with pytest.raises(ValueError,match='trajectory'):runtime.trajectory_diagnostic(task,capture,'trajectory.json',tmp_path/'diagnostic')


def test_sandbox_failure_never_invokes_submitted_program(runtime,tmp_path,monkeypatch):
    def fake(argv,cwd,stdin,output,**kwargs):
        output.mkdir();(output/'stdout.bin').write_bytes(b'');(output/'stderr.bin').write_bytes(b'namespace unavailable')
        assert '--unshare-all' in argv and '--clearenv' in argv
        return dict(status='launch_error')
    monkeypatch.setattr(runtime,'supervise',fake)
    result=runtime.probe_sandbox(tmp_path/'probe')
    assert result['status']=='unavailable' and result['submitted_program_executed'] is False


@pytest.mark.parametrize('leader_waits',[True,False])
def test_same_group_child_is_stopped_and_pipes_do_not_hang(runtime,tmp_path,leader_waits):
    code=('import subprocess,sys,time; '
          'p=subprocess.Popen([sys.executable,"-I","-S","-c","import time;time.sleep(10)"]); '
          'print(p.pid,flush=True); '+('time.sleep(10)' if leader_waits else 'sys.exit(0)'))
    output=tmp_path/'capture'
    receipt=runtime.supervise(command(code),tmp_path,b'',output,timeout_s=.3)
    child=int((output/'stdout.bin').read_text().strip())
    try:
        for _ in range(20):
            stat=Path(f'/proc/{child}/stat')
            if not stat.exists() or stat.read_text().split()[2]=='Z':break
            time.sleep(.02)
        else:pytest.fail('same process-group child still running')
    finally:
        try:os.kill(child,9)
        except ProcessLookupError:pass
    assert receipt['status']==('timeout' if leader_waits else 'succeeded')
    assert receipt['elapsed_seconds']<3


def test_fixture_cannot_be_reclassified_as_external_or_live_record(runtime,tmp_path):
    m=runtime.system.development_manifest();m['evidence_kind']='external_recording'
    task=tmp_path/'task';runtime.system.prepare_task(m,'codex-sol--decay--0--initial',task)
    output=tmp_path/'capture'
    with pytest.raises(ValueError,match='scripted'):runtime.run_fixture(task,output)
    assert not output.exists()


def test_changed_stage_input_refused_before_process(runtime,task,tmp_path):
    (task/'runtime/source.txt').write_text('different input')
    output=tmp_path/'capture'
    with pytest.raises(ValueError):runtime.run_fixture(task,output)
    assert not output.exists()


def test_trajectory_unit_and_value_disagreements_remain_separate(runtime,task,tmp_path):
    series=deepcopy(runtime.readiness.analytic_reference('decay')['series'])
    series['values']['A']=[0.]*len(series['times'])
    capture=tmp_path/'delivery';make_capture(runtime,capture,series)
    result=runtime.trajectory_diagnostic(task,capture,'trajectory.json',tmp_path/'diagnostic')
    assert result['numeric']['comparison']['status']=='fail' and result['formal_task_score']=='not_assigned'
    series['time_unit']='second'
    other=tmp_path/'other-delivery';make_capture(runtime,other,series)
    with pytest.raises(ValueError):runtime.trajectory_diagnostic(task,other,'trajectory.json',tmp_path/'bad-diagnostic')
    assert not (tmp_path/'bad-diagnostic').exists()


def test_blocked_stdin_uses_same_deadline(runtime,tmp_path):
    r=runtime.supervise(command('import time;time.sleep(10)'),tmp_path,b'x'*100000,tmp_path/'capture',timeout_s=.2)
    assert r['status']=='timeout' and r['stdin_bytes_written']<100000
    assert r['elapsed_seconds']<3


def test_overflowing_timeout_is_refused_before_launch(runtime,tmp_path):
    with pytest.raises(ValueError):runtime.supervise(command('print(1)'),tmp_path,b'',tmp_path/'capture',timeout_s=10**1000)
    assert not (tmp_path/'capture').exists()
