"""Local external-Agent workspace packaging; no provider or scientific run."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.resources
import importlib.util
import json
from pathlib import Path
import platform
import shutil
import sys
import tempfile


REQUIRED = {'numpy': 'numpy', 'scipy': 'scipy', 'python-libsbml': 'libsbml',
            'libroadrunner': 'roadrunner', 'pypdf': 'pypdf', 'httpx': 'httpx'}


def _assets():
    return importlib.resources.files('biomodel_agent').joinpath('workspace_assets')


def doctor():
    pins = {}
    for line in _assets().joinpath('requirements.lock').read_text(encoding='utf-8').splitlines():
        if '==' in line and not line.startswith('#'):
            key, value = line.split('==', 1); pins[key] = value
    packages = {}
    for distribution, module in REQUIRED.items():
        try:
            version = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            version = None
        available = importlib.util.find_spec(module) is not None
        packages[distribution] = dict(installed_version=version, locked_version=pins.get(distribution),
                                     available=available, matches_lock=version is not None and version == pins.get(distribution))
    local_locking = importlib.util.find_spec('fcntl') is not None
    python_supported = sys.version_info >= (3, 12)
    ready = python_supported and local_locking and all(p['available'] for p in packages.values())
    return dict(version=1, ready=ready, python_executable=sys.executable, python_version=platform.python_version(),
        python_supported=python_supported, local_file_locking=local_locking, packages=packages,
        matches_locked_dependencies=all(p['matches_lock'] for p in packages.values()),
        provider_required=False, provider_connectivity='not_tested', scientific_execution='not_run',
        host_requirement='File access plus CLI/code execution; Skill discovery depends on the host.',
        limitations=['Dependency discovery is not a solver or provider health test.',
                     'Use WSL/Linux for the local execution-owner workflow when fcntl is unavailable.'])


def _copy_assets(resource, destination, hashes, prefix=''):
    for child in resource.iterdir():
        relative = prefix + child.name
        if child.is_dir():
            _copy_assets(child, destination, hashes, relative + '/')
        else:
            # Store the skill in the generated workspace discovery location.
            target_name = '.agents/' + relative if relative.startswith('skills/') else relative
            target = destination / target_name
            target.parent.mkdir(parents=True, exist_ok=True)
            content = child.read_bytes(); target.write_bytes(content)
            hashes[target_name] = hashlib.sha256(content).hexdigest()


def initialize(destination):
    """Create a new task workspace atomically, preserving existing paths."""
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError('workspace destination must be a new path')
    if not destination.parent.is_dir():
        raise ValueError('workspace parent must already exist')
    environment = doctor()
    if not environment['ready']:
        raise ValueError('workspace environment is incomplete; inspect workspace-doctor')
    stage = Path(tempfile.mkdtemp(prefix='.biomodel-workspace-', dir=destination.parent))
    try:
        hashes = {}; _copy_assets(_assets(), stage, hashes)
        from . import application, evidence, modeling_plan
        from .storage import ArtifactStore
        store = ArtifactStore(stage / 'store')
        source_refs = {}
        for case in ('complete', 'ambiguous', 'missing'):
            source_refs[case] = evidence.ingest_source(store, stage / 'examples' / f'{case}-source.txt')
            path = stage / 'examples' / f'{case}-plan.json'
            spec = json.loads(path.read_text(encoding='utf-8'))
            for anchor in spec['evidence']:
                if anchor['source_ref'] != 'SOURCE_REF':
                    raise ValueError('unexpected example source placeholder')
                anchor['source_ref'] = source_refs[case]
            modeling_plan.validate(store, spec, [source_refs[case]])
            path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            hashes[str(path.relative_to(stage))] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest = dict(version=1, profile='external_agent_ode_workspace_v1',
            created_at_utc=datetime.now(timezone.utc).isoformat(), python_executable=sys.executable,
            project_source_sha256=application.environment_fingerprint()['modules_sha256'],
            example_source_refs=source_refs, file_sha256=hashes, environment=environment,
            scientific_runs=0, provider_calls=0,
            note='Synthetic development controls. No host compatibility or scientific performance is certified.')
        (stage / 'workspace.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        # destination was checked before preparation; reject concurrent creation.
        if destination.exists() or destination.is_symlink():
            raise ValueError('workspace destination appeared during preparation')
        stage.rename(destination)
        return dict(workspace=str(destination), manifest=str(destination / 'workspace.json'),
                    example_source_refs=source_refs, scientific_runs=0, provider_calls=0)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def command(args):
    try:
        result = doctor() if args.command == 'workspace-doctor' else initialize(args.output)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if result.get('ready', True) else 2
    except Exception as exc:
        print(f'error: {args.command} failed: {exc}', file=sys.stderr)
        return 1


def register(sub):
    p = sub.add_parser('workspace-doctor', help='check local dependencies; no network or model execution')
    p.set_defaults(func=command)
    p = sub.add_parser('workspace-init', help='create a new external-Agent workspace with explicit synthetic examples')
    p.add_argument('--output', required=True); p.set_defaults(func=command)
