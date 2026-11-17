"""Bounded local PDF region candidates and attributable immutable revisions."""
from contextlib import redirect_stdout
import io
import hashlib
import math
import multiprocessing as mp
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import evidence,model_contracts as c

REGION='material_region'
REVIEW='material_transcription'


def _worker(raw,page,bbox,dpi,ocr,pipe):
    try:
        import pymupdf
        with redirect_stdout(io.StringIO()),pymupdf.open(stream=raw,filetype='pdf') as document:
            if document.needs_pass or not 1<=page<=len(document):raise ValueError('Invalid/encrypted PDF or page.')
            native=document[page-1];rect=native.rect if bbox is None else pymupdf.Rect(bbox)
            if rect.is_empty or not native.rect.contains(rect):raise ValueError('Region must be inside the page.')
            if rect.width*rect.height*(dpi/72)**2>4_000_000:raise ValueError('Region exceeds four million pixels.')
            pix=native.get_pixmap(clip=rect,dpi=dpi,alpha=False);png=pix.tobytes('png')
            candidate=native.get_text(clip=rect)[:100000];method='native_pdf_text';ocr_state='not_requested'
            data_sha=None;ocr_pdf=None
            if ocr:
                data_dir=os.environ.get('BIOMODEL_TESSDATA') or os.environ.get('TESSDATA_PREFIX')
                data_dir=Path(data_dir).resolve() if data_dir else None
                data_file=data_dir/'eng.traineddata' if data_dir is not None else None
                engine=shutil.which('tesseract')
                if data_file is not None and data_file.is_file():
                    data_sha=hashlib.sha256(data_file.read_bytes()).hexdigest()
                    # MuPDF's embedded Tesseract cannot open non-ASCII absolute
                    # tessdata paths on native Windows. This worker is isolated, so
                    # run from the verified data directory and pass an ASCII-relative
                    # path; always restore the worker's cwd.
                    previous_cwd=os.getcwd()
                    try:
                        os.chdir(data_dir)
                        ocr_pdf=pix.pdfocr_tobytes(language='eng',tessdata='.')
                    finally:
                        os.chdir(previous_cwd)
                    if len(ocr_pdf)>16_000_000:raise ValueError('OCR candidate PDF exceeds limit')
                    with pymupdf.open(stream=ocr_pdf,filetype='pdf') as recognized:
                        candidate=recognized[0].get_text()[:100000]
                    method='mupdf_tesseract_eng';ocr_state='candidate_only'
                elif engine is None:ocr_state='unavailable'
                else:
                    with tempfile.TemporaryDirectory(prefix='biomodel-ocr-') as directory:
                        path=Path(directory)/'region.png';path.write_bytes(png)
                        out=subprocess.run([engine,str(path),'stdout','-l','eng','--psm','6'],capture_output=True,timeout=15)
                        if out.returncode==0:
                            candidate=out.stdout.decode('utf-8')[:100000];method='tesseract_eng_psm6';ocr_state='candidate_only'
                        else:ocr_state='failed'
            pipe.send(dict(status='ok',png=png,bbox=list(rect),dpi=dpi,candidate_text=candidate,
                candidate_method=method,ocr_status=ocr_state,review_status='unreviewed',engine='pymupdf',
                engine_version=str(getattr(pymupdf,'__version__','unknown')),ocr_data_sha256=data_sha,ocr_pdf=ocr_pdf,scientific_support='unknown'))
    except Exception as exc:pipe.send(dict(status='error',error_type=type(exc).__name__))
    finally:pipe.close()


