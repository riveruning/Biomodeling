import hashlib
import json
from pathlib import Path
import shutil

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
inventory = {}
for folder in ('src', 'tests', 'docs', 'tasks', 'benchmarks/development'):
    for p in sorted((ROOT/folder).rglob('*')):
        if not p.is_file() or '__pycache__' in p.parts:
            continue
        name = str(p.relative_to(ROOT))
        inventory[name] = hashlib.sha256(p.read_bytes()).hexdigest()
        target = OUT/'before'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, target)
(OUT/'baseline-hashes.json').write_text(json.dumps(inventory, indent=2)+'\n')
prior = {}
for folder in ('audit-t08c-http-transport-20261019', 'audit-t08d-0-reporting-20261020', 'research-readiness-audit'):
    for p in sorted((ROOT/'artifacts'/folder).rglob('*')):
        if p.is_file():
            prior[str(p.relative_to(ROOT))] = hashlib.sha256(p.read_bytes()).hexdigest()
(OUT/'prior-artifact-hashes.json').write_text(json.dumps(prior, indent=2)+'\n')
print(json.dumps({'baseline_files': len(inventory), 'prior_artifact_files': len(prior)}))
