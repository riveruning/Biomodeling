"""Verify the final T08a delivery and save a snapshot/unified patch without Git."""
from datetime import datetime, timezone
import difflib
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    baseline = load(OUT/'baseline-hashes.json')
    differences = [p for p, value in baseline.items() if sha(ROOT/p) != value]
    assert differences == [], differences
    current_product = {str(p.relative_to(ROOT)) for p in (ROOT/'src').rglob('*.py')}
    assert current_product == {p for p in baseline if p.startswith('src/')}
    original_tests = {p for p in baseline if p.startswith('tests/')}
    assert {str(p.relative_to(ROOT)) for p in (ROOT/'tests').rglob('*.py')} == original_tests | {'tests/test_agent_evaluation_protocol.py'}
    spec = importlib.util.spec_from_file_location('t08_final_audit',ROOT/'benchmarks/development/agent_evaluation.py')
    ev = importlib.util.module_from_spec(spec); spec.loader.exec_module(ev)
    manifest = load(OUT/'frozen-plan/manifest.json')
    plan_audit = ev.validate_manifest(manifest,ROOT)
    assert plan_audit == {'cases':3,'families':1,'planned_trials':30,'real_papers':0,
                          'independent_review':'pending','semantic_support':'unknown'}
    projections = list((OUT/'frozen-plan/trial-inputs').glob('*.json'))
    assert len(projections) == 30
    for path in projections:
        projection = load(path)
        assert 'private_rubric' not in path.read_text(encoding='utf-8')
        assert projection['trial']['manifest_sha256'] == ev.digest(manifest)
        if projection['trial']['phase'] == 'initial':
            if projection['trial']['case_id'] == 'translation':
                assert 'Initial P = 0.2.' not in str(projection)
            if projection['trial']['case_id'] == 'logistic':
                assert 'The alternative is rejected.' not in str(projection)
    controls = load(OUT/'final-controls/controls.json')
    assert controls['status'] == 'passed'
    assert controls['actual_model_calls'] == controls['actual_scientific_tool_calls'] == controls['actual_human_review_decisions'] == 0
    scorer_hash = sha(ROOT/'benchmarks/development/agent_evaluation.py')
    for name in ('nominal','adverse','missing'):
        implementation = load(OUT/f'final-controls/{name}-results/implementation.json')
        assert implementation['scorer_sha256'] == scorer_hash
        result = load(OUT/f'final-controls/{name}-results/results.json')
        assert len(result['rows']) == 30 and result['status'] == 'scripted_protocol_control'
        assert all(s['planned_trials'] == 10 for s in result['summary'].values())
    verification = {}
    for name in ('baseline','first','expanded','focused','final-check','full','full-final'):
        suites = ET.parse(OUT/f'{name}.xml').getroot().iter('testsuite')
        verification[name] = {k:0 for k in ('tests','failures','errors','skipped')}
        verification[name]['time'] = 0.0
        for suite in suites:
            for key in ('tests','failures','errors','skipped'):
                verification[name][key] += int(suite.attrib.get(key,0))
            verification[name]['time'] += float(suite.attrib.get('time',0))
    assert verification['baseline']['errors'] == 2
    assert verification['first']['failures'] == 1
    assert verification['final-check']['tests'] == 47
    assert verification['full-final']['tests'] == 1122
    for name in ('expanded','focused','final-check','full','full-final'):
        assert verification[name]['errors'] == verification[name]['failures'] == verification[name]['skipped'] == 0

    backlog = ROOT/'docs/optimization-backlog.md'
    text = backlog.read_text(encoding='utf-8')
    text = text.replace('T08a离线协议已实现，最终回归待验收；T08b真实适配待做，整体未结项',
                        'T08a离线协议已验收；T08b真实适配待做，整体未结项')
    text = text.replace('现有产品源码、原测试和历史评测器未修改；三套显式控制不执行模型或科学工具',
                        '最终专项47项、完整1122项通过；现有产品源码、原测试和历史评测器未修改；三套显式控制不执行模型或科学工具')
    backlog.write_text(text,encoding='utf-8')
    doc = ROOT/'docs/agent-evaluation-protocol.md'
    text = doc.read_text(encoding='utf-8').replace('首轮完整 1121 项通过，最终完整结果见',
        f"首轮完整 1121 项通过，最终完整 **1122 项通过**（{verification['full-final']['time']:.2f} 秒），详情见")
    doc.write_text(text,encoding='utf-8')
    files = ['benchmarks/development/agent_evaluation.py','tests/test_agent_evaluation_protocol.py',
             'docs/agent-evaluation-protocol.md','docs/optimization-backlog.md','docs/seven-point-status.md',
             'tasks/audit-t08a-evaluation-protocol.json']
    patch = []
    for name in files:
        path = ROOT/name; snapshot = OUT/'final'/name
        snapshot.parent.mkdir(parents=True,exist_ok=True); snapshot.write_bytes(path.read_bytes())
        before = OUT/'before'/name
        patch += difflib.unified_diff(before.read_text(encoding='utf-8').splitlines(keepends=True) if before.exists() else [],
            path.read_text(encoding='utf-8').splitlines(keepends=True),
            fromfile='before/'+name if before.exists() else '/dev/null', tofile='final/'+name)
    (OUT/'changes.patch').write_text(''.join(patch),encoding='utf-8')
    acceptance = {
        'task_id':'T08a','parent_task':'T08','status':'engineering_accepted',
        'parent_status':'live_adapters_independent_tasks_and_comparisons_pending',
        'accepted_at_utc':datetime.now(timezone.utc).isoformat(),'implementation_owner':'Codex alone',
        'dsh_or_subagents_used':False,'previous_accepted_full_regression':1075,'new_tests':47,
        'verification':verification,'frozen_original_files_checked':len(baseline),
        'modified_original_product_tests_or_evaluators':differences,
        'product_implementation_sha256':manifest['toolchain_sha256'],
        'protocol':ev.PROTOCOL,'plan_audit':plan_audit,'manifest_sha256':ev.digest(manifest),
        'scorer_sha256':scorer_hash,'isolated_trial_input_files':len(projections),
        'actual_model_calls':0,'actual_scientific_tool_calls':0,'actual_human_review_decisions':0,
        'controls':'final-controls/controls.json','control_rows_per_set':30,'control_sets':['nominal','adverse','missing'],
        'changed_files':files,'final_hashes':{name:sha(ROOT/name) for name in files},
        'diff':'changes.patch','task_card':'../../tasks/audit-t08a-evaluation-protocol.json',
        'execution_plan':'execution-plan.md','T07_status':'independent_review_and_real_paper_comparison_pending',
        'limits':['Offline manifest and attributed record scorer only; no three-workflow runtime adapters or budget enforcement.',
                 'All inputs are seen synthetic development controls, one family, zero papers and pending independent review.',
                 'Model settings, telemetry, reviewer identity and assessment evidence are declared rather than authenticated.',
                 'Generated observations, execution flags, judgment passes, usage and human time are explicit fixture values, not real runs.',
                 'No new scientific/model performance result, module ablation, independent evalset access, paper ingestion or c13/c18 transmission.'],
        'next_task':'T08b: implement three actual workflow adapters with common tool access, accounting and isolated inputs; verify offline before any applicable authorized live calls.'}
    ev._write(OUT/'acceptance.json',acceptance)
    link_count = 0
    for name in ('docs/agent-evaluation-protocol.md','docs/optimization-backlog.md','docs/seven-point-status.md'):
        path = ROOT/name
        for target in re.findall(r'\[[^\]]+\]\(([^)]+)\)',path.read_text(encoding='utf-8')):
            if '://' not in target and not target.startswith('#'):
                assert (path.parent/target.split('#',1)[0]).exists(), (name,target)
                link_count += 1
    ev._write(OUT/'delivery-hashes.json',{str(p.relative_to(OUT)):sha(p) for p in OUT.rglob('*')
        if p.is_file() and p.name not in ('delivery-hashes.json','finalize.log')})
    print(json.dumps({'status':'engineering_accepted','original_files_unchanged':len(baseline),
                      'new_tests':47,'full_final':verification['full-final'],'markdown_links_checked':link_count,
                      'model_calls':0,'T07_and_T08_overall_complete':False}))


if __name__ == '__main__':
    main()
