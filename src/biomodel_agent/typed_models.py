"""Native multi-model lifecycle: strict import, frozen plans, bounded execution.

The ODE and FBA routes retain their existing protocols. These adapters never
translate stochastic/spatial/discrete models into an ODE or an SBML façade.
"""
from contextlib import contextmanager
import importlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import time
from uuid import uuid4

from . import model_contracts as c
from .storage import canonical_bytes

TYPES = ('stochastic', 'pde', 'boolean', 'individual')
VERSION = 1


def backend(model_type):
    if model_type not in TYPES:
        raise ValueError('unsupported native model_type; choose '+', '.join(TYPES))
    return importlib.import_module('.'+model_type, __package__)


def capabilities():
    result = [dict(model_type='ode', profile='existing bounded SBML ODE', entrypoint='agent/run and existing ODE CLI'),
              dict(model_type='fba', profile='existing bounded steady-state LP/FBC v2', entrypoint='agent-fba/fba-run')]
    for name in TYPES:
        b = backend(name)
        result.append(dict(model_type=name, profile=b.PROFILE, entrypoint='typed-import/typed-prepare/typed-execute/typed-run/typed-replay/agent-typed',
                           limitations=b.LIMITATIONS, agent_recovery='opt_in_typed_agent_v2; legacy_not_supported; unknown effects block retry',
                           scientific_support='unknown'))
    return result


def _pairs(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError('duplicate JSON field: '+k)
        out[k] = v
    return out


def _decode(raw):
    if len(raw) > 2_000_000:
        raise ValueError('native JSON import exceeds 2MB')
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs)
    canonical_bytes(value)  # rejects JSON NaN and infinity before any simulation
    if not isinstance(value, dict):
        raise ValueError('native model must be a JSON object')
    backend(value.get('model_type')).validate(value)
    return value


def import_model(store, path):
    with Path(path).open('rb') as handle:
        raw = handle.read(2_000_001)
    model = _decode(raw); b = backend(model['model_type']); blob = store.put_blob(raw)
    return store.put('typed_model', dict(version=VERSION, model_type=b.MODEL_TYPE, profile=b.PROFILE,
        model=model, blob_ref=blob, origin='operator_supplied_native_json', scientific_support='unknown'), refs=(blob,))


def load(store, ref, *, model_type=None):
    obj = store.get(ref); p = obj['payload']
    if obj['kind']!='typed_model' or type(p.get('version')) is not int or p['version']!=VERSION:
        raise ValueError('model_type requires a native typed_model artifact')
    b = backend(p['model_type'])
    if model_type is not None and model_type!=b.MODEL_TYPE:
        raise ValueError('model_type does not match the frozen native model')
    if p['profile']!=b.PROFILE or p['blob_ref'] not in obj['refs']:
        raise ValueError('native model profile/blob binding mismatch')
    raw = _decode(store.get_blob(p['blob_ref']))
    if canonical_bytes(raw)!=canonical_bytes(p['model']) or raw['model_type']!=b.MODEL_TYPE:
        raise ValueError('native model disagrees with its original raw file')
    return b.validate(p['model'])


def prepare(store, model_ref, settings=None, *, replay_of=None):
    model = load(store, model_ref); b = backend(model['model_type']); s = b.config(settings); b.compatible(model, s)
    from .application import environment_fingerprint
    refs = [model_ref]
    if replay_of is not None:
        summary(store, replay_of); refs.append(replay_of)
    return store.put('typed_input', dict(version=VERSION, id=uuid4().hex, model_type=b.MODEL_TYPE, profile=b.PROFILE,
        model_ref=model_ref, settings=s, environment=environment_fingerprint(), replay_of=replay_of,
        execution_policy='one_reservation_per_plan_in_this_store; unknown_or_partial_effects_block_retry',
        timeout_scope='worker_wall_time_including_startup; not_entire_research_task'), refs=tuple(refs))


