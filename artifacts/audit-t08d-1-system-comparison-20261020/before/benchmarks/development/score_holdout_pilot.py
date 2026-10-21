"""External scoring of frozen pilot outputs; model contexts never see this module."""
import csv
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess

import numpy as np
from holdout_sandbox import PROJECT,base,clean_environment

OUT=PROJECT/'artifacts/holdout-evaluation-20261017'
DATA=Path('/home/riveruning/biomodel_agent_evalset')


def dump(path,obj):path.write_text(json.dumps(obj,indent=2,ensure_ascii=False,allow_nan=False))


def numeric_match(actual, reference, required, threshold=.001):
    if not isinstance(actual,dict) or not isinstance(actual.get('values'),dict):raise ValueError('invalid trajectory shape')
    t=np.asarray(actual['times'],dtype=float); expected=np.asarray(reference['times'],dtype=float)
    if t.shape!=expected.shape or not np.isfinite(t).all() or not np.allclose(t,expected,rtol=0,atol=1e-12):
        raise ValueError('sampling grid differs')
    if set(actual['values'])!=set(required):raise ValueError('reported species differ')
    errors={}
    for name in required:
        value=np.asarray(actual['values'][name],dtype=float);target=np.asarray(reference['values'][name],dtype=float)
        if value.shape!=target.shape or not np.isfinite(value).all():raise ValueError('invalid species values')
        errors[name]=float(np.max(np.abs(value-target))/max(1.,float(np.max(np.abs(target)))))
    return {'status':'pass' if max(errors.values())<=threshold else 'fail',
            'max_scaled_error':max(errors.values()),'per_species_scaled_errors':errors,'threshold':threshold}


def targeted_question(case,questions):
    text='\n'.join(questions)
    patterns={
        'c05':(r'\bEs\b|E\*|complex|复合',r'initial|初[始值]|missing|not reported|not provided'),
        'c09':(r'\bKI\b|inhibition constant|抑制常数',r'parameter|constant|value|参数|数值|missing|report'),
        'c13':(r'\bI\b|inhibitor|抑制剂',r'free|bound|total|include|interpret|meaning|游离|结合|总'),
        'c18':(r'\bkf\b|fast.?route|快速',r'unit|minute|hour|second|单位|分钟|小时|秒'),
    }
    if case[:3] not in patterns:return False
    named,kind=patterns[case[:3]]
    return bool(re.search(named,text,re.I) and re.search(kind,text,re.I))


