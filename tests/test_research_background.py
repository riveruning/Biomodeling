"""MM-to-Expt evidence reviews through the normal project operations."""
from copy import deepcopy
import httpx
import json
import pytest

from biomodel_agent import research_background as bg, research_projects as p
from biomodel_agent import research_literature as literature, research_schema, research_supervisor as supervisor
from biomodel_agent.storage import ArtifactStore
from biomodel_agent import bundles

METHOD = 'We measured growth in bacterial cultures.'
RESULT = 'Plasmids increased persistence under the tested conditions.'
CONTEXT = 'Co-infection can alter plasmid persistence.'


def source(store, doi, body, refs=''):
    raw = (f'<article><front><article-meta><title-group><article-title>Study</article-title></title-group>'
           f'<article-id pub-id-type="doi">{doi}</article-id></article-meta></front><body>{body}</body>'
           f'<back><ref-list>{refs}</ref-list></back></article>').encode()
    text, warnings = literature._extract(raw)
    blob = store.put_blob(raw)
    ref = store.put('source', dict(blob_ref=blob, filename='study.xml', parser=literature.VERSION,
        parser_version='1', pages=[dict(number=1,text=text)], warnings=warnings), refs=(blob,))
    return ref


def setup(tmp_path, literature_requests=0):
    store = ArtifactStore(tmp_path/'store')
    refs = '<ref id="R1"><element-citation><article-title>Experiment</article-title><pub-id pub-id-type="doi">10.1234/expt</pub-id></element-citation></ref>'
    mm = source(store, '10.1234/mm', f'<sec><title>Background</title><p>{CONTEXT}<xref ref-type="bibr" rid="R1"/></p></sec>', refs)
    expt = source(store, '10.1234/expt', f'<sec><title>Introduction</title><p>Prior authors proposed persistence effects.</p></sec><sec><title>Methods</title><p>{METHOD}</p></sec><sec><title>Results</title><p>{RESULT}</p></sec>')
    project = p.create(store, dict(scenario='Reproduce the stated plasmid study.', mode='reproduction', source_refs=[mm,expt],
        policy=dict(max_literature_requests=literature_requests)))
    return store,project,mm,expt


def anchor(ref,quote):
    return dict(source_ref=ref,page=1,quote=quote)


def arguments(index,mm,expt):
    return dict(index_ref=index, requirements=[dict(id='persistence',question='How does co-infection affect persistence?',mm_context=anchor(mm,CONTEXT))],
        reviews=[dict(reference_id='R1',source_ref=expt,study_type='experimental',mathematical_modeling='absent',fulltext_reviewed=True,
            rationale='Reviewed experimental methods and results with ordinary statistical analysis.',methods_evidence=[anchor(expt,METHOD)],results_evidence=[anchor(expt,RESULT)],modeling_evidence=[],
            coverage=[dict(requirement_id='persistence',explanation='Observed persistence change.',evidence=[anchor(expt,RESULT)])])])


def indexed(tmp_path, literature_requests=0):
    store,project,mm,expt=setup(tmp_path, literature_requests)
    result=p.operate(store,project,'index_mm_background',dict(source_ref=mm))
    return store,project,mm,expt,result['index_ref']


def test_normal_entry_review_paging_versioning_and_export_closure(tmp_path):
    store,project,mm,expt,index=indexed(tmp_path)
    args=arguments(index,mm,expt)
    result=p.operate(store,project,'review_expt_background',args,key='review')
    assert result['status']=='complete' and result['qualified_count']==1
    assert p.operate(store,project,'review_expt_background',args,key='review')==result
    saved=store.get(result['review_ref'])['payload']
    assert saved['scientific_support']=='unknown' and saved['independent_review']=='pending'
    assert saved['curator_only'] and not saved['task_b_packet_ready']
    assert saved['reviews'][0]['methods_evidence'][0]['start']>=0
    assert p.inspect(store,project)['background_index_refs']==[index]
    page=p.operate(store,project,'inspect_mm_background',dict(index_ref=index,limit=1))
    assert page['items'][0]['reference_id']=='R1' and page['items'][0]['intro_contexts']
    args['reviews'][0]['fulltext_reviewed']=False
    revised=p.operate(store,project,'review_expt_background',args)
    assert revised['status']=='incomplete' and revised['review_ref']!=result['review_ref']
    assert store.get(result['review_ref'])['payload']==saved
    snapshot=p.snapshot(store,project)
    manifest=bundles.export_bundle(store,snapshot,tmp_path/'bundle')
    for ref in [index,result['review_ref'],revised['review_ref'],mm,expt,store.get(expt)['payload']['blob_ref']]:
        assert ref in manifest['objects_and_blobs']
    detached=ArtifactStore(tmp_path/'bundle')
    assert p.inspect(detached,project)['background_review_refs']==[result['review_ref'],revised['review_ref']]
    assert p.inspect(store,project)['budget']['literature_requests']==0


@pytest.mark.parametrize('study,modeling,expected', [('experimental','present','excluded'),('mathematical_modeling','absent','excluded'),('review','absent','excluded'),('uncertain','uncertain','pending_review')])
def test_excluded_and_uncertain_sources_never_fill_coverage(tmp_path,study,modeling,expected):
    store,project,mm,expt,index=indexed(tmp_path);args=arguments(index,mm,expt)
    args['reviews'][0].update(study_type=study,mathematical_modeling=modeling)
    result=p.operate(store,project,'review_expt_background',args)
    assert result['status']=='incomplete' and result['qualified_count']==0
    assert store.get(result['review_ref'])['payload']['reviews'][0]['eligibility']==expected
    assert result['uncovered_requirement_ids']==['persistence']


@pytest.mark.parametrize('defect', ['no_source','no_methods','no_results','partial','intro_coverage','wrong_identity'])
def test_incomplete_evidence_and_identity_keep_gaps(tmp_path,defect):
    store,project,mm,expt,index=indexed(tmp_path);args=arguments(index,mm,expt);review=args['reviews'][0]
    if defect=='no_source':
        review.pop('source_ref');review.update(methods_evidence=[],results_evidence=[],coverage=[])
    elif defect=='no_methods':review['methods_evidence']=[]
    elif defect=='no_results':review['results_evidence']=[]
    elif defect=='partial':review['fulltext_reviewed']=False
    elif defect=='intro_coverage':review['coverage'][0]['evidence']=[anchor(expt,'Prior authors proposed persistence effects.')]
    else:
        other=source(store,'10.1234/different',f'<sec><title>Methods</title><p>{METHOD}</p></sec><sec><title>Results</title><p>{RESULT}</p></sec>')
        p.operate(store,project,'admit_source',dict(source_ref=other,reason='Additional observed material.'),actor='operator')
        review['source_ref']=other
        for key in ('methods_evidence','results_evidence'):
            for item in review[key]:item['source_ref']=other
        review['coverage'][0]['evidence'][0]['source_ref']=other
    result=p.operate(store,project,'review_expt_background',args)
    assert result['status']=='incomplete' and result['uncovered_requirement_ids']==['persistence']


@pytest.mark.parametrize('defect', ['foreign_source','wrong_quote','unknown_requirement','unknown_reference','duplicate_reference','mm_as_expt'])
def test_bad_bindings_are_rejected_without_wedging_project(tmp_path,defect):
    store,project,mm,expt,index=indexed(tmp_path);args=arguments(index,mm,expt)
    if defect=='foreign_source':args['reviews'][0]['source_ref']=source(store,'10.1234/expt','<sec><title>Methods</title><p>Unadmitted</p></sec>')
    elif defect=='wrong_quote':args['reviews'][0]['methods_evidence'][0]['quote']='Invented methods.'
    elif defect=='unknown_requirement':args['reviews'][0]['coverage'][0]['requirement_id']='other'
    elif defect=='unknown_reference':args['reviews'][0]['reference_id']='absent'
    elif defect=='duplicate_reference':args['reviews'].append(deepcopy(args['reviews'][0]))
    else:args['reviews'][0]['source_ref']=mm
    with pytest.raises(ValueError):p.operate(store,project,'review_expt_background',args)
    assert p.inspect(store,project)['state']=='ready'
    assert not store.list(bg.REVIEW)
    assert p.operate(store,project,'inspect_mm_background',dict(index_ref=index))['total']==1


def test_schema_and_supervisor_use_owned_index_refs(tmp_path):
    store,project,mm,expt=setup(tmp_path)
    class Provider:
        def __init__(self):self.calls=0
        def respond(self,messages):
            self.calls+=1
            if self.calls==1:return dict(action='index_mm_background',args=dict(source_ref=mm))
            if self.calls==2:
                index=p.inspect(store,project)['background_index_refs'][0]
                return dict(action='review_expt_background',args=arguments(index,mm,expt))
            return dict(action='conclude',args=dict(outcome='insufficient_evidence',summary='Background reviewed; model execution remains pending.',assessment_refs=[],limitations=['No model execution.']))
    # Freeze the public budget before the first call.
    config=p.load(store,project)
    project=p.create(store,dict(scenario=config['scenario'],mode='reproduction',source_refs=[mm,expt],policy=dict(planner_max_calls=3)))
    provider=Provider()
    snapshot=supervisor.run(store,provider,project)
    view=p.inspect(store,project)
    assert len(view['background_review_refs'])==1
    schema=research_schema.schemas(view)['review_expt_background']
    assert schema['properties']['args']['properties']['index_ref']['enum']==view['background_index_refs']
    assert provider.calls==3
    assert view['state']=='insufficient_evidence'


def test_statistics_and_animal_models_do_not_trigger_keyword_exclusion(tmp_path):
    store,project,mm,expt,index=indexed(tmp_path)
    text = 'We used an animal model and ordinary linear mixed-effects statistical analysis.'
    other=source(store,'10.1234/expt',f'<sec><title>Methods</title><p>{text}</p></sec><sec><title>Results</title><p>{RESULT}</p></sec>')
    p.operate(store,project,'admit_source',dict(source_ref=other,reason='Additional experimental material.'),actor='operator')
    args=arguments(index,mm,other)
    args['reviews'][0]['methods_evidence']=[anchor(other,text)]
    assert p.operate(store,project,'review_expt_background',args)['qualified_count']==1


