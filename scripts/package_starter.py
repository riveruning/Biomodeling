"""Build a clean source ZIP without Git, credentials, or historical run output."""
from __future__ import annotations

import argparse
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TREES = ('src', 'examples', 'docs', 'tests', 'benchmarks', 'scripts', '.github')
FILES = ('README.md', 'CONTRIBUTING.md', 'pyproject.toml', 'requirements.lock',
         'requirements-pdf.lock', '.env.example', '.gitignore', '.gitattributes', 'artifacts/README.md')
SKIP_DIRS = {'__pycache__', '.pytest_cache', '.git', '.venv', 'node_modules'}


def source_files(root: Path):
    paths = [root / name for name in FILES if (root / name).is_file()]
    for tree in TREES:
        for path in (root / tree).rglob('*'):
            relative = path.relative_to(root)
            if path.is_symlink() or any(part in SKIP_DIRS or part.endswith('.egg-info') for part in relative.parts):
                continue
            if not path.is_file() or path.suffix in {'.pyc', '.pyo'} or path.name.startswith('.env'):
                continue
            paths.append(path)
    return sorted(paths)


def package(output: Path, root: Path = ROOT) -> int:
    paths = source_files(root)
    # Never replace an existing deliverable or source file.
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, 'Biomodeling/' + path.relative_to(root).as_posix())
    return len(paths)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/biomodel-agent-starter.zip')
    args = parser.parse_args()
    count = package(args.output)
    print(f'{args.output.resolve()}: {count} files, {args.output.stat().st_size:,} bytes')
