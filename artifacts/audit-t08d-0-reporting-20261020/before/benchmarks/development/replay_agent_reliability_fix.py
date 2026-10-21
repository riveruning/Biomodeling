"""No-network replay of exact recorded provider outputs against final product code."""
import json
import re
from pathlib import Path

from biomodel_agent import agent, application, evidence
from biomodel_agent.domain.contracts import ExperimentSpec
from biomodel_agent.llm import ChatProvider, ProviderError
from biomodel_agent.storage import ArtifactStore
from competitor_common import write_json
from prepare_reliability import ROOT

BASE=ROOT/'artifacts/agent-reliability-fix-20261017/live-regression'
OUT=ROOT/'artifacts/agent-reliability-fix-20261017/final-code-replay'


class RecordedProvider(ChatProvider):
    def __init__(self,folder,payload):
        super().__init__('https://offline.invalid/v1','recorded-no-network')
        self.folder=folder;self.index=0
        self.steps=iter(e for e in payload['events'] if e['type'] in ['llm_call','provider_error'])

    def _headers(self):return {}  # No credentials are required or read.

    def _post(self,url,payload,headers):
        step=next(self.steps)
        if step['type']=='provider_error':
            kind=step.get('kind')
            if kind=='transport':raise ProviderError(step['error'],kind=kind,retryable=step.get('retryable',False))
            if kind=='http':
                status=int(re.search(r'HTTP (\d+)',step['error']).group(1))
                return status,''
            if kind=='response_envelope' and 'non-JSON body' in step['error']:return 200,'invalid envelope'
        self.index+=1
        data=json.loads((self.folder/f'observed-response-{self.index}.json').read_text())
        return 200,json.dumps(data,ensure_ascii=False)


def main():
    if OUT.exists():raise FileExistsError('Preserve prior replay')
    protocol=json.loads((BASE/'protocol.json').read_text());rows=[]
    assert len(list((BASE/'biomodel').glob('*/*/result.json')))==18
    OUT.mkdir()
    for name,case in protocol['cases'].items():
        for phase,config in case['phases'].items():
            folder=BASE/'biomodel'/name/phase
            previous=json.loads((folder/'agent.json').read_text())
            dest=OUT/'biomodel'/name/phase;store=ArtifactStore(dest/'store')
            source=evidence.ingest_source(store,ROOT/config['source'])
            provider=RecordedProvider(folder,previous)
            result=store.get(agent.run_agent(store,provider,[source],ExperimentSpec(tuple(protocol['times'])),max_calls=8,max_repairs=2))['payload']
            write_json(dest/'agent.json',result)
            assert result['state']==previous['state'],(name,phase,previous['state'],result['state'])
            assert result['call_count']==previous['call_count'],(name,phase,'call counts changed')
            if result['run_ref']:
                run=store.get(result['run_ref'])['payload'];write_json(dest/'trajectory.json',run['simulation'])
                from score_reliability import compare
                reference=json.loads((BASE/'references'/f'{name}.json').read_text())
                numeric=compare(run['simulation'],reference,case['scales'])
                assert config['expected_execution'] and numeric['status']=='pass'
            else:numeric={'status':'not_run'}
            rows.append({'case':name,'phase':phase,'state':result['state'],'calls':result['call_count'],'numeric':numeric})
    report={'rows':rows,'network_calls':0,'credentials_read':False,
            'source':'exact stored provider response bodies; no model output corrections',
            'scope':'deterministic behavior replay, not fresh LLM trials',
            'environment':application.environment_fingerprint()}
    write_json(OUT/'results.json',report)
    executed=sum(r['numeric']['status']=='pass' for r in rows)
    print(f'18/18 final-code replays match live states and call counts; {executed} executable trajectories pass; no network calls.')

if __name__=='__main__':main()
