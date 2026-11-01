"""Curated reuse cases, immutable material snapshots and quarantined proposals."""
from copy import deepcopy
from pathlib import Path
import json
import shutil

import pytest
from biomodel_agent import case_library as cases,evidence
from biomodel_agent.storage import ArtifactStore

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def library(tmp_path_factory):
 store=ArtifactStore(tmp_path_factory.mktemp('case-library')/'store')
 refs={row['id']:cases.load(store,row['id']) for row in cases.search()}
 return store,refs


def test_catalog_has_four_entries_but_shared_goodwin_family():
 rows=cases.search();assert len(rows)==4
 assert sum(r['family']=='goodwin2013' for r in rows)==2
 assert all(r['scientific_support']=='unknown' and r['independent_review']=='pending' for r in rows)
 assert len(cases.search('fba'))==2 and len(cases.search('goodwin'))==2
 assert cases.search('0.8739215069684307')==[]


def test_search_reads_metadata_only(monkeypatch):
 seen=[];original=cases._read
 def read(root,name,limit=2_000_000):
  seen.append(name);assert name==cases.CATALOG
  return original(root,name,limit)
 monkeypatch.setattr(cases,'_read',read)
 assert cases.search('goodwin')
 assert seen==[cases.CATALOG]


@pytest.mark.parametrize('ident',list(cases.CARDS))
def test_selected_case_loads_and_validates(library,ident):
 store,refs=library;p=cases.validate_loaded(store,refs[ident])
 assert p['card']['id']==ident and p['purpose']=='explicit_new_task_model_reuse'
 assert p['source_refs'] and p['files']
 for source in p['source_refs']:assert store.get(source)['kind']=='source'


def test_load_is_idempotent_and_executes_nothing(library,monkeypatch):
 from biomodel_agent import application,fba
 def refuse(*args,**kwargs):raise AssertionError('loading must not execute')
 monkeypatch.setattr(application,'run_reproduction',refuse);monkeypatch.setattr(fba,'run',refuse)
 store,refs=library
 for ident,ref in refs.items():assert cases.load(store,ident)==ref


def test_strict_case_has_no_exploratory_model_reference_or_initial_values(library,monkeypatch):
 from biomodel_agent import application,fba
 def refuse(*args,**kwargs):raise AssertionError('strict missing case executed')
 monkeypatch.setattr(application,'run_reproduction',refuse);monkeypatch.setattr(fba,'run',refuse)
 store,refs=library;p=cases.validate_loaded(store,refs['goodwin-original-initials-unknown'])
 assert p['model_ref'] is None and p['card']['recipe']['reference'] is None
 assert not any(n.endswith('reference.json') or n.endswith('model.xml') for n in p['files'])
 for ref in p['source_refs']:
  text=store.get(ref)['payload']['pages'][0]['text']
  assert 'Initial X = 0.1' not in text and 'Initial Y = 0.2' not in text
 result=store.get(cases.run(store,refs['goodwin-original-initials-unknown']))['payload']
 assert result['status']=='blocked_missing_initials' and result['run_ref'] is None
 assert len(result['missing_inputs'])==3


@pytest.mark.parametrize('ident',['goodwin-exploratory','fba-analytic-chain','fba-textbook'])
def test_clean_loaded_cases_perform_real_numeric_reproduction(library,ident):
 store,refs=library;ref=cases.run(store,refs[ident]);p=store.get(ref)['payload']
 assert p['status']=='numeric_diagnostic_pass'
 assert store.get(p['run_ref'])['kind']==('run' if ident.startswith('goodwin') else 'fba_run')
 assert p['scientific_support']=='unknown' and p['independent_review']=='pending'


@pytest.mark.parametrize('version',['2.0.0','latest','',None])
def test_unknown_version_never_falls_back(version):
 with pytest.raises(ValueError):cases.inspect('goodwin-exploratory',version)


def test_unknown_id_does_not_choose_nearest_match():
 with pytest.raises(ValueError):cases.inspect('goodwin')


def copied(tmp_path,ident):
 card=cases.inspect(ident);root=tmp_path/'project'
 for name in [cases.CATALOG,*card['files']]:
  dest=root/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes((ROOT/name).read_bytes())
 return root,card


def test_altered_catalog_refused_before_injected_path_opened(tmp_path,monkeypatch):
 root=tmp_path/'project';(root/Path(cases.CATALOG).parent).mkdir(parents=True)
 value=json.loads((ROOT/cases.CATALOG).read_text());value['cases'][0]['files']['../../biomodel_agent_evalset/never-read']={}
 (root/cases.CATALOG).write_text(json.dumps(value))
 seen=[];original=cases._read
 def read(root,name,limit=2_000_000):seen.append(name);return original(root,name,limit)
 monkeypatch.setattr(cases,'_read',read)
 with pytest.raises(ValueError,match='pinned'):cases.catalog(root)
 assert seen==[cases.CATALOG]


