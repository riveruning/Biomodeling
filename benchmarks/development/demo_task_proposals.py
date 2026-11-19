"""Public CLI development controls; scripted proposals, one real Boolean run.

Seen synthetic inputs only. No HTTP calls, independent evaluation or domain review.
"""
import argparse
from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
from unittest.mock import patch

from biomodel_agent import cli,evidence,recovery,research_cli,task_proposals
from biomodel_agent.storage import ArtifactStore

TEXT='Initial A = False.\nInitial B = False.\nRule A = not B.\nRule B = A.\nUpdate = synchronous.\nTime unit = sweep.\nClamps = none.\n'


class Scripted:
    model='scripted-proposal-development-control'
    base_url='https://invalid.example/v1'
    timeout_s=60
    max_tokens=4000
    json_mode=False
    last_usage={}
    def __init__(self,actions):self.actions=iter(actions);self.calls=0
    def respond(self,messages):self.calls+=1;return deepcopy(next(self.actions))


def action(name,**args):return dict(action=name,args=args)


def run(output):
    output=Path(output).absolute();output.mkdir(exist_ok=False)
    store=ArtifactStore(output/'store');pending=[];providers=[];commands=[]
    def factory(*args,**kwargs):
        provider=pending.pop(0);providers.append(provider);return provider
    def file(name,value):
        path=output/name;path.write_text(json.dumps(value,ensure_ascii=False,indent=2));return path
    def call(name,args=(),expected=0,json_result=True):
        stdout,stderr=io.StringIO(),io.StringIO()
        argv=[name,*map(str,args),'--store',str(store.root)]
        with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):code=cli.main(argv)
        index=len(commands)+1
        (output/f'{index:02d}-{name}.stdout').write_text(stdout.getvalue())
        (output/f'{index:02d}-{name}.stderr').write_text(stderr.getvalue())
        commands.append(dict(argv=argv,return_code=code,expected=expected))
        (output/'commands.json').write_text(json.dumps(commands,indent=2))
        if code!=expected:raise RuntimeError(name+': '+stderr.getvalue())
        return json.loads(stdout.getvalue()) if json_result else stdout.getvalue()
    def source(name,text):
        path=output/name;path.write_text(text);return evidence.ingest_source(store,path)
    def boolean_spec(ref):
        anchors=[dict(id=f'E{i}',kind='source',source_ref=ref,page=1,quote=line) for i,line in enumerate(TEXT.splitlines())]
        return dict(schema_version=1,summary='A literal two-node Boolean draft with source declarations.',evidence=anchors,
            candidates=[dict(id='direct',model_type='boolean',rationale='Explicit discrete rules and initial states are supplied.',
                evidence_ids=[a['id'] for a in anchors],unknowns=[],boolean_draft=dict(schema_version=1,model_type='boolean',
                    id='literal_network',units={'time':'sweep'},nodes=[dict(id='A',initial=False,rule='not B'),dict(id='B',initial=False,rule='A')],
                    update='synchronous',clamps={}),evidence_map=dict(zip(
                        ['A/initial','B/initial','A/rule','B/rule','update','time_unit','clamps'],[[a['id']] for a in anchors])))],
            recommendation='direct',questions=[])
    def intake(name,goal,ref):
        return call('research-task-create',['--config',file(name,dict(schema_version=1,goal=goal,source_refs=[ref],
            proposal_max_calls=1,max_calls=3,execution={'settings':{'steps':3,'seed':123,'timeout_s':15.}}))],2)['request_ref']
    with patch.object(research_cli,'_provider',factory):
        request=intake('boolean-intake.json','Execute the literal source-declared Boolean feedback network.',source('boolean.txt',TEXT))
        spec=boolean_spec(store.get(request)['payload']['spec']['source_refs'][0])
        pending.append(Scripted([action('propose_task',proposal=spec)]))
        proposal=call('research-task-propose',[request]);pref=proposal['proposal_ref']
        call('research-proposal-show',[pref])
        call('report',[pref,'--output',output/'proposal-report.md'],json_result=False)
        assert not store.list('typed_run')
        selected=call('research-proposal-select',[pref,'--candidate','direct','--reviewer','Scripted local development operator',
            '--reason','Select the exact source declarations for this synthetic development control.'])['request_ref']
        assert not store.list('typed_run')
        pending.append(Scripted([action('run_typed'),action('finish')]))
        result=call('research-task-run',[selected]);rref=result['result_ref']
        before=len(providers);assert call('research-task-run',[selected])['result_ref']==rref and len(providers)==before
        status=call('research-task-show',[selected])
        assert (status['proposal_call_count'],status['native_call_count'],status['total_call_count'])==(1,2,3)
        call('report',[rref,'--output',output/'result-report.md'],json_result=False)
        call('export',[rref,'--output',output/'result-bundle'],json_result=False)
        # Replay inspection through the detached bundle uses its own store argument.
        stdout=io.StringIO()
        with contextlib.redirect_stdout(stdout):
            assert cli.main(['report',rref,'--output',str(output/'detached-report.md'),'--store',str(output/'result-bundle')])==0
        assert (output/'result-report.md').read_bytes()==(output/'detached-report.md').read_bytes()
        before_runs=len(store.list('typed_run'))
        conflicting=intake('conflict-intake.json','Review the conflicting declarations before using any rule.',source('conflict.txt',TEXT+'Rule A = B.\n'))
        spec=boolean_spec(store.get(conflicting)['payload']['spec']['source_refs'][0])
        pending.append(Scripted([action('propose_task',proposal=spec)]))
        blocked=call('research-task-propose',[conflicting]);assert not blocked['candidates'][0]['draft_adoptable']
        call('research-proposal-select',[blocked['proposal_ref'],'--candidate','direct','--reviewer','Development operator',
            '--reason','Deliberate conflicting-rule negative control.'],1,json_result=False)
        assert len(store.list('typed_run'))==before_runs
        missing_source=source('missing.txt','Molecule conversion is described, but reaction counts and rate constants are absent.\n')
        missing=intake('missing-intake.json','Could a stochastic reaction model describe this conversion?',missing_source)
        unknowns=['Reaction stoichiometry, initial integer counts and rate constants are not supplied.']
        candidate=dict(id='ssa',model_type='stochastic',rationale='A count-based reaction model is only a possible method pending explicit inputs.',
            evidence_ids=['G'],unknowns=unknowns)
        pending.append(Scripted([action('propose_task',proposal=dict(schema_version=1,summary='A stochastic candidate still needs mathematical inputs.',
            evidence=[dict(id='G',kind='goal',quote='Could a stochastic reaction model describe this conversion?')],
            candidates=[candidate],recommendation=None,questions=['Which reactions, initial integer counts and rate constants apply?']))]))
        ambiguous=call('research-task-propose',[missing],2)
        assert ambiguous['state']=='awaiting_input' and ambiguous['candidates'][0]['missing_inputs']==['model_ref']
        awaiting=call('research-proposal-select',[ambiguous['proposal_ref'],'--candidate','ssa','--reviewer','Development operator',
            '--reason','Keep missing inputs explicit.','--acknowledged-unknowns-file',file('unknowns.json',unknowns)],2)
        assert awaiting['state']=='awaiting_input' and len(store.list('typed_run'))==before_runs
        assert not pending and sum(p.calls for p in providers)==5
    from biomodel_agent import research_tasks
    summary=dict(implementation_sha256=recovery.implementation_fingerprint(),live_provider_calls=0,
        scripted_provider_calls=sum(p.calls for p in providers),proposal_calls=3,native_agent_calls=2,scientific_runs=1,
        complete=dict(request_ref=selected,result_ref=rref,state=result['state'],proposal_call_count=1,native_call_count=2,total_call_count=3),
        conflict=dict(proposal_ref=blocked['proposal_ref'],draft_adoptable=False,selection_rejected=True,scientific_runs=0),
        missing=dict(request_ref=awaiting['request_ref'],state='awaiting_input',scientific_runs=0),
        total_proposal_attempts=len(research_tasks._records(store,task_proposals.ATTEMPT)),
        detached_report_matches=True,scientific_support='unknown',inputs='seen synthetic development controls',domain_review=False)
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    print(json.dumps(run(parser.parse_args().output),ensure_ascii=False,indent=2))
