"""Raw PDF/CSV preservation, explicit review lineage, conversion negatives and direct validator boundaries."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import pytest

from biomodel_agent import evidence,material_review as review,observation_data as observations,research_projects as projects
from biomodel_agent.storage import ArtifactStore


def _raster_pdf(engine,path,text='CONTROL SAMPLE 123'):
    document=engine.open();page=document.new_page(width=400,height=200);page.insert_text((35,90),text,fontsize=25)
    image=page.get_pixmap(dpi=144,alpha=False).tobytes('png');document.close()
    scanned=engine.open();page=scanned.new_page(width=400,height=200);page.insert_image(page.rect,stream=image)
    path.write_bytes(scanned.tobytes());scanned.close()
    return path


def test_raster_only_pdf_uses_real_local_ocr_without_promoting_candidate(tmp_path,monkeypatch):
    engine=pytest.importorskip('pymupdf')
    data=os.environ.get('BIOMODEL_TESSDATA') or os.environ.get('TESSDATA_PREFIX')
    if not data or not (Path(data)/'eng.traineddata').is_file():pytest.skip('explicit optional local OCR data not configured')
    path=_raster_pdf(engine,tmp_path/'scan.pdf')
    store=ArtifactStore(tmp_path/'s');source=evidence.ingest_source(store,path)
    assert not evidence._source_page_text(store,source,1).strip()
    region=review.region(store,source,1,ocr=True)
    payload=store.get(region)['payload']
    assert payload['ocr_status']=='candidate_only' and payload['candidate_method']=='mupdf_tesseract_eng'
    assert 'CONTROL' in payload['candidate_text'] and 'SAMPLE' in payload['candidate_text']
    assert payload['review_status']=='unreviewed' and payload['scientific_support']=='unknown'
    assert len(payload['ocr_data_sha256'])==64 and payload['ocr_pdf_blob_ref'] in store.get(region)['refs']
    assert not store.list(review.REVIEW) and len(store.list('source'))==1


def test_raster_ocr_accepts_unicode_tessdata_directory_and_preserves_parent_cwd(tmp_path,monkeypatch):
    """Native Windows regression: the verified data path may contain spaces/Unicode.

    A test-only copy of the configured eng.traineddata exercises the real embedded
    engine through region(). The old implementation handed the absolute Unicode
    tessdata path to pdfocr_tobytes and failed on native Windows; the isolated
    worker must instead run there with tessdata='.' and leave the parent cwd alone.
    """
    engine=pytest.importorskip('pymupdf')
    configured=os.environ.get('BIOMODEL_TESSDATA') or os.environ.get('TESSDATA_PREFIX')
    source_data=Path(configured)/'eng.traineddata' if configured else None
    if source_data is None or not source_data.is_file():pytest.skip('explicit optional local OCR data not configured')
    tessdata=tmp_path/'中文 OCR 数据';tessdata.mkdir()
    copied=tessdata/'eng.traineddata';copied.write_bytes(source_data.read_bytes())
    monkeypatch.setenv('BIOMODEL_TESSDATA',str(tessdata))
    monkeypatch.delenv('TESSDATA_PREFIX',raising=False)
    path=_raster_pdf(engine,tmp_path/'unicode-scan.pdf')
    raw=path.read_bytes()
    store=ArtifactStore(tmp_path/'s');source=evidence.ingest_source(store,path)
    parent_cwd=os.getcwd()
    region=review.region(store,source,1,ocr=True)
    assert os.getcwd()==parent_cwd
    payload=store.get(region)['payload']
    assert payload['ocr_status']=='candidate_only' and payload['candidate_method']=='mupdf_tesseract_eng'
    assert 'CONTROL' in payload['candidate_text'] and 'SAMPLE' in payload['candidate_text']
    assert payload['ocr_data_sha256']==hashlib.sha256(copied.read_bytes()).hexdigest()
    assert payload['source_ref']==source
    assert store.get_blob(store.get(source)['payload']['blob_ref'])==raw
    assert source in store.get(region)['refs']
    assert payload['ocr_pdf_blob_ref'] in store.get(region)['refs']
    assert payload['review_status']=='unreviewed' and payload['scientific_support']=='unknown'
    assert not store.list(review.REVIEW) and len(store.list('source'))==1


def test_pdf_region_and_reviewed_revisions_retain_original_bytes(tmp_path):
    engine=pytest.importorskip('pymupdf')
    document=engine.open();page=document.new_page(width=300,height=200);page.insert_text((30,60),'Equation dx/dt = -k*x.')
    raw=document.tobytes();document.close();path=tmp_path/'equation.pdf';path.write_bytes(raw)
    store=ArtifactStore(tmp_path/'s');source=evidence.ingest_source(store,path)
    region=review.region(store,source,1,bbox=[10,20,290,100],dpi=100)
    candidate=store.get(region)['payload']
    assert candidate['review_status']=='unreviewed' and 'Equation' in candidate['candidate_text']
    assert store.get_blob(candidate['image_blob_ref']).startswith(b'\x89PNG')
    first=review.transcribe(store,region,'Equation dx/dt = -k*x.','reviewer','Read the original equation.')
    second=review.transcribe(store,region,'Equation dx/dt = -k*x.\nParameter k = 0.4.','reviewer','Add the separately checked declaration.',parent_ref=first['review_ref'])
    assert store.get(second['review_ref'])['payload']['revision']==1
    assert store.get_blob(store.get(source)['payload']['blob_ref'])==raw==path.read_bytes()
    assert evidence._source_page_text(store,first['source_ref'],1)=='Equation dx/dt = -k*x.'
    assert first['review_ref'] in store.get(second['review_ref'])['refs']
    assert evidence.source_summary(store,second['source_ref'])['authority']=='attributed_human_transcription_not_independent_review'


@pytest.mark.parametrize('bbox',[[0,0,0,1],[0,0,float('nan'),1],[True,0,2,3],[0,1,2]])
def test_invalid_region_does_not_write_a_region(tmp_path,bbox):
    engine=pytest.importorskip('pymupdf');document=engine.open();document.new_page();raw=document.tobytes();document.close()
    path=tmp_path/'a.pdf';path.write_bytes(raw);store=ArtifactStore(tmp_path/'s');source=evidence.ingest_source(store,path)
    with pytest.raises(ValueError):review.region(store,source,1,bbox=bbox)
    assert not store.list(review.REGION)


@pytest.mark.parametrize('defect',[
    'source_text','source_blob','source_original','source_refs','source_marker',
    'review_content','review_refs','review_authority','parent_revision',
])
def test_reviewed_source_reads_recheck_transcription_provenance(tmp_path,defect):
    import base64
    from test_storage_evidence import _write_pdf
    path=tmp_path/'original.pdf'
    _write_pdf(path,['Initial x = 1.'])
    store=ArtifactStore(tmp_path/'s')
    original=evidence.ingest_source(store,path)
    # A saved region is sufficient for reading its attributed transcription.
    # Rendering is covered separately and is optional on a consuming machine.
    png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZQmcAAAAASUVORK5CYII=')
    image=store.put_blob(png)
    region=store.put(review.REGION,dict(version=1,source_ref=original,page=1,
        image_blob_ref=image,dpi=100,bbox=[0,0,1,1],review_status='unreviewed',
        scientific_support='unknown'),refs=(original,image))
    result=review.transcribe(store,region,'Initial x = 1.','reviewer','Read the original region.')
    if defect=='parent_revision':
        result=review.transcribe(store,region,'Initial x = 2.','reviewer','Correct the transcription.',parent_ref=result['review_ref'])
    source=store.get(result['source_ref'])
    if defect=='source_text':
        source['payload']['pages'][0]['text']='Initial x = 99.'
    elif defect=='source_blob':
        source['payload']['blob_ref']=store.put_blob(b'Initial x = 99.')
    elif defect=='source_original':
        other=tmp_path/'other.pdf'
        other.write_bytes(path.read_bytes())
        source['payload']['original_source_ref']=evidence.ingest_source(store,other)
    elif defect=='source_refs':
        source['refs']=[source['payload']['blob_ref']]
    elif defect=='source_marker':
        del source['payload']['transcription_ref']
    else:
        record=store.get(result['review_ref'])
        if defect=='review_content':
            record['payload']['content_blob_ref']=store.put_blob(b'Initial x = 99.')
        elif defect=='review_refs':
            record['refs']=[record['payload']['content_blob_ref']]
        elif defect=='review_authority':
            record['payload']['authority']='independent_review'
        else:
            parent=store.get(record['payload']['parent_ref'])
            parent['payload']['revision']=9
            forged_parent=store.put(review.REVIEW,parent['payload'],refs=parent['refs'])
            old_parent=record['payload']['parent_ref']
            record['payload']['parent_ref']=forged_parent
            record['refs']=[forged_parent if ref==old_parent else ref for ref in record['refs']]
        forged_review=store.put(review.REVIEW,record['payload'],refs=record['refs'])
        source['payload']['transcription_ref']=forged_review
        source['refs']=[forged_review if ref==result['review_ref'] else ref for ref in source['refs']]
    forged=store.put('source',source['payload'],refs=source['refs'])
    before=store.list()
    with pytest.raises(ValueError):
        evidence.source_summary(store,forged)
    with pytest.raises(ValueError):
        evidence.add_claim(store,forged,1,0,7,'Inspect only attributed source text.')
    assert store.list()==before


PNG_1X1='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZQmcAAAAASUVORK5CYII='


def _attributed(tmp_path):
    """Pure-python fixture: real ArtifactStore, a saved PNG region and one transcription.

    Deliberately engine-free: the PDF is written by the pure-python helper and the
    region is stored directly, so the direct validator tests below also run on a
    machine without the optional PDF engine.
    """
    import base64
    from test_storage_evidence import _write_pdf
    path=tmp_path/'original.pdf';_write_pdf(path,['Initial x = 1.'])
    store=ArtifactStore(tmp_path/'s')
    original=evidence.ingest_source(store,path)
    image=store.put_blob(base64.b64decode(PNG_1X1))
    region=store.put(review.REGION,dict(version=1,source_ref=original,page=1,
        image_blob_ref=image,dpi=100,bbox=[0,0,1,1],review_status='unreviewed',
        scientific_support='unknown'),refs=(original,image))
    result=review.transcribe(store,region,'Initial x = 1.','reviewer','Read the original region.')
    return store,region,result


def test_validate_review_accepts_root_and_revisions_read_only(tmp_path):
    store,region,first=_attributed(tmp_path)
    before=store.list()
    head=review.validate_review(store,first['review_ref'])
    assert head['revision']==0 and head['authority']
    assert store.get_blob(head['content_blob_ref']).decode()=='Initial x = 1.'
    assert review.validate_source(store,first['source_ref'])==head
    assert store.list()==before
    second=review.transcribe(store,region,'Initial x = 2.','reviewer','Correct the transcription.',parent_ref=first['review_ref'])
    before=store.list()
    head=review.validate_review(store,second['review_ref'])
    assert head['revision']==1 and head['parent_ref']==first['review_ref']
    assert store.get_blob(head['content_blob_ref']).decode()=='Initial x = 2.'
    assert review.validate_source(store,second['source_ref'])==head
    assert evidence.source_summary(store,second['source_ref'])['authority']=='attributed_human_transcription_not_independent_review'
    assert store.list()==before


@pytest.mark.parametrize('defect',['revision','authority','content_blob_ref','blank_content','empty_content'])
def test_validate_review_rejects_head_missing_fields_or_blank_content(tmp_path,defect):
    store,region,first=_attributed(tmp_path)
    record=store.get(first['review_ref'])
    if defect=='revision':del record['payload']['revision']
    elif defect=='authority':del record['payload']['authority']
    elif defect=='content_blob_ref':del record['payload']['content_blob_ref']
    else:
        old=record['payload']['content_blob_ref']
        blank=store.put_blob(b'' if defect=='empty_content' else b' \t\n  ')
        record['payload']['content_blob_ref']=blank
        record['refs']=[blank if ref==old else ref for ref in record['refs']]
    forged=store.put(review.REVIEW,record['payload'],refs=record['refs'])
    source=store.get(first['source_ref'])
    source['payload']['transcription_ref']=forged
    source['refs']=[forged if ref==first['review_ref'] else ref for ref in source['refs']]
    rebind=store.put('source',source['payload'],refs=source['refs'])
    with pytest.raises(ValueError):review.validate_review(store,forged)
    with pytest.raises(ValueError):review.validate_source(store,rebind)


def test_validate_review_rejects_region_kind_impersonating_a_review(tmp_path):
    store,region,first=_attributed(tmp_path)
    head=store.get(first['review_ref'])
    fake=store.put(review.REGION,head['payload'],refs=head['refs'])
    with pytest.raises(ValueError):review.validate_review(store,fake)
    with pytest.raises(ValueError):review.validate_review(store,region)


@pytest.mark.parametrize('breaks',['review_refs','region_refs'])
def test_validate_review_requires_region_refs_closure(tmp_path,breaks):
    store,region,first=_attributed(tmp_path)
    record=store.get(first['review_ref'])
    if breaks=='review_refs':
        record['refs']=[ref for ref in record['refs'] if ref!=region]
    else:
        broken=store.put(review.REGION,store.get(region)['payload'],refs=(store.get(region)['payload']['source_ref'],))
        record['refs']=[broken if ref==region else ref for ref in record['refs']]
    forged=store.put(review.REVIEW,record['payload'],refs=record['refs'])
    with pytest.raises(ValueError):review.validate_review(store,forged)


@pytest.mark.parametrize('pages',[None,2,'Initial x = 1.',True])
def test_validate_source_rejects_malformed_transcription_source_pages(tmp_path,pages):
    store,region,first=_attributed(tmp_path)
    source=store.get(first['source_ref'])
    source['payload']['pages']=pages
    forged=store.put('source',source['payload'],refs=source['refs'])
    with pytest.raises(ValueError):review.validate_source(store,forged)


def test_validate_source_ignores_unmarked_utf8_jats_and_binary_sources(tmp_path):
    from test_storage_evidence import _write_pdf
    store,region,first=_attributed(tmp_path)
    source=store.get(first['source_ref'])
    def unmarked(blob):
        payload=deepcopy(source['payload']);payload.pop('transcription_ref');payload.pop('original_source_ref')
        payload['blob_ref']=store.put_blob(blob)
        refs=[ref for ref in source['refs'] if ref!=first['review_ref']]
        return store.put('source',payload,refs=refs+[payload['blob_ref']])
    assert review.validate_source(store,unmarked(b'\xff\xfb\x00not utf8 text')) is None
    assert review.validate_source(store,unmarked('time,signal\n0,1\n'.encode('utf-8'))) is None
    jats='<?xml version="1.0"?><article>JATS full text is not a transcription.</article>'
    assert review.validate_source(store,unmarked(jats.encode('utf-8'))) is None
    pdf_path=tmp_path/'scan.pdf';_write_pdf(pdf_path,['Untranscribed scan page.'])
    assert review.validate_source(store,evidence.ingest_source(store,pdf_path)) is None
    csv_path=tmp_path/'plain.csv';csv_path.write_text('time,signal\n0,1\n',encoding='utf-8')
    assert review.validate_source(store,evidence.ingest_source(store,csv_path)) is None


def _rebind_region(store,first,region_ref):
    """Clone first's review and attributed source onto region_ref, altering nothing else.

    The saved review is re-put with payload.region_ref retargeted to region_ref and the
    old region ref swapped out of refs, while original_source_ref is taken from
    region_ref's payload.source_ref. The attributed source is then re-put with
    transcription_ref, original_source_ref and the review ref updated the same way.
    All remaining fields and the full ref closure are preserved verbatim and the
    region node itself is never touched. Returns the new source ref.
    """
    bound=store.get(region_ref)['payload']['source_ref']
    record=store.get(first['review_ref'])
    payload=deepcopy(record['payload']);refs=list(record['refs'])
    old=payload['region_ref']
    payload['region_ref']=region_ref
    payload['original_source_ref']=bound
    head=store.put(review.REVIEW,payload,refs=[region_ref if ref==old else ref for ref in refs])
    source=store.get(first['source_ref'])
    spayload=deepcopy(source['payload']);srefs=list(source['refs'])
    spayload['transcription_ref']=head;spayload['original_source_ref']=bound
    return store.put('source',spayload,refs=[head if ref==first['review_ref'] else ref for ref in srefs])


def _rebind_original_pdf_pages(store,region,first,pages):
    """Clone only the original PDF source's pages, retarget a cloned region, and rebind.

    The cloned PDF keeps its payload (apart from pages) and refs. The cloned region
    keeps its complete payload except source_ref, replacing the old PDF ref with the
    clone in refs. _rebind_region is the only operation that moves the review/source
    binding, so any rejection belongs to validate_source rather than a broken chain.
    """
    original_pdf=store.get(region)['payload']['source_ref']
    pdf=store.get(original_pdf)
    pdf_payload=deepcopy(pdf['payload'])
    pdf_payload['pages']=deepcopy(pages)
    cloned_pdf=store.put('source',pdf_payload,refs=pdf['refs'])
    saved_region=store.get(region)
    region_payload=deepcopy(saved_region['payload'])
    region_payload['source_ref']=cloned_pdf
    region_refs=[cloned_pdf if ref==original_pdf else ref for ref in saved_region['refs']]
    cloned_region=store.put(review.REGION,region_payload,refs=region_refs)
    return _rebind_region(store,first,cloned_region),cloned_pdf,cloned_region


@pytest.mark.parametrize('pages',[
    None,
    7,
    'Initial x = 1.',
    [dict(number=True,text='Initial x = 1.')],
    [dict(number=1,text=123)],
    [None],
    [7],
    ['Initial x = 1.'],
],ids=['none','number','string','bool-page-number','nonstring-page-text',
       'none-page-entry','number-page-entry','string-page-entry'])
def test_validate_source_rejects_malformed_original_pdf_pages(tmp_path,pages):
    store,region,first=_attributed(tmp_path)
    bound,cloned_pdf,cloned_region=_rebind_original_pdf_pages(store,region,first,pages)
    assert store.get(cloned_pdf)['payload']['pages']==pages
    assert store.get(cloned_region)['payload']['source_ref']==cloned_pdf
    assert cloned_pdf in store.get(cloned_region)['refs']
    assert store.get(bound)['payload']['original_source_ref']==cloned_pdf
    rewired=store.get(bound)['payload']['transcription_ref']
    before=store.list()
    # The rebuilt review chain is valid, so only the PDF page payload can cause failure.
    assert review.validate_review(store,rewired)['revision']==0
    assert store.list()==before
    with pytest.raises(ValueError):review.validate_source(store,bound)
    assert store.list()==before


def test_validate_source_accepts_valid_original_pdf_dict_page_after_rebind(tmp_path):
    store,region,first=_attributed(tmp_path)
    pdf=store.get(store.get(region)['payload']['source_ref'])
    pages=deepcopy(pdf['payload']['pages'])
    assert pages and all(isinstance(page,dict) for page in pages)
    bound,cloned_pdf,cloned_region=_rebind_original_pdf_pages(store,region,first,pages)
    assert store.get(cloned_pdf)['payload']['pages']==pages
    assert store.get(cloned_region)['payload']['source_ref']==cloned_pdf
    rewired=store.get(bound)['payload']['transcription_ref']
    before=store.list()
    head=review.validate_review(store,rewired)
    assert review.validate_source(store,bound)==head
    assert store.list()==before


def test_validate_source_rejects_region_refs_missing_image_after_rebind(tmp_path):
    store,region,first=_attributed(tmp_path)
    saved=store.get(region)
    image=saved['payload']['image_blob_ref']
    assert image in saved['refs']
    broken=store.put(review.REGION,deepcopy(saved['payload']),
        refs=[ref for ref in saved['refs'] if ref!=image])
    assert store.get(broken)['payload']==saved['payload']
    assert image not in store.get(broken)['refs']
    bad=_rebind_region(store,first,broken)
    rewired=store.get(bad)['payload']['transcription_ref']
    before=store.list()
    # Region payload and the review chain remain valid; only its refs closure is short.
    assert review.validate_review(store,rewired)['revision']==0
    assert store.list()==before
    with pytest.raises(ValueError):review.validate_source(store,bad)
    assert store.list()==before


def test_validate_source_applicability_gate_accepts_marked_non_utf8_parser(tmp_path):
    """Unit applicability gate only: this is not a native-JATS ingestion test.

    The marked attributed source remains otherwise complete; only its parser id is
    changed to a supported non-UTF8 identifier, which makes validate_source
    inapplicable and therefore return None without writing.
    """
    store,region,first=_attributed(tmp_path)
    source=store.get(first['source_ref'])
    assert source['payload']['transcription_ref']==first['review_ref']
    assert source['payload']['original_source_ref']==store.get(region)['payload']['source_ref']
    payload=deepcopy(source['payload'])
    source['payload']['parser']='jats_visual_candidate_v1'
    assert {key:value for key,value in source['payload'].items() if key!='parser'}=={
        key:value for key,value in payload.items() if key!='parser'}
    gated=store.put('source',source['payload'],refs=source['refs'])
    before=store.list()
    assert review.validate_source(store,gated) is None
    assert store.list()==before


def test_validate_source_region_kind_gate_with_fully_rebound_chain(tmp_path):
    store,region,first=_attributed(tmp_path)
    text=store.get_blob(store.get(first['review_ref'])['payload']['content_blob_ref']).decode()
    # Positive control: rebinding onto the same region is content-addressed idempotent,
    # so the complete binding graph stays valid and validation reads without writing.
    good=_rebind_region(store,first,region)
    before=store.list()
    head=review.validate_source(store,good)
    assert head['revision']==0 and store.get_blob(head['content_blob_ref']).decode()==text
    assert evidence._source_page_text(store,good,1)==text=='Initial x = 1.'
    assert store.list()==before
    # Negative control: the very same region payload and refs saved under a wrong node kind.
    wrong=store.put(review.REVIEW,store.get(region)['payload'],refs=store.get(region)['refs'])
    assert store.get(wrong)['kind']==review.REVIEW
    assert store.get(wrong)['payload']==store.get(region)['payload'] and store.get(wrong)['refs']==store.get(region)['refs']
    bad=_rebind_region(store,first,wrong)
    rewired=store.get(bad)['payload']['transcription_ref']
    assert rewired!=first['review_ref'] and wrong in store.get(rewired)['refs']
    before=store.list()
    # The rebuilt review chain still validates, so the rejection below can only come
    # from validate_source's own region kind and PDF attribution inspection.
    assert review.validate_review(store,rewired)['revision']==0
    with pytest.raises(ValueError):review.validate_source(store,bad)
    assert store.list()==before


def table(tmp_path,text='time [minute],signal [mM]\r\n0,1\r\n1,0.5\r\n'):
    store=ArtifactStore(tmp_path/'s');path=tmp_path/'supplement.csv';path.write_bytes(text.encode('utf-8'))
    source=evidence.ingest_source(store,path)
    def mapping(column,source_unit,target_unit,factor):return dict(column=column,source_unit=source_unit,target_unit=target_unit,multiplier=factor,offset=0.)
    spec=dict(delimiter=',',time=mapping('time [minute]','minute','second',60.),
        observables={'X':mapping('signal [mM]','mM','uM',1000.)},scales={'X':1000.},threshold=.001,conditions={'treatment':'control'})
    return store,source,spec


def test_mapping_keeps_raw_cells_and_conversion_ledger_and_reference(tmp_path):
    store,source,spec=table(tmp_path);before=evidence._source_page_text(store,source,1)
    result=observations.prepare(store,source,spec,'reviewer','Explicitly reviewed time and concentration scales.')
    ref=projects._reference(store,result['reference_ref']);ledger=store.get(result['review_ref'])['payload']['ledger']
    assert ref['reference']['times']==[0.,60.] and ref['reference']['values']['X']==[1000.,500.]
    assert ledger['X']['raw_cells']==['1','0.5'] and ledger['X']['mapping']['multiplier']==1000.
    assert ref['independent'] is False and ref['scientific_support']=='unknown'
    assert evidence._source_page_text(store,source,1)==before
    assert store.get_blob(store.get(source)['payload']['blob_ref']).decode()==before


@pytest.mark.parametrize('defect',['unit','missing','nonfinite','duplicate_column','unsorted','forged_ledger'])
def test_mapping_rejects_ambiguity_and_recomputes_saved_values(tmp_path,defect):
    text='time [minute],signal [mM]\n0,1\n1,0.5\n'
    if defect=='missing':text=text.replace('1,0.5','1,')
    if defect=='nonfinite':text=text.replace('1,0.5','1,nan')
    if defect=='unsorted':text=text.replace('1,0.5','0,0.5')
    store,source,spec=table(tmp_path,text)
    if defect=='unit':spec['observables']['X']['source_unit']='M'
    if defect=='duplicate_column':spec['observables']['X']=deepcopy(spec['time'])
    if defect=='forged_ledger':
        result=observations.prepare(store,source,spec,'reviewer','Frozen mapping.')
        obj=store.get(result['review_ref']);obj['payload']['ledger']['X']['values'][0]=999.
        forged=store.put(observations.REVIEW,obj['payload'],refs=obj['refs'])
        original=store.get(result['reference_ref']);original['payload']['observation_review_ref']=forged
        ref=store.put(projects.REFERENCE,original['payload'],refs=(forged,))
        with pytest.raises(ValueError,match='ledger'):projects._reference(store,ref)
    else:
        with pytest.raises(ValueError):observations.prepare(store,source,spec,'reviewer','Reject this mapping.')
        assert not store.list(observations.REVIEW)