def test_section_projection_does_not_admit_unseen_article_sections(tmp_path):
    from biomodel_agent import jats_sources
    store,project,mm,expt=setup(tmp_path)
    raw=store.get_blob(store.get(mm)['payload']['blob_ref']).replace(b'<sec>',b'<sec id="intro">',1)
    path=tmp_path/'original.xml';path.write_bytes(raw)
    selected=jats_sources.ingest(store,path,'intro')
    scoped=p.create(store,dict(scenario='Read only the admitted section.',mode='reproduction',source_refs=[selected]))
    result=p.operate(store,scoped,'index_mm_background',dict(source_ref=selected))
    assert result['status']=='unsupported' and result['reference_count']==0
    assert 'selected JATS section' in result['unsupported_reason']


def test_corrupt_source_blob_is_an_integrity_error(tmp_path):
    store,project,mm,expt=setup(tmp_path)
    blob=store.get(mm)['payload']['blob_ref']
    (store.blobs_dir/blob).write_bytes(b'corrupted')
    with pytest.raises(ValueError):
        p.operate(store,project,'index_mm_background',dict(source_ref=mm))
    assert not store.list(bg.INDEX)


def test_unregistered_index_cannot_claim_project_ownership(tmp_path):
    store,project,mm,expt,index=indexed(tmp_path)
    payload=store.get(index)['payload'];payload['untrusted_extra']='forged'
    forged=store.put(bg.INDEX,payload,refs=(project,mm))
    with pytest.raises(ValueError,match='successful project history'):
        p.operate(store,project,'review_expt_background',arguments(forged,mm,expt))


def test_conflicting_reference_ids_prevent_qualification(tmp_path):
    store,project,mm,expt=setup(tmp_path)
    raw=store.get_blob(store.get(mm)['payload']['blob_ref'])
    raw=raw.replace(b'10.1234/expt</pub-id>',b'10.1234/expt</pub-id><pub-id pub-id-type="doi">10.1234/other</pub-id>')
    text,warnings=literature._extract(raw);blob=store.put_blob(raw)
    conflicting=store.put('source',dict(blob_ref=blob,filename='conflict.xml',parser=literature.VERSION,parser_version='1',pages=[dict(number=1,text=text)],warnings=warnings),refs=(blob,))
    scoped=p.create(store,dict(scenario='Conflict control.',mode='reproduction',source_refs=[conflicting,expt]))
    index=p.operate(store,scoped,'index_mm_background',dict(source_ref=conflicting))['index_ref']
    result=p.operate(store,scoped,'review_expt_background',arguments(index,conflicting,expt))
    review=store.get(result['review_ref'])['payload']['reviews'][0]
    assert review['identity_status']=='identity_conflict' and review['eligibility']=='pending_review'


def test_index_pagination_has_a_real_byte_bound(tmp_path):
    from biomodel_agent.storage import canonical_bytes
    store,project,mm,expt=setup(tmp_path)
    refs=''.join(f'<ref id="R{i}"><element-citation><article-title>{"x"*40000}</article-title></element-citation></ref>' for i in range(10))
    large=source(store,'10.1234/mm','<sec><title>Background</title><p>Large references.</p></sec>',refs)
    scoped=p.create(store,dict(scenario='Pagination control.',mode='reproduction',source_refs=[large]))
    index=p.operate(store,scoped,'index_mm_background',dict(source_ref=large))['index_ref']
    seen=0;offset=0
    while offset is not None:
        page=p.operate(store,scoped,'inspect_mm_background',dict(index_ref=index,offset=offset,limit=10))
        assert len(canonical_bytes(page))<40000
        assert all(item['truncated'] for item in page['items'])
        seen+=len(page['items']);offset=page['next_offset']
    assert seen==10


def test_plain_text_format_gap_and_scenario_input_are_explicit(tmp_path):
    from biomodel_agent import evidence
    store=ArtifactStore(tmp_path/'store');path=tmp_path/'paper.txt';path.write_text('Unstructured paper text.')
    source_ref=evidence.ingest_source(store,path)
    project=p.create(store,dict(scenario='Review supplied paper.',mode='reproduction',source_refs=[source_ref]))
    result=p.operate(store,project,'index_mm_background',dict(source_ref=source_ref))
    assert result['status']=='unsupported' and result['reference_count']==0
    assert p.inspect(store,project)['state']=='ready'
    scenario=p.operate(store,project,'scenario_source',{})['source_ref']
    with pytest.raises(ValueError,match='full-text material'):
        p.operate(store,project,'index_mm_background',dict(source_ref=scenario))


def test_review_inspection_includes_background_questions(tmp_path):
    store,project,mm,expt,index=indexed(tmp_path)
    review=p.operate(store,project,'review_expt_background',arguments(index,mm,expt))
    page=p.operate(store,project,'inspect_background_review',dict(review_ref=review['review_ref']))
    assert page['requirements'][0]['id']=='persistence'
    assert 'co-infection' in page['requirements'][0]['question']
    assert page['items'][0]['identity_status']=='matched'


def test_chinese_method_and_result_headings_support_literal_evidence(tmp_path):
    store,project,mm,expt,index=indexed(tmp_path)
    method='培养细菌并测定携带质粒的细胞数量。';result='携带质粒的细胞保持率升高。'
    translated=source(store,'10.1234/expt',f'<sec><title>材料与方法</title><p>{method}</p></sec><sec><title>结果</title><p>{result}</p></sec>')
    p.operate(store,project,'admit_source',dict(source_ref=translated,reason='Chinese experimental source.'),actor='operator')
    args=arguments(index,mm,translated)
    args['reviews'][0]['methods_evidence']=[anchor(translated,method)]
    args['reviews'][0]['results_evidence']=[anchor(translated,result)]
    args['reviews'][0]['coverage'][0]['evidence']=[anchor(translated,result)]
    assert p.operate(store,project,'review_expt_background',args)['status']=='complete'


def test_experimental_heading_inside_intro_cannot_fill_coverage(tmp_path):
    store,project,mm,expt,index=indexed(tmp_path)
    prior='Earlier experiments suggested an effect.'
    body=f'<sec><title>Introduction</title><sec><title>Experimental background</title><p>{prior}</p></sec></sec><sec><title>Methods</title><p>{METHOD}</p></sec><sec><title>Results</title><p>{RESULT}</p></sec>'
    other=source(store,'10.1234/expt',body)
    p.operate(store,project,'admit_source',dict(source_ref=other,reason='Introduction boundary control.'),actor='operator')
    args=arguments(index,mm,other)
    args['reviews'][0]['coverage'][0]['evidence']=[anchor(other,prior)]
    review=p.operate(store,project,'review_expt_background',args)
    assert review['qualified_count']==1 and review['status']=='incomplete'


def test_chinese_intro_subsection_is_not_experimental_evidence(tmp_path):
    store,project,mm,expt,index=indexed(tmp_path)
    prior='既有研究提示质粒保持率可能改变。'
    body=f'<sec><title>引言</title><sec><title>实验背景</title><p>{prior}</p></sec></sec><sec><title>材料与方法</title><p>{METHOD}</p></sec><sec><title>结果</title><p>{RESULT}</p></sec>'
    other=source(store,'10.1234/expt',body)
    p.operate(store,project,'admit_source',dict(source_ref=other,reason='Chinese introduction boundary control.'),actor='operator')
    args=arguments(index,mm,other)
    args['reviews'][0]['coverage'][0]['evidence']=[anchor(other,prior)]
    assert p.operate(store,project,'review_expt_background',args)['status']=='incomplete'


def ref_entry(rid, title):
    return (f'<ref id="{rid}"><element-citation><article-title>{title}</article-title>'
            f'<pub-id pub-id-type="doi">10.99/{rid}</pub-id></element-citation></ref>')


def multi_indexed(tmp_path):
    """Six XML reference IDs plus one source-duplicated ID (R4 twice)."""
    store,project,mm,expt=setup(tmp_path)
    refs=''.join([ref_entry(f'R{i}', f'Experiment {i}') for i in range(1,7)]+[ref_entry('R4','Repeated entry')])
    body=f'<sec><title>Background</title><p>{CONTEXT}<xref ref-type="bibr" rid="R2"/></p></sec>'
    multi=source(store,'10.1234/multi',body,refs)
    p.operate(store,project,'admit_source',dict(source_ref=multi,reason='Multi-reference MM source.'),actor='operator')
    index=p.operate(store,project,'index_mm_background',dict(source_ref=multi))['index_ref']
    return store,project,index


def test_context_rich_reference_pages_stay_bounded_and_all_rows_remain_reachable(tmp_path):
    store, project, _, _ = setup(tmp_path)
    refs = ''.join(ref_entry(f'R{i}', f'Experiment {i}') for i in range(12))
    citations = ''.join(f'<xref ref-type="bibr" rid="R{i}"/>' for i in range(12))
    mm = source(store, '10.1234/long-intro', '<sec><title>Introduction</title><p>' +
                'Biological premise. ' * 100 + citations + '</p></sec>', refs)
    p.operate(store, project, 'admit_source', dict(source_ref=mm, reason='Read cited biological context.'),
              actor='operator')
    index = p.operate(store, project, 'index_mm_background', dict(source_ref=mm))['index_ref']
    offset = 0
    seen = []
    while offset is not None:
        result = p.operate(store, project, 'inspect_mm_background',
                           dict(index_ref=index, offset=offset, limit=10))
        assert result['items'] and len(json.dumps(result, ensure_ascii=False)) < 10000
        assert len(result['items']) < 10
        seen.extend(item['reference_id'] for item in result['items'])
        messages = supervisor._messages(store, project)
        observed = [json.loads(message['content']) for message in messages if message['role'] == 'user']
        assert any(row.get('tool') == 'inspect_mm_background' and row.get('result') == result
                   for row in observed)
        offset = result['next_offset']
    assert seen == [f'R{i}' for i in range(12)]


