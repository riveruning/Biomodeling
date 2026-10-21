"""Execute the pinned official Talk2Biomodels graph without editing vendor files."""
import argparse
import importlib
import json
import os
from pathlib import Path
import sys
import time
import types

from competitor_common import OUT, ROOT, environment, verify_source, write_json


def load_official():
    verify_source('talk2biomodels')
    # The repository root initializer eagerly imports unrelated single-cell and
    # knowledge-graph apps. A namespace bootstrap loads only the T2B package.
    # All T2B initializers, graph nodes, tools and prompts remain original.
    namespace=types.ModuleType('aiagents4pharma')
    namespace.__path__=[str(OUT/'vendor/talk2biomodels/aiagents4pharma')]
    sys.modules['aiagents4pharma']=namespace
    return importlib.import_module('aiagents4pharma.talk2biomodels.agents.t2b_agent')


def run(case, model, key_env, offline_smoke=False, clarify_contract=False):
    group='talk2biomodels-contract-clarified' if clarify_contract else 'talk2biomodels'
    dest=OUT/group/case;dest.mkdir(parents=True,exist_ok=True)
    record={'system':'Talk2Biomodels','case':case,'model':model,
            'variant':'official_graph_alternative_LLM_module_only_environment',
            'vendor_source_modified':False,'original_model_available':False,
            'adaptations':['namespace bootstrap skips only unrelated root app imports',
                           'caller-supplied ChatOpenAI uses alternative provider/model',
                           'module-specific dependencies; full monorepo extras not installed'],
            'environment':environment(),'extra_contract_guidance':clarify_contract}
    started=time.monotonic()
    try:
        module=load_official()
        from langchain_core.callbacks import BaseCallbackHandler
        from langchain_core.messages import HumanMessage, message_to_dict
        from langchain_openai import ChatOpenAI
        import httpx
        audit=[]

        class Audit(BaseCallbackHandler):
            raise_error=True

            def on_chat_model_start(self,serialized,messages,**kwargs):
                if sum(e['type']=='model_start' for e in audit)>=8:
                    raise RuntimeError('integration_model_call_budget_exhausted')
                audit.append({'type':'model_start','messages':[
                    [message_to_dict(m) for m in batch] for batch in messages]})

            def on_llm_end(self,response,**kwargs):
                audit.append({'type':'model_end','llm_output':response.llm_output,
                    'messages':[message_to_dict(g.message) for batch in response.generations for g in batch]})

            def on_tool_start(self,serialized,input_str,**kwargs):
                audit.append({'type':'tool_start','tool':serialized.get('name'),'input':input_str})

            def on_tool_error(self,error,**kwargs):
                audit.append({'type':'tool_error','error':str(error)[:1000]})

        callback=Audit()
        llm=ChatOpenAI(model=model,base_url='https://api.llm.ustc.edu.cn/v1',
            api_key=os.environ.get(key_env,'offline-smoke-placeholder'),temperature=0,
            max_tokens=4000,max_retries=0,timeout=90,
            http_client=httpx.Client(trust_env=False,timeout=90),callbacks=[callback])
        app=module.get_app('integration-'+case,llm)
        record['graph_import_and_construction']='pass'
        if offline_smoke:
            record.update(status='offline_graph_smoke_passed',elapsed_seconds=time.monotonic()-started)
            write_json(dest/'smoke.json',record);print('Official graph constructed; no model call.')
            return record
        config=json.loads((OUT/'sbml-protocol.json').read_text())['cases'][case]
        sbml=str((ROOT/config['sbml']).resolve())
        prompt=config['prompt']
        if clarify_contract:
            prompt+=('\nIntegration API clarification: this is a locally uploaded model. '
                     'Omit the optional biomodel_id field entirely; do not pass null or invent an ID. '
                     'Use use_uploaded_sbml_file=true. In this implementation time_data.interval '
                     'is the NUMBER of output intervals, not the time step. '
                     f"Use duration={config['times'][-1]} and interval={len(config['times'])-1}. "
                     'Only a simulate_model tool call is needed.')
        state={'messages':[HumanMessage(prompt)],'llm_model':llm,
               'text_embedding_model':None,'pdf_file_name':'',
               'model_id':[],'sbml_file_path':[sbml],'model_as_string':[],
               'dic_simulated_data':[],'dic_scanned_data':[],
               'dic_steady_state_data':[],'dic_annotations_data':[]}
        write_json(dest/'task.json',{'prompt':prompt,'sbml':config['sbml'],
                                    'sbml_sha256':config['sbml_sha256']})
        last=state
        try:
            for update in app.stream(state,{'configurable':{'thread_id':'integration-'+case},
                                            'recursion_limit':20},stream_mode='values'):
                last=update
        finally:
            write_json(dest/'audit.json',audit)
        trajectories=last.get('dic_simulated_data',[])
        write_json(dest/'simulation-state.json',trajectories)
        write_json(dest/'messages.json',[message_to_dict(m) for m in last.get('messages',[])])
        record.update(status='executed' if trajectories else 'completed_without_simulation',
            simulations=len(trajectories),calls=sum(e['type']=='model_start' for e in audit),
            tool_calls=[e for e in audit if e['type']=='tool_start'])
    except Exception as exc:
        record.update(status='integration_or_execution_failed',error_type=type(exc).__name__,error=str(exc)[:1800])
        if 'audit' in locals():
            write_json(dest/'audit.json',audit)
            record['calls']=sum(e['type']=='model_start' for e in audit)
    record['elapsed_seconds']=time.monotonic()-started
    write_json(dest/'result.json',record)
    print(json.dumps({k:record.get(k) for k in ['system','case','status','error_type','error','calls','elapsed_seconds']}),flush=True)
    return record


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--case',choices=['decay','mapk'],required=True)
    parser.add_argument('--model',default='qwen3.8-chat');parser.add_argument('--key-env',default='BIOMODEL_API_KEY')
    parser.add_argument('--offline-smoke',action='store_true')
    parser.add_argument('--clarify-contract',action='store_true',
        help='Separate assisted diagnostic; never overwrites unassisted evaluation')
    args=parser.parse_args();run(args.case,args.model,args.key_env,args.offline_smoke,args.clarify_contract)
