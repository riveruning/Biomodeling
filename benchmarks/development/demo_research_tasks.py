"""Six public-CLI lifecycle controls with scripted providers and real solvers.

Seen synthetic examples only. Not a live-model benchmark or scientific review.
"""
from copy import deepcopy
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
from unittest.mock import patch

from biomodel_agent import cli, fba, llm, typed_models, workspace_tools
from biomodel_agent.storage import ArtifactStore

ROOT=Path(__file__).resolve().parents[2]


def action(name,**args):return dict(action=name,args=args)


class Scripted:
    last_usage={}
    model='scripted-development-control'
    base_url='https://invalid.example/v1'
    timeout_s=60
    max_tokens=4000
    json_mode=False
    def __init__(self,actions):self.actions=iter(actions);self.calls=0
    def respond(self,messages):self.calls+=1;return deepcopy(next(self.actions))


def run(output):
    output=Path(output).absolute();output.mkdir(exist_ok=False)
    initialized=workspace_tools.initialize(output/'workspace')
    store=ArtifactStore(output/'workspace/store');commands=[];providers=[];pending=[]
    def factory(*args,**kwargs):
        provider=pending.pop(0);providers.append(provider);return provider
    def invoke(args,expected=0,parse=True):
        stream,error=io.StringIO(),io.StringIO()
        with contextlib.redirect_stdout(stream),contextlib.redirect_stderr(error):code=cli.main(list(map(str,args)))
        index=len(commands)+1
        (output/f'{index:02d}-{args[0]}.stdout').write_text(stream.getvalue())
        (output/f'{index:02d}-{args[0]}.stderr').write_text(error.getvalue())
        commands.append(dict(argv=list(map(str,args)),return_code=code,expected=expected))
        (output/'commands.json').write_text(json.dumps(commands,indent=2))
        if code!=expected:raise RuntimeError(f'{args[0]} exit {code}, expected {expected}: {error.getvalue()}')
        return json.loads(stream.getvalue()) if parse else stream.getvalue()
    base=['--store',store.root]
    provider_args=['--base-url',Scripted.base_url,'--model',Scripted.model,'--api-key-env','BIOMODEL_SCRIPTED_KEY']
    results={}
    def config(name,spec):
        path=output/(name+'.json');path.write_text(json.dumps(spec,indent=2));return path
    invoke(['research-task-capabilities'])
    intake=invoke(['research-task-create','--config',config('intake',dict(schema_version=1,goal='Reproduce an explicit supplied model.')),*base],2)
    invoke(['research-task-show',intake['request_ref'],*base])
    with patch.object(llm,'ChatProvider',factory),patch.dict(os.environ,{'BIOMODEL_SCRIPTED_KEY':'synthetic-placeholder'}):
        for kind in ('ode','fba',*typed_models.TYPES):
            spec=dict(schema_version=1,goal='Execute the supplied bounded '+kind+' workflow.',model_type=kind,max_calls=6)
            if kind=='ode':
                spec.update(source_refs=[initialized['example_source_refs']['complete']],
                    execution=json.loads((output/'workspace/examples/execution.json').read_text()))
                plan=json.loads((output/'workspace/examples/complete-plan.json').read_text())
                actions=[action('submit_plan',plan=plan),action('run'),action('finish')]
            else:
                if kind=='fba':
                    model=fba.import_model(store,ROOT/'examples/fba-v1/chain.json');settings={};tool='run_fba'
                else:
                    model=typed_models.import_model(store,ROOT/f'examples/model-types/{kind}.json')
                    settings=json.loads((ROOT/f'examples/model-types/{kind}-settings.json').read_text());tool='run_typed'
                spec.update(model_ref=model,execution={'settings':settings})
                actions=[action(tool),action('finish')]
            extra=['--parent-ref',intake['request_ref'],'--reason','Select the supplied ODE workflow.'] if kind=='ode' else []
            prepared=invoke(['research-task-create','--config',config(kind,spec),*extra,*base])
            pending.append(Scripted(actions))
            result=invoke(['research-task-run',prepared['request_ref'],*base,*provider_args])
            before=len(providers)
            cached=invoke(['research-task-run',prepared['request_ref'],*base])
            assert cached['result_ref']==result['result_ref'] and len(providers)==before
            invoke(['research-task-show',prepared['request_ref'],*base])
            invoke(['report',result['result_ref'],'--output',output/f'{kind}-report.md',*base],parse=False)
            invoke(['agent-timeline',result['result_ref'],'--output',output/f'{kind}-timeline.md',*base],parse=False)
            invoke(['export',result['result_ref'],'--output',output/f'{kind}-bundle',*base],parse=False)
            invoke(['report',result['result_ref'],'--output',output/f'{kind}-detached-report.md','--store',output/f'{kind}-bundle'],parse=False)
            assert (output/f'{kind}-report.md').read_bytes()==(output/f'{kind}-detached-report.md').read_bytes()
            results[kind]=dict(request_ref=prepared['request_ref'],result_ref=result['result_ref'],state=result['state'],
                call_count=result['call_count'],run_count=result['run_count'],detached_report_matches=True)
            if kind=='boolean':boolean_spec=deepcopy(spec)
        paused=invoke(['research-task-create','--config',config('paused-boolean',boolean_spec),*base])
        pending.append(Scripted([action('ask_researcher',question='Confirm the frozen input before execution?')]))
        initial=invoke(['research-task-run',paused['request_ref'],*base,*provider_args],2)
        reply=output/'reply.txt';reply.write_text('Proceed with this frozen native model and seed.')
        pending.append(Scripted([action('run_typed'),action('finish')]))
        final=invoke(['research-task-resume',paused['request_ref'],'--reply-file',reply,'--respondent','Synthetic operator',*base,*provider_args])
        assert final['native_task_ref']==initial['native_task_ref'] and final['call_count']==3 and final['run_count']==1
        results['paused_boolean']=dict(request_ref=paused['request_ref'],result_ref=final['result_ref'],state=final['state'],
            call_count=final['call_count'],run_count=final['run_count'],same_native_task=True)
    summary=dict(scope='seen_synthetic_scripted_public_cli_lifecycle_controls',results=results,
        scripted_provider_responses=sum(p.calls for p in providers),live_provider_requests=0,
        scientific_application_calls=len(store.list('run'))+len(store.list('fba_run'))+len(store.list('typed_run')),
        independent_domain_reviews=0,scientific_support='unknown',token_usage_measured=False)
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True)
    args=p.parse_args();print(json.dumps(run(args.output),ensure_ascii=False,indent=2))
