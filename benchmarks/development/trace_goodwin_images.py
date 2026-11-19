"""Offline seen Goodwin image-gap/visual-provenance diagnostic; no OCR/LLM.

Candidate text follows the eight already saved images. It is an analyst visual
pre-review, not independent expert truth or an automatic extraction result.
"""
import argparse
import json
from pathlib import Path
from hashlib import sha256
from biomodel_agent import jats_sources as jats, evidence, bindings, modeling, bundles
from biomodel_agent.storage import ArtifactStore
from biomodel_agent.domain.contracts import ModelDraft, State, Parameter, RateRule
from biomodel_agent.domain.serialization import to_dict

ROOT=Path(__file__).resolve().parents[2]
CASE=ROOT/'benchmarks/inputs/literature/gonze2013'
VISUAL={
 'e004':'Equation dX/dt = k1*Ki**n/(Ki**n+Z**n)-k2*X.',
 'e005':'Equation dY/dt = k3*X-k4*Y.',
 'e006':'Equation dZ/dt = k5*Y-k6*Z.',
 'e010':'Hill function f(Z) = Ki**n/(Ki**n+Z**n).',
 'e021':'Parameter k1 = 1.0.\nParameter k3 = 1.0.\nParameter k5 = 1.0.',
 'e022':'Parameter k2 = 0.1.\nParameter k4 = 0.1.\nParameter k6 = 0.1.',
 'e023':'Parameter Ki = 1.0.',
 'e024':'Parameter n = 10.0.',
}
VALUES={'k1':1.,'k2':.1,'k3':1.,'k4':.1,'k5':1.,'k6':.1,'Ki':1.,'n':10.}
RULES={'X':'k1*Ki**n/(Ki**n+Z**n)-k2*X','Y':'k3*X-k4*Y','Z':'k5*Y-k6*Z'}


def save(path,value):path.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+'\n')


def main(output):
 output=Path(output);output.mkdir(parents=True,exist_ok=False);store=ArtifactStore(output/'store')
 asset_paths={'info:doi/10.1371/journal.pone.0069573.'+i:'equations/'+i+'.tiff' for i in VISUAL}
 source=jats.ingest(store,CASE/'article.xml','s2a',asset_paths);projection=jats.validate(store,source)
 entries=[]
 for gap in projection['gaps']:
  ident=gap['href'].split('.')[-1] if gap['href'] else ''
  if ident in VISUAL:entries.append(dict(gap_id=gap['id'],asset_ref=gap['asset']['blob_ref'],text=VISUAL[ident]))
 derived=jats.transcribe(store,source,entries,'Codex development visual pre-review; not independent',
  'Viewed existing PNG copies; original TIFF blobs retained. Ki denotes K_i; ** denotes exponentiation; chained equalities split into separate Parameter declarations; 1/10 rendered 0.1; no initials supplied. Independent review pending.')
 payload=store.get(derived)['payload'];text=payload['pages'][0]['text'];raw=projection['pages'][0]['text']
 (output/'native-section.txt').write_text(raw);(output/'visual-candidate-section.txt').write_text(text)
 save(output/'assets.json',asset_paths);save(output/'candidates.json',entries)
 expected={**{k+'/value':v for k,v in VALUES.items()},**{'rule:'+k+'/expression':v for k,v in RULES.items()}}
 names=set(VALUES)|set('XYZ');claims={};checks=[]
 for target,value in expected.items():
  if target.endswith('/value'):
   name=target.split('/')[0];quote=f'Parameter {name} = {value}.'
  else:
   name=target.split(':')[1].split('/')[0];quote=f'Equation d{name}/dt = {value}.'
  start=text.index(quote)
  claim=evidence.add_claim(store,derived,1,start,start+len(quote),target,expected=value,allowed_symbols=names)
  record=store.get(claim)['payload'];assert evidence._claim_exists_quoted(store,claim)
  claims[target]=[claim]
  before=bindings.check(raw,source,1,0,len(raw),target,value,names)
  checks.append(dict(target=target,native_status=before['status'],candidate_status=record['binding']['status'],
                     claim_ref=claim,visual_anchors=record['locator']['visual_anchors'],semantic_support='unknown'))
 save(output/'binding-checks.json',checks)
 draft=ModelDraft('goodwin_visual_candidates',states=tuple(State(n,None) for n in 'XYZ'),
  parameters=tuple(Parameter(n,v,origin='analyst_visual_candidate') for n,v in VALUES.items()),
  rate_rules=tuple(RateRule(n,r) for n,r in RULES.items()),time_unit='unknown',element_map=claims)
 report=modeling.validate_draft(draft)
 assert not report.can_execute and sum(c.code=='initial_missing' for c in report.checks)==3
 draft_ref=store.put('model_draft',to_dict(draft),refs=tuple(c[0] for c in claims.values()))
 save(output/'draft-validation.json',to_dict(report));bundles.export_bundle(store,draft_ref,output/'bundle')
 manifest={str(p.relative_to(ROOT)):sha256(p.read_bytes()).hexdigest() for p in [CASE/'article.xml',CASE/'raw-text.txt',CASE/'assisted-transcription.txt',*[CASE/'equations'/(i+ext) for i in VISUAL for ext in ('.tiff','.png')]]}
 receipt=dict(source_ref=source,derived_source_ref=derived,draft_ref=draft_ref,input_sha256=manifest,
  diagnostic='seen development mechanical binding/readability control; not LLM extraction accuracy',
  native_gap_count=len(projection['gaps']),formula_image_gaps=sum(g['kind']=='image_formula' for g in projection['gaps']),
  figure_image_gaps=sum(g['kind']=='image_figure' for g in projection['gaps']),local_formula_assets=len(entries),
  candidate_blocks=len(entries),native_matched=sum(c['native_status']=='matched' for c in checks),candidate_matched=sum(c['candidate_status']=='matched' for c in checks),binding_targets=len(checks),
  missing_original_initials=list('XYZ'),author_absence_claim=False,model_execution='blocked_missing_initials',
  ocr='not_run',formula_recognition='not_run',independent_review='pending',scientific_support='unknown',actual_model_calls=0)
 save(output/'receipt.json',receipt);print(json.dumps(receipt,indent=2))


if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True,type=Path);args=p.parse_args();main(args.output)
