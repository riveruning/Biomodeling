"""External, deterministic reliability scoring. No LLM judge or product schema requirement."""
import csv
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import tempfile
import numpy as np

from prepare_reliability import ROOT, OUT, dump
from run_reliability import verify_freeze


def compare(data, reference, scales, threshold=.001):
    t=np.asarray(data['times'],dtype=float); expected=np.asarray(reference['times'],dtype=float)
    if t.shape!=expected.shape or not np.allclose(t,expected,rtol=0,atol=1e-12):
        raise ValueError('wrong sampling grid')
    errors=[]
    for name,values in reference['values'].items():
        y=np.asarray(data['values'][name],dtype=float)
        if y.shape!=expected.shape or not np.isfinite(y).all():raise ValueError('invalid trajectory')
        errors.append(float(np.max(np.abs(y-np.asarray(values)))/scales[name]))
    error=max(errors)
    return {'status':'pass' if error<=threshold else 'fail','max_scaled_error':error,'threshold':threshold}


def gap_identified(text,target,category):
    """Frozen bilingual lexicon; save exact evidence for operator audit."""
    text=text.lower(); symbol=target.split('/')[0].removeprefix('rule:').lower()
    symbol_found=bool(re.search(r'(?<![a-z0-9_])'+re.escape(symbol)+r'(?![a-z0-9_])',text))
    terms={'missing_initial':r'initial|初[始值]|/initial',
           'missing_parameter':r'parameter|参数|/value',
           'ambiguous_equation':r'equation|alternative|ambig|方程|公式|歧义',
           'unit_conflict':r'unit|minute|second|单位|分钟|秒'}
    return symbol_found and bool(re.search(terms[category],text))


def unresolved_numeric_prediction(facts,target):
    item=facts.get(target,{})
    return target.endswith(('/initial','/value')) and item.get('value') is not None


def codex_prediction(answer):
    facts={}
    for field,ident,value,prefix,suffix in [('parameters','id','value','','/value'),('states','id','initial','','/initial'),('equations','state','expression','rule:','/expression')]:
        for entry in answer.get(field,[]):facts[prefix+entry[ident]+suffix]={'value':entry.get(value)}
    return facts


