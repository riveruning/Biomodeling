"""Freeze and optionally run eight seen development cases through the product.

No independent evaluation material is read. Default preparation is offline.
Live mode uses BIOMODEL_* environment variables and never persists the key.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from biomodel_agent import evidence,fba,jats_sources,recovery,reporting
from biomodel_agent import research_projects as projects,research_supervisor as supervisor
from biomodel_agent.llm import ChatProvider
from biomodel_agent.storage import ArtifactStore


def definitions():
    native='benchmarks/inputs/native-plan-pilot-20261029/'
    paper='benchmarks/inputs/literature/gonze2013/'
    cases=[
        dict(id='production_loss',kind='ode',family='linear_kinetics',source=native+'production-loss.txt',expected='numeric_and_reference',material='synthetic_control'),
        dict(id='transfer',kind='ode',family='linear_kinetics',source=native+'transfer.txt',expected='numeric_and_reference',material='synthetic_control'),
        dict(id='missing_rate',kind='ode',family='linear_kinetics',source=native+'missing-rate.txt',expected='clarification_without_computation',material='synthetic_control'),
        dict(id='goodwin_jats',kind='ode',family='goodwin_feedback',source=paper+'pmc.xml',section='s2a',expected='unresolved_images_without_invented_values',material='paper'),
        dict(id='goodwin_assisted',kind='ode',family='goodwin_feedback',source=paper+'assisted-transcription.txt',expected='missing_initials_without_computation',material='assisted_paper_control'),
        dict(id='boolean_source',kind='boolean',family='logic_feedback',source='examples/task-proposals/boolean-source.txt',expected='source_bound_numeric',material='synthetic_control'),
        dict(id='ssa_source',kind='stochastic',family='linear_kinetics',source='examples/source-stochastic/birth-death.txt',expected='source_bound_numeric',material='synthetic_control'),
        dict(id='fba_prepared',kind='fba',family='stoichiometry',model='examples/fba-v1/chain.json',expected='prepared_hypothesis_numeric',material='synthetic_control'),
    ]
    for case in cases:
        case['paper_id']='PMC3731313' if case['family']=='goodwin_feedback' else 'synthetic:'+case['id']
        case['files']={name:hashlib.sha256((ROOT/case[name]).read_bytes()).hexdigest()
                       for name in ('source','model') if name in case}
    return cases


def analytic(case):
    times=[i/2 for i in range(21)]
    if case['id']=='production_loss':values={'p':[2-math.exp(-.3*t) for t in times]}
    elif case['id']=='transfer':
        values={'a':[2*math.exp(-.2*t) for t in times],'b':[3-2*math.exp(-.2*t) for t in times]}
    else:return None
    return dict(reference=dict(times=times,values=values,time_unit='second',
        backend='independent_analytic_control_from_explicit_source',status='succeeded'),
        scales={k:1. for k in values},threshold=.001,note='Frozen analytic development control; not independent domain review.')


def freeze(args):
    manifest=dict(version=1,partition='development',seen_material=True,source_commit='593affca82ee2b16afd798752d0623536fd4434c',
        implementation_sha256=recovery.implementation_fingerprint(),
        runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        provider=dict(base_url=os.environ.get('BIOMODEL_BASE_URL') if args.run_live else None,
            model=os.environ.get('BIOMODEL_MODEL') if args.run_live else None,timeout_s=45,max_tokens=args.max_tokens),
        policy=dict(planner_max_calls=8,max_iterations=1,max_native_calls=4,native_calls_per_iteration=4,max_literature_requests=0),
        definitions=definitions(),unique_papers=1,unique_families=4,
        scoring='Separate actual numerical/source/reference checks and qualitative pending review; no overall scientific success score.',
        independent_evalset_accessed=False,scientific_support='unknown')
    for case in manifest['definitions']:case['analytic_reference']=analytic(case)
    return manifest


def event(**data):print(json.dumps(data,ensure_ascii=False),flush=True)


def run_case(case,manifest,directory):
    store=ArtifactStore(directory/'store');sources=[];models=[];refs=[]
    if case.get('source'):
        path=ROOT/case['source']
        sources.append(jats_sources.ingest(store,path,case['section']) if case.get('section') else evidence.ingest_source(store,path))
    if case.get('model'):models.append(fba.import_model(store,ROOT/case['model']))
    if case['analytic_reference']:refs.append(projects.register_reference(store,case['analytic_reference'],'development_trial_operator'))
    scenario=('Reproduce the supplied explicit mathematical model using only authorized materials. '
        'Inspect materials and unresolved content before deciding. Never infer missing numerical values. '
        'Use the declared time unit. For complete ODE controls freeze samples from 0 to 10 seconds every 0.5 seconds '
        'and use the supplied reference. Explicit ODE decision plans execute directly. '
        'For Boolean use 4 sweeps and seed 123; for SSA use 2 seconds, 5 samples, 8 trajectories, seed 123. '
        'When source data are missing or inaccessible, ask the researcher a precise question and stop. '
        'Independent biological validation has not been performed.')
    mode='reproduction'
    if models:
        mode='application';scenario=('Run the prepared synthetic steady-state FBA network as an explicit hypothetical application. '
            'Reuse the supplied model; check actual numerical results, assess the frozen checks and conclude within scope. '
            'No source proof or biological support is claimed.')
    root=projects.create(store,dict(scenario=scenario,mode=mode,source_refs=sources,model_refs=models,
        reference_refs=refs,policy=case.get('policy',manifest['policy'])))
    config=manifest['provider']
    provider=ChatProvider(config['base_url'],config['model'],timeout_s=config['timeout_s'],max_tokens=config['max_tokens'])
    started=time.monotonic();error=None
    try:snapshot=supervisor.run(store,provider,root)
    except Exception as exc:
        error=type(exc).__name__;snapshot=projects.snapshot(store,root)
    view=projects.inspect(store,root)
    facts=[projects.facts(store,r) for r in view['iteration_refs']]
    attempts,returns,_=projects.history(store,root)
    diagnostics=[dict(operation=x['operation'],state=returns[r][1]['state'],error_type=returns[r][1].get('error_type'),diagnostic=returns[r][1].get('diagnostic'))
                 for r,x in attempts if r in returns and returns[r][1]['state']!='returned']
    invalid=[returns[r][1]['result'] for r,x in attempts if x['operation']=='invalid_response' and r in returns]
    result=dict(case_id=case['id'],material=case['material'],paper_id=case['paper_id'],family=case['family'],
        project_ref=root,snapshot_ref=snapshot,state=view['state'],expected_behavior=case['expected'],
        actual_iterations=facts,scientific_executions=sum(x['executed'] for x in facts),
        human_questions=[projects.own(store,r,projects.QUESTION,root)['question'] for r in view['pending_questions']],
        usage=supervisor.usage(store,root),elapsed_s=time.monotonic()-started,error_type=error,
        diagnostics=diagnostics,invalid_actions=invalid,semantic_review='pending',scientific_support='unknown')
    (directory/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (directory/'report.md').write_text(reporting.render_report(store,snapshot),encoding='utf-8')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--run-live',action='store_true')
    parser.add_argument('--cases',nargs='+',help='Subset of frozen development case IDs; never adds cases.')
    parser.add_argument('--max-tokens',type=int,default=3000)
    parser.add_argument('--prior-results',type=Path,help='Explicit new diagnostic projects, capped by remaining original per-case call allocations.')
    args=parser.parse_args()
    if not 256<=args.max_tokens<=8000:parser.error('max-tokens must be 256..8000')
    if args.run_live and not all(os.environ.get(k) for k in ('BIOMODEL_BASE_URL','BIOMODEL_MODEL','BIOMODEL_API_KEY')):
        parser.error('Set BIOMODEL_BASE_URL, BIOMODEL_MODEL and BIOMODEL_API_KEY before live mode.')
    manifest=freeze(args)
    ids={x['id'] for x in manifest['definitions']}
    if args.cases and (set(args.cases)-ids or len(set(args.cases))!=len(args.cases)):parser.error('Unknown or repeated case ID.')
    selected=[x for x in manifest['definitions'] if not args.cases or x['id'] in args.cases]
    manifest['unique_papers']=len({x['paper_id'] for x in selected if x['material'] in ('paper','assisted_paper_control')})
    manifest['unique_families']=len({x['family'] for x in selected})
    if args.prior_results:
        prior={x['case_id']:x for x in json.loads(args.prior_results.read_text(encoding='utf-8'))}
        manifest['prior_results_sha256']=hashlib.sha256(args.prior_results.read_bytes()).hexdigest()
        manifest['phase']='explicit_diagnostic_followup_preserving_original_results'
        for case in selected:
            old=prior.get(case['id'])
            if old is None:parser.error('Every followup case needs an original result.')
            used=old['usage']['planner_calls'];native=old['usage']['native_provider_calls']
            if type(used) is not int or type(native) is not int or not 0<=used<8 or not 0<=native<4:
                parser.error('No safely known remaining call allocation for '+case['id'])
            case['prior_usage']=old['usage'];case['prior_project_ref']=old['project_ref']
            case['policy']={**manifest['policy'],'planner_max_calls':8-used,
                'max_native_calls':4-native,'native_calls_per_iteration':4-native}
    manifest['selected_case_ids']=[x['id'] for x in selected]
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    shutil.copytree(ROOT/'src/biomodel_agent',args.output/'source_snapshot'/'biomodel_agent',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copy2(Path(__file__),args.output/'runner.py')
    event(event='frozen',cases=manifest['selected_case_ids'],live=args.run_live)
    results=[]
    if args.run_live:
        for case in selected:
            event(event='case_started',case=case['id'])
            directory=args.output/case['id'];directory.mkdir()
            result=run_case(case,manifest,directory);results.append(result)
            event(event='case_finished',case=case['id'],state=result['state'],calls=result['usage']['planner_calls'],
                  scientific_executions=result['scientific_executions'])
            (args.output/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    event(event='finished',executed_cases=len(results),prepared_cases=len(selected))


if __name__=='__main__':main()