def load_input(store, ref):
    obj = store.get(ref); p = obj['payload']
    if obj['kind']!='typed_input' or type(p.get('version')) is not int or p['version']!=VERSION or p['model_ref'] not in obj['refs']:
        raise ValueError('invalid typed execution input')
    model = load(store, p['model_ref'], model_type=p['model_type']); b = backend(p['model_type'])
    if p['profile']!=b.PROFILE or canonical_bytes(p['settings'])!=canonical_bytes(b.config(p['settings'])):
        raise ValueError('typed input profile/settings mismatch')
    if p.get('replay_of') and p['replay_of'] not in obj['refs']:
        raise ValueError('typed replay provenance missing from input')
    b.compatible(model, p['settings'])
    return p, model


def _worker(model, settings, connection):
    try:
        data = backend(model['model_type']).execute(model, settings)
        canonical_bytes(data)
        connection.send(dict(status='succeeded', data=data))
    except c.WorkLimit:
        connection.send(dict(status='work_limit', data=None, error_type='WorkLimit'))
    except (ArithmeticError, ValueError, TypeError):
        connection.send(dict(status='numerical_failure', data=None, error_type='InvalidNumericalResult'))
    except Exception:
        connection.send(dict(status='failed', data=None, error_type='BackendFailure'))
    finally:
        connection.close()


def solve(model, settings):
    # Revalidate at the public boundary, even when called outside the lifecycle.
    b = backend(model['model_type']); b.validate(model); settings = b.config(settings); b.compatible(model, settings)
    ctx = mp.get_context('spawn'); parent, child = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_worker, args=(model, settings, child), daemon=True)
    stop = time.monotonic()+settings['timeout_s']
    try:
        process.start(); child.close()
        remaining = max(0., stop-time.monotonic())
        if not parent.poll(remaining):
            return dict(status='time_limit', data=None, error_type='WorkerDeadlineExceeded')
        try:
            result = parent.recv()
        except (EOFError, OSError):
            return dict(status='failed', data=None, error_type='WorkerExitedWithoutResult')
        if time.monotonic()>stop:
            return dict(status='time_limit', data=None, error_type='WorkerDeadlineExceeded')
        canonical_bytes(result)
        return result
    finally:
        child.close(); parent.close()
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=1.)
            if process.is_alive():
                process.kill(); process.join(timeout=1.)
            process.close()


def verification(model, settings, result):
    b = backend(model['model_type']); checks = []
    if result.get('status')=='succeeded' and isinstance(result.get('data'), dict):
        try:
            checks = b.checks(model, settings, result['data'])
        except (ValueError, TypeError, KeyError, IndexError, OverflowError):
            checks = [c.check('result_contract', False, 'Malformed result cannot be accepted.')]
    eligible = bool(checks) and all(v['status']=='pass' for v in checks)
    status = result.get('status', 'failed')
    if status=='succeeded' and not eligible:
        status='numerical_failure'
    if status not in ('succeeded','work_limit','time_limit','failed','numerical_failure'):
        status='failed'
    return dict(model_type=b.MODEL_TYPE, profile=b.PROFILE, status=status,
                checks=checks, completion_eligible=eligible, scientific_support='unknown', comparison=dict(status='not_requested'),
                limitations=b.LIMITATIONS)


def _records(store, kind, input_ref):
    # Inspect immutable files instead of relying on a possibly stale SQLite index.
    out = []
    for path in sorted(store.objects_dir.glob('*.json')):
        raw = json.loads(path.read_text())
        if raw.get('kind')==kind and raw.get('payload', {}).get('input_ref')==input_ref:
            record = store.get(path.stem); out.append((path.stem, record))
    return out