def test_changed_case_file_and_symlink_refused(tmp_path):
 root,card=copied(tmp_path,'fba-analytic-chain');name=card['recipe']['model'];path=root/name
 path.write_bytes(path.read_bytes()+b'\n');store=ArtifactStore(tmp_path/'store')
 with pytest.raises(ValueError,match='hash'):cases.load(store,'fba-analytic-chain',root=root)
 path.unlink();path.symlink_to(ROOT/name)
 with pytest.raises(ValueError,match='symlink'):cases.load(store,'fba-analytic-chain',root=root)
 assert not store.list('case_material')


@pytest.mark.parametrize('change',['card','file','sources','model','review','dependencies'])
def test_changed_snapshot_cannot_run(library,change,tmp_path):
 store,refs=library;obj=store.get(refs['fba-analytic-chain']);p=obj['payload']
 if change=='card':p['card']['recipe']['config']['reference']={'objective':999,'source':'invented'}
 elif change=='file':p['files'][p['card']['recipe']['model']]=store.put_blob(b'changed')
 elif change=='sources':p['source_refs']=[]
 elif change=='model':p['model_ref']=cases.validate_loaded(store,refs['fba-textbook'])['model_ref']
 elif change=='review':p['scientific_support']='supported'
 else:obj['refs']=obj['refs'][:-1]
 obj['refs']=sorted(set(p['files'].values())|set(p['source_refs'])|({p['model_ref']} if p['model_ref'] else set())) if change in ('file','sources','model') else obj['refs']
 forged=store.put('case_material',p,refs=obj['refs'])
 with pytest.raises(ValueError):cases.run(store,forged)


def test_snapshot_runs_after_export_without_catalog_or_original_files(library,tmp_path,monkeypatch):
 from biomodel_agent.bundles import export_bundle
 store,refs=library;ref=refs['fba-analytic-chain'];export_bundle(store,ref,tmp_path/'bundle')
 detached=ArtifactStore(tmp_path/'bundle')
 def refuse(*args,**kwargs):raise AssertionError('loaded snapshot must not read original files')
 monkeypatch.setattr(cases,'_read',refuse)
 assert detached.get(cases.run(detached,ref))['payload']['status']=='numeric_diagnostic_pass'


def test_reference_answers_are_not_source_refs(library):
 store,refs=library
 for ident,ref in refs.items():
  p=cases.validate_loaded(store,ref);recipe=p['card']['recipe'];reference=recipe.get('reference')
  if reference:
   assert all(store.get(s)['payload']['blob_ref']!=p['files'][reference] for s in p['source_refs'])


def test_knowledge_candidates_remain_quarantined_and_duplicate_idempotent(library,tmp_path):
 store,refs=library;material=refs['fba-analytic-chain'];source=cases.validate_loaded(store,material)['source_refs'][0]
 args=dict(proposed_version='1.0.1',summary='Existing source proposed as reusable note.',applicability='Only the fixed chain network.',author='Explicit unit fixture, not an expert')
 before=cases.search();ref=cases.propose(store,material,[source],**args)
 assert cases.propose(store,material,[source],**args)==ref
 p=store.get(ref)['payload'];assert p['searchable'] is False and p['status']=='pending_operator_and_domain_review'
 assert p['scientific_support']=='unknown' and cases.search()==before
 args['author']='Second explicit fixture';other=cases.propose(store,material,[source],**args)
 assert ref in store.get(other)['payload']['duplicates']


def test_same_version_changed_material_and_conflicting_proposals_do_not_replace_case(library,tmp_path):
 store,refs=library;material=refs['fba-analytic-chain'];before=cases.validate_loaded(store,material)
 src=tmp_path/'note.txt';src.write_text('Explicit new analyst statement; not scientific approval.')
 source=evidence.ingest_source(store,src)
 args=dict(proposed_version='1.0.0',summary='Altered applicability candidate A',applicability='Development comparison only.',author='Explicit fixture')
 first=cases.propose(store,material,[source],**args);p=store.get(first)['payload']
 assert p['version_warning']=='same_version_different_material'
 args['summary']='Different claim B';second=cases.propose(store,material,[source],**args)
 assert first in store.get(second)['payload']['conflicts']
 assert cases.validate_loaded(store,material)==before


def test_result_or_reference_artifact_cannot_be_a_knowledge_source(library):
 store,refs=library;p=cases.validate_loaded(store,refs['fba-analytic-chain'])
 with pytest.raises(ValueError,match='source artifacts'):
  cases.propose(store,refs['fba-analytic-chain'],[p['model_ref']],proposed_version='1.0.1',summary='not a source',applicability='fixture',author='fixture')