def test_reference_id_selection_reads_requested_rows_directly(tmp_path):
    store,project,index=multi_indexed(tmp_path)
    page=p.operate(store,project,'inspect_mm_background',dict(index_ref=index,reference_ids=['R6']))
    assert [item['reference_id'] for item in page['items']]==['R6']
    assert page['total']==1 and page['index_total']==7 and page['missing_reference_ids']==[]
    assert page['next_offset'] is None
    ordered=p.operate(store,project,'inspect_mm_background',dict(index_ref=index,reference_ids=['R5','R1']))
    assert [item['reference_id'] for item in ordered['items']]==['R5','R1']
    unknown=p.operate(store,project,'inspect_mm_background',dict(index_ref=index,reference_ids=['R2','absent']))
    assert [item['reference_id'] for item in unknown['items']]==['R2']
    assert unknown['missing_reference_ids']==['absent'] and unknown['total']==1 and unknown['index_total']==7
    ambiguous=p.operate(store,project,'inspect_mm_background',dict(index_ref=index,reference_ids=['R4']))
    assert [item['reference_id'] for item in ambiguous['items']]==['R4','R4']
    assert len({item['citation_text'] for item in ambiguous['items']})==2
    assert ambiguous['total']==2 and ambiguous['missing_reference_ids']==[]
    cased=p.operate(store,project,'inspect_mm_background',dict(index_ref=index,reference_ids=['r1']))
    assert cased['items']==[] and cased['missing_reference_ids']==['r1'] and cased['total']==0
    assert p.inspect(store,project)['state']=='ready'


def test_reference_id_selection_paginates_the_selected_rows(tmp_path):
    store,project,index=multi_indexed(tmp_path)
    ids=['R1','R2','R3','R5','R6']
    first=p.operate(store,project,'inspect_mm_background',dict(index_ref=index,reference_ids=ids,limit=2))
    assert [item['reference_id'] for item in first['items']]==['R1','R2']
    assert first['total']==5 and first['index_total']==7 and first['next_offset']==2
    middle=p.operate(store,project,'inspect_mm_background',dict(index_ref=index,reference_ids=ids,offset=2,limit=2))
    assert [item['reference_id'] for item in middle['items']]==['R3','R5']
    assert middle['total']==5 and middle['next_offset']==4
    last=p.operate(store,project,'inspect_mm_background',dict(index_ref=index,reference_ids=ids,offset=4,limit=2))
    assert [item['reference_id'] for item in last['items']]==['R6']
    assert last['next_offset'] is None
    beyond=p.operate(store,project,'inspect_mm_background',dict(index_ref=index,reference_ids=ids,offset=9))
    assert beyond['items']==[] and beyond['next_offset'] is None and beyond['total']==5
    duplicated=p.operate(store,project,'inspect_mm_background',dict(index_ref=index,reference_ids=['R3','R4'],limit=2))
    assert [item['reference_id'] for item in duplicated['items']]==['R3','R4']
    assert duplicated['total']==3 and duplicated['next_offset']==2
    tail=p.operate(store,project,'inspect_mm_background',dict(index_ref=index,reference_ids=['R3','R4'],offset=2))
    assert [item['reference_id'] for item in tail['items']]==['R4'] and tail['next_offset'] is None


@pytest.mark.parametrize('bad', [
    ['R1','R1'], [], [f'X{i}' for i in range(11)], 'R1', [1], [None], [''],
])
def test_reference_id_request_validation_rejects_bad_shapes(tmp_path,bad):
    store,project,index=multi_indexed(tmp_path)
    with pytest.raises(ValueError):
        p.operate(store,project,'inspect_mm_background',dict(index_ref=index,reference_ids=bad))
    assert p.inspect(store,project)['state']=='ready'
    assert p.operate(store,project,'inspect_mm_background',dict(index_ref=index))['total']==7


def test_include_intro_contexts_toggle_keeps_provenance(tmp_path):
    store,project,index=multi_indexed(tmp_path)
    full=p.operate(store,project,'inspect_mm_background',dict(index_ref=index,reference_ids=['R2']))
    assert full['items'][0]['intro_contexts'] and full['items'][0]['intro_context_count']>=1
    assert full['items'][0]['intro_context_paths'] and full['intro_contexts_included'] is True
    slim=p.operate(store,project,'inspect_mm_background',dict(index_ref=index,reference_ids=['R2'],include_intro_contexts=False))
    item=slim['items'][0]
    assert 'intro_contexts' not in item
    assert item['intro_context_count']==full['items'][0]['intro_context_count']
    assert item['intro_context_paths']==full['items'][0]['intro_context_paths']
    assert slim['intro_contexts_included'] is False
    plain=p.operate(store,project,'inspect_mm_background',dict(index_ref=index,offset=1,limit=1,include_intro_contexts=False))
    assert plain['items'][0]['reference_id']=='R2' and 'intro_contexts' not in plain['items'][0]
    assert plain['items'][0]['intro_context_count']>=1 and plain['items'][0]['intro_context_paths']
    assert plain['intro_contexts_included'] is False
    kept=p.operate(store,project,'inspect_mm_background',dict(index_ref=index,offset=1,limit=1))
    assert kept['intro_contexts_included'] is True and kept['items'][0]['intro_contexts']
    for wrong in (1,'false',0):
        with pytest.raises(ValueError):
            p.operate(store,project,'inspect_mm_background',dict(index_ref=index,include_intro_contexts=wrong))
    assert p.inspect(store,project)['state']=='ready'


def test_selected_oversized_rows_stay_truncated_and_bounded(tmp_path):
    from biomodel_agent.storage import canonical_bytes
    store,project,mm,expt=setup(tmp_path)
    refs=''.join(f'<ref id="R{i}"><element-citation><article-title>{"x"*40000}</article-title></element-citation></ref>' for i in range(10))
    large=source(store,'10.1234/mm','<sec><title>Background</title><p>Large references.</p></sec>',refs)
    scoped=p.create(store,dict(scenario='Selection bound control.',mode='reproduction',source_refs=[large]))
    index=p.operate(store,scoped,'index_mm_background',dict(source_ref=large))['index_ref']
    page=p.operate(store,scoped,'inspect_mm_background',dict(index_ref=index,reference_ids=['R3','R9']))
    assert len(canonical_bytes(page))<10000
    assert [item['reference_id'] for item in page['items']]==['R3']
    assert page['next_offset']==1
    tail=p.operate(store,scoped,'inspect_mm_background',
                   dict(index_ref=index,reference_ids=['R3','R9'],offset=page['next_offset']))
    assert len(canonical_bytes(tail))<10000
    assert [item['reference_id'] for item in tail['items']]==['R9']
    assert all(item['truncated'] for item in [*page['items'], *tail['items']])
    assert page['total']==2 and page['index_total']==10 and page['missing_reference_ids']==[]
    assert tail['next_offset'] is None
    mixed=p.operate(store,scoped,'inspect_mm_background',dict(index_ref=index,reference_ids=['R3','nope']))
    assert [item['reference_id'] for item in mixed['items']]==['R3'] and mixed['missing_reference_ids']==['nope']


def test_inspection_extensions_stay_scoped_and_preserve_the_index(tmp_path):
    store,project,mm,expt,index=indexed(tmp_path)
    payload=deepcopy(store.get(index)['payload'])
    for args in (dict(index_ref=index),dict(index_ref=index,reference_ids=['R1']),
                 dict(index_ref=index,include_intro_contexts=False),
                 dict(index_ref=index,reference_ids=['R1'],include_intro_contexts=False,offset=0,limit=1)):
        p.operate(store,project,'inspect_mm_background',args)
    assert store.get(index)['payload']==payload
    review=p.operate(store,project,'review_expt_background',arguments(index,mm,expt))
    for extra in (dict(review_ref=review['review_ref'],reference_ids=['R1']),
                  dict(review_ref=review['review_ref'],include_intro_contexts=False)):
        with pytest.raises(ValueError):
            p.operate(store,project,'inspect_background_review',extra)
    view=p.inspect(store,project)
    args_schema=research_schema.schemas(view)['inspect_mm_background']['properties']['args']['properties']
    assert args_schema['reference_ids']['minItems']==1 and args_schema['reference_ids']['maxItems']==10
    assert args_schema['reference_ids']['items']['maxLength']==1000
    assert args_schema['include_intro_contexts']['type']=='boolean'
    review_args=research_schema.schemas(view)['inspect_background_review']['properties']['args']['properties']
    assert 'reference_ids' not in review_args and 'include_intro_contexts' not in review_args
    assert 'reference_ids' in supervisor.PROMPT and 'include_intro_contexts' in supervisor.PROMPT


def test_pdf_source_index_uses_page_locators_and_supports_review(tmp_path, monkeypatch):
    from biomodel_agent import evidence
    store = ArtifactStore(tmp_path/'store')
    text = ('Synthetic background study\ndoi:10.1234/mm\nIntroduction\n'
            f'{CONTEXT} [1].\nMethods\nWe simulated plasmid dynamics.\nReferences\n'
            '1. Alpha A: Experiment. Journal 2001, 1:12. doi:10.1234/expt')
    monkeypatch.setattr(evidence, '_pdf_page_texts', lambda raw, warnings: [text])
    path = tmp_path/'study.pdf'
    path.write_bytes(b'%PDF-1.4\nsynthetic parser-boundary fixture')
    mm = evidence.ingest_source(store, path)
    expt = source(store, '10.1234/expt',
                  f'<sec><title>Methods</title><p>{METHOD}</p></sec>'
                  f'<sec><title>Results</title><p>{RESULT}</p></sec>')
    project = p.create(store, dict(scenario='PDF review.', mode='reproduction', source_refs=[mm,expt]))
    index = p.operate(store, project, 'index_mm_background', dict(source_ref=mm))['index_ref']
    page = p.operate(store, project, 'inspect_mm_background', dict(index_ref=index, reference_ids=['1']))
    item = page['items'][0]
    assert item['intro_context_count'] == 1
    assert item['intro_contexts'][0]['locator']['kind'] == 'pdf_text'
    assert 'xml_path' not in item['intro_contexts'][0]
    slim = p.operate(store, project, 'inspect_mm_background',
                     dict(index_ref=index, reference_ids=['1'], include_intro_contexts=False))
    assert 'intro_contexts' not in slim['items'][0]
    assert slim['items'][0]['intro_context_locators'] == item['intro_context_locators']
    args = arguments(index, mm, expt)
    args['reviews'][0]['reference_id'] = '1'
    assert p.operate(store, project, 'review_expt_background', args)['status'] == 'complete'