@contextmanager
def _owner(store, ref):
    import fcntl
    directory = store.root/'.typed-owners'; directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink():
        raise ValueError('typed owner directory must not be a symlink')
    fd = os.open(directory/(ref+'.lock'), os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('typed input already has an active owner') from exc
        yield
    finally:
        os.close(fd)


def execute(store, input_ref):
    inp, model = load_input(store, input_ref)
    with _owner(store, input_ref):
        prior = _records(store, 'typed_run', input_ref)
        reserved = _records(store, 'typed_execution', input_ref)
        if prior:
            if len(prior)!=1 or len(reserved)!=1:
                raise ValueError('conflicting typed execution history')
            summary(store, prior[0][0]); return prior[0][0]
        if reserved:
            raise ValueError('typed execution has unknown or partial effects; inspect receipts, no automatic retry')
        from .application import environment_fingerprint
        if inp['environment']!=environment_fingerprint():
            raise ValueError('frozen typed environment changed; prepare a new plan')
        execution_ref = store.put('typed_execution', dict(version=VERSION, input_ref=input_ref, phase='reserved', id=uuid4().hex), refs=(input_ref,))
        result = solve(model, inp['settings'])
        verified = verification(model, inp['settings'], result)
        receipt_ref = store.put('typed_execution_receipt', dict(version=VERSION, input_ref=input_ref, execution_ref=execution_ref,
            phase='returned', result=result), refs=(input_ref, execution_ref))
        return store.put('typed_run', dict(version=VERSION, model_type=inp['model_type'], input_ref=input_ref, model_ref=inp['model_ref'],
            execution_ref=execution_ref, receipt_ref=receipt_ref, verification=verified, scientific_support='unknown'),
            refs=(input_ref, execution_ref, receipt_ref))


def run(store, model_ref, settings=None):
    return execute(store, prepare(store, model_ref, settings))


def result(store, run_ref):
    obj = store.get(run_ref); p = obj['payload']
    if obj['kind']!='typed_run' or type(p.get('version')) is not int or p['version']!=VERSION or not {p['input_ref'],p['execution_ref'],p['receipt_ref']}<=set(obj['refs']):
        raise ValueError('invalid native typed run binding')
    inp, model = load_input(store, p['input_ref'])
    exe = store.get(p['execution_ref']); rec = store.get(p['receipt_ref']); receipt = rec['payload']
    if (p['model_ref']!=inp['model_ref'] or p['model_type']!=inp['model_type'] or exe['kind']!='typed_execution'
        or type(exe['payload'].get('version')) is not int or exe['payload']['version']!=VERSION or exe['payload'].get('phase')!='reserved'
        or exe['payload'].get('input_ref')!=p['input_ref'] or p['input_ref'] not in exe['refs']
        or rec['kind']!='typed_execution_receipt' or type(receipt.get('version')) is not int or receipt['version']!=VERSION or receipt.get('phase')!='returned'
        or receipt.get('input_ref')!=p['input_ref'] or receipt.get('execution_ref')!=p['execution_ref']
        or not {p['input_ref'],p['execution_ref']}<=set(rec['refs'])):
        raise ValueError('typed input/execution/receipt mismatch')
    return inp, model, receipt['result']


def summary(store, run_ref):
    inp, model, data = result(store, run_ref)
    return verification(model, inp['settings'], data)


def status(store, input_ref):
    load_input(store, input_ref)
    runs = _records(store, 'typed_run', input_ref); reserved = _records(store, 'typed_execution', input_ref)
    if len(runs)>1 or len(reserved)>1:
        raise ValueError('conflicting native execution history')
    if runs:
        return dict(state='returned', run_ref=runs[0][0], outcome=summary(store, runs[0][0]))
    return dict(state='unknown_effects' if reserved else 'ready', run_ref=None,
                automatic_retry=False, execution_refs=[v[0] for v in reserved])


def replay(store, run_ref):
    # A new explicit scientific execution, never recovery of an interrupted Agent.
    inp, _, _ = result(store, run_ref); summary(store, run_ref)
    return execute(store, prepare(store, inp['model_ref'], inp['settings'], replay_of=run_ref))


def report_lines(store, ref):
    obj = store.get(ref); p = obj['payload']
    if obj['kind']=='typed_agent_run':
        recoverable=p.get('version')==2 and p.get('typed_protocol')=='typed_agent_v2'
        lines = [f"Native typed Agent: **{p['state']}**", f"Model type: `{p['model_type']}`",
                 f"Provider calls: {p['call_count']}; scientific executions: {p['run_count']}",
                 f"Task: `{p['task_ref']}`", f"Execution journal: `{p['execution_ref']}`",
                 ('Agent continuation protocol: **typed_agent_v2**; verified returns can be adopted; unknown effects block retry.' if recoverable else
                  'Agent continuation/recovery: not supported; unknown effects are never retried.')]
        if recoverable:
            from .typed_agent_runtime import validate_task, bound_result
            from .timeline import cell
            task=validate_task(store,p['task_ref'])
            if p['run_ref']:bound_result(store,task,p['run_ref'])
            lines.extend([f"Remaining provider calls: {max(0,task['max_calls']-p['call_count'])}",
                          f"Checkpoint: `{p['checkpoint_ref']}`"])
            for event in p['events']:
                if event.get('type')=='awaiting_input':lines.append('Question: '+cell(event['question']))
                elif event.get('type')=='researcher_reply':
                    clarification=store.get(event['clarification_ref'])['payload']
                    lines.append('Reply by '+cell(clarification['respondent'])+': '+cell(clarification['reply']))
        return lines+(['']+report_lines(store, p['run_ref']) if p['run_ref'] else ['', 'No confirmed scientific result. Scientific support: **unknown**.'])
    if obj['kind']=='typed_model':
        model = load(store, ref); b = backend(model['model_type'])
        return [f"Native {model['model_type']} model: `{model['id']}`", f"Profile: `{b.PROFILE}`",
                'Scientific support: **unknown**; units and input provenance are declared, not independently validated.',
                '', '```json', json.dumps(model, indent=2, ensure_ascii=False), '```', *b.LIMITATIONS]
    inp, model, outcome = result(store, ref); verified = summary(store, ref)
    lines = [f"Native {model['model_type']} simulation: **{verified['status']}**", f"Profile: `{verified['profile']}`",
             'Scientific support: **unknown**; independent domain review: **not_run**.',
             'Reference comparison: **not_requested**. Numerical checks are not scientific certification.',
             f"Model: `{inp['model_ref']}`", f"Frozen input: `{p['input_ref']}`", f"Returned receipt: `{p['receipt_ref']}`",
             f"Replay of: `{inp['replay_of']}`", f"Settings: `{json.dumps(inp['settings'], sort_keys=True)}`",
             f"Units (declared): `{json.dumps(model['units'], sort_keys=True)}`",
             'Timeout applies to the calculation worker including startup; it is not a whole-research deadline.', '',
             '| Check | Status | Meaning |', '|---|---|---|']
    lines += [f"| {v['code']} | {v['status']} | {v['message']} |" for v in verified['checks']]
    lines += ['', *verified['limitations']]
    d = outcome.get('data')
    if d is not None:
        if model['model_type']=='stochastic':
            preview = dict(species=d['species'], final_mean=d['mean'][-1], final_variance=d['variance'][-1], events=d['event_count'])
        elif model['model_type']=='pde':
            preview = dict(grid_kind=d['grid_kind'], dx=d['dx'], initial_mass=d['mass'][0], final_mass=d['mass'][-1], final_values=d['values'][-1][:12])
        elif model['model_type']=='boolean':
            preview = {k:d[k] for k in ('nodes','terminal_fixed_point','synchronous_cycle','asynchronous_attractor')}; preview['final_state']=d['states'][-1]
        else:
            preview = dict(initial_population=d['population'][0], final_population=d['population'][-1], activations=d['activation_count'], final_cells=d['frames'][-1][:12])
        lines += ['', 'Saved trajectory preview (full data in returned receipt):', '```json', json.dumps(preview, indent=2), '```']
    else:
        lines += ['', f"No complete trajectory: `{outcome.get('error_type', 'unknown')}`."]
    return lines