def replay(folder,phase_source,reviewed):
    program=folder/'simulate.py'; key=str(program.relative_to(ROOT))
    if not program.exists():return {'status':'not_delivered'}
    digest=sha256(program.read_bytes()).hexdigest()
    if reviewed.get(key)!=digest:return {'status':'awaiting_code_review','sha256':digest}
    with tempfile.TemporaryDirectory(prefix='biomodel-reliability-replay-') as tmp:
        work=Path(tmp)
        for name in ['simulate.py']: (work/name).write_bytes((folder/name).read_bytes())
        for name in ['source.txt','experiment.json']:(work/name).write_bytes((phase_source.parent/name).read_bytes())
        try:
            result=subprocess.run([str(ROOT/'.venv/bin/python'),'-I','simulate.py'],cwd=work,
              env={'PATH':'/usr/bin:/bin','OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','PYTHONHASHSEED':'0'},
              capture_output=True,text=True,timeout=30)
            dump(folder/'independent-replay.json',{'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
            if result.returncode:return {'status':'execution_failed'}
            output=json.loads(result.stdout)
            if output.get('status')=='needs_input':return {'status':'declined','reason':output.get('reason','')}
            dump(folder/'trajectory.json',output)
            return {'status':'executed'}
        except (ValueError,subprocess.TimeoutExpired) as exc:return {'status':'invalid_output_or_timeout','error':str(exc)}


def score_row(system,name,phase,case,reviewed):
    folder=OUT/system/name/phase; config=case['phases'][phase]; source=ROOT/config['source']
    row={'system':system,'case':name,'phase':phase,'category':case['category'],'expected_execution':config['expected_execution']}
    if not (folder/'result.json').exists():return {**row,'status':'not_run','successful_execution':False}
    result=json.loads((folder/'result.json').read_text());row.update(status=result['status'],elapsed_seconds=result['elapsed_seconds'],calls=result.get('calls'),tokens=result.get('tokens'))
    facts={}; question_text='';artifact=False
    if system=='biomodel':
        prediction=json.loads((folder/'prediction.json').read_text()) if (folder/'prediction.json').exists() else {}
        facts=prediction.get('facts',{});question_text='\n'.join(result.get('questions',[])+result.get('problems',[]))
        artifact=bool(result.get('model_ref'))
    elif system=='codex':
        try:
            answer=json.loads((folder/'answer.json').read_text());facts=codex_prediction(answer)
            question_text='\n'.join(answer.get('questions',[])+answer.get('limitations',[]))
            row['answer_status']=answer.get('status')
            replay_result=replay(folder,source,reviewed);row['replay']=replay_result
            if replay_result['status']=='declined':question_text+='\n'+replay_result.get('reason','')
            artifact=replay_result['status']=='executed'
        except (ValueError,KeyError,TypeError) as exc:row['answer_error']=str(exc)
    else:
        artifact=(folder/'generated-sbml.xml').exists()
    trajectory=folder/'trajectory.json'; executed=trajectory.exists()
    row.update(executable_artifact=artifact,actual_trajectory=executed,gap_evidence=question_text)
    if executed:
        try:
            reference=json.loads((OUT/'references'/f'{name}.json').read_text())
            row['numeric']=compare(json.loads(trajectory.read_text()),reference,case['scales']) if config['expected_execution'] else {'status':'not_scored_unresolved_source'}
        except (ValueError,KeyError,TypeError) as exc:row['numeric']={'status':'invalid_trajectory','error':str(exc)}
    else:row['numeric']={'status':'not_run'}
    row['successful_execution']=(config['expected_execution'] and row['numeric']['status']=='pass'
      and (system!='biomodel' or result['status']=='completed'))
    if not config['expected_execution']:
        target=case['blocking_target']
        row['unsupported_proposal']=unresolved_numeric_prediction(facts,target)
        # Rejected drafts are a proposal diagnostic, not successful fabricated delivery.
        row['unsupported_completion']=bool(artifact or executed or (system=='codex' and row['unsupported_proposal']))
        row['gap_detected']=not row['unsupported_completion'] and gap_identified(question_text,target,case['category'])
    return row


def aggregate(rows):
    summaries={}
    for system in ['biomodel','codex','kinmodgpt']:
        own=[r for r in rows if r['system']==system]; complete=[r for r in own if r['phase']=='initial' and r['category']=='complete']
        blocked=[r for r in own if not r['expected_execution']]; clarified=[r for r in own if r['phase']=='clarified']
        initial_gaps={r['case'] for r in blocked if r.get('gap_detected')}
        conditional=[r for r in clarified if r['case'] in initial_gaps]
        def metric(items,key):return {'count':sum(bool(r.get(key)) for r in items),'denominator':len(items)}
        summaries[system]={'complete_success':metric(complete,'successful_execution'),
          'unsupported_completion':metric(blocked,'unsupported_completion'),'gap_detection':metric(blocked,'gap_detected'),
          'clarified_rebuild':metric(clarified,'successful_execution'),'conditional_rebuild':metric(conditional,'successful_execution'),
          'wall_seconds_sum':sum(r.get('elapsed_seconds',0) for r in own),
          'provider_tokens_sum':sum(r.get('tokens') or 0 for r in own) if system!='codex' else None,
          'incomplete_or_failed_trials':sum(r['status'] in ['not_run','adapter_or_provider_failed','cli_failed','timed_out','failed'] for r in own)}
    return summaries


def score():
    verify_freeze();protocol=json.loads((OUT/'protocol.json').read_text())
    review=json.loads((OUT/'reviewed-programs.json').read_text()) if (OUT/'reviewed-programs.json').exists() else {}
    rows=[score_row(system,name,phase,case,review) for system in ['biomodel','codex','kinmodgpt'] for name,case in protocol['cases'].items() for phase in case['phases']]
    result={'rows':rows,'summary':aggregate(rows)};dump(OUT/'results.json',result)
    fields=['system','case','phase','category','status','successful_execution','unsupported_completion','gap_detected','elapsed_seconds','calls','tokens']
    with (OUT/'results.csv').open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)
    print(json.dumps(result['summary'],ensure_ascii=False,indent=2))

if __name__=='__main__':score()