@pytest.mark.parametrize('parent', ['Discussion', 'Results'])
def test_nested_methods_cannot_supply_results_evidence(tmp_path, parent):
    store, project, mm, expt, index = indexed(tmp_path)
    body = (f'<sec><title>{parent}</title><sec><title>Methods</title>'
            f'<p>{METHOD}</p></sec></sec>'
            f'<sec><title>Results</title><p>{RESULT}</p></sec>')
    nested = source(store, '10.1234/expt', body)
    p.operate(store, project, 'admit_source',
              dict(source_ref=nested, reason='Nested methods section control.'), actor='operator')
    args = arguments(index, mm, nested)
    args['reviews'][0]['results_evidence'] = [anchor(nested, METHOD)]
    rejected = p.operate(store, project, 'review_expt_background', args)
    assert rejected['qualified_count'] == 0 and rejected['pending_count'] == 1
    args['reviews'][0]['results_evidence'] = [anchor(nested, RESULT)]
    assert p.operate(store, project, 'review_expt_background', args)['qualified_count'] == 1


def joint_env(tmp_path):
    """Two MM references backed by two independently qualifying experimental sources."""
    store = ArtifactStore(tmp_path/'store')
    refs = ''.join(f'<ref id="R{n}"><element-citation><article-title>Experiment {n}</article-title>'
                   f'<pub-id pub-id-type="doi">10.1234/expt{n}</pub-id></element-citation></ref>'
                   for n in (1, 2))
    mm = source(store, '10.1234/mm',
                f'<sec><title>Background</title><p>{CONTEXT}<xref ref-type="bibr" rid="R1"/>'
                f'<xref ref-type="bibr" rid="R2"/></p></sec>', refs)
    intro = 'Prior authors proposed persistence effects.'
    design = 'The study pre-registered two replicate runs before any measurement.'
    method2 = 'We tracked infection loads in duplicate cultures.'
    result2 = 'Loads dropped after the second week of treatment.'
    expt1 = source(store, '10.1234/expt1',
                   f'<sec><title>Introduction</title><p>{intro}</p></sec>'
                   f'<sec><title>Methods</title><p>{METHOD}</p></sec>'
                   f'<sec><title>Results</title><p>{RESULT}</p></sec>')
    expt2 = source(store, '10.1234/expt2',
                   f'<sec><title>Study design</title><p>{design}</p></sec>'
                   f'<sec><title>Methods</title><p>{method2}</p></sec>'
                   f'<sec><title>Results</title><p>{result2}</p></sec>')
    project = p.create(store, dict(scenario='Reproduce the stated plasmid study.', mode='reproduction',
                                   source_refs=[mm, expt1, expt2]))
    index = p.operate(store, project, 'index_mm_background', dict(source_ref=mm))['index_ref']
    return dict(store=store, project=project, mm=mm, index=index, expt1=expt1, expt2=expt2,
                intro=intro, design=design, method2=method2, result2=result2)


def joint_review(rid, expt, method, result, coverage=None):
    return dict(reference_id=rid, source_ref=expt, study_type='experimental',
                mathematical_modeling='absent', fulltext_reviewed=True,
                rationale='Reviewed experimental methods and results with ordinary statistics.',
                methods_evidence=[anchor(expt, method)], results_evidence=[anchor(expt, result)],
                modeling_evidence=[], coverage=coverage or [])


def joint_claim(requirement_id, support, evidence, explanation='Joint cross-source assessment.'):
    return dict(requirement_id=requirement_id, support=support, explanation=explanation, evidence=evidence)


def joint_args(env, reviews, joint=None, requirements=None):
    return dict(index_ref=env['index'],
                requirements=requirements or [dict(id='persistence',
                    question='How does co-infection affect persistence?', mm_context=anchor(env['mm'], CONTEXT))],
                reviews=reviews, requirement_reviews=joint or [])


def pair(env, coverage1=None, coverage2=None):
    return [joint_review('R1', env['expt1'], METHOD, RESULT, coverage1),
            joint_review('R2', env['expt2'], env['method2'], env['result2'], coverage2)]


def test_omitted_support_keeps_legacy_full_contract(tmp_path):
    store, project, mm, expt, index = indexed(tmp_path)
    result = p.operate(store, project, 'review_expt_background', arguments(index, mm, expt))
    assert result['status'] == 'complete' and result['partial_requirement_ids'] == []
    assert result['joint_requirement_count'] == 0
    saved = store.get(result['review_ref'])['payload']
    assert saved['reviews'][0]['coverage'][0]['support'] == 'full'
    assert saved['requirement_reviews'] == [] and saved['partial_requirement_ids'] == []
    assert p.operate(store, project, 'review_expt_background', arguments(index, mm, expt)) == result


@pytest.mark.parametrize('support', ['partial', 'uncertain'])
def test_single_partial_and_uncertain_support_never_complete(tmp_path, support):
    store, project, mm, expt, index = indexed(tmp_path)
    args = arguments(index, mm, expt)
    args['reviews'][0]['coverage'][0]['support'] = support
    result = p.operate(store, project, 'review_expt_background', args)
    assert result['status'] == 'incomplete' and result['qualified_count'] == 1
    assert result['uncovered_requirement_ids'] == ['persistence']
    assert result['partial_requirement_ids'] == (['persistence'] if support == 'partial' else [])
    saved = store.get(result['review_ref'])['payload']
    assert saved['reviews'][0]['coverage'][0]['support'] == support
    assert saved['reviews'][0]['coverage'][0]['evidence'][0]['start'] >= 0


def test_two_qualified_sources_joint_full_covers_one_requirement(tmp_path):
    env = joint_env(tmp_path)
    joint = [joint_claim('persistence', 'full',
                         [anchor(env['expt1'], RESULT), anchor(env['expt2'], env['result2'])])]
    result = p.operate(env['store'], env['project'], 'review_expt_background',
                       joint_args(env, pair(env), joint))
    assert result['status'] == 'complete' and result['qualified_count'] == 2
    assert result['uncovered_requirement_ids'] == [] and result['partial_requirement_ids'] == []
    item = env['store'].get(result['review_ref'])['payload']['requirement_reviews'][0]
    assert item['support'] == 'full' and item['evidence_in_experimental_sections'] is True
    assert all(e['in_experimental_section'] and e['start'] >= 0 for e in item['evidence'])


def test_joint_partial_overrides_single_full_claim(tmp_path):
    env = joint_env(tmp_path)
    coverage1 = [dict(requirement_id='persistence', explanation='First paper claims full.',
                      evidence=[anchor(env['expt1'], RESULT)])]
    joint = [joint_claim('persistence', 'partial', [anchor(env['expt2'], env['result2'])],
                         explanation='Only the second paper matches the tested condition.')]
    result = p.operate(env['store'], env['project'], 'review_expt_background',
                       joint_args(env, pair(env, coverage1), joint))
    assert result['status'] == 'incomplete' and result['qualified_count'] == 2
    assert result['uncovered_requirement_ids'] == ['persistence']
    assert result['partial_requirement_ids'] == ['persistence']


def test_joint_full_overrides_single_partial_claim(tmp_path):
    env = joint_env(tmp_path)
    coverage1 = [dict(requirement_id='persistence', explanation='Only partial here.',
                      evidence=[anchor(env['expt1'], RESULT)], support='partial')]
    joint = [joint_claim('persistence', 'full',
                         [anchor(env['expt1'], RESULT), anchor(env['expt2'], env['result2'])])]
    result = p.operate(env['store'], env['project'], 'review_expt_background',
                       joint_args(env, pair(env, coverage1), joint))
    assert result['status'] == 'complete' and result['partial_requirement_ids'] == []
    assert result['uncovered_requirement_ids'] == []


def test_accumulated_partial_supports_never_become_full(tmp_path):
    env = joint_env(tmp_path)
    coverage1 = [dict(requirement_id='persistence', explanation='Partial first paper.',
                      evidence=[anchor(env['expt1'], RESULT)], support='partial')]
    coverage2 = [dict(requirement_id='persistence', explanation='Partial second paper.',
                      evidence=[anchor(env['expt2'], env['result2'])], support='partial')]
    result = p.operate(env['store'], env['project'], 'review_expt_background',
                       joint_args(env, pair(env, coverage1, coverage2)))
    assert result['status'] == 'incomplete' and result['partial_requirement_ids'] == ['persistence']
    assert result['uncovered_requirement_ids'] == ['persistence']


@pytest.mark.parametrize('defect', ['review_paper', 'unread_fulltext', 'unreviewed_source', 'mm_source'])
def test_joint_evidence_requires_qualified_experimental_sources(tmp_path, defect):
    env = joint_env(tmp_path)
    store, project = env['store'], env['project']
    reviews = pair(env)
    if defect == 'review_paper':
        reviews[1]['study_type'] = 'review'
        bad = anchor(env['expt2'], env['result2'])
    elif defect == 'unread_fulltext':
        reviews[1]['fulltext_reviewed'] = False
        bad = anchor(env['expt2'], env['result2'])
    elif defect == 'unreviewed_source':
        extra = source(store, '10.1234/extra',
                       '<sec><title>Methods</title><p>Extra methods text.</p></sec>'
                       '<sec><title>Results</title><p>Extra results text.</p></sec>')
        p.operate(store, project, 'admit_source', dict(source_ref=extra, reason='Never reviewed.'),
                  actor='operator')
        bad = anchor(extra, 'Extra results text.')
    else:
        bad = anchor(env['mm'], CONTEXT)
    joint = [joint_claim('persistence', 'full', [anchor(env['expt1'], RESULT), bad])]
    with pytest.raises(ValueError):
        p.operate(store, project, 'review_expt_background', joint_args(env, reviews, joint))
    assert p.inspect(store, project)['state'] == 'ready'
    assert not store.list(bg.REVIEW)