def region(store,source_ref,page,*,bbox=None,dpi=144,ocr=False):
    source=store.get(source_ref)
    if source['kind']!='source' or source['payload'].get('parser') not in ('pypdf','pymupdf'):
        raise ValueError('PDF source required')
    c.integer(page,1,10000,'page');c.integer(dpi,72,200,'dpi')
    if type(ocr) is not bool:raise ValueError('ocr must be boolean')
    if bbox is not None:
        if not isinstance(bbox,list) or len(bbox)!=4:raise ValueError('bbox needs four page coordinates')
        for value in bbox:c.number(value,0,100000,'bbox coordinate')
        if bbox[2]<=bbox[0] or bbox[3]<=bbox[1]:raise ValueError('bbox must have positive area')
    raw=store.get_blob(source['payload']['blob_ref'])
    ctx=mp.get_context('spawn');parent,child=ctx.Pipe(duplex=False)
    process=ctx.Process(target=_worker,args=(raw,page,bbox,dpi,ocr,child),daemon=True)
    try:
        process.start();child.close()
        if not parent.poll(30):raise ValueError('PDF region timeout')
        try:out=parent.recv()
        except EOFError as exc:raise ValueError('PDF region worker exited without result') from exc
        if out['status']!='ok':raise ValueError('PDF region could not be rendered: '+out['error_type'])
        png=out.pop('png')
        if not isinstance(png,bytes) or len(png)>8_000_000:raise ValueError('PDF image output exceeds limit')
        blob=store.put_blob(png)
        refs=[source_ref,blob];ocr_pdf=out.pop('ocr_pdf',None)
        if ocr_pdf is not None:
            if not isinstance(ocr_pdf,bytes) or len(ocr_pdf)>16_000_000:raise ValueError('OCR candidate output exceeds limit')
            candidate_ref=store.put_blob(ocr_pdf);refs.append(candidate_ref);out['ocr_pdf_blob_ref']=candidate_ref
        return store.put(REGION,dict(version=1,source_ref=source_ref,page=page,image_blob_ref=blob,**out),refs=tuple(refs))
    finally:
        if process.pid is not None:
            if process.is_alive():process.terminate()
            process.join(.5)
            if process.is_alive():process.kill();process.join(.5)
        parent.close();child.close()


def transcribe(store,region_ref,content,actor,note,*,parent_ref=None):
    original=store.get(region_ref)
    if original['kind']!=REGION:raise ValueError('review requires a frozen PDF region')
    if not isinstance(content,str) or not content.strip() or len(content)>100000:raise ValueError('bounded reviewed transcription required')
    c.label(actor,200);c.label(note,2000)
    revision=0;refs=[region_ref]
    if parent_ref:
        parent=store.get(parent_ref)
        if parent['kind']!=REVIEW or parent['payload']['region_ref']!=region_ref:raise ValueError('transcription parent region differs')
        revision=parent['payload']['revision']+1;refs.append(parent_ref)
        c.integer(revision,1,16,'review revision')
    blob=store.put_blob(content.encode('utf-8'));refs.append(blob)
    review=store.put(REVIEW,dict(version=1,region_ref=region_ref,content_blob_ref=blob,actor=actor,note=note,
        revision=revision,parent_ref=parent_ref,authority='attributed_human_transcription_not_independent_review',
        original_source_ref=original['payload']['source_ref'],scientific_support='unknown'),refs=tuple(refs))
    # New source; neither the PDF bytes nor its extracted text is overwritten.
    source=store.put('source',dict(blob_ref=blob,filename=f'reviewed-region-{revision}.txt',url=None,parser='utf8',parser_version='1',
        pages=[dict(number=1,text=content)],warnings=['Human-reviewed region transcription; whole-paper coverage and biological support remain unknown.'],
        transcription_ref=review,original_source_ref=original['payload']['source_ref']),refs=(blob,review))
    return dict(source_ref=source,review_ref=review)


_REVIEW_FIELDS=('version','region_ref','content_blob_ref','actor','note','revision',
    'parent_ref','authority','original_source_ref','scientific_support')


def _review_object(store,ref):
    obj=store.get(ref)
    if not isinstance(obj,dict) or not isinstance(obj.get('payload'),dict):
        raise ValueError('review object required')
    return obj


