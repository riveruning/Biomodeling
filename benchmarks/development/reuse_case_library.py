"""Offline curated case reuse demonstrations, not live Agent/evaluation runs."""
import argparse
import json
from pathlib import Path
from biomodel_agent import case_library as cases,bundles,reporting,evidence
from biomodel_agent.storage import ArtifactStore


def save(path,value):path.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+'\n')


def main(output):
 output=Path(output);output.mkdir(parents=True,exist_ok=False);rows=[]
 for entry in cases.search():
  folder=output/entry['id'];folder.mkdir();store=ArtifactStore(folder/'store')
  loaded=cases.load(store,entry['id']);material=cases.validate_loaded(store,loaded)
  run=cases.run(store,loaded);result=store.get(run)['payload']
  expected='blocked_missing_initials' if entry['id']=='goodwin-original-initials-unknown' else 'numeric_diagnostic_pass'
  assert result['status']==expected
  if result['run_ref']:(folder/'run-report.md').write_text(reporting.render_report(store,result['run_ref']))
  manifest=bundles.export_bundle(store,loaded,folder/'material-bundle')
  detached=ArtifactStore(folder/'material-bundle');repeat=cases.run(detached,loaded);again=detached.get(repeat)['payload']
  assert again['status']==expected
  bundles.export_bundle(store,run,folder/'run-bundle')
  row=dict(case_id=entry['id'],family=entry['family'],version=entry['version'],material_ref=loaded,
    case_run_ref=run,run_ref=result['run_ref'],status=result['status'],detached_case_run_ref=repeat,
    detached_status=again['status'],source_refs=material['source_refs'],objects_and_blobs=len(manifest['objects_and_blobs']),
    scientific_support='unknown',independent_review='pending')
  save(folder/'receipt.json',row);rows.append(row)
 # Explicit candidate controls use only existing loaded model context and a new
 # analyst-labelled synthetic note; nothing is published or searched by default.
 folder=output/'fba-analytic-chain';store=ArtifactStore(folder/'store');row=next(r for r in rows if r['case_id']=='fba-analytic-chain')
 material=cases.validate_loaded(store,row['material_ref']);sources=material['source_refs']
 kwargs=dict(proposed_version='1.0.1',summary='Reusable fixed-network context candidate.',applicability='Original chain only; no biological applicability claim.',author='Codex development control, not an independent reviewer')
 first=cases.propose(store,row['material_ref'],sources,**kwargs)
 assert cases.propose(store,row['material_ref'],sources,**kwargs)==first
 kwargs['author']='Second explicit development actor';duplicate=cases.propose(store,row['material_ref'],sources,**kwargs)
 assert first in store.get(duplicate)['payload']['duplicates']
 note=folder/'synthetic-new-note.txt';note.write_text('Explicit synthetic analyst note: a different bound could be studied. No change adopted.\n')
 source=evidence.ingest_source(store,note)
 kwargs.update(proposed_version='1.0.0',summary='Unadopted alternative-bound proposal.',author='Codex explicit development control')
 conflict=cases.propose(store,row['material_ref'],[source],**kwargs)
 kwargs['summary']='Different unadopted applicability proposal.'
 other=cases.propose(store,row['material_ref'],[source],**kwargs)
 assert conflict in store.get(other)['payload']['conflicts']
 assert len(cases.search())==4
 candidates=[dict(ref=r,**store.get(r)['payload']) for r in (first,duplicate,conflict,other)]
 save(output/'candidate-controls.json',dict(candidates=candidates,searchable=False,activation='not_implemented',operator_or_domain_reviews_created=0))
 save(output/'summary.json',dict(evidence_kind='explicit_numeric_development_reuse',cases=rows,
  entries=4,goodwin_family_entries=2,live_model_calls=0,independent_domain_reviews=0,
  warning='Cases, families and repeated runs are not independent benchmark samples. References are numeric checks, not author data or a scientific certificate.'))
 print(json.dumps({r['case_id']:r['status'] for r in rows},indent=2))


if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True,type=Path);args=p.parse_args();main(args.output)
