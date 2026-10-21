"""Freeze bounded F1 inputs before any product edit; no external data access."""
import hashlib
import json
from pathlib import Path
import shutil

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    paths = set()
    for folder, pattern in [('src', '*.py'), ('tests', '*.py'), ('docs', '*.md'),
                            ('tasks', '*.json'), ('benchmarks/development', '*.py')]:
        paths.update((ROOT/folder).rglob(pattern))
    for case in ('decay', 'translation', 'logistic'):
        paths.update(p for p in (ROOT/'benchmarks/inputs/reliability'/case).rglob('*') if p.is_file())
    hashes = {}
    for path in sorted(paths):
        relative = path.relative_to(ROOT)
        hashes[str(relative)] = sha(path)
        target = OUT/'before'/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    (OUT/'baseline-hashes.json').write_text(json.dumps(hashes, indent=2)+'\n')
    prior = {}
    for folder in ('audit-t08a-evaluation-protocol-20261019', 'audit-t08b-workflow-adapters-20261019'):
        for path in sorted((ROOT/'artifacts'/folder).rglob('*')):
            if path.is_file():
                prior[str(path.relative_to(ROOT))] = sha(path)
    (OUT/'prior-artifact-hashes.json').write_text(json.dumps(prior, indent=2)+'\n')
    print(json.dumps({'baseline_files': len(hashes), 'prior_artifact_files': len(prior)}))


if __name__ == '__main__':
    main()
