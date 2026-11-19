"""Diagnostic scope and shared request cap, entirely offline."""
import importlib
import json
from pathlib import Path

import httpx
import pytest


@pytest.fixture
def diagnostic(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]/'benchmarks/development'))
    return importlib.import_module('diagnose_native_plan')


def test_timeout_diagnostic_preserves_material_and_other_settings(tmp_path, diagnostic):
    parent=tmp_path/'parent';original=diagnostic.pilot.prepare(parent)
    output=tmp_path/'diagnostic';m=diagnostic.prepare(output,parent,['production-loss'])
    assert diagnostic.checked(output)==m
    assert m['request_timeout_s']==90 and original['request_timeout_s']==45
    assert m['max_calls_per_case']==original['max_calls_per_case']
    assert (parent/'production-loss.txt').read_bytes()==(output/'production-loss.txt').read_bytes()
    m['max_tokens']=9999;diagnostic.pilot.save(output/'manifest.json',m)
    (output/'manifest.sha256').write_text(diagnostic.pilot.digest(output/'manifest.json'))
    with pytest.raises(ValueError,match='more than timeout'):diagnostic.checked(output)


def test_reserved_and_failed_attempts_consume_shared_cap(tmp_path, diagnostic, monkeypatch):
    monkeypatch.setenv(diagnostic.pilot.KEY_ENV,'synthetic')
    parent=tmp_path/'parent';m=diagnostic.pilot.prepare(parent)
    old=tmp_path/'old'/'case'/'provider-calls';old.mkdir(parents=True)
    for i in range(17):(old/f'{i}.json').write_text(json.dumps({'state':'reserved' if i%2 else 'failed'}))
    (old/'envelope.json').write_text('{}')
    sent=[]
    def response(request):
        sent.append(request)
        return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':'{"action":"finish","args":{}}'},'finish_reason':'stop'}]})
    (tmp_path/'new'/'case').mkdir(parents=True)
    provider=diagnostic.BudgetedProvider(tmp_path/'new'/'case'/'provider-calls',m,tmp_path,transport=httpx.MockTransport(response))
    assert provider.respond([{'role':'user','content':'synthetic'}])['action']=='finish'
    assert len(diagnostic.attempts(tmp_path))==18
    with pytest.raises(diagnostic.ProviderError,match='budget exhausted'):
        provider.respond([{'role':'user','content':'synthetic'}])
    assert len(sent)==1 and len(diagnostic.attempts(tmp_path))==18


def test_compact_prompt_is_frozen_and_actual_requests_are_audited(tmp_path, diagnostic, monkeypatch):
    monkeypatch.setenv(diagnostic.pilot.KEY_ENV,'synthetic')
    parent=tmp_path/'parent';diagnostic.pilot.prepare(parent)
    prompt=tmp_path/'prompt.txt';prompt.write_text('Experimental compact system instructions.')
    output=tmp_path/'compact';m=diagnostic.prepare(output,parent,['production-loss'],prompt)
    assert diagnostic.checked(output)==m
    original=[{'role':'system','content':'Original system instructions.'},{'role':'user','content':'Same task.'}]
    sent=[]
    def response(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':'{"action":"finish","args":{}}'},'finish_reason':'stop'}]})
    case=output/'case';case.mkdir()
    provider=diagnostic.BudgetedProvider(case/'provider-calls',m,tmp_path,transport=httpx.MockTransport(response))
    provider.respond(original)
    audit=json.loads((case/'provider-calls/01.json').read_text())
    assert sent[0]['messages']==audit['messages']
    assert audit['messages'][0]['content']==prompt.read_text()
    assert audit['messages'][1]==original[1] and original[0]['content']=='Original system instructions.'
    (output/'experimental-system.txt').write_text('changed')
    with pytest.raises(ValueError,match='system prompt changed'):diagnostic.checked(output)


def test_diagnostic_cannot_move_to_separate_budget_directory(tmp_path, diagnostic):
    parent=tmp_path/'parent';diagnostic.pilot.prepare(parent)
    other=tmp_path/'other';other.mkdir()
    with pytest.raises(ValueError,match='share'):
        diagnostic.prepare(other/'diagnostic',parent,['production-loss'])