def _validate_review_node(store,review,region_ref,source_ref):
    if review.get('kind')!=REVIEW:raise ValueError('material transcription review required')
    payload=review['payload']
    for field in _REVIEW_FIELDS:
        if field not in payload:raise ValueError('review payload missing '+field)
    c.integer(payload['version'],1,1,'review version')
    c.integer(payload['revision'],0,16,'review revision')
    if payload['authority']!='attributed_human_transcription_not_independent_review':
        raise ValueError('review authority must be attributed human transcription, not independent review')
    if payload['scientific_support']!='unknown':raise ValueError('review scientific_support must be unknown')
    c.label(payload['actor'],200);c.label(payload['note'],2000)
    if payload['region_ref']!=region_ref:raise ValueError('review chain region differs')
    if payload['original_source_ref']!=source_ref:raise ValueError('review chain original source differs')
    try:content=store.get_blob(payload['content_blob_ref']).decode('utf-8')
    except UnicodeDecodeError as exc:raise ValueError('review content must be UTF-8 text') from exc
    if not content.strip() or len(content)>100000:raise ValueError('bounded reviewed transcription required')
    refs=review.get('refs')
    expected={region_ref,payload['content_blob_ref']}
    if payload['parent_ref'] is not None:expected.add(payload['parent_ref'])
    if not isinstance(refs,(list,tuple)) or any(not isinstance(ref,str) for ref in refs) \
            or len(set(refs))!=len(refs) or set(refs)!=expected:
        raise ValueError('review refs must close over exactly region, content blob and optional parent')


def validate_review(store,review_ref):
    """Read-only validation of an attributed transcription revision chain.

    Checks the head and every ancestor: kind, version, bounded attributed
    actor/note, non-blank UTF-8 content blob, authority, exact reference
    closure, and a
    parent chain stepping down one revision to a parentless revision 0 that
    shares one region and original source. Returns the head payload.
    """
    head=_review_object(store,review_ref);payload=head['payload']
    node=head;previous=None
    for _ in range(17):
        _validate_review_node(store,node,payload.get('region_ref'),payload.get('original_source_ref'))
        current=node['payload']
        if previous is not None and current['revision']!=previous-1:
            raise ValueError('review revisions must decrease by one per parent')
        previous=current['revision'];parent_ref=current['parent_ref']
        if current['revision']==0:
            if parent_ref is not None:raise ValueError('revision 0 must not have a parent')
            return payload
        if not isinstance(parent_ref,str) or not parent_ref:
            raise ValueError('revisions above 0 require a parent reference')
        node=_review_object(store,parent_ref)
    raise ValueError('review chain exceeds seventeen revisions')


def _fetch(store,ref,what):
    try:obj=store.get(ref)
    except Exception as exc:raise ValueError(what+' reference could not be loaded') from exc
    if not isinstance(obj,dict):raise ValueError(what+' object required')
    return obj


