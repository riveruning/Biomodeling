"""Execute a frozen comparison with injected transports; CLI is scripted-only."""
from __future__ import annotations

import argparse
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

_spec=importlib.util.spec_from_file_location('comparison_workflow_adapters',Path(__file__).with_name('workflow_adapters.py'))
adapters=importlib.util.module_from_spec(_spec); _spec.loader.exec_module(adapters)
protocol=adapters.protocol


class ScriptedTransport:
    """Literal action fixtures; placeholders read only visible request metadata."""
    evidence_kind='scripted_control'
    def __init__(self,projection,actions):
        self.model_config=deepcopy(projection['model_config']); self.actions=deepcopy(actions)
    def request(self,payload,timeout_s):
        main=reply=length=None
        for message in payload['messages']:
            try: value=json.loads(message['content'])
            except (TypeError,ValueError): continue
            if not isinstance(value,dict): continue
            if main is None and isinstance(value.get('materials'),dict) and value['materials']:
                main=next(iter(value['materials'])); length=value['materials'][main]['pages'][0]['chars']
            if value.get('tool')=='researcher_reply': reply=value['source_ref']
        substitutions={'$main':main,'$reply':reply,'$main_length':length}
        def resolve(value):
            if isinstance(value,dict): return {k:resolve(v) for k,v in value.items()}
            if isinstance(value,list): return [resolve(v) for v in value]
            if isinstance(value,str) and value in substitutions:
                if substitutions[value] is None: raise ValueError('fixture references unavailable source metadata')
                return substitutions[value]
            return value
        if not self.actions: raise ValueError('scripted response list exhausted')
        action=resolve(self.actions.pop(0))
        return {'model':self.model_config['model'],'choices':[{'message':{'content':json.dumps(action,ensure_ascii=False)}}],
                'usage':None}


def validate_scripts(manifest,scripts):
    protocol._fields(scripts,'evidence_kind trials')
    if scripts['evidence_kind']!='scripted_control' or manifest['evidence_kind']!='scripted_control':
        raise ValueError('CLI accepts explicit scripted controls only; no online client is created')
    if not isinstance(scripts['trials'],dict) or set(scripts['trials'])!={t['id'] for t in protocol.schedule(manifest)}:
        raise ValueError('scripted fixture must name every planned trial exactly once')
    for actions in scripts['trials'].values():
        if not isinstance(actions,list) or not 1<=len(actions)<=50: raise ValueError('bounded action list required')
        for action in actions:
            if not isinstance(action,dict) or set(action)-{'action','args','reason'} or not isinstance(action.get('action'),str) or not isinstance(action.get('args'),dict):
                raise ValueError('invalid literal scripted action')


def run_comparison(manifest,transport_factory,output,*,root=protocol.ROOT):
    audit=protocol.validate_manifest(manifest,root)
    plan=protocol.schedule(manifest)
    # Seal every phase before any execution. Only the current projection is
    # handed to a workflow or transport; the rubric remains in this driver.
    inputs=[protocol.trial_input(manifest,t,root) for t in plan]
    output=Path(output)
    if output.exists(): raise ValueError('comparison output already exists')
    output.mkdir(parents=True,exist_ok=False)
    adapters._write(output/'manifest.json',manifest); adapters._write(output/'schedule.json',plan)
    adapters._write(output/'implementation.json',{
        'protocol':protocol.PROTOCOL,'toolchain_sha256':manifest['toolchain_sha256'],
        'adapter_sha256':protocol.hashlib.sha256(Path(adapters.__file__).read_bytes()).hexdigest(),
        'scorer_sha256':protocol.hashlib.sha256(Path(protocol.__file__).read_bytes()).hexdigest(),
        'driver_sha256':protocol.hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    records=[]; sessions={}; receipts=[]
    try:
        for p in inputs:
            t=p['trial']; key=(t['workflow'],t['case_id'],t['repeat']); parent=sessions.get(key)
            transport=transport_factory(deepcopy(p))
            result=adapters.run_trial(p,transport,output/'trials'/t['id'],
                store_root=output/'stores'/f"{t['workflow']}--{t['case_id']}--{t['repeat']}",
                parent_session_ref=parent['session_ref'] if parent else None)
            sessions[key]=result; records.append(result['record'])
            receipts.append({'trial_id':t['id'],'session_ref':result['session_ref'],
                             'store_root':result['store_root'],'audit':result['audit']})
            adapters._write(output/'records.json',records); adapters._write(output/'receipts.json',receipts)
    except Exception as exc:
        # Keep all paid/partial effects and the original denominator. This
        # driver never silently rebuilds a task or replays a failed phase.
        adapters._write(output/'driver-error.json',{'type':type(exc).__name__,'completed_records':len(records)})
        adapters._write(output/'records.json',records)
        raise
    result=protocol.score(manifest,records,root)
    adapters._write(output/'results.json',result)
    engineering={'status':'adapter_execution_complete','planned_trials':audit['planned_trials'],
        'finished_records':len(records),'operational_statuses':dict(protocol.Counter(r['status'] for r in records)),
        'observed_states':dict(protocol.Counter(r['observation']['state'] for r in records)),
        'provider_requests':sum(r['telemetry']['model_calls'] for r in records),
        'online_inference_calls':0 if manifest['evidence_kind']=='scripted_control' else None,
        'scientific_execution_attempts':sum(r['observation']['executed'] for r in records),
        'evidence_kind':manifest['evidence_kind'],'scientific_support':'unknown',
        'note':'Adapter execution evidence; scripted decisions and unreviewed assessments are not model capability scores.'}
    adapters._write(output/'engineering.json',engineering)
    return {'engineering':engineering,'results':result,'records':records,'receipts':receipts}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True,type=Path)
    parser.add_argument('--scripts',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args(argv)
    try:
        manifest=protocol._read(args.manifest); scripts=protocol._read(args.scripts)
        protocol.validate_manifest(manifest); validate_scripts(manifest,scripts)
        result=run_comparison(manifest,lambda p:ScriptedTransport(p,scripts['trials'][p['trial']['id']]),args.output)
        print(json.dumps(result['engineering'],ensure_ascii=False))
        return 1 if any(r['status'] not in ('finished','not_run') for r in result['records']) else 0
    except (ValueError,TypeError,KeyError,OSError) as exc:
        print(json.dumps({'status':'refused','error_type':type(exc).__name__})); return 2


if __name__=='__main__': raise SystemExit(main())
