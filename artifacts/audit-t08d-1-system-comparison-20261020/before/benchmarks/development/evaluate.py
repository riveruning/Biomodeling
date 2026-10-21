"""Frozen execution / synthetic mechanism tracks; LLM superiority is not inferred."""
from pathlib import Path
import json
import argparse
from biomodel_agent import application,hypotheses,reporting
from biomodel_agent.domain.contracts import ModelArtifact,ExperimentSpec,SimulationResult
from biomodel_agent.storage import ArtifactStore

ROOT=Path(__file__).resolve().parents[2]


def evaluate(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    store=ArtifactStore(output/'research-store')
    manifest=json.loads((ROOT/'benchmarks/manifests/public-models.json').read_text())
    rows=[]
    for model in manifest['models']:
        mid=model['id']
        path=ROOT/model.get('execution_variant',model)['path']
        gold=json.loads((ROOT/'benchmarks/development/references'/f'{mid}.json').read_text())
        artifact=ModelArtifact(mid,path.read_text())
        experiment=ExperimentSpec(gold['times'])
        ref=application.run_reproduction(store,artifact,experiment,
            reference=SimulationResult('succeeded',gold['times'],gold['values']),
            scales=gold['scales'],threshold=gold['threshold'],use_cache=False)
        p=store.get(ref)['payload']
        rows.append({'id':mid,'run_ref':ref,'execution':p['status'],
                     'comparison':p['comparison'], 'scientific_status':p['scientific_status'],
                     'source_support':p['source_validation']['semantic_support'],
                     'input_variant':model.get('execution_variant',{}).get('scientific_mode','original_curated_sbml')})
        (output/f'{mid}.md').write_text(reporting.render_report(store,ref))
    training=json.loads((ROOT/'benchmarks/inputs/adaptation/training.json').read_text())
    holdout=json.loads((ROOT/'benchmarks/inputs/adaptation/holdout.json').read_text())
    study_ref=hypotheses.run_hypothesis_study(store,hypotheses.adaptation_candidates(),training,holdout)
    study=store.get(study_ref)['payload']
    (output/'adaptation-study.md').write_text(reporting.render_report(store,study_ref))
    # Reuse the same frozen fits to form the explicitly data-insufficient result.
    compatible=[n for n,r in study['candidates'].items() if r['training_weighted_rmse'] is not None and r['training_weighted_rmse']<=3]
    insufficient={'ranking':'indistinguishable_from_training' if len(compatible)>1 else 'none_or_single_compatible',
                  'training_compatible':compatible,
                  'note':'This training-only assessment reuses frozen training fits and excludes heldout scores.'}
    result={'schema_version':'1','execution_track':rows,'synthetic_b':{'study_ref':study_ref,
            'comparison':study['comparison'],'candidates':study['candidates'],
            'data_insufficient_case':insufficient},
            'literature_extraction_track':{'status':'not_run','note':'Public cases execute curated SBML; automatic paper extraction was not scored.'},
            'agent_architecture_baselines':{'status':'not_run','groups':['generic coding assistant','fixed scientific workflow','budgeted agent'],
                'note':'No paid controlled baseline runs; no demonstrated superiority claim.'}}
    (output/'evaluation.json').write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False))
    print(json.dumps({'models':[{'id':r['id'],'status':r['comparison']['status'],'error':r['comparison'].get('max_scaled_error')} for r in rows],
                     'study_ref':study_ref,'b_comparison':study['comparison'],'training_only':insufficient},indent=2))
    if any(r['comparison']['status']!='pass' for r in rows):
        raise SystemExit('An execution reference comparison failed')
    if (study['comparison']['ranking']!='best_compatible_candidate'
            or study['comparison']['winner']!='cand_feedforward'
            or not all(r['crosscheck']['ok'] for r in study['candidates'].values())
            or insufficient['ranking']!='indistinguishable_from_training'
            or set(compatible)!={'cand_feedback','cand_feedforward'}):
        raise SystemExit('Synthetic discrimination/abstention acceptance failed')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default=str(ROOT/'artifacts/v0.1'))
    evaluate(parser.parse_args().output)