def test_identity_pending_source_cannot_supply_joint_evidence(tmp_path):
    from biomodel_agent import evidence
    env = joint_env(tmp_path)
    store, project = env['store'], env['project']
    path = tmp_path/'pending.txt'
    path.write_text('Unstructured second study. Second results text about loads.')
    pending = evidence.ingest_source(store, path)
    p.operate(store, project, 'admit_source', dict(source_ref=pending, reason='Unparsed second study.'),
              actor='operator')
    reviews = [joint_review('R1', env['expt1'], METHOD, RESULT),
               joint_review('R2', pending, 'Second results text about loads.',
                            'Second results text about loads.')]
    base = dict(index_ref=env['index'],
                requirements=[dict(id='persistence', question='How does co-infection affect persistence?',
                                   mm_context=anchor(env['mm'], CONTEXT))], reviews=reviews)
    recorded = p.operate(store, project, 'review_expt_background', dict(base, requirement_reviews=[]))
    saved = store.get(recorded['review_ref'])['payload']
    assert saved['reviews'][1]['identity_status'] == 'pending_identity'
    assert saved['reviews'][1]['eligibility'] == 'pending_review'
    joint = [joint_claim('persistence', 'full', [anchor(pending, 'Second results text about loads.')])]
    with pytest.raises(ValueError):
        p.operate(store, project, 'review_expt_background', dict(base, requirement_reviews=joint))
    assert len(store.list(bg.REVIEW)) == 1
    assert p.inspect(store, project)['state'] == 'ready'


@pytest.mark.parametrize('defect', ['unknown', 'duplicate', 'unbound', 'full_without_evidence'])
def test_joint_review_input_validation(tmp_path, defect):
    env = joint_env(tmp_path)
    store, project = env['store'], env['project']
    joint = [joint_claim('persistence', 'full', [anchor(env['expt1'], RESULT)])]
    if defect == 'unknown':
        joint[0]['requirement_id'] = 'other'
    elif defect == 'duplicate':
        joint.append(deepcopy(joint[0]))
    elif defect == 'unbound':
        joint[0]['evidence'] = [anchor(env['expt1'], 'Invented joint result text.')]
    else:
        joint[0]['evidence'] = []
    with pytest.raises(ValueError):
        p.operate(store, project, 'review_expt_background', joint_args(env, pair(env), joint))
    assert p.inspect(store, project)['state'] == 'ready'
    assert not store.list(bg.REVIEW)


@pytest.mark.parametrize('bad', ['intro', 'design'])
def test_intro_and_wrong_section_joint_anchors_cannot_cover(tmp_path, bad):
    env = joint_env(tmp_path)
    quote, ref = (env['intro'], env['expt1']) if bad == 'intro' else (env['design'], env['expt2'])
    joint = [joint_claim('persistence', 'full',
                         [anchor(ref, quote), anchor(env['expt2'], env['result2'])])]
    result = p.operate(env['store'], env['project'], 'review_expt_background',
                       joint_args(env, pair(env), joint))
    assert result['status'] == 'incomplete' and result['uncovered_requirement_ids'] == ['persistence']
    item = env['store'].get(result['review_ref'])['payload']['requirement_reviews'][0]
    assert item['support'] == 'full' and item['evidence_in_experimental_sections'] is False
    assert item['evidence'][0]['in_experimental_section'] is False
    assert item['evidence'][1]['in_experimental_section'] is True


@pytest.mark.parametrize('over', ['anchors', 'entries'])
def test_joint_review_bounds_reject_overruns(tmp_path, over):
    env = joint_env(tmp_path)
    if over == 'anchors':
        joint = [joint_claim('persistence', 'full', [anchor(env['expt1'], RESULT)] * 9)]
    else:
        joint = [joint_claim(f'req{i}', 'uncertain', []) for i in range(17)]
    with pytest.raises(ValueError):
        p.operate(env['store'], env['project'], 'review_expt_background',
                  joint_args(env, pair(env), joint))
    assert p.inspect(env['store'], env['project'])['state'] == 'ready'
    assert not env['store'].list(bg.REVIEW)


def test_joint_review_bounds_accept_eight_anchors_and_sixteen_entries(tmp_path):
    env = joint_env(tmp_path)
    requirements = [dict(id='persistence', question='How does co-infection affect persistence?',
                         mm_context=anchor(env['mm'], CONTEXT))]
    requirements += [dict(id=f'req{i}', question=f'Question {i}?', mm_context=anchor(env['mm'], CONTEXT))
                     for i in range(15)]
    joint = [joint_claim('persistence', 'full',
                         [anchor(env['expt1'], RESULT), anchor(env['expt2'], env['result2'])] * 4)]
    joint += [joint_claim(f'req{i}', 'uncertain', []) for i in range(15)]
    result = p.operate(env['store'], env['project'], 'review_expt_background',
                       joint_args(env, pair(env), joint, requirements=requirements))
    assert result['status'] == 'incomplete'
    assert result['partial_requirement_ids'] == []
    assert result['uncovered_requirement_ids'] == sorted(f'req{i}' for i in range(15))
    saved = env['store'].get(result['review_ref'])['payload']
    assert len(saved['requirement_reviews'][0]['evidence']) == 8
    assert len(saved['requirement_reviews']) == 16


def test_joint_review_revisions_never_rewrite_saved_objects(tmp_path):
    env = joint_env(tmp_path)
    full = [joint_claim('persistence', 'full',
                        [anchor(env['expt1'], RESULT), anchor(env['expt2'], env['result2'])])]
    first = p.operate(env['store'], env['project'], 'review_expt_background',
                      joint_args(env, pair(env), full))
    saved = deepcopy(env['store'].get(first['review_ref'])['payload'])
    partial = [joint_claim('persistence', 'partial', [anchor(env['expt2'], env['result2'])])]
    second = p.operate(env['store'], env['project'], 'review_expt_background',
                       joint_args(env, pair(env), partial))
    assert second['review_ref'] != first['review_ref'] and second['status'] == 'incomplete'
    assert env['store'].get(first['review_ref'])['payload'] == saved


def test_inspect_reports_joint_support_and_partial_ids(tmp_path):
    env = joint_env(tmp_path)
    joint = [joint_claim('persistence', 'partial', [anchor(env['expt2'], env['result2'])])]
    result = p.operate(env['store'], env['project'], 'review_expt_background',
                       joint_args(env, pair(env), joint))
    page = p.operate(env['store'], env['project'], 'inspect_background_review',
                     dict(review_ref=result['review_ref']))
    assert page['requirements'][0]['joint_support'] == 'partial'
    assert page['summary']['partial_requirement_ids'] == ['persistence']
    assert page['summary']['joint_requirement_count'] == 1
    assert page['summary']['uncovered_requirement_ids'] == ['persistence']


def test_legacy_review_payload_without_new_fields_stays_readable(tmp_path, monkeypatch):
    store, project, mm, expt, index = indexed(tmp_path)
    result = p.operate(store, project, 'review_expt_background', arguments(index, mm, expt))
    payload = store.get(result['review_ref'])['payload']
    legacy = {key: value for key, value in payload.items()
              if key not in ('requirement_reviews', 'partial_requirement_ids')}
    summary = bg._summary(legacy)
    assert summary['partial_requirement_ids'] == []
    assert summary['partial_requirement_count'] == 0 and summary['joint_requirement_count'] == 0
    assert summary['discovery_review_count'] == 0
    real_own = p.own
    monkeypatch.setattr(p, 'own', lambda s, ref, kind, proj:
                        legacy if kind == bg.REVIEW else real_own(s, ref, kind, proj))
    page = bg.perform(store, project, 'inspect_background_review', dict(review_ref=result['review_ref']))
    assert page['requirements'][0]['joint_support'] is None
    assert page['summary']['partial_requirement_ids'] == []


def test_schema_and_prompt_describe_support_and_joint_reviews(tmp_path):
    store, project, mm, expt, index = indexed(tmp_path)
    view = p.inspect(store, project)
    action = research_schema.schemas(view)['review_expt_background']['properties']['args']
    assert 'requirement_reviews' not in action['required']
    args = action['properties']
    coverage = args['reviews']['items']['properties']['coverage']['items']
    assert coverage['properties']['support']['enum'] == ['full', 'partial', 'uncertain']
    assert 'support' not in coverage['required']
    joint = args['requirement_reviews']
    assert joint['maxItems'] == 16 and 'required' not in joint
    item = joint['items']
    assert item['properties']['support']['enum'] == ['full', 'partial', 'uncertain']
    assert item['properties']['evidence']['maxItems'] == 8
    assert 'minItems' not in item['properties']['evidence']
    assert item['required'] == ['requirement_id', 'support', 'explanation', 'evidence']
    assert 'requirement_reviews' in supervisor.PROMPT
    assert 'never count as covered' in supervisor.PROMPT


@pytest.mark.parametrize('defect', ['excluded', 'unread', 'wrong_section'])
def test_ineligible_partial_claim_does_not_count_as_partial(tmp_path, defect):
    env = joint_env(tmp_path)
    quote = env['intro'] if defect == 'wrong_section' else RESULT
    coverage = [dict(requirement_id='persistence', explanation='Limited support claimed.',
                     support='partial', evidence=[anchor(env['expt1'], quote)])]
    reviews = pair(env, coverage)
    if defect == 'excluded':
        reviews[0]['study_type'] = 'review'
    if defect == 'unread':
        reviews[0]['fulltext_reviewed'] = False
    result = p.operate(env['store'], env['project'], 'review_expt_background',
                       joint_args(env, reviews))
    assert result['partial_requirement_ids'] == []
    assert result['uncovered_requirement_ids'] == ['persistence']


def test_joint_uncertain_overrides_single_partial(tmp_path):
    env = joint_env(tmp_path)
    coverage = [dict(requirement_id='persistence', explanation='Limited support claimed.',
                     support='partial', evidence=[anchor(env['expt1'], RESULT)])]
    joint = [joint_claim('persistence', 'uncertain', [])]
    result = p.operate(env['store'], env['project'], 'review_expt_background',
                       joint_args(env, pair(env, coverage), joint))
    assert result['partial_requirement_ids'] == []
    assert result['uncovered_requirement_ids'] == ['persistence']


@pytest.mark.parametrize('missing', ['explanation', 'evidence'])
def test_joint_required_fields_match_schema(tmp_path, missing):
    env = joint_env(tmp_path)
    claim = joint_claim('persistence', 'uncertain', [])
    del claim[missing]
    with pytest.raises(ValueError):
        p.operate(env['store'], env['project'], 'review_expt_background',
                  joint_args(env, pair(env), [claim]))


