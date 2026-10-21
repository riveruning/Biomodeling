"""Check real adapter artifacts, retain limitations and snapshot a small delivery."""
from datetime import datetime, timezone
import difflib
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from biomodel_agent.storage import ArtifactStore

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[1]


def read(path): return json.loads(path.read_text(encoding='utf-8'))
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def write(path,value): path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def main():
    frozen=read(OUT/'baseline-hashes.json')
    changed=[p for p,h in frozen.items() if sha(ROOT/p)!=h]
    assert not changed,changed
    old_tests={p for p in frozen if p.startswith('tests/')}
    assert {str(p.relative_to(ROOT)) for p in (ROOT/'tests').rglob('*.py')}==old_tests|{'tests/test_workflow_adapters.py'}
    assert {str(p.relative_to(ROOT)) for p in (ROOT/'src').rglob('*.py')}=={p for p in frozen if p.startswith('src/')}
    spec=importlib.util.spec_from_file_location('t08b_final_driver',ROOT/'benchmarks/development/run_agent_comparison.py')
    driver=importlib.util.module_from_spec(spec); spec.loader.exec_module(driver)
    run=OUT/'development-execution'; manifest=read(run/'manifest.json')
    driver.protocol.validate_manifest(manifest,ROOT)
    implementations=read(run/'implementation.json')
    assert implementations['adapter_sha256']==sha(ROOT/'benchmarks/development/workflow_adapters.py')
    assert implementations['driver_sha256']==sha(ROOT/'benchmarks/development/run_agent_comparison.py')
    assert implementations['scorer_sha256']==sha(ROOT/'benchmarks/development/agent_evaluation.py')
    engineering=read(run/'engineering.json'); scientific=read(OUT/'scientific-results.json')
    assert engineering['planned_trials']==engineering['finished_records']==30
    assert engineering['provider_requests']==58 and engineering['online_inference_calls']==0
    assert engineering['observed_states']=={'completed':12,'awaiting_input':18}
    assert scientific['actual_reproduction_runs']==scientific['convergence_fine_runs']==12
    assert scientific['clarified_phases_blocked_by_shared_grammar']==6
    assert scientific['human_review_decisions']==0
    assert any(not r['initial_exploratory_2e_6_check_satisfied'] for r in scientific['scientific_runs'])
    records=read(run/'records.json'); receipts=read(run/'receipts.json')
    by_id={t['id']:t for t in driver.protocol.schedule(manifest)}
    total_requests=0
    for r,receipt in zip(records,receipts):
        driver.protocol._validate_record(manifest,by_id[r['trial_id']],r)
        assert r['assessment']['authority']=='unreviewed'
        store=ArtifactStore(receipt['store_root'])
        for ref in receipt['audit']['request_refs']:
            request=store.get(ref)['payload']['request']; total_requests+=1
            assert request['model']==manifest['model_config']['model']
            assert request['temperature']==manifest['model_config']['temperature']
            assert request['top_p']==manifest['model_config']['top_p']
            assert request['seed']==manifest['model_config']['seed']
            assert request['max_tokens']==manifest['model_config']['max_output_tokens']
            text=json.dumps(request,ensure_ascii=False)
            assert 'private_rubric' not in text
            if r['trial_id'].endswith('initial'):
                assert 'Initial P = 0.2.' not in text and 'The alternative is rejected.' not in text
    assert total_requests==58
    results=read(run/'results.json')
    assert len(results['rows'])==30
    assert all(s['planned_trials']==10 and s['correct_completion']['numerator']==0 and
               s['input_tokens']['total_if_complete'] is None for s in results['summary'].values())
    verification={}
    for name in ('baseline','first','parity-baseline','parity-fixed','expanded','stabilized','extended','focused-final','full'):
        summary={k:0 for k in ('tests','failures','errors','skipped')}; summary['time']=0.
        for suite in ET.parse(OUT/f'{name}.xml').getroot().iter('testsuite'):
            for k in ('tests','failures','errors','skipped'): summary[k]+=int(suite.attrib.get(k,0))
            summary['time']+=float(suite.attrib.get('time',0))
        verification[name]=summary
    assert verification['baseline']['errors']==4 and verification['parity-baseline']['failures']==3
    for name in ('first','stabilized','extended','focused-final','full'):
        assert verification[name]['failures']==verification[name]['errors']==verification[name]['skipped']==0
    assert verification['focused-final']['tests']==97 and verification['full']['tests']==1172
    backlog=ROOT/'docs/optimization-backlog.md'
    text=backlog.read_text(encoding='utf-8').replace('T08b执行适配已实现，待完整回归验收','T08b执行适配已验收')
    text=text.replace('T08b已实现固定流程、通用工具Agent和产品领域Agent的执行适配。',
                      'T08b已验收固定流程、通用工具Agent和产品领域Agent的执行适配，最终定向97项、完整1172项通过。')
    backlog.write_text(text,encoding='utf-8')
    doc=ROOT/'docs/workflow-adapters.md'
    text=doc.read_text(encoding='utf-8').replace('完整回归及补丁见',f"完整 **1172 项通过**（{verification['full']['time']:.2f} 秒），源码差异见")
    doc.write_text(text,encoding='utf-8')
    files=['benchmarks/development/workflow_adapters.py','benchmarks/development/run_agent_comparison.py',
           'tests/test_workflow_adapters.py','docs/workflow-adapters.md','docs/agent-evaluation-protocol.md',
           'docs/optimization-backlog.md','docs/seven-point-status.md','tasks/audit-t08b-workflow-adapters.json']
    patch=[]
    for name in files:
        path=ROOT/name; target=OUT/'final'/name; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(path.read_bytes())
        before=OUT/'before'/name
        patch.extend(difflib.unified_diff(before.read_text(encoding='utf-8').splitlines(keepends=True) if before.exists() else [],
            path.read_text(encoding='utf-8').splitlines(keepends=True),fromfile='before/'+name if before.exists() else '/dev/null',tofile='final/'+name))
    (OUT/'changes.patch').write_text(''.join(patch),encoding='utf-8')
    acceptance={'task_id':'T08b','status':'engineering_accepted','parent_task':'T08','parent_status':'actual_model_comparison_and_independent_review_pending',
        'accepted_at_utc':datetime.now(timezone.utc).isoformat(),'implementation_owner':'Codex alone','dsh_or_subagents_used':False,
        'previous_accepted_full_tests':1122,'new_test_cases':50,'verification':verification,
        'frozen_original_files_unchanged':len(frozen),'modified_original_product_tests_or_T08a_code':changed,
        'product_implementation_sha256':manifest['toolchain_sha256'],'actual_execution':engineering,
        'scientific_diagnostics':'scientific-results.json','analytic_exploratory_absolute_check_passed':False,
        'analytic_check_failure_retained':'scientific-results-first.log','new_precision_pass_threshold_adopted':False,
        'native_domain_agent_and_resume_used':True,'injected_script_decisions_not_model_capability':True,
        'all_assessments_unreviewed':True,'actual_human_review_decisions':0,
        'followup':'T08b-F1: bounded intended-equation declaration grammar; then shared authorized online transport and actual model comparison',
        'execution_plan':'execution-plan.md','frozen_control_inputs':'control-inputs','execution_artifacts':'development-execution',
        'diff':'changes.patch','changed_files':files,'final_hashes':{p:sha(ROOT/p) for p in files},
        'limits':['No online model client created or called; injected transports receive exact common payloads, but provider identity is not independently authenticated.',
                  'All material remains seen synthetic development data with pending independent review; T07/T08 are not scientifically complete.',
                  'Six clarified logistic phases preserve a shared binding grammar limit; no material rewriting, rule relaxation or positive relabeling.',
                  'Literal scripted decisions, actual numeric/backend checks and independently reviewed model performance are separate evidence.',
                  'Initial exploratory 2e-6 absolute analytic check failed on translation; actual coarse/refined errors reported with no post hoc replacement pass threshold.',
                  'POSIX fork supervision, reported/unknown tokens, trusted Python transports and operation-level tool charging have the documented bounds.',
                  'No new papers, dependencies, credentials, independent evalset or c13/c18 access; no rejected USTC transmission retried.']}
    write(OUT/'acceptance.json',acceptance)
    link_count=0
    for name in ('docs/workflow-adapters.md','docs/agent-evaluation-protocol.md','docs/optimization-backlog.md','docs/seven-point-status.md'):
        path=ROOT/name
        for target in re.findall(r'\[[^\]]+\]\(([^)]+)\)',path.read_text(encoding='utf-8')):
            if '://' not in target and not target.startswith('#'):
                assert (path.parent/target.split('#',1)[0]).exists(),(name,target)
                link_count+=1
    write(OUT/'delivery-hashes.json',{str(p.relative_to(OUT)):sha(p) for p in OUT.rglob('*')
        if p.is_file() and p.name not in ('delivery-hashes.json','finalize.log')})
    print(json.dumps({'status':'engineering_accepted','original_files_unchanged':len(frozen),
        'new_tests':50,'full':verification['full'],'actual_runs':12,'online_calls':0,'shared_grammar_pauses':6,
        'exploratory_abs_check_passed':False,'links_checked':link_count},ensure_ascii=False))


if __name__=='__main__': main()
