"""R11b-1 bounded trusted-process captures and read-only trajectory diagnostics.

No model service or submitted program is launched by this CLI. The generic
supervisor is an operational primitive for trusted operator commands, NOT an
OS sandbox. Fixture captures are always scripted_control. Sandbox availability
is checked separately and never replaced by an unisolated execution fallback.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from uuid import uuid4
import importlib.util


def _load(name):
    spec = importlib.util.spec_from_file_location('runtime_' + name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


system = _load('system_comparison')
readiness = _load('evaluation_readiness')
native = system.native
PROTOCOL = 'trusted_comparison_capture_v1'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    tmp.replace(path)


def _number(value, low, high):
    try:valid=type(value) in (int,float) and math.isfinite(value) and low<=value<=high
    except OverflowError:valid=False
    if not valid:
        raise ValueError('invalid bounded process limit')


def supervise(argv, cwd, stdin, output, *, timeout_s=5., max_bytes=1_000_000):
    """Capture a trusted command with a single wall deadline and shared pipe cap.

    One-shot reservation survives parent crashes as outcome_unknown. Process
    groups are killed on timeout/cap and after leader exit. A descendant that
    deliberately escapes the session needs a real sandbox/cgroup, not this API.
    """
    if not isinstance(argv, list) or not 1 <= len(argv) <= 64 or any(not isinstance(a, str) or '\0' in a or len(a)>64000 for a in argv):
        raise ValueError('bounded argv list required; no shell command strings')
    if not Path(argv[0]).is_absolute() or not Path(cwd).is_dir():
        raise ValueError('absolute executable and existing working directory required')
    if not isinstance(stdin, bytes) or len(stdin)>100_000:
        raise ValueError('bounded byte stdin required')
    _number(timeout_s, .05, 3600)
    if type(max_bytes) is not int or not 128 <= max_bytes <= 8_000_000:
        raise ValueError('invalid shared stdout/stderr budget')
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    reservation = dict(protocol=PROTOCOL, argv_sha256=readiness.digest(argv), stdin_sha256=sha(stdin),
                       stdin_bytes=len(stdin),
                       timeout_s=timeout_s, max_combined_pipe_bytes=max_bytes,
                       cwd=str(Path(cwd).resolve()), scope='trusted_operator_command_only_not_a_sandbox',
                       implementation_sha256=sha(Path(__file__).read_bytes()), outcome='unknown_until_receipt')
    save(output/'reservation.json', reservation)
    started=time.monotonic(); proc=None; selector=selectors.DefaultSelector()
    streams={}; handles={}; written=0; captured=0; reason=None; error=None

    def kill_group():
        if proc is not None:
            try: os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError: pass

    def close(key):
        try: selector.unregister(key.fileobj)
        except (KeyError, ValueError): pass
        key.fileobj.close()

    try:
        handles={name:(output/(name+'.bin')).open('wb') for name in ('stdout','stderr')}
        try:
            proc=subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, start_new_session=True, close_fds=True,
                                  env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'})
        except OSError as exc:
            reason='launch_error';error=str(exc)
        if proc is not None:
            for name, pipe in (('stdout',proc.stdout),('stderr',proc.stderr)):
                os.set_blocking(pipe.fileno(),False);selector.register(pipe,selectors.EVENT_READ,name)
            if stdin:
                os.set_blocking(proc.stdin.fileno(),False);selector.register(proc.stdin,selectors.EVENT_WRITE,'stdin')
            else:proc.stdin.close()
            stop_at=None
            while selector.get_map() or proc.poll() is None:
                now=time.monotonic()
                if reason is None and now-started>=timeout_s:
                    reason='timeout';kill_group();stop_at=now+.25
                # Ordinary children may leave inherited pipes open. Never wait
                # beyond the same deadline; detached hostile trees are out of scope.
                if proc.poll() is not None:
                    kill_group()
                if stop_at is not None and now>=stop_at:break
                for key,_ in selector.select(.02):
                    if key.data=='stdin':
                        try:n=os.write(key.fd,stdin[written:written+16384]);written+=n
                        except BrokenPipeError:close(key);continue
                        if written==len(stdin):close(key)
                    else:
                        data=os.read(key.fd,16384)
                        if not data:close(key);continue
                        keep=data[:max(0,max_bytes-captured)]
                        handles[key.data].write(keep);captured+=len(keep)
                        if len(keep)!=len(data) and reason is None:
                            reason='output_limit';kill_group();stop_at=time.monotonic()+.25
            kill_group()
            proc.wait(timeout=1.)
            if reason is None:reason='succeeded' if proc.returncode==0 else 'process_error'
    except Exception as exc:
        reason='supervision_error';error=repr(exc);kill_group()
        if proc is not None:proc.wait(timeout=1.)
    finally:
        kill_group()
        if proc is not None:
            try:proc.wait(timeout=1.)
            except subprocess.TimeoutExpired:pass
        for key in list(selector.get_map().values()):close(key)
        selector.close()
        for handle in handles.values():handle.close()
    logs={}
    for name in ('stdout','stderr'):
        path=output/(name+'.bin')
        if not path.exists():path.write_bytes(b'')
        raw=path.read_bytes();logs[name]=dict(sha256=sha(raw),bytes=len(raw))
    receipt=dict(**reservation,status=reason,elapsed_seconds=time.monotonic()-started,
                 exit_code=proc.returncode if proc is not None else None,stdin_bytes_written=written,
                 logs=logs,error=error,isolation='not_provided',recording_authenticated=False,
                 usage=None,scientific_support='unknown')
    save(output/'receipt.json',receipt)
    return receipt


def verify_capture(path):
    path=Path(path)
    reservation=native.decode(native.read_file(path,'reservation.json',100_000))
    receipt=native.decode(native.read_file(path,'receipt.json',100_000))
    if any(receipt.get(k)!=v for k,v in reservation.items()) or receipt['protocol']!=PROTOCOL:
        raise ValueError('process reservation/receipt binding changed')
    if receipt['status'] not in ('succeeded','process_error','launch_error','timeout','output_limit','supervision_error'):
        raise ValueError('invalid process terminal status')
    _number(receipt['elapsed_seconds'],0,100_000)
    if type(receipt['stdin_bytes_written']) is not int or not 0<=receipt['stdin_bytes_written']<=reservation['stdin_bytes']:
        raise ValueError('invalid recorded stdin delivery')
    code=receipt['exit_code']
    if code is not None and (type(code) is not int or not -255<=code<=255):
        raise ValueError('invalid captured process exit code')
    if receipt['status']=='succeeded' and code!=0:
        raise ValueError('successful process requires zero exit code')
    for name in ('stdout','stderr'):
        raw=native.read_file(path,name+'.bin')
        if receipt['logs'][name]!={'sha256':sha(raw),'bytes':len(raw)}:
            raise ValueError('captured process bytes changed')
    if sum(v['bytes'] for v in receipt['logs'].values())>receipt['max_combined_pipe_bytes']:
        raise ValueError('shared capture budget exceeded')
    return receipt


def probe_sandbox(output):
    """Probe only a fixed trusted Python payload, with no external connection."""
    output=Path(output)
    bwrap=Path('/usr/bin/bwrap')
    if not bwrap.is_file():
        output.mkdir(parents=True,exist_ok=False)
        result=dict(status='unavailable',reason='bwrap_missing',submitted_program_executed=False)
        save(output/'sandbox.json',result);return result
    with tempfile.TemporaryDirectory(prefix='biomodel-canary-') as temp:
        canary=Path(temp)/'private-canary';canary.write_text('test canary only')
        before=os.readlink('/proc/self/ns/net')
        code=('import os; assert not os.path.exists('+repr(str(canary))+'); '
              'assert os.readlink("/proc/self/ns/net") != '+repr(before)+'; print("namespace-probe-ok")')
        argv=[str(bwrap),'--unshare-all','--new-session','--die-with-parent',
              '--ro-bind','/usr','/usr','--ro-bind','/lib','/lib','--ro-bind','/lib64','/lib64',
              '--proc','/proc','--dev','/dev','--tmpfs','/tmp','--clearenv',
              '--setenv','PATH','/usr/bin','--','/usr/bin/python3','-I','-S','-c',code]
        receipt=supervise(argv,Path('/tmp'),b'',output,timeout_s=3.,max_bytes=8000)
    valid=receipt['status']=='succeeded' and (output/'stdout.bin').read_bytes()==b'namespace-probe-ok\n'
    result=dict(status='available_probe_only' if valid else 'unavailable',receipt_sha256=readiness.digest(receipt),
                submitted_program_executed=False,network_request_made=False,
                reason=None if valid else (output/'stderr.bin').read_text(errors='replace')[:2000],
                limitations='Availability probe only; no full hostile-code sandbox, cgroup or model runner certification.')
    save(output/'sandbox.json',result);return result


def run_fixture(task, output, scenario='success'):
    """Real subprocess -> measured capture -> unchanged native offline importer."""
    if scenario not in ('success','process_error','timeout','output_limit'):
        raise ValueError('unsupported trusted fixture')
    m, prepared, _=system.check_task(task)
    if prepared['trial']['runtime']!='codex_cli' or m['evidence_kind']!='scripted_control':
        raise ValueError('only explicitly scripted native capture fixtures are implemented')
    if prepared['remaining_elapsed_seconds']<=0:raise ValueError('remaining task budget is exhausted')
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    capture=output/'capture';capture.mkdir()
    sid=str(uuid4())
    code='import sys,json,time,pathlib\n'
    code+='prompt=sys.stdin.buffer.read()\n'
    code+='print(json.dumps({"type":"thread.started","thread_id":'+repr(sid)+'}),flush=True)\n'
    code+='print(json.dumps({"type":"turn.started"}),flush=True)\n'
    if scenario=='process_error':code+='sys.stderr.write("explicit local fixture failure\\n");sys.exit(7)\n'
    elif scenario=='timeout':code+='time.sleep(10)\n'
    elif scenario=='output_limit':code+='sys.stdout.write("x"*100000);sys.stdout.flush();time.sleep(10)\n'
    else:
        code+='pathlib.Path(sys.argv[1],"delivery.json").write_text(json.dumps({"state":"incomplete","execution_declared":False,"question_texts":[],"artifacts":[]}))\n'
        code+='print(json.dumps({"type":"item.completed","item":{"id":"answer","type":"agent_message","text":"Explicit scripted subprocess control; no model inference. EOF received."}}),flush=True)\n'
        code+='print(json.dumps({"type":"turn.completed","usage":None}),flush=True)\n'
    script=output/'trusted-fixture.py';script.write_text(code)
    fixture_hash=sha(script.read_bytes())
    budget=min(.5,prepared['remaining_elapsed_seconds'])
    receipt=supervise([str(Path(sys.executable).resolve()),'-I','-S',str(script.resolve()),str(capture.resolve())],
                       Path(task)/'runtime',native.read_file(Path(task)/'runtime','TASK.md'),output/'process',
                       timeout_s=budget,max_bytes=8000)
    verify_capture(output/'process')
    if sha(script.read_bytes())!=fixture_hash:raise ValueError('trusted fixture changed during execution')
    (capture/'events.jsonl').write_bytes((output/'process/stdout.bin').read_bytes())
    record=system.import_codex(task,capture,output/'imported',exit_code=receipt['exit_code'],
                              timed_out=receipt['status']=='timeout',elapsed_seconds=receipt['elapsed_seconds'])
    result=dict(status=receipt['status'],process_receipt_sha256=readiness.digest(receipt),
                trusted_fixture_sha256=fixture_hash,preparation_sha256=readiness.digest(prepared),
                imported_record_sha256=readiness.digest(record),evidence_kind='scripted_control',
                actual_model_calls=0,recording_authenticated=False,execution_observed=None,
                scope='trusted fixture only; no model service, submitted program or OS isolation',
                process_capture_budget_status='within' if receipt['elapsed_seconds']<=prepared['remaining_elapsed_seconds'] else 'exceeded')
    save(output/'result.json',result);return result


def trajectory_diagnostic(task, capture, trajectory, output):
    """Read a declared trajectory; execute nothing and never assign a task score."""
    _,prepared,_=system.check_task(task)
    delivery,blobs=native.read_delivery(capture)
    matches=[a for a in delivery['artifacts'] if a['path']==trajectory and a['role']=='trajectory']
    if len(matches)!=1:raise ValueError('one hash-bound trajectory artifact is required')
    trial=prepared['trial']; actual=native.decode(blobs[trajectory])
    if trial['phase']!=readiness.ANALYTIC_SOURCES[trial['case_id']][0]:
        result=dict(status='not_comparable_unresolved_phase',numeric=None)
    else:
        result=dict(status='numeric_diagnostic_only',numeric=readiness.numeric_diagnostic(trial['case_id'],trial['phase'],actual))
    result.update(preparation_sha256=readiness.digest(prepared),delivery_sha256=sha(native.read_file(capture,'delivery.json')),
                  implementation_sha256=sha(Path(__file__).read_bytes()),
                  numeric_protocol_sha256=sha(Path(readiness.__file__).read_bytes()),
                  trajectory_sha256=sha(blobs[trajectory]),case=trial['case_id'],phase=trial['phase'],
                  execution_observed=None,scientific_support='unknown',independent_review='pending',formal_task_score='not_assigned')
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    save(output/'diagnostic.json',result);return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    probe=sub.add_parser('probe-sandbox');probe.add_argument('--output',required=True,type=Path)
    fixture=sub.add_parser('fixture');fixture.add_argument('--task',required=True,type=Path);fixture.add_argument('--output',required=True,type=Path)
    fixture.add_argument('--scenario',choices=('success','process_error','timeout','output_limit'),default='success')
    numeric=sub.add_parser('trajectory-check')
    for name in ('task','capture','output'):numeric.add_argument('--'+name,required=True,type=Path)
    numeric.add_argument('--trajectory',required=True)
    args=parser.parse_args(argv)
    try:
        if args.action=='probe-sandbox':value=probe_sandbox(args.output)
        elif args.action=='fixture':value=run_fixture(args.task,args.output,args.scenario)
        else:value=trajectory_diagnostic(args.task,args.capture,args.trajectory,args.output)
    except (ValueError,KeyError,TypeError,OSError) as exc:print('error: '+str(exc));return 2
    print(json.dumps(value,ensure_ascii=False))
    if args.action=='probe-sandbox':return 0 if value['status']=='available_probe_only' else 1
    if args.action=='fixture':return 0 if value['status']=='succeeded' else 1
    if value['numeric'] is None:return 1
    return 0 if value['numeric']['comparison']['status']=='pass' else 1


if __name__=='__main__':raise SystemExit(main())
