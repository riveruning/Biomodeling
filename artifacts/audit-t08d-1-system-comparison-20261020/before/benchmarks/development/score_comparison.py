"""Score native submissions independently of the product's internal schema.

Generated programs run only after a human/operator has inspected their exact
hashes and put them in reviewed-programs.json. No agent credentials enter them.
"""
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile

import numpy as np
from scipy.integrate import solve_ivp

from biomodel_agent.literature_scoring import score_extraction
from biomodel_agent.storage import ArtifactStore

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'artifacts/comparison-pilot-20261017'


def read(path):
    return json.loads(Path(path).read_text())


def normalize(answer):
    facts = {};duplicates = []
    for field, identity, value, prefix, suffix in [
            ('parameters','id','value','','/value'),
            ('states','id','initial','','/initial'),
            ('equations','state','expression','rule:','/expression')]:
        entries = answer.get(field, [])
        if not isinstance(entries,list):
            raise ValueError(f'{field} must be an array')
        for entry in entries:
            if not isinstance(entry,dict) or not isinstance(entry.get(identity),str):
                raise ValueError('invalid model entry')
            target = prefix+entry[identity]+suffix
            if target in facts: duplicates.append(target)
            facts[target] = {'value':entry.get(value),'evidence':[
                {'source_id':'case','quote':entry['quote']}] if entry.get('quote') else []}
    return {'facts':facts,'state':answer.get('status'),'duplicate_targets':duplicates}


def compare_trajectory(times, values, expected_times):
    actual_times = np.asarray(times,dtype=float)
    expected_times = np.asarray(expected_times,dtype=float)
    if actual_times.shape != expected_times.shape or not np.allclose(actual_times,expected_times,rtol=0,atol=1e-12):
        raise ValueError('wrong sampling grid')
    actual = np.array([values[k] for k in ('X','Y','Z')],dtype=float)
    if actual.shape != (3,len(times)) or not np.isfinite(actual).all():
        raise ValueError('invalid trajectory shape or nonfinite values')
    def rhs(t,y):
        x,p,z = y
        return [1/(1+z**10)-.1*x,x-.1*p,p-.1*z]
    reference = solve_ivp(rhs,(expected_times[0],expected_times[-1]),(.1,.2,.3),
        t_eval=expected_times,method='DOP853',rtol=1e-11,atol=1e-13)
    if not reference.success: raise RuntimeError('reference solver failed')
    error = float(np.max(np.abs(actual-reference.y))/10.)
    return {'status':'pass' if error<=.001 else 'fail','max_scaled_error':error,
            'scale':10.,'threshold':.001,'reference':'independent_handwritten_DOP853'}


