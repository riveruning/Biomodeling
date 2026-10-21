"""Offline native-system interfaces, not model performance tests."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def system():
    spec = importlib.util.spec_from_file_location('system_comparison_test',
        ROOT/'benchmarks/development/system_comparison.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plan_has_actual_systems_not_workflow_aliases(system):
    m = system.development_manifest()
    assert [s['runtime'] for s in m['systems']] == ['biomodel_agent', 'codex_cli', 'codex_cli']
    assert len(system.schedule(m)) == 30
    assert system.validate_manifest(m)['real_papers'] == 0
    assert m['evidence_kind'] == 'scripted_control'


def test_current_phase_bundle_contains_no_private_or_future_material(system, tmp_path):
    m = system.development_manifest()
    t = next(t for t in system.schedule(m) if t['system_id'] == 'codex-sol' and
             t['case_id'] == 'translation' and t['phase'] == 'initial')
    prepared = system.prepare_task(m, t['id'], tmp_path/'task')
    runtime = tmp_path/'task'/'runtime'
    assert {p.name for p in runtime.iterdir()} == {'source.txt', 'experiment.json', 'TASK.md'}
    source = (runtime/'source.txt').read_text()
    assert 'Researcher clarification' not in source
    assert 'private_rubric' not in (runtime/'TASK.md').read_text()
    assert prepared['status'] == 'prepared_only_no_authorization'
