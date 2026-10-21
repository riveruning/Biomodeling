import hashlib
import json
from pathlib import Path
import shutil

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
paths = [p for folder in ('src', 'tests', 'docs', 'tasks', 'benchmarks/development')
         for p in sorted((ROOT/folder).rglob('*'))
         if p.is_file() and '__pycache__' not in p.parts]
paths += [ROOT/name for name in ('README.md', 'pyproject.toml', 'requirements.lock')]
inventory = {}
for p in paths:
    name = str(p.relative_to(ROOT))
    inventory[name] = hashlib.sha256(p.read_bytes()).hexdigest()
    target = OUT/'before'/name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(p, target)
(OUT/'baseline-hashes.json').write_text(json.dumps(inventory, indent=2)+'\n')
prior = {}
for folder in ('audit-t08a-evaluation-protocol-20261019', 'audit-t08b-workflow-adapters-20261019',
               'audit-t08b-f1-equation-prose-20261019', 'audit-t08c-http-transport-20261019',
               'research-readiness-audit'):
    for p in sorted((ROOT/'artifacts'/folder).rglob('*')):
        if p.is_file():
            prior[str(p.relative_to(ROOT))] = hashlib.sha256(p.read_bytes()).hexdigest()
(OUT/'prior-artifact-hashes.json').write_text(json.dumps(prior, indent=2)+'\n')
shutil.copyfile(ROOT/'artifacts/audit-t08c-http-transport-20261019/http-mock-execution/results.json', OUT/'legacy-v1-results-before.json')
print(json.dumps({'baseline_files': len(inventory), 'prior_artifact_files': len(prior)}))
