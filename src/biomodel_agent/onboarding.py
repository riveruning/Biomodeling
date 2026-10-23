"""Local installation checks and a portable, synthetic first run."""
from __future__ import annotations

import importlib
from importlib.metadata import version
import math
import os
from pathlib import Path
import sys


def check_environment(args) -> dict:
    checks = [{'name': 'python', 'ok': sys.version_info >= (3, 12),
               'version': '.'.join(map(str, sys.version_info[:3]))}]
    for distribution, module in (
        ('numpy', 'numpy'), ('scipy', 'scipy'), ('python-libsbml', 'libsbml'),
        ('libroadrunner', 'roadrunner'), ('pypdf', 'pypdf'), ('httpx', 'httpx'),
        ('python-dotenv', 'dotenv'),
    ):
        try:
            importlib.import_module(module)
            checks.append({'name': distribution, 'ok': True, 'version': version(distribution)})
        except Exception as exc:
            checks.append({'name': distribution, 'ok': False, 'error': type(exc).__name__})
    if args.llm:
        supported_lock = importlib.util.find_spec('fcntl') is not None and all(
            hasattr(os, flag) for flag in ('O_CLOEXEC', 'O_NOFOLLOW'))
        lock_check = {'name': 'agent_file_lock', 'ok': supported_lock}
        if not supported_lock:
            lock_check['error'] = 'Unix file locking required; use Linux or WSL2'
        checks.append(lock_check)
        settings = {
            'base_url': args.base_url or os.environ.get('BIOMODEL_BASE_URL'),
            'model': args.model or os.environ.get('BIOMODEL_MODEL'),
            'api_key': os.environ.get(args.api_key_env),
        }
        for name, value in settings.items():
            present = bool(value and value.strip())
            placeholder = bool(present and value.strip().upper().startswith('YOUR_'))
            placeholder = placeholder or (name == 'base_url' and 'YOUR_PROVIDER' in (value or '').upper())
            check = {'name': f'llm_{name}', 'ok': present and not placeholder}
            if not check['ok']:
                check['error'] = 'replace_template_value' if placeholder else 'missing_setting'
            checks.append(check)
        if all(check['ok'] for check in checks if check['name'].startswith('llm_')):
            from .llm import ChatProvider, ProviderError
            try:
                ChatProvider(settings['base_url'], settings['model'], args.api_key_env)
                checks.append({'name': 'llm_provider_config', 'ok': True})
            except (ProviderError, ValueError):
                checks.append({'name': 'llm_provider_config', 'ok': False,
                               'error': 'invalid_provider_config; use HTTPS or localhost HTTP'})
    return {'ok': all(check['ok'] for check in checks), 'network_checked': False, 'checks': checks}


def run_demo(directory: str) -> dict:
    from .application import run_reproduction
    from .domain.contracts import ExperimentSpec, ModelDraft, Parameter, RateRule, SimulationResult, State
    from .modeling import build_model
    from .reporting import render_report
    from .storage import ArtifactStore

    # Analytic reference is independent of the SBML compiler and ODE solver.
    draft = ModelDraft('synthetic_decay_demo', (State('X', 1.0),),
                       (Parameter('k', 0.2, origin='synthetic_example'),),
                       rate_rules=(RateRule('X', '-k*X'),))
    times = tuple(float(t) for t in range(11))
    experiment = ExperimentSpec(times)
    reference = SimulationResult('succeeded', times, {'X': tuple(math.exp(-0.2*t) for t in times)})
    store = ArtifactStore(directory)
    ref = run_reproduction(store, build_model(draft), experiment,
                           reference=reference, scales={'X': 1.0}, threshold=1e-5, use_cache=False)
    run = store.get(ref)['payload']
    report_path = Path(directory).resolve() / 'report.md'
    report_path.write_text(render_report(store, ref), encoding='utf-8')
    return {'ref': ref, 'execution': run['status'], 'comparison': run['comparison']['status'],
            'scientific_status': run['scientific_status'], 'report': str(report_path),
            'note': 'Synthetic installation check; no LLM call or paper reproduction.'}
