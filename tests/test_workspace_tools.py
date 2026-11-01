"""An external CLI consumer can complete or stop the generated examples."""
import json
from pathlib import Path

import pytest

from biomodel_agent import cli, modeling_plan as plans, modeling_workflow as workflow, workspace_tools
from biomodel_agent.storage import ArtifactStore


def invoke(capsys, argv, expected=0):
    code = cli.main(argv)
    captured = capsys.readouterr()
    assert code == expected, captured.err + captured.out
    return json.loads(captured.out) if captured.out else None


def initialized(tmp_path):
    root = tmp_path / 'workspace'
    result = workspace_tools.initialize(root)
    manifest = json.loads((root / 'workspace.json').read_text())
    return root, result, manifest


def test_init_is_preparation_only_and_seeds_exact_source_references(tmp_path):
    root, result, manifest = initialized(tmp_path)
    assert result['scientific_runs'] == 0 and result['provider_calls'] == 0
    assert (root / '.agents/skills/biomodel-ode/SKILL.md').is_file()
    assert (root / 'AGENTS.md').is_file()
    store = ArtifactStore(root / 'store')
    assert len(store.list('source')) == 3 and not store.list('run')
    for case, source in manifest['example_source_refs'].items():
        spec = json.loads((root / f'examples/{case}-plan.json').read_text())
        assert all(e['source_ref'] == source for e in spec['evidence'])
        plans.validate(store, spec, [source])
    assert not list(tmp_path.glob('.biomodel-workspace-*'))


def test_complete_example_through_only_public_cli_then_report_export_and_replay(tmp_path, capsys):
    root, _, manifest = initialized(tmp_path)
    store_path = str(root / 'store'); source = manifest['example_source_refs']['complete']
    base = ['--store', store_path]
    prepared = invoke(capsys, ['model-plan-prepare', '--config', str(root/'examples/complete-plan.json'), '--source-ref', source, *base])
    plan_ref = prepared['plan_ref']
    compiled = invoke(capsys, ['model-plan-compile', plan_ref, *base])
    task = invoke(capsys, ['model-plan-execution-prepare', compiled['compilation_ref'], '--config', str(root/'examples/execution.json'), *base])
    result = invoke(capsys, ['model-plan-execute', task['task_ref'], *base])
    assert result['state'] == 'completed' and result['scientific_support'] == 'unknown'
    again = invoke(capsys, ['model-plan-execute', task['task_ref'], *base])
    assert again['run_ref'] == result['run_ref']
    view = invoke(capsys, ['agent-timeline', result['run_ref'], '--format', 'json', *base])
    assert view['call_count'] == 0 and view['run_count'] == 1
    report = root / 'report.md'
    assert cli.main(['report', result['run_ref'], '--output', str(report), *base]) == 0
    capsys.readouterr()
    text = report.read_text()
    assert 'Actual application report' in text and 'rule:x/expression' in text and 'unknown' in text
    bundle = root / 'bundle'
    assert cli.main(['export', result['run_ref'], '--output', str(bundle), *base]) == 0
    capsys.readouterr()
    assert 'model-plan-replay' in (bundle/'REPLAY.md').read_text()
    replayed = invoke(capsys, ['model-plan-replay', result['run_ref'], '--store', str(bundle)])
    assert replayed['run_ref'] != result['run_ref'] and replayed['state'] == 'completed'


@pytest.mark.parametrize('case,state', [('ambiguous', 'awaiting_selection'), ('missing', 'awaiting_input')])
def test_blocked_examples_return_saved_gaps_and_never_compute(tmp_path, capsys, case, state):
    root, _, manifest = initialized(tmp_path)
    store_path = str(root/'store')
    result = invoke(capsys, ['model-plan-prepare', '--config', str(root/f'examples/{case}-plan.json'),
        '--source-ref', manifest['example_source_refs'][case], '--store', store_path], expected=2)
    assert result['state'] == state and result['issues'][0]['question']
    compiled = invoke(capsys, ['model-plan-compile', result['plan_ref'], '--store', store_path], expected=2)
    assert compiled['model_ref'] is None
    store = ArtifactStore(store_path)
    assert not store.list('run') and not store.list(workflow.EXECUTION)


def test_existing_destination_and_file_are_preserved(tmp_path):
    folder = tmp_path/'existing'; folder.mkdir(); (folder/'keep.txt').write_text('keep')
    with pytest.raises(ValueError, match='new path'): workspace_tools.initialize(folder)
    assert (folder/'keep.txt').read_text() == 'keep'
    file = tmp_path/'file'; file.write_text('keep')
    with pytest.raises(ValueError): workspace_tools.initialize(file)
    assert file.read_text() == 'keep'


def test_missing_environment_does_not_leave_partial_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_tools, 'doctor', lambda: {'ready': False})
    with pytest.raises(ValueError, match='incomplete'): workspace_tools.initialize(tmp_path/'new')
    assert not (tmp_path/'new').exists() and not list(tmp_path.glob('.biomodel-workspace-*'))


def test_failed_asset_copy_cleans_only_its_own_stage(tmp_path, monkeypatch):
    def fail(*a, **k): raise RuntimeError('copy interrupted')
    monkeypatch.setattr(workspace_tools, '_copy_assets', fail)
    with pytest.raises(RuntimeError): workspace_tools.initialize(tmp_path/'new')
    assert not (tmp_path/'new').exists() and not list(tmp_path.glob('.biomodel-workspace-*'))


def test_readonly_cli_does_not_create_missing_store(tmp_path, capsys):
    missing = tmp_path/'missing-store'
    assert cli.main(['model-plan-show', 'a'*64, '--store', str(missing)]) == 1
    assert not missing.exists()
    capsys.readouterr()


def test_duplicate_and_nonfinite_json_rejected(tmp_path):
    from biomodel_agent.modeling_cli import read_json
    path = tmp_path/'bad.json'
    for text in ['{"mode":"reproduction","mode":"exploration"}', '{"a":{"x":1,"x":2}}', '{"a":NaN}', '{"a":Infinity}']:
        path.write_text(text)
        with pytest.raises(ValueError): read_json(path)


def test_doctor_reports_readiness_without_provider_calls(capsys):
    result = invoke(capsys, ['workspace-doctor'])
    assert result['ready'] and result['provider_connectivity'] == 'not_tested'
    assert result['scientific_execution'] == 'not_run' and not result['provider_required']


def test_timeline_output_preserves_existing_file(tmp_path, capsys):
    store = ArtifactStore(tmp_path/'store'); ref = store.put('typed_agent_run', dict(state='incomplete', events=[]))
    output = tmp_path/'timeline.md'; output.write_text('previous result')
    assert cli.main(['agent-timeline', ref, '--store', str(store.root), '--output', str(output)]) == 1
    assert output.read_text() == 'previous result'
    capsys.readouterr()