def replay(work,reviewed):
    program=work/'simulate.py';dest=work/'delivery'
    if not program.exists():return {'status':'not_delivered'}
    key=str(program.relative_to(OUT));digest=sha256(program.read_bytes()).hexdigest()
    if reviewed.get(key,{}).get('sha256')!=digest:return {'status':'awaiting_operator_code_review','sha256':digest}
    cached=dest/'independent-replay.json'
    if cached.exists():return json.loads(cached.read_text())
    command=base(work,network=False)+['--ro-bind',str(PROJECT/'.venv'),str(PROJECT/'.venv'),
              '--',str(PROJECT/'.venv/bin/python'),'-I','/work/simulate.py']
    try:
        result=subprocess.run(command,env=clean_environment(),stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=30)
        record={'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr,'reviewed_sha256':digest}
        if result.returncode:record['status']='execution_failed'
        else:
            data=json.loads(result.stdout)
            if data.get('status')=='needs_input':record.update(status='declined',reason=data.get('reason',''))
            elif 'times' in data and 'values' in data:record.update(status='executed',trajectory=data)
            else:record['status']='invalid_output'
    except (ValueError,subprocess.TimeoutExpired) as exc:
        record={'status':'invalid_output_or_timeout','error':str(exc)[:1000],'reviewed_sha256':digest}
    dump(cached,record)
    return record


def product_row(case,phase,reference,required):
    work=OUT/'runs/biomodel'/case/('incomplete' if phase=='resume' else phase)
    dest=work/phase;path=dest/'result.json'
    row={'system':'biomodel','case':case,'phase':phase}
    if not path.exists():return {**row,'status':'not_run','successful_execution':False,'questions':[]}
    result=json.loads(path.read_text());row.update(result)
    row['executable_delivery']=bool(result.get('model_ref'))
    row['actual_trajectory']=(dest/'trajectory.json').exists()
    row['numeric']={'status':'not_run'}
    if row['actual_trajectory']:
        try:row['numeric']=numeric_match(json.loads((dest/'trajectory.json').read_text()),reference,required)
        except (ValueError,KeyError,TypeError) as exc:row['numeric']={'status':'invalid_trajectory','error':str(exc)}
    row['successful_execution']=result['status']=='completed' and row['numeric']['status']=='pass'
    row['false_blocking']=result['status']=='awaiting_input'
    events=json.loads((dest/'agent.json').read_text())['events']
    row['provider_failures']=[{k:e.get(k) for k in ['call','kind','error']} for e in events if e.get('type')=='provider_error']
    row['protocol_errors']=sum(e.get('type')=='provider_error' and e.get('kind')=='model_response' for e in events)
    row['terminal_protocol_failure']=result['status']=='failed' and bool(row['provider_failures'])
    return row


def codex_row(case,phase,reference,required,reviewed):
    work=OUT/'codex-transport-diagnostic/runs'/case/phase;dest=work/'delivery'
    row={'system':'codex','case':case,'phase':phase}
    if not (dest/'result.json').exists():return {**row,'status':'not_run','successful_execution':False,'questions':[]}
    result=json.loads((dest/'result.json').read_text());row.update(result)
    answer={}
    try:answer=json.loads((work/'answer.json').read_text())
    except (OSError,ValueError) as exc:row['answer_error']=str(exc)
    row.update(answer_status=answer.get('status'),questions=answer.get('questions',[]))
    row['limitations']=answer.get('limitations',[])
    outcome=replay(work,reviewed);row['replay']=outcome['status']
    row['executable_delivery']=outcome['status']=='executed'
    row['actual_trajectory']=outcome['status']=='executed'
    row['numeric']={'status':'not_run'}
    if row['actual_trajectory']:
        try:row['numeric']=numeric_match(outcome['trajectory'],reference,required)
        except (ValueError,KeyError,TypeError) as exc:row['numeric']={'status':'invalid_trajectory','error':str(exc)}
    if outcome['status']=='declined' and outcome.get('reason'):row['questions']=[*row['questions'],outcome['reason']]
    row['successful_execution']=result['status']=='delivered' and answer.get('status')=='ready' and row['numeric']['status']=='pass'
    row['false_blocking']=answer.get('status')=='needs_input' or outcome['status']=='declined'
    return row


def main():
    protocol=json.loads((OUT/'pilot-protocol.json').read_text());manifest=json.loads((OUT/'case_manifest.json').read_text())
    reviewed=json.loads((OUT/'reviewed-programs.json').read_text()) if (OUT/'reviewed-programs.json').exists() else {}
    rows=[]
    for case in protocol['cases']:
        config=manifest['cases'][case]
        reference=json.loads((DATA/config['reference']).read_text())
        exp=json.loads((DATA/config['phases']['incomplete']['experiment']).read_text())
        required=exp['species_to_report']
        for phase in config['phases']:
            for system,fn in [('biomodel',product_row),('codex',codex_row)]:
                row=fn(case,phase,reference,required,*([reviewed] if system=='codex' else []))
                row['category']=config['category'];row['sufficient']=config['category']=='complete' or phase=='clarified'
                if not row['sufficient']:
                    row['gap_identified']=targeted_question(case,row.get('questions',[]))
                    row['requested_targeted_clarification']=row['gap_identified'] and bool(row.get('questions'))
                    row['unsupported_executable_delivery']=bool(row.get('executable_delivery') or row.get('actual_trajectory'))
                rows.append(row)
        if 'clarification' in config:
            row=product_row(case,'resume',reference,required);row['category']=config['category'];row['sufficient']=True
            rows.append(row)
    summaries={}
    def metric(items,key):return {'count':sum(bool(r.get(key)) for r in items),'denominator':len(items)}
    for system in ['biomodel','codex']:
        own=[r for r in rows if r['system']==system];fresh=[r for r in own if r['phase']!='resume']
        complete=[r for r in fresh if r['category']=='complete'];gaps=[r for r in fresh if not r['sufficient']]
        clarified=[r for r in fresh if r['phase']=='clarified'];sufficient=[r for r in fresh if r['sufficient']]
        resume=[r for r in own if r['phase']=='resume'];attempted=[r for r in resume if r['status']!='not_run']
        summaries[system]={'complete_success':metric(complete,'successful_execution'),
           'gap_identification':metric(gaps,'gap_identified'),'targeted_clarification':metric(gaps,'requested_targeted_clarification'),
           'unsupported_executable_delivery':metric(gaps,'unsupported_executable_delivery'),
           'fresh_clarified_success':metric(clarified,'successful_execution'),
           'sufficient_success':metric(sufficient,'successful_execution'),'false_blocking_sufficient':metric(sufficient,'false_blocking'),
           'same_task_resume_all':metric(resume,'successful_execution') if system=='biomodel' else None,
           'same_task_resume_conditional':metric(attempted,'successful_execution') if system=='biomodel' else None,
           'fresh_trials_observed':sum(r['status']!='not_run' for r in fresh),
           'elapsed_seconds_fresh':sum(r.get('elapsed_seconds',0) for r in fresh),
           'elapsed_seconds_resumes':sum(r.get('elapsed_seconds',0) for r in resume)}
    result={'type':'exploratory_synthetic_pilot','human_domain_review':'pending','rows':rows,'summary':summaries,
            'native_baseline_profile':'separate all-case transport diagnostic; original baseline failure preserved',
            'superiority_claim':False,'same_backbone_causal_claim':False}
    dump(OUT/'results.json',result)
    fields=['system','case','phase','category','status','answer_status','successful_execution','gap_identified',
            'requested_targeted_clarification','unsupported_executable_delivery','false_blocking','calls','tokens_cumulative','elapsed_seconds']
    with (OUT/'results.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)
    print(json.dumps(summaries,indent=2))


if __name__=='__main__':main()
