"""Frozen, isolated live development checks on two already-exposed cases.

Four predeclared attempts: original-task resume and fresh clarified session for
each case. No selective reruns and no edits to model responses. The original
initial checkpoints are exported with their exact immutable dependency closure.
Model credentials are passed in memory to the configured USTC inference service;
they are never serialized. Numerical references are read only by the scorer.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from biomodel_agent import application, bundles
from biomodel_agent.storage import ArtifactStore

CASES = ['c13_mm_with_inhibitor_ambiguous', 'c18_two_pathway_unit_conflict']
DEADLINE = datetime(2026, 10, 18, 14, 0, tzinfo=timezone.utc)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False))


def prepare(project, data, output, diagnostic_of=None):
    if (output / 'protocol.json').exists():
        raise FileExistsError('protocol already exists; preserve the first preparation')
    output.mkdir(parents=True, exist_ok=True)
    shutil.copytree(project / 'src', output / 'candidate/src', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copyfile(project / 'requirements.lock', output / 'candidate/requirements.lock')
    shutil.copyfile(project / 'benchmarks/development/run_holdout_product.py', output / 'runner.py')
    shutil.copyfile(project / 'benchmarks/development/holdout_sandbox.py', output / 'holdout_sandbox.py')
    jobs = []
    input_hashes = {}
    old = project / 'artifacts/holdout-evaluation-20261017/runs/biomodel'
    for case in CASES:
        # Copy before opening ArtifactStore so its SQLite initialization cannot
        # write to an original evaluation store, even during a nominal read.
        scratch = output / 'preparation-only' / case
        shutil.copytree(old / case / 'incomplete/store', scratch)
        original = json.loads((old / case / 'incomplete/incomplete/result.json').read_text())
        assert original['status'] == 'awaiting_input'
        resume = output / 'runs' / case / 'original-resume'
        bundles.export_bundle(ArtifactStore(scratch), original['agent_ref'], resume / 'store')
        dump(resume / 'incomplete/result.json', original)
        for name in ['source.txt', 'experiment.json', 'TASK.md']:
            source = data / 'cases' / case / 'incomplete' / name
            shutil.copyfile(source, resume / name)
            input_hashes[str(source)] = sha(source)
        reply = data / 'clarifications' / (case + '.clarification.txt')
        shutil.copyfile(reply, resume / 'researcher-reply.txt')
        input_hashes[str(reply)] = sha(reply)
        jobs.append({'case': case, 'mode': 'original-resume', 'phase': 'resume',
                     'work': str(resume), 'parent_calls': original['calls'],
                     'parent_ref': original['agent_ref']})
        fresh = output / 'runs' / case / 'fresh-clarified'
        fresh.mkdir(parents=True)
        for name in ['source.txt', 'experiment.json', 'TASK.md']:
            source = data / 'cases' / case / 'clarified' / name
            shutil.copyfile(source, fresh / name)
            input_hashes[str(source)] = sha(source)
        jobs.append({'case': case, 'mode': 'fresh-clarified', 'phase': 'clarified',
                     'work': str(fresh), 'parent_calls': 0, 'parent_ref': None})
    candidate_files = {str(p.relative_to(output)): sha(p) for p in sorted((output / 'candidate').rglob('*')) if p.is_file()}
    workspace_files = {str(p.relative_to(output)): sha(p) for p in sorted((output / 'runs').rglob('*')) if p.is_file()}
    protocol = {'kind': 'already_seen_live_development_regression', 'independent_holdout': False,
                'prepared_utc': datetime.now(timezone.utc).isoformat(),
                'candidate_files': candidate_files, 'workspace_files': workspace_files,
                'adapters': {name: sha(output / name) for name in ['runner.py', 'holdout_sandbox.py']},
                'input_hashes': input_hashes, 'jobs': jobs,
                'launcher_sha256': sha(Path(__file__)),
                'transport_diagnostic_of': str(diagnostic_of) if diagnostic_of else None,
                'transport_change': 'host-approved network execution; same child filesystem isolation' if diagnostic_of else None,
                'model': 'qwen3.8-chat', 'json_mode': True,
                'max_calls_total': 8, 'max_repairs': 2, 'max_retries': 2,
                'timeout_s': 90, 'max_tokens': 4000, 'request_pacing_s': 5,
                'attempts_per_job': 1, 'original_checkpoints_from_frozen_version': True,
                'resume_budget_is_cumulative': True, 'response_editing': False,
                'numeric_threshold': .001, 'required_grid_tolerance': 1e-12,
                'environment': application.environment_fingerprint()}
    dump(output / 'protocol.json', protocol)
    print(json.dumps({'prepared_jobs': len(jobs), 'source_modules': protocol['environment']['modules_sha256']}), flush=True)


def verify(output, protocol):
    for name, digest in {**protocol['candidate_files'], **protocol['adapters']}.items():
        assert sha(output / name) == digest, name
    for name, digest in protocol['input_hashes'].items():
        assert sha(Path(name)) == digest, name


def run(project, output):
    protocol = json.loads((output / 'protocol.json').read_text())
    verify(output, protocol)
    if (output / 'started.json').exists():
        raise FileExistsError('run already started; do not selectively rerun')
    for name, digest in protocol['workspace_files'].items():
        assert sha(output / name) == digest, name
    spec = importlib.util.spec_from_file_location('recovery_sandbox', output / 'holdout_sandbox.py')
    sandbox = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sandbox)
    sandbox.PROJECT = project
    sandbox.CANDIDATE = output / 'candidate'
    # Reuse exactly the credential reference already used by the original
    # project evaluation; no key appears in commands, saved files or stdout.
    sys.path.append('/usr/lib/python3/dist-packages')
    import yaml
    credential = yaml.safe_load(Path('/home/riveruning/.dsh/.credentials.yaml').read_text())['refs']['USTC_API_KEY']
    dump(output / 'started.json', {'started_utc': datetime.now(timezone.utc).isoformat(), 'job_count': len(protocol['jobs'])})
    for job in protocol['jobs']:
        if datetime.now(timezone.utc) >= DEADLINE:
            dump(output / 'stopped.json', {'reason': 'user deadline reached'})
            return
        verify(output, protocol)
        work, phase = Path(job['work']), job['phase']
        started = time.monotonic()
        try:
            with (work / 'launcher.log').open('w') as log:
                result = subprocess.run(sandbox.product_command(work, phase, output / 'runner.py'),
                                        env=sandbox.clean_environment(credential), stdin=subprocess.DEVNULL,
                                        stdout=log, stderr=subprocess.STDOUT, timeout=900)
        except subprocess.TimeoutExpired:
            dump(work / 'launcher-result.json', {'status': 'launcher_timeout', 'elapsed_s': time.monotonic() - started})
            raise RuntimeError('child timeout; preserve first output and stop remaining requests') from None
        dump(work / 'launcher-result.json', {'exit_code': result.returncode, 'elapsed_s': time.monotonic() - started})
        if result.returncode:
            raise RuntimeError('child failed; inspect launcher.log without rerunning')
        record = json.loads((work / phase / 'result.json').read_text())
        assert record['modules_sha256'] == protocol['environment']['modules_sha256']
        events = json.loads((work / phase / 'agent.json').read_text())['events']
        print(json.dumps({'case': job['case'], 'mode': job['mode'], 'status': record['status'],
                          'calls_cumulative': record['calls'], 'calls_incremental': record['calls'] - job['parent_calls'],
                          'repairs': record['repairs'], 'elapsed_s': time.monotonic() - started}), flush=True)
        failures = [e for e in events if e.get('type') in ['provider_error', 'retry_deferred']]
        persistent = record['status'] != 'completed' and any(
            e.get('type') == 'retry_deferred' or e.get('kind') in ['http', 'transport'] for e in failures)
        if persistent:
            dump(output / 'stopped.json', {'reason': 'provider failure; stop without selective reruns', 'case': job['case'], 'mode': job['mode']})
            return
    verify(output, protocol)
    dump(output / 'completed.json', {'completed_utc': datetime.now(timezone.utc).isoformat(), 'jobs': len(protocol['jobs'])})


def score(data, output):
    import numpy as np
    protocol = json.loads((output / 'protocol.json').read_text())
    rows = []
    for job in protocol['jobs']:
        work, phase = Path(job['work']), job['phase']
        result_path = work / phase / 'result.json'
        row = {'case': job['case'], 'mode': job['mode'], 'status': 'not_run', 'numeric': {'status': 'not_run'}}
        if result_path.exists():
            result = json.loads(result_path.read_text())
            agent = json.loads((work / phase / 'agent.json').read_text())
            row.update(result)
            row['calls_incremental'] = result['calls'] - job['parent_calls']
            row['submit_attempts'] = [{'state': e['state'], 'problems': e['problems']} for e in agent['events']
                                      if e.get('type') == 'tool_result' and e.get('tool') == 'submit_draft']
            if (work / phase / 'trajectory.json').exists():
                actual = json.loads((work / phase / 'trajectory.json').read_text())
                reference_path = data / 'references' / (job['case'] + '.reference.json')
                reference = json.loads(reference_path.read_text())
                required = json.loads((work / 'experiment.json').read_text())['species_to_report']
                t, expected = np.asarray(actual['times'], dtype=float), np.asarray(reference['times'], dtype=float)
                if t.shape != expected.shape or not np.isfinite(t).all() or not np.allclose(t, expected, rtol=0, atol=1e-12):
                    raise ValueError('wrong sampling grid')
                if set(actual['values']) != set(required):
                    raise ValueError('wrong reported species')
                errors = {}
                for name in required:
                    a, r = np.asarray(actual['values'][name], dtype=float), np.asarray(reference['values'][name], dtype=float)
                    if a.shape != r.shape or not np.isfinite(a).all():
                        raise ValueError('invalid trajectory')
                    errors[name] = float(np.max(np.abs(a - r)) / max(1., float(np.max(np.abs(r)))))
                row['numeric'] = {'status': 'pass' if max(errors.values()) <= .001 else 'fail',
                                  'max_scaled_error': max(errors.values()), 'per_species': errors,
                                  'reference_sha256': sha(reference_path)}
        row['successful_execution'] = row['status'] == 'completed' and row['numeric']['status'] == 'pass'
        rows.append(row)
    dump(output / 'results.json', {'kind': 'already_seen_live_development_regression', 'independent_holdout': False,
                                 'rows': rows, 'successful': sum(r['successful_execution'] for r in rows),
                                 'denominator': len(rows), 'original_evaluation_modified': False})
    for row in rows:
        print(json.dumps({k: row[k] for k in ['case', 'mode', 'status', 'numeric', 'successful_execution']}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', type=Path, default=Path('/home/riveruning/biologymodel_agent'))
    parser.add_argument('--dataset', type=Path, default=Path('/home/riveruning/biomodel_agent_evalset'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--diagnostic-of', type=Path)
    parser.add_argument('--phase', choices=['prepare', 'run', 'score'], required=True)
    args = parser.parse_args()
    if args.phase == 'prepare':
        prepare(args.project, args.dataset, args.output, args.diagnostic_of)
    elif args.phase == 'run':
        run(args.project, args.output)
    else:
        score(args.dataset, args.output)


if __name__ == '__main__':
    main()
