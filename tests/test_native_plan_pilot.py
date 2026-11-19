"""Offline audit controls for the bounded live-development runner."""
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

spec = importlib.util.spec_from_file_location('native_plan_pilot', Path(__file__).parents[1]/'benchmarks/development/native_plan_pilot.py')
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


def test_prepare_is_offline_and_changed_sources_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv(pilot.KEY_ENV, raising=False)
    monkeypatch.setattr(httpx.Client, 'post', lambda *a, **k: pytest.fail('offline preparation made a request'))
    out=tmp_path/'pilot';m=pilot.prepare(out)
    assert pilot.checked(out)==m
    assert not (out/'execution-started.json').exists()
    (out/m['cases'][0]['source']).write_text('changed')
    with pytest.raises(ValueError, match='snapshot'):pilot.checked(out)


def test_unknown_usage_not_summed_as_zero():
    result=pilot.usage_summary([{'usage':{'total_tokens':17}}, {'usage':None}])
    assert result['total_tokens']=={'known_subtotal':17,'missing_calls':1,'total':None}
    assert pilot.usage_summary([])['total_tokens']['total'] is None


@pytest.mark.parametrize('finish,accepted', [('stop',True),('length',False)])
def test_attempt_receipt_keeps_returned_usage_on_envelope_failure(tmp_path, monkeypatch, finish, accepted):
    monkeypatch.setenv(pilot.KEY_ENV,'synthetic-not-a-secret')
    m=pilot.prepare(tmp_path/'prepared')
    def response(request):
        assert request.url==pilot.BASE+'/chat/completions'
        body=json.loads(request.content)
        assert body['model']==m['model'] and body['temperature']==0 and body['max_tokens']==5000
        return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':'{"action":"finish","args":{}}'},
            'finish_reason':finish}], 'usage':{'prompt_tokens':11,'completion_tokens':6,'total_tokens':17}})
    provider=pilot.AuditedProvider(tmp_path/'receipts',m,transport=httpx.MockTransport(response))
    if accepted:
        assert provider.respond([{'role':'user','content':'synthetic'}])['action']=='finish'
    else:
        with pytest.raises(pilot.ProviderError):provider.respond([{'role':'user','content':'synthetic'}])
    receipt=json.loads((tmp_path/'receipts/01.json').read_text())
    assert receipt['state']==('returned' if accepted else 'failed')
    assert receipt['usage']['total_tokens']==17
    assert 'synthetic-not-a-secret' not in json.dumps(receipt)


def test_existing_execution_marker_blocks_implicit_rerun(tmp_path):
    out=tmp_path/'pilot';pilot.prepare(out)
    (out/'execution-started.json').write_text('{}')
    with pytest.raises(FileExistsError):pilot.execute(out)


def test_manifest_budget_edit_invalidates_preparation(tmp_path):
    out=tmp_path/'pilot';m=pilot.prepare(out)
    m['max_calls_per_case']=99;pilot.save(out/'manifest.json',m)
    with pytest.raises(ValueError,match='manifest changed'):pilot.checked(out)
