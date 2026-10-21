"""Score already-seen development regression using the unchanged original rubric."""
from collections import Counter
import csv
import json
from pathlib import Path

from competitor_common import write_json
from prepare_reliability import ROOT
import score_reliability as original

OUT=ROOT/'artifacts/agent-reliability-fix-20261017/live-regression'
BASE=ROOT/'artifacts/reliability-20261017'


def score(allow_partial=False):
    protocol=json.loads((BASE/'protocol.json').read_text())
    original.OUT=OUT
    rows=[]
    for name,case in protocol['cases'].items():
        for phase in case['phases']:
            folder=OUT/'biomodel'/name/phase
            if not (folder/'result.json').exists():continue
            row=original.score_row('biomodel',name,phase,case,{})
            payload=json.loads((folder/'agent.json').read_text()) if (folder/'agent.json').exists() else {}
            events=payload.get('events',[])
            row['retry_count']=payload.get('retry_count',0)
            row['protocol_errors_observed']=sum(e.get('type')=='provider_error' and e.get('kind')=='model_response' for e in events)
            row['model_questions']=sum(e.get('type')=='awaiting_input' and e.get('origin','model')=='model' for e in events)
            row['validation_questions']=sum(e.get('type')=='awaiting_input' and e.get('origin')=='validation' for e in events)
            if row.get('successful_execution'):cause='successful_execution'
            elif row.get('gap_detected'):cause='specific_gap_identified'
            elif row.get('unsupported_completion'):cause='unjustified_execution'
            elif events and events[-1].get('type')=='provider_error':cause=events[-1].get('kind','provider_error')
            elif events and events[-1].get('type')=='retry_deferred':cause='provider_cooldown_deferred'
            else:cause=row['status']
            row['cause']=cause;rows.append(row)
    if len(rows)!=18 and not allow_partial:raise RuntimeError(f'Only {len(rows)}/18 trials completed; do not report a final result')
    if allow_partial:
        write_json(OUT/'completed-case-checks.json',rows)
        for row in rows:print(row['case'],row['phase'],row['status'],row['numeric']['status'],row['cause'])
        return rows
    summary=original.aggregate(rows)['biomodel']
    result={'rows':rows,'summary':summary,'diagnostics':{
        'causes':dict(Counter(r['cause'] for r in rows)),
        'retries':sum(r['retry_count'] for r in rows),
        'protocol_errors_observed':sum(r['protocol_errors_observed'] for r in rows),
        'terminal_protocol_failures':sum(r['cause']=='model_response' for r in rows),
        'model_questions':sum(r['model_questions'] for r in rows),
        'validation_questions':sum(r['validation_questions'] for r in rows)},
        'rubric':'unchanged score_reliability.py, already-seen synthetic development regression',
        'holdout_accessed':False,'fresh_session_clarified_rebuild_not_resume':True}
    write_json(OUT/'results.json',result)
    fields=['system','case','phase','category','status','cause','successful_execution','unsupported_completion','gap_detected','retry_count','protocol_errors_observed','calls','tokens','elapsed_seconds']
    with (OUT/'results.csv').open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    print(json.dumps({'summary':summary,'diagnostics':result['diagnostics']},indent=2,ensure_ascii=False))
    return result


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--partial',action='store_true')
    parser.add_argument('--run-dir',type=Path,default=OUT)
    args=parser.parse_args();OUT=args.run_dir.resolve()
    if not OUT.is_relative_to(ROOT/'artifacts'):
        raise ValueError('Development inputs must be under project artifacts')
    score(args.partial)