def test_joint_inspection_pages_explanation_and_literal_evidence(tmp_path):
    env = joint_env(tmp_path)
    requirements = [dict(id=f'req{i}', question=f'Question {i}?',
                         mm_context=anchor(env['mm'], CONTEXT)) for i in range(3)]
    joint = [joint_claim(f'req{i}', 'partial', [anchor(env['expt1'], RESULT)],
                         explanation=f'Limited support for question {i}.') for i in range(3)]
    result = p.operate(env['store'], env['project'], 'review_expt_background',
                       joint_args(env, pair(env), joint, requirements))
    page = p.operate(env['store'], env['project'], 'inspect_background_review',
                     dict(review_ref=result['review_ref'], section='requirement_reviews', limit=2))
    assert page['section'] == 'requirement_reviews' and page['total'] == 3
    assert page['next_offset'] == 2
    assert [item['requirement_id'] for item in page['items']] == ['req0', 'req1']
    assert page['items'][0]['explanation'] == 'Limited support for question 0.'
    assert page['items'][0]['evidence'][0]['quote'] == RESULT
    last = p.operate(env['store'], env['project'], 'inspect_background_review',
                     dict(review_ref=result['review_ref'], section='requirement_reviews', offset=2))
    assert last['next_offset'] is None and len(last['items']) == 1
    with pytest.raises(ValueError):
        p.operate(env['store'], env['project'], 'inspect_background_review',
                  dict(review_ref=result['review_ref'], section='unknown'))


DISC_PMID = '35578043'
DISC_PMCID = 'PMC9000001'
DISC_DOI = '10.9000/discovered'
METHOD2 = 'We counted biofilm colonies on duplicate plates.'
RESULT2 = 'Colony counts doubled after the nutrient shift.'


def disc_row(**overrides):
    row = dict(id=DISC_PMID, source='MED', pmcid=DISC_PMCID, doi=DISC_DOI,
               title='Discovered persistence experiment', authorString='A. Author',
               pubYear='2020', isOpenAccess='Y', abstractText='Background. Methods. Results.')
    row.update(overrides)
    return row


def fetched_xml(doi=DISC_DOI, pmid=DISC_PMID, pmcid=DISC_PMCID, method=METHOD2, result=RESULT2):
    return (f'<article><front><article-meta><title-group><article-title>Discovered study</article-title>'
            f'</title-group><article-id pub-id-type="pmcid">{pmcid}</article-id>'
            f'<article-id pub-id-type="pmid">{pmid}</article-id>'
            f'<article-id pub-id-type="doi">{doi}</article-id></article-meta></front>'
            f'<body><sec><title>Methods</title><p>{method}</p></sec>'
            f'<sec><title>Results</title><p>{result}</p></sec></body>'
            f'<back><ref-list></ref-list></back></article>').encode()


def discovery_transport(rows, xml_by_pmcid):
    def handler(request):
        path = request.url.path
        if path.endswith('/search'):
            return httpx.Response(200, json=dict(hitCount=len(rows), resultList=dict(result=rows)))
        parts = path.strip('/').split('/')
        pmcid = parts[-2] if parts[-1] == 'fullTextXML' else parts[-1]
        if pmcid not in xml_by_pmcid:
            return httpx.Response(404, text='missing article')
        return httpx.Response(200, content=xml_by_pmcid[pmcid])
    return httpx.MockTransport(handler)


def discovery_review(source_ref, search_ref, method, result, coverage=None, **overrides):
    review = dict(source_ref=source_ref,
                  discovery=dict(search_ref=search_ref, publication_source='MED', publication_id=DISC_PMID),
                  study_type='experimental', mathematical_modeling='absent', fulltext_reviewed=True,
                  rationale='Reviewed the discovered experimental full text; ordinary statistics only.',
                  methods_evidence=[anchor(source_ref, method)], results_evidence=[anchor(source_ref, result)],
                  modeling_evidence=[], coverage=coverage or [])
    review.update(overrides)
    return review


def discovery_args(index, mm, reviews, joint=None):
    return dict(index_ref=index,
                requirements=[dict(id='persistence', question='How does co-infection affect persistence?',
                                   mm_context=anchor(mm, CONTEXT))],
                reviews=reviews, requirement_reviews=joint or [])


def fetched(tmp_path, rows=None, xml=None):
    """Search and fetch one uncited experimental full text through normal projects.operate."""
    store, project, mm, expt, index = indexed(tmp_path, literature_requests=2)
    transport = discovery_transport(rows if rows is not None else [disc_row()],
                                    xml if xml is not None else {DISC_PMCID: fetched_xml()})
    search = p.operate(store, project, 'search_literature',
                       dict(query='discovered plasmid persistence experiment'), literature_transport=transport)
    fetch = p.operate(store, project, 'fetch_literature',
                      dict(search_ref=search['search_ref'], pmcid=DISC_PMCID), literature_transport=transport)
    return dict(store=store, project=project, mm=mm, expt=expt, index=index, search=search,
                fetch=fetch, transport=transport)


def test_discovered_uncited_experimental_fulltext_qualifies(tmp_path):
    env = fetched(tmp_path)
    store, project, search, fetch = env['store'], env['project'], env['search'], env['fetch']
    disc = fetch['source_ref']
    review = discovery_review(disc, search['search_ref'], METHOD2, RESULT2,
                              coverage=[dict(requirement_id='persistence',
                                             explanation='Discovered paper reports the persistence change.',
                                             evidence=[anchor(disc, RESULT2)])])
    result = p.operate(store, project, 'review_expt_background', discovery_args(env['index'], env['mm'], [review]))
    assert result['status'] == 'complete' and result['qualified_count'] == 1
    assert result['discovery_review_count'] == 1
    assert result['unreviewed_reference_ids'] == ['R1']
    saved = store.get(result['review_ref'])['payload']
    row = saved['reviews'][0]
    assert row['discovery'] == dict(search_ref=search['search_ref'], publication_source='MED',
                                    publication_id=DISC_PMID)
    assert row['discovery_project_ref'] == project
    assert row['discovery_expected_identity'] == [dict(type='pmcid', value=DISC_PMCID),
                                                  dict(type='pmid', value=DISC_PMID),
                                                  dict(type='doi', value=DISC_DOI)]
    assert row['identity_status'] == 'matched' and row['eligibility'] == 'qualified_experimental'
    assert 'reference_id' not in row
    refs = store.get(result['review_ref'])['refs']
    assert search['search_ref'] in refs and disc in refs
    assert store.get(search['search_ref'])['payload']['request_ref'] == search['request_ref']
    page = p.operate(store, project, 'inspect_background_review', dict(review_ref=result['review_ref']))
    assert page['items'][0]['discovery']['publication_source'] == 'MED'
    assert page['items'][0]['identity_status'] == 'matched'
    assert p.operate(store, project, 'review_expt_background',
                     discovery_args(env['index'], env['mm'], [review])) == result
    revised = p.operate(store, project, 'review_expt_background',
                        discovery_args(env['index'], env['mm'],
                                       [discovery_review(disc, search['search_ref'], METHOD2, RESULT2,
                                                         fulltext_reviewed=False)]))
    assert revised['review_ref'] != result['review_ref'] and revised['status'] == 'incomplete'
    assert store.get(result['review_ref'])['payload'] == saved
    manifest = bundles.export_bundle(store, p.snapshot(store, project), tmp_path/'bundle')
    for ref in (search['search_ref'], disc):
        assert ref in manifest['objects_and_blobs']


def test_cross_project_fetch_operator_admit_and_search_replacement(tmp_path):
    store, project_a, mm, expt, _ = indexed(tmp_path, literature_requests=2)
    transport = discovery_transport([disc_row()], {DISC_PMCID: fetched_xml()})
    search = p.operate(store, project_a, 'search_literature', dict(query='discovered persistence study'),
                       literature_transport=transport)
    fetch = p.operate(store, project_a, 'fetch_literature',
                      dict(search_ref=search['search_ref'], pmcid=DISC_PMCID), literature_transport=transport)
    disc = fetch['source_ref']
    project_b = p.create(store, dict(scenario='Second project imports fetched material.', mode='reproduction',
                                     source_refs=[mm], policy=dict(max_literature_requests=1)))
    index_b = p.operate(store, project_b, 'index_mm_background', dict(source_ref=mm))['index_ref']
    p.operate(store, project_b, 'admit_source',
              dict(source_ref=disc, reason='Operator imports the full text fetched in the original project.'),
              actor='operator')
    review = discovery_review(disc, search['search_ref'], METHOD2, RESULT2,
                              coverage=[dict(requirement_id='persistence',
                                             explanation='Imported full text covers persistence.',
                                             evidence=[anchor(disc, RESULT2)])])
    result = p.operate(store, project_b, 'review_expt_background', discovery_args(index_b, mm, [review]))
    assert result['status'] == 'complete' and result['qualified_count'] == 1
    saved = store.get(result['review_ref'])['payload']['reviews'][0]
    assert saved['discovery_project_ref'] == project_a
    assert search['search_ref'] in store.get(result['review_ref'])['refs']
    replacement = p.operate(store, project_b, 'search_literature', dict(query='replacement search query'),
                            literature_transport=transport)
    swapped = discovery_review(disc, replacement['search_ref'], METHOD2, RESULT2)
    with pytest.raises(ValueError):
        p.operate(store, project_b, 'review_expt_background', discovery_args(index_b, mm, [swapped]))
    assert p.inspect(store, project_b)['state'] == 'ready'
    assert len(store.list(bg.REVIEW)) == 1
    project_c = p.create(store, dict(scenario='Third project never admitted the source.', mode='reproduction',
                                     source_refs=[mm]))
    index_c = p.operate(store, project_c, 'index_mm_background', dict(source_ref=mm))['index_ref']
    with pytest.raises(ValueError):
        p.operate(store, project_c, 'review_expt_background', discovery_args(index_c, mm, [review]))
    assert p.inspect(store, project_c)['state'] == 'ready'


@pytest.mark.parametrize('defect', ['forged_search', 'unknown_publication', 'title_selection',
                                    'both_bindings', 'no_binding', 'discovery_without_source',
                                    'mm_as_source', 'unfetched_source'])
