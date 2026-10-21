"""Attach failure causes and separated diagnostic summaries without changing frozen scoring."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

from prepare_reliability import ROOT, OUT as INITIAL, dump
import score_reliability as scorer

SERIAL=ROOT/'artifacts/reliability-20261017-serial-diagnostic'


def cause(folder,row):
    if not (folder/'result.json').exists():return 'not_run'
    record=json.loads((folder/'result.json').read_text())
    events=json.loads((folder/'agent.json').read_text())['events'] if (folder/'agent.json').exists() else []
    errors=' '.join(e.get('error','') for e in events)+' '+record.get('error','')
    if '429' in errors:return 'provider_rate_limit'
    if 'transport failure' in errors or 'provider_http_' in errors:return 'provider_transport_or_http'
    if 'not a single JSON object' in errors:return 'invalid_action_json'
    if 'model response' in errors:return 'model_protocol_error'
    if row.get('successful_execution'):return 'successful_execution'
    if row.get('gap_detected'):return 'specific_gap_identified'
    if row.get('unsupported_completion'):return 'unjustified_model_or_completion'
    if record.get('status')=='adapter_or_provider_failed':return 'adapter_or_provider_failure'
    if row.get('numeric',{}).get('status') in ['fail','invalid_trajectory']:return 'numerically_wrong'
    if any(e.get('problems') for e in events):return 'draft_contract_or_evidence_failure'
    if 'Antimony:' in errors:return 'antimony_conversion_failure'
    return record.get('status','unknown')


def audit(base,results,systems):
    observations=[]; summaries={}
    for system in systems:
        rows=[r for r in results['rows'] if r['system']==system]
        for row in rows:
            folder=base/system/row['case']/row['phase'];category=cause(folder,row)
            observations.append({'system':system,'case':row['case'],'phase':row['phase'],'cause':category})
        own=[o for o in observations if o['system']==system]
        by_id={(r['case'],r['phase']):r for r in rows}
        available=[by_id[o['case'],o['phase']] for o in own if o['cause'] not in ['not_run','provider_rate_limit','provider_transport_or_http','adapter_or_provider_failure']]
        blocked=[r for r in available if not r['expected_execution']]
        usage=Counter()
        for r in rows:
            file=base/system/r['case']/r['phase']/'result.json'
            if file.exists():
                for k,v in json.loads(file.read_text()).get('usage',{}).items():
                    if isinstance(v,int):usage[k]+=v
        summaries[system]={'causes':dict(Counter(o['cause'] for o in own)),
          'available_trials':len(available),'planned_trials':len(rows),
          'available_blocked_cases':len(blocked),
          'gap_detection_among_available':sum(r.get('gap_detected',False) for r in blocked),
          'unsupported_completion_among_available':sum(r.get('unsupported_completion',False) for r in blocked),
          'native_usage_totals':dict(usage) or None}
    dump(base/'failure-audit.json',{'observations':observations,'summary':summaries,
         'availability_note':'Availability-filtered denominators are descriptive; original fixed-denominator results remain intact. Invalid model JSON is an operational agent failure, not an external outage.'})
    return summaries


def main():
    original=json.loads((INITIAL/'results.json').read_text())
    initial=audit(INITIAL,original,['biomodel','codex','kinmodgpt'])
    if SERIAL.exists():
        scorer.OUT=SERIAL
        protocol=json.loads((SERIAL/'protocol.json').read_text())
        rows=[scorer.score_row(s,n,p,c,{}) for s in ['biomodel','kinmodgpt'] for n,c in protocol['cases'].items() for p in c['phases']]
        summary=scorer.aggregate(rows)
        serial={'rows':rows,'summary':{s:summary[s] for s in ['biomodel','kinmodgpt']},
                'context':'Separate all-case diagnostic, never replaces initial trials; no Codex rerun.'}
        dump(SERIAL/'results.json',serial)
        audit(SERIAL,serial,['biomodel','kinmodgpt'])
        print(json.dumps({'serial_summary':serial['summary']},ensure_ascii=False,indent=2))
    print(json.dumps({'initial_causes':initial},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
