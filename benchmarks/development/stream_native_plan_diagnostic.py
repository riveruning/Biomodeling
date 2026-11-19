"""Experimental streaming at the same authorized route and shared request cap.

Partial output is diagnostic data only. No Agent action returns before a normal
finish and explicit stream terminator. Original product transport is unchanged.
"""
import argparse
import json
from pathlib import Path
import time

import httpx
import diagnose_native_plan as diag
from biomodel_agent.llm import ProviderError


class StreamingProvider(diag.BudgetedProvider):
    def __init__(self,directory,manifest,budget_root,transport=None):
        super().__init__(directory,manifest,budget_root,transport)
        self.reasoning_effort=manifest.get('experimental_reasoning_effort')
        if self.reasoning_effort not in (None,'low'):
            raise ValueError('only the explicitly selected low-effort variant is supported')

    def _post(self, url, payload, headers):
        started=time.monotonic()
        meta=dict(mode='experimental_stream',events=0,content_chars=0,reasoning_chars=0,
                  first_event_s=None,first_content_s=None,finish_reason=None,done=False,usage=None)
        content=[]
        body={**payload,'stream':True,'stream_options':{'include_usage':True}}
        if self.reasoning_effort is not None:
            body['reasoning_effort']=self.reasoning_effort
            meta['requested_reasoning_effort']=self.reasoning_effort
        try:
            with httpx.Client(trust_env=False,follow_redirects=False,transport=self._transport,
                timeout=httpx.Timeout(45,connect=15,write=15,pool=15)) as client:
                with client.stream('POST',url,json=body,headers=headers) as response:
                    meta['http_status']=response.status_code
                    if not 200<=response.status_code<300:
                        return response.status_code,'{}'
                    byte_count=0
                    for line in response.iter_lines():
                        if time.monotonic()-started>180:
                            raise ProviderError('stream exceeded 180-second event-check deadline',kind='transport')
                        byte_count+=len(line.encode())
                        if byte_count>2_000_000:
                            raise ProviderError('stream exceeded response byte cap',kind='response_envelope')
                        if not line.startswith('data:'):continue
                        data=line[5:].strip()
                        if data=='[DONE]':
                            meta['done']=True;break
                        meta['events']+=1
                        if meta['first_event_s'] is None:meta['first_event_s']=time.monotonic()-started
                        try:
                            event=json.loads(data)
                            if event.get('usage') is not None:
                                meta['usage']=event['usage'];self.last_usage=self._usage(event)
                            choices=event.get('choices',[])
                            if not isinstance(choices,list) or len(choices)>1:raise ValueError('choices')
                            for choice in choices:
                                if type(choice.get('index')) is not int or choice['index']!=0:raise ValueError('choice index')
                                delta=choice.get('delta') or {}
                                if (not isinstance(delta,dict) or delta.get('tool_calls') or delta.get('function_call')
                                        or delta.get('refusal') or delta.get('role') not in (None,'assistant')):
                                    raise ValueError('unsupported streamed message')
                                fragment=delta.get('content')
                                if fragment is not None:
                                    if not isinstance(fragment,str):raise ValueError('content')
                                    if fragment and meta['first_content_s'] is None:meta['first_content_s']=time.monotonic()-started
                                    content.append(fragment);meta['content_chars']+=len(fragment)
                                reasoning=delta.get('reasoning_content')
                                if reasoning is not None:
                                    if not isinstance(reasoning,str):raise ValueError('reasoning')
                                    meta['reasoning_chars']+=len(reasoning)
                                if choice.get('finish_reason') is not None:
                                    meta['finish_reason']=choice['finish_reason']
                        except (ValueError,TypeError,AttributeError):
                            raise ProviderError('invalid streamed envelope',kind='response_envelope') from None
                    if not meta['done'] or meta['finish_reason']!='stop':
                        raise ProviderError('stream did not terminate normally',kind='response_envelope')
                    return response.status_code,json.dumps({'choices':[{'index':0,'finish_reason':'stop',
                        'message':{'role':'assistant','content':''.join(content)}}],'usage':meta['usage']})
        except httpx.HTTPError as exc:
            raise ProviderError('stream transport failure: '+type(exc).__name__,kind='transport') from None
        finally:
            meta['elapsed_s']=time.monotonic()-started
            diag.pilot.save(self.directory/f'{len(self.receipts)+1:02d}-stream.json',meta)


def prepare(output,parent,prompt,cases,reasoning_effort=None):
    m=diag.prepare(output,parent,cases,prompt)
    m.update(experimental_transport='chat_completions_stream',stream_read_timeout_s=45,
        stream_event_check_deadline_s=180,stream_max_response_bytes=2_000_000,
        stream_controller_sha256=diag.pilot.digest(Path(__file__)))
    if reasoning_effort is not None:
        if reasoning_effort!='low':raise ValueError('unsupported reasoning effort')
        m['experimental_reasoning_effort']='low'
    output=Path(output)
    diag.pilot.save(output/'manifest.json',m)
    (output/'manifest.sha256').write_text(diag.pilot.digest(output/'manifest.json'))
    return m


def execute(output):
    m=diag.checked(output)
    if (m.get('stream_controller_sha256')!=diag.pilot.digest(Path(__file__))
            or m.get('experimental_transport')!='chat_completions_stream'):
        raise ValueError('stream diagnostic identity changed')
    diag.BudgetedProvider=StreamingProvider
    return diag.execute(output)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['prepare','execute']);p.add_argument('--output',required=True)
    p.add_argument('--parent');p.add_argument('--system-prompt')
    p.add_argument('--cases',nargs='+',choices=diag.pilot.CASES,default=['production-loss'])
    p.add_argument('--reasoning-effort',choices=['low'])
    args=p.parse_args()
    result=prepare(args.output,args.parent,args.system_prompt,args.cases,args.reasoning_effort) if args.mode=='prepare' else execute(args.output)
    print(json.dumps(result,ensure_ascii=False,indent=2))
