"""No partial streamed JSON may become an Agent action."""
import importlib
import json
from pathlib import Path

import httpx
import pytest


@pytest.mark.parametrize('mode', ['complete','missing_done','truncated','native_tool','low'])
def test_streamed_action_requires_normal_complete_termination(tmp_path, monkeypatch, mode):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]/'benchmarks/development'))
    module=importlib.import_module('stream_native_plan_diagnostic')
    monkeypatch.setenv(module.diag.pilot.KEY_ENV,'synthetic')
    m=module.diag.pilot.prepare(tmp_path/'prepared')
    if mode=='low':m['experimental_reasoning_effort']='low'
    def event(delta,finish=None):return {'choices':[{'index':0,'delta':delta,'finish_reason':finish}]}
    chunks=[event({'role':'assistant','reasoning_content':'Checking the request.'}),
        event({'content':'{"action":"inspect_'}),event({'content':'progress","args":{}}'}),
        event({},'length' if mode=='truncated' else 'stop'),
        {'choices':[],'usage':{'prompt_tokens':10,'completion_tokens':15,'total_tokens':25}}]
    if mode=='native_tool':chunks[1]=event({'tool_calls':[{'id':'unexpected'}]})
    wire=''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)
    if mode!='missing_done':wire+='data: [DONE]\n\n'
    def response(request):
        body=json.loads(request.content)
        assert body['stream'] and body['stream_options']=={'include_usage':True}
        assert body.get('reasoning_effort')==('low' if mode=='low' else None)
        return httpx.Response(200,headers={'content-type':'text/event-stream'},text=wire)
    case=tmp_path/'diagnostic'/'case';case.mkdir(parents=True)
    provider=module.StreamingProvider(case/'provider-calls',m,tmp_path,transport=httpx.MockTransport(response))
    messages=[{'role':'system','content':'Synthetic.'},{'role':'user','content':'Synthetic.'}]
    accepted=mode in ('complete','low')
    if accepted:
        assert provider.respond(messages)['action']=='inspect_progress'
        assert provider.last_usage['total_tokens']==25
    else:
        with pytest.raises(module.ProviderError):provider.respond(messages)
    audit=json.loads((case/'provider-calls/01.json').read_text())
    assert audit['state']==('returned' if accepted else 'failed')
    assert ('response' in audit)==accepted
    assert len(module.diag.attempts(tmp_path))==1
