"""Run the pinned author's KinModGPT conversion, with an explicit backend variant.

Original instruction, bullet cleaning and Tellurium conversion stay unchanged.
Only talk_to_GPT transport/model selection is replaced outside vendor code.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import time

from competitor_common import OUT, ROOT, environment, verify_source, write_json


def run(case, model, key_env):
    import httpx
    import tellurium as te
    verify_source('kinmodgpt')
    source=OUT/'vendor/kinmodgpt/Conversion/6. KinModGPT_gpt-4'
    module_spec=importlib.util.spec_from_file_location('official_kinmodgpt',source/'KinModGPT.py')
    module=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(module)
    dest=OUT/'kinmodgpt'/case;dest.mkdir(parents=True,exist_ok=True)
    text=(ROOT/'benchmarks/inputs/comparison-pilot'/case/'source.txt').read_text()
    instruction=(source/'Instruction.txt').read_text()
    calls=[]

    def transport(original_instruction,description,unused_api_key_path):
        messages=[{'role':'system','content':original_instruction},
                  {'role':'user','content':description}]
        write_json(dest/'request-without-credentials.json',{'model':model,'messages':messages,
                    'temperature':0,'max_tokens':6144,'endpoint':'https://api.llm.ustc.edu.cn/v1/chat/completions'})
        with httpx.Client(trust_env=False,timeout=90) as client:
            response=client.post('https://api.llm.ustc.edu.cn/v1/chat/completions',
                headers={'Authorization':'Bearer '+os.environ[key_env]},
                json={'model':model,'messages':messages,'temperature':0,'max_tokens':6144,
                      'top_p':1,'frequency_penalty':0,'presence_penalty':0})
        if response.status_code!=200:
            raise RuntimeError(f'provider_http_{response.status_code}')
        payload=response.json();calls.append(payload)
        write_json(dest/'provider-response.json',payload)
        return payload

    module.talk_to_GPT=transport
    record={'system':'KinModGPT','variant':'official_pipeline_alternative_LLM_modern_runtime',
            'model':model,'original_model':'gpt-4','case':case,
            'original_model_available_in_configured_provider':False,
            'environment':environment(),'vendor_source_modified':False,
            'adaptations':['replace talk_to_GPT outside vendor module with equivalent Chat Completions messages and alternative model',
                           'use Python 3.12/Tellurium 2.2.11.2 instead of original macOS Python 3.7/Tellurium 2.2.3'],
            'task_eligibility':'complete is simulation; assisted is diagnostic for unreported initial defaults, not native abstention benchmark'}
    started=time.monotonic()
    try:
        prompts,responses,raw,antimony,sbml=module.KinModGPT(instruction,[text],None,verbose=0)
        (dest/'generated-antimony.txt').write_text(antimony)
        (dest/'generated-sbml.xml').write_text(sbml)
        runner=te.loadSBMLModel(sbml)
        times=json.loads((ROOT/'benchmarks/inputs/comparison-pilot'/case/'experiment.json').read_text())['times']
        data=runner.simulate(times=times,selections=['time','X','Y','Z'])
        write_json(dest/'trajectory.json',{'times':data[:,0].tolist(),
                    'values':{k:data[:,i+1].tolist() for i,k in enumerate(['X','Y','Z'])}})
        record.update(status='executed',initial_values={k:float(runner[f'init({k})']) for k in ['X','Y','Z']})
    except Exception as exc:
        # Persist actual model output even if the original cleaner/converter fails.
        if calls:
            raw=calls[-1]['choices'][0]['message']['content']
            (dest/'raw-model-output.txt').write_text(raw)
            (dest/'cleaned-model-output.txt').write_text(module.clean_GPT_output(raw))
        record.update(status='integration_or_execution_failed',error_type=type(exc).__name__,error=str(exc)[:1200])
    record.update(elapsed_seconds=time.monotonic()-started,calls=len(calls),
                  reported_tokens=sum(c.get('usage',{}).get('total_tokens',0) for c in calls))
    write_json(dest/'result.json',record)
    print(json.dumps({k:record.get(k) for k in ['system','case','status','error_type','elapsed_seconds','reported_tokens']}),flush=True)
    return record


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--case',choices=['complete','assisted'],required=True)
    parser.add_argument('--model',default='qwen3.8-chat');parser.add_argument('--key-env',default='BIOMODEL_API_KEY')
    args=parser.parse_args();run(args.case,args.model,args.key_env)