def test_discovery_binding_rejections(tmp_path, defect):
    env = fetched(tmp_path)
    store, project, search, fetch = env['store'], env['project'], env['search'], env['fetch']
    review = discovery_review(fetch['source_ref'], search['search_ref'], METHOD2, RESULT2)
    if defect == 'forged_search':
        forged = store.put(literature.SEARCH, dict(version=literature.VERSION, project_ref=project,
                                                   query='forged replacement', hit_count=1,
                                                   results=[disc_row()], notice='forged'), refs=(project,))
        review['discovery']['search_ref'] = forged
    elif defect == 'unknown_publication':
        review['discovery']['publication_id'] = '5555555'
    elif defect == 'title_selection':
        review['discovery']['publication_id'] = disc_row()['title']
    elif defect == 'both_bindings':
        review['reference_id'] = 'R1'
    elif defect == 'no_binding':
        review.pop('discovery')
    elif defect == 'discovery_without_source':
        review.pop('source_ref')
    elif defect == 'mm_as_source':
        review['source_ref'] = env['mm']
    else:
        review['source_ref'] = env['expt']
        review['methods_evidence'] = [anchor(env['expt'], METHOD)]
        review['results_evidence'] = [anchor(env['expt'], RESULT)]
    with pytest.raises(ValueError):
        p.operate(store, project, 'review_expt_background', discovery_args(env['index'], env['mm'], [review]))
    assert p.inspect(store, project)['state'] == 'ready'
    assert not store.list(bg.REVIEW)


def test_ambiguous_publication_selection_is_rejected(tmp_path):
    env = fetched(tmp_path, rows=[disc_row(), disc_row()])
    store, project, search, fetch = env['store'], env['project'], env['search'], env['fetch']
    review = discovery_review(fetch['source_ref'], search['search_ref'], METHOD2, RESULT2)
    with pytest.raises(ValueError, match='exactly one structured search row'):
        p.operate(store, project, 'review_expt_background', discovery_args(env['index'], env['mm'], [review]))
    assert p.inspect(store, project)['state'] == 'ready'
    assert not store.list(bg.REVIEW)


@pytest.mark.parametrize('field,value', [('doi', '10.9000/conflicting'), ('pmid', '22222222')])
def test_legacy_discovery_identity_conflicts_stay_pending(tmp_path, field, value, monkeypatch):
    # Simulate a source admitted before intake identity validation was added.
    with monkeypatch.context() as legacy:
        legacy.setattr(literature, '_check_publication_identity', lambda *args: None)
        env = fetched(tmp_path, xml={DISC_PMCID: fetched_xml(**{field: value})})
    store, project, search, fetch = env['store'], env['project'], env['search'], env['fetch']
    disc = fetch['source_ref']
    review = discovery_review(disc, search['search_ref'], METHOD2, RESULT2,
                              coverage=[dict(requirement_id='persistence', explanation='Claimed anyway.',
                                             evidence=[anchor(disc, RESULT2)])])
    result = p.operate(store, project, 'review_expt_background', discovery_args(env['index'], env['mm'], [review]))
    assert result['status'] == 'incomplete' and result['qualified_count'] == 0
    row = store.get(result['review_ref'])['payload']['reviews'][0]
    assert row['identity_status'] == 'identity_mismatch' and row['eligibility'] == 'pending_review'
    assert result['uncovered_requirement_ids'] == ['persistence']


@pytest.mark.parametrize('study,modeling', [('experimental', 'present'), ('mathematical_modeling', 'absent'),
                                            ('review', 'absent')])
def test_discovery_modeling_and_review_stay_excluded(tmp_path, study, modeling):
    env = fetched(tmp_path)
    store, project, search, fetch = env['store'], env['project'], env['search'], env['fetch']
    disc = fetch['source_ref']
    review = discovery_review(disc, search['search_ref'], METHOD2, RESULT2,
                              study_type=study, mathematical_modeling=modeling)
    result = p.operate(store, project, 'review_expt_background', discovery_args(env['index'], env['mm'], [review]))
    assert result['status'] == 'incomplete' and result['qualified_count'] == 0
    assert store.get(result['review_ref'])['payload']['reviews'][0]['eligibility'] == 'excluded'
    assert result['uncovered_requirement_ids'] == ['persistence']


@pytest.mark.parametrize('support', ['partial', 'uncertain'])
def test_discovery_partial_and_uncertain_keep_gaps(tmp_path, support):
    env = fetched(tmp_path)
    store, project, search, fetch = env['store'], env['project'], env['search'], env['fetch']
    disc = fetch['source_ref']
    review = discovery_review(disc, search['search_ref'], METHOD2, RESULT2,
                              coverage=[dict(requirement_id='persistence', explanation='Limited support.',
                                             support=support, evidence=[anchor(disc, RESULT2)])])
    result = p.operate(store, project, 'review_expt_background', discovery_args(env['index'], env['mm'], [review]))
    assert result['status'] == 'incomplete' and result['qualified_count'] == 1
    assert result['uncovered_requirement_ids'] == ['persistence']
    assert result['partial_requirement_ids'] == (['persistence'] if support == 'partial' else [])
    saved = store.get(result['review_ref'])['payload']
    assert saved['reviews'][0]['coverage'][0]['support'] == support
    assert saved['reviews'][0]['discovery']['publication_id'] == DISC_PMID


def test_direct_and_discovered_sources_jointly_cover(tmp_path):
    store = ArtifactStore(tmp_path/'store')
    refs = ('<ref id="R1"><element-citation><article-title>Experiment</article-title>'
            '<pub-id pub-id-type="doi">10.1234/expt1</pub-id></element-citation></ref>')
    mm = source(store, '10.1234/mm',
                f'<sec><title>Background</title><p>{CONTEXT}<xref ref-type="bibr" rid="R1"/></p></sec>', refs)
    expt1 = source(store, '10.1234/expt1',
                   f'<sec><title>Methods</title><p>{METHOD}</p></sec><sec><title>Results</title><p>{RESULT}</p></sec>')
    project = p.create(store, dict(scenario='Reproduce the stated plasmid study.', mode='reproduction',
                                   source_refs=[mm, expt1], policy=dict(max_literature_requests=2)))
    index = p.operate(store, project, 'index_mm_background', dict(source_ref=mm))['index_ref']
    transport = discovery_transport([disc_row()], {DISC_PMCID: fetched_xml()})
    search = p.operate(store, project, 'search_literature', dict(query='second persistence experiment'),
                       literature_transport=transport)
    fetch = p.operate(store, project, 'fetch_literature',
                      dict(search_ref=search['search_ref'], pmcid=DISC_PMCID), literature_transport=transport)
    disc = fetch['source_ref']
    reviews = [joint_review('R1', expt1, METHOD, RESULT,
                            [dict(requirement_id='persistence', explanation='Partial direct paper.',
                                  support='partial', evidence=[anchor(expt1, RESULT)])]),
               discovery_review(disc, search['search_ref'], METHOD2, RESULT2,
                                [dict(requirement_id='persistence', explanation='Partial discovered paper.',
                                      support='partial', evidence=[anchor(disc, RESULT2)])])]
    joint = [joint_claim('persistence', 'full', [anchor(expt1, RESULT), anchor(disc, RESULT2)])]
    result = p.operate(store, project, 'review_expt_background',
                       discovery_args(index, mm, reviews, joint=joint))
    assert result['status'] == 'complete' and result['qualified_count'] == 2
    assert result['discovery_review_count'] == 1 and result['partial_requirement_ids'] == []
    saved = store.get(result['review_ref'])['payload']
    assert saved['requirement_reviews'][0]['evidence_in_experimental_sections'] is True
    assert search['search_ref'] in store.get(result['review_ref'])['refs']


def test_inspect_truncation_keeps_discovery_binding_identifiable(tmp_path):
    from biomodel_agent.storage import canonical_bytes
    env = fetched(tmp_path)
    store, project, search, fetch = env['store'], env['project'], env['search'], env['fetch']
    disc = fetch['source_ref']
    coverage = [dict(requirement_id='persistence', explanation=f'Coverage block {i}.',
                     evidence=[anchor(disc, RESULT2)] * 8) for i in range(16)]
    review = discovery_review(disc, search['search_ref'], METHOD2, RESULT2, coverage=coverage)
    result = p.operate(store, project, 'review_expt_background', discovery_args(env['index'], env['mm'], [review]))
    page = p.operate(store, project, 'inspect_background_review', dict(review_ref=result['review_ref'], limit=1))
    item = page['items'][0]
    assert item['truncated'] is True
    assert item['source_ref'] == disc
    assert item['discovery'] == dict(search_ref=search['search_ref'], publication_source='MED',
                                     publication_id=DISC_PMID)
    assert len(canonical_bytes(page)) < 40000


def test_schema_and_prompt_describe_discovery_binding(tmp_path):
    store, project, mm, expt, index = indexed(tmp_path)
    view = p.inspect(store, project)
    args = research_schema.schemas(view)['review_expt_background']['properties']['args']['properties']
    review = args['reviews']['items']
    assert set(review['required']) == {'study_type', 'mathematical_modeling', 'fulltext_reviewed',
                                       'rationale', 'methods_evidence', 'results_evidence',
                                       'modeling_evidence', 'coverage'}
    discovery = review['properties']['discovery']
    assert discovery['required'] == ['search_ref', 'publication_source', 'publication_id']
    assert discovery['properties']['search_ref']['minLength'] == 64
    rule = review['allOf'][0]
    assert rule['if'] == dict(required=['discovery'])
    assert 'source_ref' in rule['then']['required']
    assert rule['then']['not'] == dict(required=['reference_id'])
    inverse = review['allOf'][1]
    assert inverse['if'] == {'not': dict(required=['discovery'])}
    assert inverse['then'] == dict(required=['reference_id'])
    assert 'discovery' in supervisor.PROMPT and 'publication_id' in supervisor.PROMPT


def test_discovery_identifier_normalization_qualifies_actual_publication(tmp_path):
    env = fetched(tmp_path, rows=[disc_row(doi='https://doi.org/10.9000/DISCOVERED')])
    review = discovery_review(env['fetch']['source_ref'], env['search']['search_ref'], METHOD2, RESULT2)
    result = p.operate(env['store'], env['project'], 'review_expt_background',
                       discovery_args(env['index'], env['mm'], [review]))
    assert result['qualified_count'] == 1
    saved = env['store'].get(result['review_ref'])['payload']['reviews'][0]
    assert saved['identity_status'] == 'matched'
    assert dict(type='doi', value=DISC_DOI) in saved['discovery_expected_identity']