def validate_source(store,source_ref):
    """Read-only attribution check for transcription-derived utf8 sources.

    Applies only to utf8 sources marked with transcription_ref or
    original_source_ref; every other supported source (JATS, plain uploads,
    ingested PDFs) returns None untouched. A marked source must be a source
    record whose refs close over exactly its content blob and head review and
    must agree with validate_review's head payload: the same content blob and
    original PDF, one page carrying exactly that reviewed text, and a
    version-1 region bound to that original PDF at one of its pages, with
    region refs closing over exactly the original PDF, image and optional OCR
    blobs (existence only; no PDF engine import and no OCR re-run). Returns
    the head review payload.
    """
    try:obj=store.get(source_ref)
    except Exception as exc:raise ValueError('source reference could not be loaded') from exc
    if not isinstance(obj,dict):return None
    payload=obj.get('payload')
    if not isinstance(payload,dict):return None
    if payload.get('parser')!='utf8':return None
    if 'transcription_ref' not in payload and 'original_source_ref' not in payload:return None
    if obj.get('kind')!='source':raise ValueError('marked transcription source must have source kind')
    for field in ('blob_ref','transcription_ref','original_source_ref'):
        if not isinstance(payload.get(field),str) or not payload[field]:
            raise ValueError('transcription source missing valid '+field)
    refs=obj.get('refs')
    if not isinstance(refs,(list,tuple)) or any(not isinstance(ref,str) for ref in refs) \
            or len(set(refs))!=len(refs) or set(refs)!={payload['blob_ref'],payload['transcription_ref']}:
        raise ValueError('source refs must close over exactly content blob and head review')
    try:review=validate_review(store,payload['transcription_ref'])
    except ValueError:raise
    except Exception as exc:raise ValueError('transcription review could not be validated') from exc
    if payload['blob_ref']!=review['content_blob_ref']:raise ValueError('source blob differs from review content blob')
    if payload['original_source_ref']!=review['original_source_ref']:raise ValueError('source original PDF differs from review')
    try:content=store.get_blob(payload['blob_ref']).decode('utf-8')
    except UnicodeDecodeError as exc:raise ValueError('source content must be UTF-8 text') from exc
    except Exception as exc:raise ValueError('source content blob could not be loaded') from exc
    pages=payload.get('pages')
    if not isinstance(pages,list) or len(pages)!=1:raise ValueError('transcription source must carry exactly one page')
    page=pages[0]
    if not isinstance(page,dict) or set(page)!={'number','text'}:
        raise ValueError('transcription source page must bind exactly number and text')
    if type(page.get('number')) is not int or page['number']!=1:
        raise ValueError('transcription source page number must be 1')
    if not isinstance(page.get('text'),str) or page['text']!=content:
        raise ValueError('transcription source page text must match the reviewed content blob')
    region_obj=_fetch(store,review['region_ref'],'region')
    if region_obj.get('kind')!=REGION:raise ValueError('material region required')
    region_payload=region_obj.get('payload')
    if not isinstance(region_payload,dict):raise ValueError('region payload required')
    version=region_payload.get('version')
    if type(version) is not int or version!=1:raise ValueError('region version must be exactly 1')
    if region_payload.get('source_ref')!=payload['original_source_ref']:raise ValueError('region binds a different original PDF')
    pdf_obj=_fetch(store,payload['original_source_ref'],'original PDF')
    pdf_payload=pdf_obj.get('payload')
    if pdf_obj.get('kind')!='source' or not isinstance(pdf_payload,dict):raise ValueError('original PDF source required')
    if pdf_payload.get('parser') not in ('pypdf','pymupdf'):raise ValueError('original PDF parser must be pypdf or pymupdf')
    region_page=region_payload.get('page');pdf_pages=pdf_payload.get('pages')
    if type(region_page) is not int or region_page<1 or not isinstance(pdf_pages,(list,tuple)):
        raise ValueError('region page must be a positive integer page of the original PDF source')
    for entry in pdf_pages:
        if not isinstance(entry,dict):continue
        number=entry.get('number')
        if type(number) is not int or number<1 or not isinstance(entry.get('text'),str):continue
        if number==region_page:break
    else:raise ValueError('region page is not a page of the original PDF source')
    image=region_payload.get('image_blob_ref');ocr=region_payload.get('ocr_pdf_blob_ref')
    if not isinstance(image,str) or not image:raise ValueError('region image blob reference required')
    expected={payload['original_source_ref'],image}
    if ocr is not None:
        if not isinstance(ocr,str) or not ocr:raise ValueError('region OCR blob reference invalid')
        expected.add(ocr)
    region_refs=region_obj.get('refs')
    if not isinstance(region_refs,(list,tuple)) or any(not isinstance(ref,str) for ref in region_refs) \
            or len(set(region_refs))!=len(region_refs) or set(region_refs)!=expected:
        raise ValueError('region refs must close over exactly original PDF, image and optional OCR pdf')
    for ref in (image,ocr):
        if ref is None:continue
        try:blob=store.get_blob(ref)
        except Exception as exc:raise ValueError('region blob missing: '+ref) from exc
        if not isinstance(blob,bytes):raise ValueError('region blob must be bytes: '+ref)
    return review
