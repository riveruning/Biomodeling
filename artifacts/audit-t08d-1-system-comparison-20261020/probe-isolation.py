"""Execute only a fixed offline visibility probe, never Codex or submissions."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
spec = importlib.util.spec_from_file_location('system_probe', ROOT/'benchmarks/development/system_comparison.py')
system = importlib.util.module_from_spec(spec); spec.loader.exec_module(system)
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, default=OUT/'isolation-probe')
target = parser.parse_args().output.resolve()
target.mkdir(exist_ok=False)
task = target/'task'
system.prepare_task(system.development_manifest(), 'codex-sol--translation--0--initial', task)
delivery = target/'delivery'; delivery.mkdir()
canary = target/'operator-only-canary.txt'; canary.write_text('SYNTHETIC EVALUATOR CANARY, NOT TASK INPUT')
forbidden = [str(canary), str(task/'manifest.json'), str(ROOT/'src/biomodel_agent/agent.py')]
assert all(Path(name).is_absolute() and Path(name).is_file() for name in forbidden)
code = r'''
import hashlib, json, os
from pathlib import Path
import sys
denied = {}
for name in json.loads(sys.argv[1]):
    try:
        Path(name).read_bytes()
        denied[name] = False
    except OSError:
        denied[name] = True
try:
    Path('/work/source.txt').write_text('forbidden edit')
    readonly = False
except OSError:
    readonly = True
Path('/delivery/probe-produced.txt').write_text('fixed offline probe only')
print(json.dumps({'runtime_files': sorted(p.name for p in Path('/work').iterdir()),
    'source_sha256': hashlib.sha256(Path('/work/source.txt').read_bytes()).hexdigest(),
    'forbidden_reads_denied': denied, 'task_inputs_readonly': readonly,
    'network_namespace': os.readlink('/proc/self/ns/net'),
    'environment_names': sorted(os.environ), 'evidence_kind': 'scripted_control'}))
'''
command = ['bwrap', '--die-with-parent', '--new-session', '--unshare-all', '--clearenv',
    '--ro-bind', '/usr', '/usr', '--symlink', 'usr/bin', '/bin',
    '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
    '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
    '--ro-bind', str(task/'runtime'), '/work', '--bind', str(delivery), '/delivery',
    '--chdir', '/work', '--setenv', 'LANG', 'C.UTF-8',
    '--', '/usr/bin/python3', '-I', '-c', code, json.dumps(forbidden)]
(target/'invocation.json').write_text(json.dumps({'command': command,
    'purpose': 'fixed offline OS visibility probe', 'online_inference_calls': 0}, indent=2)+'\n')
result = subprocess.run(command, env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'},
    stdin=subprocess.DEVNULL, capture_output=True, timeout=15)
(target/'stdout.log').write_bytes(result.stdout)
(target/'stderr.log').write_bytes(result.stderr)
if result.returncode:
    (target/'result.json').write_text(json.dumps({'status': 'probe_unavailable', 'returncode': result.returncode})+'\n')
    raise SystemExit(result.returncode)
observed = json.loads(result.stdout)
assert observed['runtime_files'] == ['TASK.md', 'experiment.json', 'source.txt']
assert all(observed['forbidden_reads_denied'].values()) and observed['task_inputs_readonly']
assert observed['source_sha256'] == system._read(task/'preparation.json')['runtime_files']['source.txt']
assert observed['network_namespace'] != os.readlink('/proc/self/ns/net')
assert not {'OPENAI_API_KEY', 'CODEX_API_KEY', 'BIOMODEL_API_KEY'} & set(observed['environment_names'])
assert (delivery/'probe-produced.txt').read_text() == 'fixed offline probe only'
observed.update(status='offline_OS_visibility_probe_passed', native_Codex_execution_verified=False,
    limit='Only this fixed Python probe was executed. Future native launch still needs its own runtime/session/auth mounts and visibility checks.')
(target/'result.json').write_text(json.dumps(observed, indent=2)+'\n')
print(json.dumps(observed))
