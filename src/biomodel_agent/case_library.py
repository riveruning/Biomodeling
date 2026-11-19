"""Pinned, on-demand seen-development case reuse. Never a benchmark answer index.

Loading creates an immutable snapshot for an explicit new task. It does not run
anything, mutate current tasks, select a scientifically applicable case, or
activate quarantined knowledge candidates.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
from uuid import uuid4
from .storage import canonical_bytes

ROOT=Path(__file__).resolve().parents[2]
VERSION='1.0.0'
CATALOG='examples/case-library/catalog.json'
CATALOG_SHA='fab9762e1252700c9ce09e555a8dac8c15adf7762402b19e17e886c5ad610acd'
CARDS={
 'goodwin-exploratory':'598424e687e0b9baa2e0b340c8dd774dc94cef9f3122d98a41a771fdd9a371f5',
 'goodwin-original-initials-unknown':'775717ec9844321c741fe9f0e8f7063a365e43eb0deb8f591d831f9d8c14688f',
 'fba-analytic-chain':'39a33e866421d4ea08949771823eeaf125d986c69d6c9a74136ce745acd13158',
 'fba-textbook':'2c3485c97b8d06d4d13394727ce3582924c2ff9286668f2da41ec24cfadf00c6',
}


def digest(value):return sha256(canonical_bytes(value)).hexdigest()


def _text(value,name,limit=2000):
 if not isinstance(value,str) or not value.strip() or len(value)>limit:raise ValueError('invalid '+name)
 return value


def _decode(raw):
 def unique(pairs):
  result={}
  for k,v in pairs:
   if k in result:raise ValueError('duplicate JSON key')
   result[k]=v
  return result
 value=json.loads(raw,object_pairs_hook=unique);canonical_bytes(value)
 return value


def _read(root,name,limit=2_000_000):
 rel=Path(name)
 if rel.is_absolute() or '..' in rel.parts:raise ValueError('case path must be project-relative')
 p=Path(root)
 for part in rel.parts:
  p/=part
  if p.is_symlink():raise ValueError('symlink case material is unsupported')
 if not p.is_file() or p.stat().st_size>limit:raise ValueError('case material missing or over budget')
 raw=p.read_bytes()
 if len(raw)>limit:raise ValueError('case material grew over budget')
 return raw


def _card(card):
 if not isinstance(card,dict) or card.get('id') not in CARDS or card.get('version')!=VERSION or digest(card)!=CARDS[card['id']]:
  raise ValueError('unknown or changed case version; no silent replacement')
 return card


def catalog(root=ROOT):
 raw=_read(root,CATALOG)
 if sha256(raw).hexdigest()!=CATALOG_SHA:raise ValueError('catalog changed from pinned version')
 value=_decode(raw)
 if value.get('library_version')!=VERSION or value.get('schema_version')!=1 or {c['id'] for c in value['cases']}!=set(CARDS):raise ValueError('unsupported case catalog')
 for c in value['cases']:_card(c)
 return value


def search(query='',root=ROOT):
 if not isinstance(query,str) or len(query)>200:raise ValueError('invalid bounded case query')
 tokens=query.casefold().split();rows=[]
 for c in catalog(root)['cases']:
  # No source/model/reference file is opened or indexed by search.
  haystack=' '.join([c['id'],c['title'],c['summary'],*c['tags']]).casefold()
  if tokens and not all(t in haystack for t in tokens):continue
  rows.append({k:deepcopy(c[k]) for k in ('id','version','model_type','title','family','summary','known_gaps','purpose','exposure','independent_review','scientific_support')})
 return rows


def inspect(case_id,version=VERSION,root=ROOT):
 if version!=VERSION:raise ValueError('unknown case version')
 for c in catalog(root)['cases']:
  if c['id']==case_id:return deepcopy(c)
 raise ValueError('unknown curated case')


def _source(store,raw,filename):
 blob=store.put_blob(raw)
 return store.put('source',dict(blob_ref=blob,filename=filename,url=None,parser='utf8',parser_version='1',
  pages=[dict(number=1,text=raw.decode('utf-8'))],warnings=['Curated model-reuse input, not independent paper support; reference answers are separate.',
   'No automatic addition to an existing task or formal evaluation.']),refs=(blob,))


def _copy_bundle(store,card,files):
 manifest=card['recipe']['source_bundle_manifest'];prefix=str(Path(manifest).parent)
 bundle=_decode(files[manifest]);allowed=set(bundle['objects_and_blobs'])
 if len(allowed)>64 or bundle['root_ref']!=card['recipe']['model_ref']:raise ValueError('invalid case model closure')
 seen=set();active=set();sources=[]
 def copy(ref):
  if ref in seen:return
  if ref not in allowed or ref in active:raise ValueError('case closure escaped or cycled')
  active.add(ref);name=prefix+'/objects/'+ref+'.json'
  if name in files:
   obj=_decode(files[name])
   if digest(obj)!=ref:raise ValueError('case source object content hash changed')
   for child in obj['refs']:copy(child)
   result=store.put(obj['kind'],obj['payload'],refs=obj['refs'])
   if obj['kind']=='source':sources.append(result)
  else:
   result=store.put_blob(files[prefix+'/blobs/'+ref])
  if result!=ref:raise ValueError('case dependency content hash changed')
  seen.add(ref);active.remove(ref)
 copy(bundle['root_ref'])
 if seen!=allowed:raise ValueError('extra or incomplete case closure')
 return bundle['root_ref'],sorted(sources)


def load(store,case_id,version=VERSION,root=ROOT):
 card=inspect(case_id,version,root);files={};total=0
 for name,record in card['files'].items():
  raw=_read(root,name);total+=len(raw)
  if sha256(raw).hexdigest()!=record['sha256'] or len(raw)!=record['bytes'] or total>16_000_000:raise ValueError('case material version/hash/size changed')
  files[name]=raw
 # All selected files pass before any source/model is exposed as a case.
 blobs={name:store.put_blob(raw) for name,raw in files.items()};recipe=card['recipe'];kind=recipe['kind']
 if kind=='goodwin_ode_v1':
  model,sources=_copy_bundle(store,card,files)
  if store.get(model)['payload']['sbml'].encode()!=files[recipe['model']]:raise ValueError('case executable differs from source-bound model')
 elif kind=='goodwin_missing_initials_v1':
  from . import jats_sources as jats
  with tempfile.TemporaryDirectory(prefix='biomodel-case-jats-') as temp:
   temp=Path(temp);(temp/'article.xml').write_bytes(files[recipe['xml']]);(temp/'equations').mkdir()
   assets={}
   for ident,name in recipe['images'].items():
    relative='equations/'+Path(name).name;(temp/relative).write_bytes(files[name]);assets['info:doi/10.1371/journal.pone.0069573.'+ident]=relative
   original=jats.ingest(store,temp/'article.xml',recipe['section'],assets)
  projection=jats.validate(store,original);visual=_decode(files[recipe['visual_candidates']]);entries=[]
  for g in projection['gaps']:
   ident=g['href'].split('.')[-1] if g['href'] else ''
   if ident in visual['by_image']:entries.append(dict(gap_id=g['id'],asset_ref=g['asset']['blob_ref'],text=visual['by_image'][ident]))
  visual_ref=jats.transcribe(store,original,entries,visual['actor'],visual['notes']);model=None;sources=[original,visual_ref]
 elif kind in ('fba_json_v1','fba_fbc_v1'):
  from . import fba
  with tempfile.TemporaryDirectory(prefix='biomodel-case-fba-') as temp:
   path=Path(temp)/'model.input';path.write_bytes(files[recipe['model']]);model=fba.import_model(store,path,format='json' if kind=='fba_json_v1' else 'fbc')
  sources=[_source(store,files[recipe['model']],Path(recipe['model']).name)]
 else:raise ValueError('unsupported case recipe')
 payload=dict(version=1,library_version=VERSION,card=card,files=blobs,model_ref=model,source_refs=sources,
  purpose='explicit_new_task_model_reuse',scientific_support='unknown',independent_review='pending',
  warnings=['Loading executes nothing and changes no existing task.', 'Loaded reference files are not source_refs or searchable text.',
            'Case matching and provenance hashes do not establish scientific applicability.'])
 refs=set(blobs.values())|set(sources)|({model} if model else set())
 ref=store.put('case_material',payload,refs=tuple(sorted(refs)))
 validate_loaded(store,ref)
 return ref


def validate_loaded(store,ref):
 obj=store.get(ref);p=obj['payload']
 if obj['kind']!='case_material' or p.get('version')!=1 or p.get('library_version')!=VERSION:raise ValueError('expected a supported immutable case snapshot')
 card=_card(p['card'])
 if set(p['files'])!=set(card['files']) or p.get('scientific_support')!='unknown' or p.get('independent_review')!='pending' or p.get('purpose')!='explicit_new_task_model_reuse':raise ValueError('case snapshot scope/status changed')
 refs=set(p['files'].values())|set(p['source_refs'])|({p['model_ref']} if p['model_ref'] else set())
 if set(obj['refs'])!=refs:raise ValueError('case snapshot dependencies changed')
 for name,blob in p['files'].items():
  raw=store.get_blob(blob);record=card['files'][name]
  if blob!=record['sha256'] or len(raw)!=record['bytes']:raise ValueError('case snapshot file changed')
 from . import evidence
 for source in p['source_refs']:
  evidence._source_page_text(store,source,1)
 recipe=card['recipe']
 if recipe['kind']=='goodwin_ode_v1':
  if p['model_ref']!=recipe['model_ref'] or store.get(p['model_ref'])['payload']['sbml'].encode()!=store.get_blob(p['files'][recipe['model']]):raise ValueError('case ODE model changed')
  prefix=str(Path(recipe['source_bundle_manifest']).parent)+'/objects/'
  expected_sources=sorted(Path(name).stem for name,blob in p['files'].items() if name.startswith(prefix) and _decode(store.get_blob(blob))['kind']=='source')
  if p['source_refs']!=expected_sources:raise ValueError('case ODE sources escaped frozen closure')
 elif recipe['kind']=='goodwin_missing_initials_v1':
  from . import jats_sources as jats
  if p['model_ref'] is not None or len(p['source_refs'])!=2:raise ValueError('missing initials case cannot acquire an exploratory model')
  original,visual=p['source_refs'];jats.validate(store,visual)
  source=store.get(visual)['payload'];base=store.get(original)['payload'];t=store.get(source['transcription_ref'])['payload']
  if source['original_source_ref']!=original or base['blob_ref']!=p['files'][recipe['xml']]:raise ValueError('strict case source escaped frozen XML')
  projection=jats.validate(store,original);v=_decode(store.get_blob(p['files'][recipe['visual_candidates']]))
  ext=store.get(base['parser_output_ref'])['payload']
  expected_assets={'info:doi/10.1371/journal.pone.0069573.'+ident:dict(path='equations/'+Path(name).name,blob_ref=p['files'][name]) for ident,name in recipe['images'].items()}
  if not _same(ext['asset_bindings'],expected_assets):raise ValueError('strict case asset scope changed')
  expected=[]
  for g in projection['gaps']:
   ident=g['href'].split('.')[-1] if g['href'] else ''
   if ident in v['by_image']:
    if g['asset']['blob_ref']!=p['files'][recipe['images'][ident]]:raise ValueError('strict case image changed')
    expected.append(dict(gap_id=g['id'],asset_ref=g['asset']['blob_ref'],text=v['by_image'][ident]))
  if not _same(t['entries'],expected) or t['actor']!=v['actor'] or t['notes']!=v['notes']:raise ValueError('strict case candidates changed')
 else:
  from . import fba
  fba.load(store,p['model_ref'])
  if store.get(p['model_ref'])['payload']['source_blob_ref']!=p['files'][recipe['model']]:raise ValueError('case FBA model changed')
  if len(p['source_refs'])!=1:raise ValueError('case FBA source scope changed')
  source=store.get(p['source_refs'][0])['payload'];raw=store.get_blob(p['files'][recipe['model']])
  if source['blob_ref']!=p['files'][recipe['model']] or source['pages']!=[dict(number=1,text=raw.decode())]:raise ValueError('case FBA source changed')
 return p


def _same(a,b):return canonical_bytes(a)==canonical_bytes(b)


def run(store,material_ref):
 p=validate_loaded(store,material_ref);card=p['card'];recipe=card['recipe']
 if recipe['kind']=='goodwin_missing_initials_v1':
  return store.put('case_run',dict(version=1,material_ref=material_ref,status='blocked_missing_initials',
   run_ref=None,missing_inputs=['X/initial','Y/initial','Z/initial'],scientific_support='unknown',independent_review='pending'),refs=(material_ref,))
 reservation=store.put('case_invocation',dict(version=1,id=str(uuid4()),material_ref=material_ref,phase='reserved'),refs=(material_ref,))
 if recipe['kind']=='goodwin_ode_v1':
  from .application import run_reproduction,derive_scientific_status
  from .domain.contracts import ExperimentSpec,SimulationResult,ModelArtifact,SolverConfig
  from .domain.serialization import from_dict
  artifact=from_dict(ModelArtifact,store.get(p['model_ref'])['payload'])
  exp=from_dict(ExperimentSpec,_decode(store.get_blob(p['files'][recipe['experiment']])))
  reference=from_dict(SimulationResult,_decode(store.get_blob(p['files'][recipe['reference']])))
  result=run_reproduction(store,artifact,exp,solver=from_dict(SolverConfig,recipe['solver']),reference=reference,scales=recipe['scales'],threshold=recipe['threshold'],strict=True,use_cache=False,nonnegative=tuple('XYZ'))
  actual=store.get(result)['payload'];numeric=actual['status']=='succeeded' and actual['comparison']['status']=='pass' and derive_scientific_status(actual)!='failed'
 else:
  from . import fba
  settings={**recipe['config'],**_decode(store.get_blob(p['files'][recipe['reference']]))}
  result=fba.run(store,p['model_ref'],settings);numeric=fba.summary(store,result)['completion_eligible']
 return store.put('case_run',dict(version=1,material_ref=material_ref,reservation_ref=reservation,
  status='numeric_diagnostic_pass' if numeric else 'numeric_diagnostic_failed',run_ref=result,
  scientific_support='unknown',independent_review='pending',note='Explicit new numeric run; neither source truth nor live Agent efficacy. No automatic retry or frozen-task continuation.'),refs=(material_ref,reservation,result))


def propose(store,material_ref,source_refs,*,proposed_version,summary,applicability,author):
 p=validate_loaded(store,material_ref)
 for name,value,limit in [('proposed_version',proposed_version,80),('summary',summary,2000),('applicability',applicability,2000),('author',author,200)]:_text(value,name,limit)
 if not isinstance(source_refs,list) or not 1<=len(source_refs)<=8 or len(set(source_refs))!=len(source_refs):raise ValueError('explicit unique source refs required')
 from . import evidence
 hashes=[]
 for ref in source_refs:
  source=store.get(ref)
  if source['kind']!='source':raise ValueError('knowledge candidates require ingested source artifacts, not model/reference/result answers')
  evidence._source_page_text(store,ref,1);blob=source['payload']['blob_ref'];store.get_blob(blob);hashes.append(blob)
 core=dict(version=1,material_ref=material_ref,case_id=p['card']['id'],base_version=VERSION,proposed_version=proposed_version,
  source_refs=sorted(source_refs),source_hashes=sorted(set(hashes)),summary=summary,applicability=applicability,author=author)
 core_hash=digest(core);duplicates=[];conflicts=[]
 for ref in store.list('case_candidate'):
  old=store.get(ref)['payload']
  if old['core_sha256']==core_hash:return ref
  if old['case_id']==core['case_id'] and old['proposed_version']==proposed_version:
   if old['source_refs']==core['source_refs'] and old['summary']==summary and old['applicability']==applicability:duplicates.append(ref)
   else:conflicts.append(ref)
 warning='same_version_different_material' if proposed_version==VERSION and set(source_refs)!=set(p['source_refs']) else None
 payload=dict(**core,core_sha256=core_hash,status='pending_operator_and_domain_review',searchable=False,
  duplicates=sorted(duplicates),conflicts=sorted(conflicts),version_warning=warning,
  scientific_support='unknown',note='Quarantined candidate only. No catalog update, source activation or independent approval; declared material roles are not authenticated.')
 return store.put('case_candidate',payload,refs=(material_ref,*sorted(source_refs),*sorted(set(duplicates+conflicts))))