def run_reviewed_program(folder, case, reviewed):
    program = folder/'simulate.py'
    if not program.exists(): return {'status':'not_delivered'}
    digest = sha256(program.read_bytes()).hexdigest()
    if reviewed.get(str(program.relative_to(ROOT))) != digest:
        return {'status':'awaiting_code_review','sha256':digest}
    with tempfile.TemporaryDirectory(prefix='biomodel-pilot-replay-') as tmp:
        work = Path(tmp)
        (work/'simulate.py').write_bytes(program.read_bytes())
        (work/'experiment.json').write_text(json.dumps(case['experiment']))
        (work/'source.txt').write_bytes((ROOT/case['source']).read_bytes())
        try:
            result = subprocess.run([str(ROOT/'.venv/bin/python'),'-I','simulate.py'],
                cwd=work,env={'PATH':'/usr/bin:/bin','OPENBLAS_NUM_THREADS':'1',
                              'OMP_NUM_THREADS':'1','PYTHONHASHSEED':'0'},
                capture_output=True,text=True,timeout=30)
            execution={'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr}
            (folder/'independent-replay.json').write_text(json.dumps(execution,indent=2)+'\n')
            if result.returncode: return {'status':'execution_failed','exit_code':result.returncode}
            data=json.loads(result.stdout)
            if data.get('status')=='needs_input':return {'status':'declined_missing_information'}
            if not case['expected_execution']:
                return {'status':'unexpected_simulation_without_required_source_information'}
            return compare_trajectory(data['times'],data['values'],case['experiment']['times'])
        except (subprocess.TimeoutExpired, ValueError, KeyError, TypeError) as exc:
            return {'status':'invalid_output_or_timeout','error':str(exc)}


def native_metadata(system, name):
    folder=OUT/system/name
    if system=='codex':
        record=read(folder/'execution.json') if (folder/'execution.json').exists() else {}
        token_usage={}
        if (folder/'events.jsonl').exists():
            for line in (folder/'events.jsonl').read_text().splitlines():
                try: event=json.loads(line)
                except ValueError:continue
                if event.get('type')=='turn.completed':token_usage=event.get('usage',{})
        return {**record,'reported_usage':token_usage or None}
    log=OUT/'dsh'/f'{name}-supervisor.log'
    if log.exists():
        for line in reversed(log.read_text().splitlines()):
            try: event=json.loads(line)
            except ValueError:continue
            if event.get('event')=='summary':
                return {'elapsed_seconds':event['elapsedMs']/1000.,'reviews':event['reviews'],
                        'supervision_outcome':event['outcome'],'reported_usage':None}
    return {'status':'not_finished','reported_usage':None}


def score():
    protocol=read(OUT/'protocol.json')
    reviewed=read(OUT/'reviewed-programs.json') if (OUT/'reviewed-programs.json').exists() else {}
    rows=[]
    for system in ('biomodel','dsh','codex'):
        for name,case in protocol['cases'].items():
            folder=OUT/system/name
            source=(ROOT/case['source']).read_text()
            if sha256(source.encode()).hexdigest()!=case['source_sha256']:
                raise ValueError('frozen source changed')
            row={'system':system,'case':name,'expected_execution':case['expected_execution'],
                 'human_content_corrections':0}
            try:
                if system=='biomodel':
                    result=read(folder/'result.json')
                    prediction=read(folder/'prediction.json')
                    row.update(state=result['state'],elapsed_seconds=result['elapsed_seconds'],
                               calls=result['calls'],repairs=result['repairs'],
                               reported_usage={'total_tokens':result['usage_total_tokens']})
                    run_ref=result['run_ref']
                    row['numeric']={'status':'not_run'}
                    if run_ref:
                        payload=ArtifactStore(folder/'store').get(run_ref)['payload']
                        sim=payload.get('simulation') or {}
                        row['numeric']=compare_trajectory(sim['times'],sim['values'],case['experiment']['times'])
                else:
                    answer=read(folder/'answer.json');prediction=normalize(answer)
                    row.update(state=answer.get('status'),**native_metadata(system,name))
                    row['numeric']=run_reviewed_program(folder,case,reviewed)
                row['score']=score_extraction(case['gold'],prediction,{'case':source})
                row['duplicate_targets']=prediction.get('duplicate_targets',[])
                row['valid_executable_delivery']=(row['numeric']['status']=='pass'
                    and row['score']['counts']['fully_supported']==row['score']['counts']['expected_present']
                    and not row['score']['extra_targets'] and not row['duplicate_targets'])
            except (FileNotFoundError, ValueError, KeyError, TypeError) as exc:
                row.update(status='missing_or_invalid_submission',error=str(exc),valid_executable_delivery=False)
            rows.append(row)
    result={'schema_version':'1','protocol_sha256':sha256((OUT/'protocol.json').read_bytes()).hexdigest(),
            'scope':protocol['scope'],'fairness_limits':protocol['fairness_limits'],
            'rows':rows,'human_semantic_review':'pending_independent_human_review'}
    (OUT/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps([{k:v for k,v in row.items() if k in ['system','case','state','status','numeric','valid_executable_delivery']} for row in rows],indent=2))
    return result


if __name__=='__main__':score()
