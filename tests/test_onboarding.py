"""First-run behavior: real simulation, literal credentials, local-only checks."""
import json
import os

import httpx
import pytest

from biomodel_agent import cli, llm
from biomodel_agent.storage import ArtifactStore

HTTP_POST = httpx.Client.post


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for key in ('BIOMODEL_BASE_URL', 'BIOMODEL_MODEL', 'BIOMODEL_API_KEY', 'CUSTOM_KEY'):
        monkeypatch.delenv(key, raising=False)
    # Any accidental network access would fail the test, even with dummy keys.
    monkeypatch.setattr(httpx.Client, 'post', lambda *a, **kw: pytest.fail('unexpected HTTP call'))


def test_offline_demo_has_an_independent_reference_and_replays(tmp_path, capsys):
    assert cli.main(['demo', '--store', str(tmp_path/'run')]) == 0
    result = json.loads(capsys.readouterr().out)
    run = ArtifactStore(tmp_path/'run').get(result['ref'])['payload']
    assert run['simulation']['values']['X'][-1] == pytest.approx(0.1353352832, abs=1e-5)
    assert run['comparison']['status'] == 'pass'
    assert (tmp_path/'run/report.md').is_file()
    assert cli.main(['replay', result['ref'], '--store', str(tmp_path/'run')]) == 0


def test_doctor_offline_needs_no_llm_config(capsys):
    assert cli.main(['doctor']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['ok'] and result['network_checked'] is False
    assert not any(check['name'].startswith('llm_') for check in result['checks'])


def test_doctor_missing_settings_returns_failure(capsys):
    assert cli.main(['doctor', '--llm']) == 1
    result = json.loads(capsys.readouterr().out)
    assert {c['name'] for c in result['checks'] if not c['ok']} == {
        'llm_base_url', 'llm_model', 'llm_api_key'}


def test_default_dotenv_is_literal_does_not_leak_and_environment_wins(monkeypatch, tmp_path, capsys):
    (tmp_path/'.env').write_text('BIOMODEL_BASE_URL=https://example.org/v1\n'
        'BIOMODEL_MODEL=file-model\nBIOMODEL_API_KEY="secret-${CUSTOM_KEY}-value"\n')
    monkeypatch.setenv('BIOMODEL_MODEL', 'existing-model')
    monkeypatch.setenv('CUSTOM_KEY', 'do-not-expand')
    assert cli.main(['doctor', '--llm']) == 0
    assert os.environ['BIOMODEL_MODEL'] == 'existing-model'
    assert os.environ['BIOMODEL_API_KEY'] == 'secret-${CUSTOM_KEY}-value'
    output = capsys.readouterr().out
    assert 'secret-' not in output and 'existing-model' not in output
    assert json.loads(output)['network_checked'] is False


@pytest.mark.parametrize('url', ['http://public.example/v1', 'http://['])
def test_doctor_invalid_provider_is_a_sanitized_failure(monkeypatch, capsys, url):
    monkeypatch.setenv('BIOMODEL_BASE_URL', url)
    monkeypatch.setenv('BIOMODEL_MODEL', 'demo-model')
    monkeypatch.setenv('BIOMODEL_API_KEY', 'secret-should-not-be-printed')
    assert cli.main(['doctor', '--llm']) == 1
    output = capsys.readouterr().out
    assert 'secret-should-not-be-printed' not in output
    assert any(c['name'] == 'llm_provider_config' and not c['ok'] for c in json.loads(output)['checks'])


def test_template_and_missing_explicit_file_are_reported(tmp_path, capsys):
    env = tmp_path/'custom.env'
    env.write_text('BIOMODEL_BASE_URL=https://YOUR_PROVIDER/v1\n'
                   'BIOMODEL_MODEL=YOUR_MODEL\nBIOMODEL_API_KEY=YOUR_API_KEY\n')
    assert cli.main(['--env-file', str(env), 'doctor', '--llm']) == 1
    result = json.loads(capsys.readouterr().out)
    assert all(c['error'] == 'replace_template_value' for c in result['checks'] if not c['ok'])
    assert cli.main(['--env-file', str(tmp_path/'missing'), 'doctor']) == 1
    assert 'does not exist' in capsys.readouterr().err


def test_env_file_drives_real_agent_adapter_without_a_paid_request(monkeypatch, tmp_path, capsys):
    env = tmp_path/'connection.env'
    env.write_text('BIOMODEL_BASE_URL=https://example.org/v1\nBIOMODEL_MODEL=test-model\n'
                   'CUSTOM_KEY=local-test-key\n')
    source = tmp_path/'source.txt'
    source.write_text('The initial value is missing.')
    experiment = tmp_path/'experiment.json'
    experiment.write_text('{"times": [0.0, 1.0]}')
    seen = []

    def respond(request):
        seen.append(json.loads(request.content))
        assert request.headers['Authorization'] == 'Bearer local-test-key'
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({
            'action': 'ask_researcher', 'args': {'question': 'What is the initial value?'}})}}]})

    provider_class = llm.ChatProvider
    # MockTransport replaces HTTP at the transport boundary, not the Agent loop.
    monkeypatch.setattr(httpx.Client, 'post', HTTP_POST)
    monkeypatch.setattr(llm, 'ChatProvider', lambda **kw: provider_class(**kw, transport=httpx.MockTransport(respond)))
    code = cli.main(['--env-file', str(env), 'agent', '--source', str(source),
        '--experiment', str(experiment), '--store', str(tmp_path/'store'), '--api-key-env', 'CUSTOM_KEY'])
    assert code == 2
    assert seen[0]['model'] == 'test-model'
    result = json.loads(capsys.readouterr().out)
    assert result['state'] == 'awaiting_input'
    assert 'local-test-key' not in json.dumps(result)