def test_cli_search_inspect_load_and_blocked_run(tmp_path,capsys):
 from biomodel_agent.cli import main
 assert main(['case-search','--query','goodwin'])==0
 assert len(json.loads(capsys.readouterr().out))==2
 assert main(['case-inspect','--id','goodwin-original-initials-unknown'])==0
 assert json.loads(capsys.readouterr().out)['recipe']['initials']['X'] is None
 assert main(['case-load','--id','goodwin-original-initials-unknown','--store',str(tmp_path/'store')])==0
 ref=json.loads(capsys.readouterr().out)['ref']
 assert main(['case-run','--material-ref',ref,'--store',str(tmp_path/'store')])==2
 assert json.loads(capsys.readouterr().out)['status']=='blocked_missing_initials'


def test_same_raw_xml_different_sections_are_not_duplicate_knowledge(library,tmp_path):
 from biomodel_agent import jats_sources
 store,refs=library;xml=tmp_path/'sections.xml';xml.write_text('<article><sec id="left"><p>left</p></sec><sec id="right"><p>right</p></sec></article>')
 left=jats_sources.ingest(store,xml,'left');right=jats_sources.ingest(store,xml,'right')
 assert store.get(left)['payload']['blob_ref']==store.get(right)['payload']['blob_ref']
 args=dict(proposed_version='scope-control',summary='Scope matters',applicability='Explicit selected source only',author='Fixture analyst')
 first=cases.propose(store,refs['fba-analytic-chain'],[left],**args)
 second=cases.propose(store,refs['fba-analytic-chain'],[right],**args)
 candidate=store.get(second)['payload']
 assert first not in candidate['duplicates'] and first in candidate['conflicts']


def test_candidates_cannot_enter_catalog_by_forging_an_approval_field(library):
 store,refs=library;p=cases.validate_loaded(store,refs['fba-analytic-chain'])
 ref=cases.propose(store,refs['fba-analytic-chain'],p['source_refs'],proposed_version='fake-publish-control',summary='Not a published case',applicability='Fixture only',author='Fixture')
 obj=store.get(ref);obj['payload']['status']='approved';obj['payload']['searchable']=True
 store.put('case_candidate',obj['payload'],refs=obj['refs'])
 assert cases.search('Not a published case')==[]
 assert len(cases.search())==4


def test_cli_proposal_does_not_modify_a_task_or_activate_sources(tmp_path,capsys):
 from biomodel_agent.cli import main
 path=tmp_path/'store'
 assert main(['case-load','--id','fba-analytic-chain','--store',str(path)])==0
 loaded=json.loads(capsys.readouterr().out)
 assert main(['case-propose','--material-ref',loaded['ref'],'--source-ref',loaded['source_refs'][0],
  '--proposed-version','1.0.1','--summary','CLI pending note','--applicability','Fixed chain only','--author','CLI fixture','--store',str(path)])==0
 p=json.loads(capsys.readouterr().out)
 assert p['searchable'] is False and p['status']=='pending_operator_and_domain_review'
 assert not ArtifactStore(path).list('agent_task')


def test_goodwin_archive_preserves_raw_object_json_files_as_blobs(library,tmp_path):
 from biomodel_agent.bundles import export_bundle
 store,refs=library;ref=refs['goodwin-exploratory'];p=cases.validate_loaded(store,ref)
 overlapping=[blob for blob in p['files'].values() if (store.objects_dir/(blob+'.json')).exists()]
 assert overlapping
 export_bundle(store,ref,tmp_path/'bundle')
 detached=ArtifactStore(tmp_path/'bundle')
 assert cases.validate_loaded(detached,ref)['model_ref']==p['model_ref']
 for blob in overlapping:assert detached.get_blob(blob)==store.get_blob(blob)
 assert detached.get(cases.run(detached,ref))['payload']['status']=='numeric_diagnostic_pass'


def test_case_bundle_explains_correct_new_run_command(library,tmp_path):
 from biomodel_agent.bundles import export_bundle
 store,refs=library;ref=refs['fba-analytic-chain']
 manifest=export_bundle(store,ref,tmp_path/'bundle')
 assert 'case-run --material-ref '+ref in manifest['replay']
 text=(tmp_path/'bundle/REPLAY.md').read_text()
 assert '--material-ref '+ref in text and 'does not resume' in text


def test_case_run_bundle_requires_its_material_in_the_closure(library,tmp_path):
 from biomodel_agent.bundles import export_bundle
 store,refs=library;ref=store.put('case_run',{'material_ref':refs['fba-analytic-chain']},refs=())
 with pytest.raises(ValueError,match='closure'):export_bundle(store,ref,tmp_path/'invalid-bundle')
 assert not (tmp_path/'invalid-bundle').exists()
