"""Frozen development queue, failure denominators and inactive cross-case lessons."""
from copy import deepcopy
import json

import pytest
from biomodel_agent import bundles,reporting,research_pipeline as batch,research_projects as projects
from biomodel_agent.storage import ArtifactStore
from test_fba import Scripted,act


def config():
    def case(name,paper,family):
        return dict(id=name,paper_id='synthetic:'+paper,material_kind='synthetic_control',family=family,
            project=dict(scenario='Reproduce a synthetic development case.',mode='reproduction',
                         policy=dict(planner_max_calls=2,max_native_calls=6,max_iterations=1)))
    return dict(name='Synthetic development controls',cases=[case('first','paper1','familyA'),case('repeat','paper1','familyA'),
                                                             case('different','paper2','familyB')],lessons_max_calls=1)


def provider():
    return Scripted([act('conclude',outcome='insufficient_evidence',summary='The source does not provide an initial value.',
                        assessment_refs=[],limitations=['No scientific execution.'])])


def test_batch_runs_distinct_cases_once_and_never_calls_a_provider_on_repeated_command(tmp_path):
    store=ArtifactStore(tmp_path/'store');root=batch.create(store,config());providers=[]
    def make(case):
        p=provider();providers.append(p);return p
    result=batch.run(store,root,make)
    view=batch.inspect(store,root)
    assert view['case_count']==3 and view['unique_paper_count']==0 and view['unique_material_count']==2
    assert view['unique_family_count']==2 and view['negative_or_gap_cases']==3 and view['numeric_execution_count']==0
    assert view['failure_stages']=={'evidence':3}
    batch.run(store,root,lambda _:pytest.fail('a repeated batch command must not start another model request'))
    assert len(providers)==3 and sum(len(p.requests) for p in providers)==3
    original=reporting.render_report(store,result)
    bundles.export_bundle(store,result,tmp_path/'bundle')
    assert reporting.render_report(ArtifactStore(tmp_path/'bundle'),result)==original


@pytest.mark.parametrize('field,value',[('planner_calls',1),('native_calls',1),('iterations',1)])
def test_global_allocations_reject_before_any_child_is_created(tmp_path,field,value):
    store=ArtifactStore(tmp_path/'s');spec=config();spec['budget']={field:value}
    with pytest.raises(ValueError,match='frozen batch budget'):batch.create(store,spec)
    assert not store.list(projects.PROJECT)


@pytest.mark.parametrize('partition',['validation','heldout','independent_test'])
def test_experience_pipeline_refuses_non_development_partitions(tmp_path,partition):
    store=ArtifactStore(tmp_path/'s');spec=config();spec['partition']=partition
    with pytest.raises(ValueError,match='development'):batch.create(store,spec)


def test_declared_papers_have_identifiers_and_synthetic_runs_do_not_count_as_papers(tmp_path):
    store=ArtifactStore(tmp_path/'s');spec=config();spec['cases']=spec['cases'][:1]
    spec['cases'][0].update(material_kind='paper',paper_id='PMC4383902')
    root=batch.create(store,spec);assert batch.inspect(store,root)['unique_paper_count']==1
    spec['cases'][0]['paper_id']='Paper described by a model'
    with pytest.raises(ValueError,match='explicit DOI'):batch.create(store,spec)


def test_cross_case_lessons_are_attributed_candidates_not_activated_memory(tmp_path):
    store=ArtifactStore(tmp_path/'s');root=batch.create(store,config());batch.run(store,root,lambda _:provider())
    lesson=dict(statement='Inspect source completeness before attempting a mathematical reconstruction.',
        case_ids=['first','repeat','different'],conditions='Only the seen synthetic missing-initial controls.',
        validation_plan='Evaluate the workflow on additional development families before adoption.')
    model=Scripted([dict(lessons=[lesson])]);refs=batch.lessons(store,root,model)
    saved=store.get(refs[0])['payload']
    assert saved['unique_families']==2 and saved['status']=='candidate_pending_validation' and not saved['active']
    assert saved['scientific_support']=='unknown'
    assert batch.lessons(store,root,Scripted([]))==refs and len(model.requests)==1
    assert not store.list('memory') and not store.list('knowledge')


def test_invalid_lesson_is_saved_but_not_requested_again(tmp_path):
    store=ArtifactStore(tmp_path/'s');root=batch.create(store,config());batch.run(store,root,lambda _:provider())
    model=Scripted([dict(lessons=[dict(statement='Unsupported.',case_ids=['not_a_case'],conditions='Unknown.',validation_plan='None.')])])
    with pytest.raises(ValueError,match='absent cases'):batch.lessons(store,root,model)
    assert len(model.requests)==1 and not store.list(batch.EXPERIENCE)
    with pytest.raises(ValueError,match='absent cases'):batch.lessons(store,root,Scripted([]))


def test_unknown_case_effect_is_recorded_without_secret_text_and_not_replayed(tmp_path):
    store=ArtifactStore(tmp_path/'s');spec=config();spec['cases']=spec['cases'][:1];root=batch.create(store,spec)
    model=Scripted([ValueError('private-credential')]);batch.run(store,root,lambda _:model)
    row=batch.inspect(store,root)['cases'][0]
    assert row['state']=='needs_recovery' and row['failure_stage']=='unknown_effect'
    assert 'private-credential' not in ''.join(p.read_text() for p in store.objects_dir.glob('*.json'))
    batch.run(store,root,lambda _:pytest.fail('must not retry unknown effects'))


def test_all_lessons_validate_before_any_candidate_is_written(tmp_path):
    store=ArtifactStore(tmp_path/'s');root=batch.create(store,config());batch.run(store,root,lambda _:provider())
    good=dict(statement='Check completeness first.',case_ids=['first'],conditions='Development controls.',validation_plan='Test more families.')
    bad={**good,'case_ids':['absent']};model=Scripted([dict(lessons=[good,bad])])
    with pytest.raises(ValueError,match='absent cases'):batch.lessons(store,root,model)
    assert not store.list(batch.EXPERIENCE) and len(model.requests)==1


def test_confirmed_lesson_response_recovers_partial_local_write_without_new_request(tmp_path,monkeypatch):
    store=ArtifactStore(tmp_path/'s');root=batch.create(store,config());batch.run(store,root,lambda _:provider())
    first=dict(statement='Check completeness first.',case_ids=['first'],conditions='Development controls.',validation_plan='Test more families.')
    second={**first,'statement':'Preserve unsupported semantics.','case_ids':['different']}
    model=Scripted([dict(lessons=[first,second])]);original=store.put;count=0
    def fail(kind,payload,refs=None):
        nonlocal count
        if kind==batch.EXPERIENCE:
            count+=1
            if count==2:raise KeyboardInterrupt('local write interrupted')
        return original(kind,payload,refs)
    monkeypatch.setattr(store,'put',fail)
    with pytest.raises(KeyboardInterrupt):batch.lessons(store,root,model)
    monkeypatch.setattr(store,'put',original)
    assert len(batch.lessons(store,root,Scripted([])))==2 and len(store.list(batch.EXPERIENCE))==2 and len(model.requests)==1