@pytest.mark.parametrize('defect', ['unlinked_search_request', 'unlinked_fetch_request', 'changed_source_blob'])
def test_discovery_receipt_closure_rejects_inconsistent_sources(tmp_path, defect):
    env = fetched(tmp_path)
    store, project = env['store'], env['project']
    original = store.get(env['fetch']['source_ref'])
    payload = deepcopy(original['payload'])
    refs = list(original['refs'])
    if defect == 'unlinked_search_request':
        old_search = store.get(payload['literature_search_ref'])
        new_search = store.put(literature.SEARCH, old_search['payload'],
            refs=[ref for ref in old_search['refs'] if ref != old_search['payload']['request_ref']])
        refs = [new_search if ref == payload['literature_search_ref'] else ref for ref in refs]
        payload['literature_search_ref'] = new_search
    elif defect == 'unlinked_fetch_request':
        refs.remove(payload['request_ref'])
    else:
        raw = fetched_xml().replace(b'</body>', b'<p>Different raw material.</p></body>')
        blob = store.put_blob(raw)
        refs = [blob if ref == payload['blob_ref'] else ref for ref in refs]
        payload['blob_ref'] = blob
        payload['pages'] = [dict(number=1, text=literature._extract(raw)[0])]
    altered = store.put('source', payload, refs=refs)
    p.operate(store, project, 'admit_source', dict(source_ref=altered, reason='Inspect inconsistent legacy provenance.'),
              actor='operator')
    review = discovery_review(altered, payload['literature_search_ref'], METHOD2, RESULT2)
    with pytest.raises(ValueError):
        p.operate(store, project, 'review_expt_background', discovery_args(env['index'], env['mm'], [review]))
    assert p.inspect(store, project)['state'] == 'ready'
    assert not store.list(bg.REVIEW)


@pytest.mark.parametrize('defect', ['null_reference', 'null_discovery', 'both_null'])
def test_null_fields_cannot_bypass_exclusive_review_bindings(tmp_path, defect):
    env = fetched(tmp_path)
    review = discovery_review(env['fetch']['source_ref'], env['search']['search_ref'], METHOD2, RESULT2)
    if defect == 'null_reference':
        review['reference_id'] = None
    elif defect == 'null_discovery':
        review = arguments(env['index'], env['mm'], env['expt'])['reviews'][0]
        review['discovery'] = None
    else:
        review.update(reference_id=None, discovery=None)
    with pytest.raises(ValueError):
        p.operate(env['store'], env['project'], 'review_expt_background',
                  discovery_args(env['index'], env['mm'], [review]))


@pytest.mark.parametrize('with_reading', [False, True])
def test_json_schema_accepts_both_valid_binding_routes(tmp_path, with_reading):
    import shutil
    import subprocess
    env = fetched(tmp_path)
    packet = (p.operate(env['store'], env['project'], 'start_expt_reading',
                        dict(source_ref=env['fetch']['source_ref'])) if with_reading else None)
    schema = research_schema.schemas(p.inspect(env['store'], env['project']))[
        'review_expt_background']['properties']['args']['properties']['reviews']['items']
    direct = arguments(env['index'], env['mm'], env['expt'])['reviews'][0]
    discovered = discovery_review(env['fetch']['source_ref'], env['search']['search_ref'], METHOD2, RESULT2)
    cases = [dict(name='direct', review=direct, valid=True),
             dict(name='discovered', review=discovered, valid=True),
             dict(name='both', review=dict(discovered, reference_id='R1'), valid=False),
             dict(name='neither', review={k: v for k, v in direct.items() if k != 'reference_id'}, valid=False),
             dict(name='missing_source', review={k: v for k, v in discovered.items() if k != 'source_ref'}, valid=False),
             dict(name='null_source', review=dict(discovered, source_ref=None), valid=False),
             dict(name='pending_direct', review={k: v for k, v in direct.items() if k != 'source_ref'}, valid=True)]
    if packet is not None:
        compact = dict(reading_ref=packet['reading_ref'], discovery=discovered['discovery'])
        cases.extend([dict(name='compact', review=compact, valid=True),
                      dict(name='compact_override', review=dict(compact, fulltext_reviewed=True), valid=False)])
    python = shutil.which('python3')
    if python is None:
        pytest.skip('Optional standards-based JSON Schema verifier unavailable')
    script = '''import importlib.util, json, sys
if importlib.util.find_spec("jsonschema") is None:
    sys.exit(77)
from jsonschema import Draft202012Validator
data = json.load(sys.stdin)
validator = Draft202012Validator(data["schema"])
for case in data["cases"]:
    assert validator.is_valid(case["review"]) == case["valid"], case["name"]
'''
    checked = subprocess.run([python, '-c', script], input=json.dumps(dict(schema=schema, cases=cases)),
                             capture_output=True, text=True)
    if checked.returncode == 77:
        pytest.skip('Optional standards-based JSON Schema verifier unavailable')
    assert checked.returncode == 0, checked.stderr


def test_compact_mm_context_preserves_literal_storage_and_history(tmp_path):
    from test_research_projects import frame
    store, project, mm, expt, index = indexed(tmp_path)
    original_index = deepcopy(store.get(index))
    row = bg.intro_packet(store, project, index)['intro_contexts'][0]
    literal = arguments(index, mm, expt)
    literal['requirements'][0]['mm_context'] = row['mm_context']
    first = p.operate(store, project, 'review_expt_background', literal)
    saved = deepcopy(store.get(first['review_ref']))
    assert bg.requirements_packet(store, project, p.inspect(store, project)) is None
    compact = deepcopy(literal)
    compact['requirements'][0].pop('mm_context')
    compact['requirements'][0]['mm_context_id'] = row['context_id']
    original_input = deepcopy(compact)
    second = p.operate(store, project, 'review_expt_background', compact, key='compact')
    assert p.operate(store, project, 'review_expt_background', compact, key='compact') == second
    assert compact == original_input
    assert store.get(second['review_ref'])['payload']['requirements'] == saved['payload']['requirements']
    p.operate(store, project, 'revise_frame',
              dict(frame=frame('strict_reproduction'), reason='Choose a development frame.'), actor='operator')
    third = p.operate(store, project, 'review_expt_background', compact)
    canonical = deepcopy(literal)
    canonical['requirements'] = store.get(third['review_ref'])['payload']['requirements']
    fourth = p.operate(store, project, 'review_expt_background', canonical)
    assert store.get(fourth['review_ref'])['payload']['requirements'] == saved['payload']['requirements']
    assert store.get(first['review_ref']) == saved
    assert store.get(index) == original_index
    manifest = bundles.export_bundle(store, p.snapshot(store, project), tmp_path/'bundle')
    for ref in [index, mm, expt, first['review_ref'], fourth['review_ref'],
                store.get(mm)['payload']['blob_ref']]:
        assert ref in manifest['objects_and_blobs']


@pytest.mark.parametrize('binding', [
    {}, {'mm_context_id': None}, {'mm_context_id': 0}, {'mm_context_id': []},
    {'mm_context_id': {}}, {'mm_context_id': 'i999'},
    {'mm_context_id': 'i0', 'mm_context': None},
    {'mm_context_id': 'i0', 'source_ref': 'override'},
])
def test_compact_mm_context_rejects_invalid_bindings(tmp_path, binding):
    store, project, mm, expt, index = indexed(tmp_path)
    args = arguments(index, mm, expt)
    requirement = args['requirements'][0]
    requirement.pop('mm_context')
    requirement.update(binding)
    with pytest.raises(ValueError):
        p.operate(store, project, 'review_expt_background', args)
    assert p.inspect(store, project)['background_review_refs'] == []


def test_literal_mm_context_does_not_require_intro_packet(tmp_path, monkeypatch):
    store, project, mm, expt, index = indexed(tmp_path)
    monkeypatch.setattr(bg, 'intro_packet', lambda *args: None)
    args = arguments(index, mm, expt)
    assert p.operate(store, project, 'review_expt_background', args)['qualified_count'] == 1
    args['requirements'][0].pop('mm_context')
    args['requirements'][0]['mm_context_id'] = 'i0'
    with pytest.raises(ValueError, match='no compact'):
        p.operate(store, project, 'review_expt_background', args)


def test_compact_mm_context_rejects_other_project_index(tmp_path):
    store, project, mm, expt, index = indexed(tmp_path)
    other = p.create(store, dict(scenario='Review another project.', mode='reproduction',
                                source_refs=[mm, expt]))
    args = arguments(index, mm, expt)
    args['requirements'][0].pop('mm_context')
    args['requirements'][0]['mm_context_id'] = 'i0'
    with pytest.raises(ValueError, match='outside successful project history'):
        p.operate(store, other, 'review_expt_background', args)


def test_mm_context_schema_accepts_exactly_one_binding(tmp_path):
    import shutil
    import subprocess
    store, project, mm, expt, index = indexed(tmp_path)
    schema = research_schema.schemas(p.inspect(store, project))['review_expt_background'][
        'properties']['args']['properties']['requirements']['items']
    base = dict(id='persistence', question='What observations establish persistence?')
    literal = dict(base, mm_context=anchor(mm, CONTEXT))
    compact = dict(base, mm_context_id='i0')
    invalid = [base, dict(compact, mm_context=None), dict(literal, mm_context_id=None),
               dict(base, mm_context_id=None), dict(base, mm_context_id='other'),
               dict(compact, source_ref=mm), dict(compact, id='and')]
    python = shutil.which('python3')
    if python is None:
        pytest.skip('Optional standards-based JSON Schema verifier unavailable')
    script = '''import importlib.util, json, sys
if importlib.util.find_spec("jsonschema") is None:
    sys.exit(77)
from jsonschema import Draft202012Validator
data = json.load(sys.stdin)
validator = Draft202012Validator(data["schema"])
Draft202012Validator.check_schema(data["schema"])
assert all(validator.is_valid(row) for row in data["valid"])
assert all(not validator.is_valid(row) for row in data["invalid"])
'''
    result = subprocess.run([python, '-c', script], text=True, capture_output=True,
        input=json.dumps(dict(schema=schema, valid=[literal, compact], invalid=invalid)))
    if result.returncode == 77:
        pytest.skip('Optional standards-based JSON Schema verifier unavailable')
    assert result.returncode == 0, result.stderr
