"""Bounded single-section JATS projection and attributed visual candidates.

Image/MathML/table placeholders are gaps, never evidence that an author omitted
information. No network, OCR, equation recognition or independent review occurs.
"""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from .storage import canonical_bytes

VERSION=1
PARSERS=('jats_section_v1','jats_visual_candidate_v1')
MAX_BYTES=2_000_000
XLINK='{http://www.w3.org/1999/xlink}href'


def _equal(a,b):return canonical_bytes(a)==canonical_bytes(b)


def _tree(raw,section_id):
    if not isinstance(raw,bytes) or len(raw)>MAX_BYTES:raise ValueError('JATS byte budget exceeded')
    text=raw.decode('utf-8')
    if '\x00' in text or re.search(r'<!\s*ENTITY|<!DOCTYPE[^>]*\[',text,re.I|re.S):
        raise ValueError('entities and internal DTD subsets are unsupported')
    if not isinstance(section_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',section_id):raise ValueError('invalid explicit section id')
    try:root=ET.fromstring(text)
    except ET.ParseError as exc:raise ValueError('invalid supported UTF-8 JATS') from exc
    count=0
    def walk(node,depth=0):
        nonlocal count
        count+=1
        if depth>64 or count>20000:raise ValueError('JATS tree budget exceeded')
        if node.tag=='{http://www.w3.org/2001/XInclude}include':raise ValueError('external XML inclusions are unsupported')
        for child in node:walk(child,depth+1)
    walk(root)
    found=[n for n in root.iter() if n.tag.split('}')[-1]=='sec' and n.get('id')==section_id]
    if len(found)!=1:raise ValueError('one unambiguous selected section is required')
    return found[0]


def _node(node):
    return dict(tag=node.tag,attributes=dict(node.attrib),text=node.text or '',
                children=[dict(node=_node(child),tail=child.tail or '') for child in node])


def _bindings(assets):
    if not isinstance(assets,dict) or len(assets)>64:raise ValueError('invalid bounded asset bindings')
    for href,a in assets.items():
        if not isinstance(href,str) or len(href)>1000 or not isinstance(a,dict) or set(a)!={'path','blob_ref'}:raise ValueError('invalid saved asset binding')
        name=a['path'];ref=a['blob_ref']
        if not isinstance(name,str) or not name or len(name)>1000 or Path(name).is_absolute() or '..' in Path(name).parts:raise ValueError('invalid relative saved asset path')
        if not isinstance(ref,str) or not re.fullmatch('[0-9a-f]{64}',ref):raise ValueError('invalid saved asset hash')


def project(raw,section_id,assets):
    _bindings(assets)
    section=_tree(raw,section_id);parts=[];gaps=[];cursor=0;used=set()
    def add(text):
        nonlocal cursor
        parts.append(text);cursor+=len(text)
        if cursor>200000:raise ValueError('JATS projected text budget exceeded')
    def visit(node,path,ancestors):
        tag=node.tag.split('}')[-1]
        kind=('image_formula' if tag=='inline-graphic' or any('formula' in p for p in ancestors) else 'image_figure') if tag in ('graphic','inline-graphic') else 'mathml_uninterpreted' if tag=='math' else 'table_uninterpreted' if tag=='table-wrap' else 'subscript_uninterpreted' if tag=='sub' else 'superscript_uninterpreted' if tag=='sup' else None
        if kind:
            if len(gaps)>=128:raise ValueError('JATS gap budget exceeded')
            ident='gap'+str(len(gaps)+1);href=node.get(XLINK) or node.get('href')
            asset=deepcopy(assets.get(href)) if href else None
            if href in assets:used.add(href)
            start=cursor;add(f'[JATS_GAP:{ident}:{kind}]')
            gaps.append(dict(id=ident,kind=kind,href=href,xml_path=path,node=_node(node),start=start,end=cursor,
                             asset=asset,status='local_asset_untranscribed' if asset else 'content_uninterpreted' if kind in ('mathml_uninterpreted','table_uninterpreted','subscript_uninterpreted','superscript_uninterpreted') else 'local_asset_unavailable'))
            return
        add(node.text or '')
        counts={}
        for child in node:
            name=child.tag.split('}')[-1];counts[name]=counts.get(name,0)+1
            visit(child,path+'/'+name+'['+str(counts[name])+']',ancestors+[tag]);add(child.tail or '')
        if tag in ('title','p','caption','fig','sec'):add('\n')
    visit(section,'section['+section_id+']',[])
    if set(assets)!=used:raise ValueError('asset bindings must refer only to explicit gaps in the selected section')
    return dict(version=VERSION,section_id=section_id,pages=[dict(number=1,text=''.join(parts))],gaps=gaps,
                page_semantics='logical JATS section, not a PDF page',locator_semantics='XML child-index path relative to the frozen section; Unicode text offsets',
                capabilities=dict(ocr='not_run',formula_recognition='not_run',table_interpretation='not_run',reading_order='XML document order; visual layout not verified'),
                scientific_support='unknown',independent_review='pending')


def _asset(root,name):
    if not isinstance(name,str) or Path(name).is_absolute() or '..' in Path(name).parts:raise ValueError('asset path must remain relative to the JATS directory')
    path=Path(root)
    for part in Path(name).parts:
        path/=part
        if path.is_symlink():raise ValueError('symlink image assets are unsupported')
    if not path.is_file() or path.stat().st_size>MAX_BYTES:raise ValueError('image asset missing or over byte budget')
    raw=path.read_bytes()
    if len(raw)>MAX_BYTES:raise ValueError('image asset grew over byte budget')
    if not (raw.startswith(b'\x89PNG\r\n\x1a\n') or raw[:4] in (b'II*\x00',b'MM\x00*')):raise ValueError('only original PNG/TIFF image assets are supported')
    return raw


def ingest(store,path,section_id,asset_paths=None):
    path=Path(path)
    if path.is_symlink() or path.stat().st_size>MAX_BYTES:raise ValueError('invalid JATS source file')
    raw=path.read_bytes();project(raw,section_id,{})  # check before writing objects
    asset_paths={} if asset_paths is None else asset_paths
    if not isinstance(asset_paths,dict) or len(asset_paths)>64:raise ValueError('invalid bounded asset map')
    staged={};total=0
    for href,name in asset_paths.items():
        if not isinstance(href,str) or len(href)>1000:raise ValueError('invalid image href')
        data=_asset(path.parent,name);total+=len(data)
        if total>16_000_000:raise ValueError('image asset total budget exceeded')
        staged[href]=(name,data)
    assets={href:dict(path=name,blob_ref=sha256(data).hexdigest()) for href,(name,data) in staged.items()}
    projection=project(raw,section_id,assets)
    blob=store.put_blob(raw)
    for _,data in staged.values():store.put_blob(data)
    extraction=store.put('jats_section_extraction',dict(raw_blob_ref=blob,asset_bindings=assets,projection=projection),
                         refs=(blob,*sorted({a['blob_ref'] for a in assets.values()})))
    payload=dict(blob_ref=blob,filename=path.name,url=None,parser=PARSERS[0],parser_version=str(VERSION),
                 parser_output_ref=extraction,pages=projection['pages'],capabilities=projection['capabilities'],
                 warnings=['JATS image/MathML/table placeholders are unresolved content, not author omissions.',
                           'No OCR or equation recognition; asset href matching and all source semantics need review.'],
                 jats_projection=True,scientific_support='unknown',independent_review='pending')
    return store.put('source',payload,refs=(blob,extraction))


def validate(store,ref):
    obj=store.get(ref);p=obj['payload']
    if obj['kind']!='source':raise ValueError('expected a source artifact')
    if not isinstance(p,dict):raise ValueError('malformed source payload')
    if p.get('parser') not in PARSERS:
        if p.get('jats_projection'):raise ValueError('JATS source parser marker changed')
        return None
    if p.get('parser_version')!=str(VERSION) or p.get('jats_projection') is not True or p.get('scientific_support')!='unknown' or p.get('independent_review')!='pending':
        raise ValueError('JATS projection version or scientific status changed')
    if p['parser']==PARSERS[0]:
        ext=store.get(p['parser_output_ref']);e=ext['payload']
        if ext['kind']!='jats_section_extraction' or set(obj['refs'])!={p['blob_ref'],p['parser_output_ref']} or e['raw_blob_ref']!=p['blob_ref']:raise ValueError('invalid JATS projection binding')
        _bindings(e['asset_bindings'])
        asset_refs={a['blob_ref'] for a in e['asset_bindings'].values()}
        if set(ext['refs'])!={p['blob_ref'],*asset_refs}:raise ValueError('JATS asset references changed')
        for asset in e['asset_bindings'].values():store.get_blob(asset['blob_ref'])
        expected=project(store.get_blob(p['blob_ref']),e['projection']['section_id'],e['asset_bindings'])
        if not _equal(expected,e['projection']) or not _equal(p['pages'],expected['pages']) or not _equal(p['capabilities'],expected['capabilities']):raise ValueError('JATS source differs from raw projection')
        return expected
    base_ref=p['original_source_ref'];base=store.get(base_ref)['payload']
    if base.get('parser')!=PARSERS[0]:raise ValueError('visual candidates must derive directly from the original JATS projection')
    projection=validate(store,base_ref)
    t=store.get(p['transcription_ref']);entries=t['payload']['entries']
    expected,blocks=_visual(projection,entries)
    required={base_ref,p['transcription_ref'],p['blob_ref']}
    if t['kind']!='jats_visual_candidates' or t['payload']['version']!=VERSION or t['payload']['source_ref']!=base_ref or set(obj['refs'])!=required:
        raise ValueError('visual source reference binding changed')
    asset_refs={entry['asset_ref'] for entry in entries}
    if set(t['refs'])!={base_ref,*asset_refs} or t['payload']['review_status']!='analyst_checked_pending_independent':raise ValueError('visual candidate provenance or review changed')
    if not isinstance(t['payload']['actor'],str) or not t['payload']['actor'].strip():raise ValueError('visual candidate actor missing')
    if not isinstance(t['payload']['notes'],str) or not t['payload']['notes'].strip() or t['payload'].get('scientific_support')!='unknown':raise ValueError('visual attribution notes or status changed')
    if p['pages']!=[dict(number=1,text=expected)] or store.get_blob(p['blob_ref'])!=expected.encode():raise ValueError('visual text differs from attributed candidates')
    if p.get('independent_review')!='pending' or p.get('scientific_support')!='unknown':raise ValueError('visual text cannot certify scientific support')
    if not _equal(p['capabilities'],projection['capabilities']):raise ValueError('visual candidates cannot change extraction capabilities')
    return dict(**projection,visual_blocks=blocks,original_source_ref=base_ref,transcription_ref=p['transcription_ref'])


def _visual(projection,entries):
    if not isinstance(entries,list) or not 1<=len(entries)<=32:raise ValueError('expected 1..32 explicit visual candidates')
    text=projection['pages'][0]['text'];blocks=[];seen=set();by_id={g['id']:g for g in projection['gaps']}
    for entry in entries:
        if not isinstance(entry,dict) or set(entry)!={'gap_id','asset_ref','text'} or entry['gap_id'] in seen:raise ValueError('invalid or duplicate visual candidate')
        seen.add(entry['gap_id']);g=by_id.get(entry['gap_id'])
        if not g or g['kind']!='image_formula' or not g['asset'] or entry['asset_ref']!=g['asset']['blob_ref']:raise ValueError('candidate must bind a locally saved formula image')
        value=entry['text']
        if not isinstance(value,str) or not value.strip() or len(value)>8000:raise ValueError('invalid bounded visual text')
        text+='\n\n[ANALYST VISUAL CANDIDATE '+g['id']+'; independent review pending]\n'
        start=len(text);text+=value+'\n'
        blocks.append(dict(start=start,end=start+len(value),gap_id=g['id'],asset_ref=entry['asset_ref'],xml_path=g['xml_path']))
    if len(text)>250000:raise ValueError('visual projection text budget exceeded')
    return text,blocks


def transcribe(store,source_ref,entries,actor,notes):
    projection=validate(store,source_ref)
    if store.get(source_ref)['payload'].get('parser')!=PARSERS[0]:raise ValueError('only a fresh native JATS projection can receive visual candidates')
    if not isinstance(actor,str) or not actor.strip() or len(actor)>200 or not isinstance(notes,str) or not notes.strip() or len(notes)>4000:
        raise ValueError('attributed analyst and transformation notes required')
    text,blocks=_visual(projection,entries)
    record=store.put('jats_visual_candidates',dict(version=VERSION,source_ref=source_ref,entries=deepcopy(entries),actor=actor,notes=notes,
        review_status='analyst_checked_pending_independent',scientific_support='unknown'),refs=(source_ref,*sorted({e['asset_ref'] for e in entries})))
    blob=store.put_blob(text.encode())
    return store.put('source',dict(blob_ref=blob,filename='jats-visual-candidates.txt',url=None,parser=PARSERS[1],parser_version=str(VERSION),
        original_source_ref=source_ref,transcription_ref=record,pages=[dict(number=1,text=text)],jats_projection=True,
        capabilities=projection['capabilities'],independent_review='pending',scientific_support='unknown',
        warnings=['Analyst visual candidates are not native author text, OCR or independent scientific review.',
                  'The original JATS projection remains unchanged; adding this source to a task is explicit.']),refs=(source_ref,record,blob))


def locate(store,source_ref,page,start,end):
    p=store.get(source_ref)['payload']
    if p.get('parser') not in PARSERS:return None
    projection=validate(store,source_ref)
    if page!=1 or not 0<=start<end<=len(p['pages'][0]['text']):raise ValueError('invalid JATS logical-section location')
    blocks=projection.get('visual_blocks',[])
    anchors=[b for b in blocks if b['start']<end and b['end']>start]
    native_gaps=[{k:g[k] for k in ('id','kind','xml_path','start','end','asset')} for g in projection['gaps'] if g['start']<end and g['end']>start]
    return dict(kind='jats_visual_candidate' if blocks else 'jats_section',source_blob_ref=p['blob_ref'],section_id=projection['section_id'],
        page_semantics=projection['page_semantics'],start=start,end=end,
        original_source_ref=projection.get('original_source_ref'),transcription_ref=projection.get('transcription_ref'),
        parser_output_ref=p.get('parser_output_ref'),visual_anchors=anchors,native_gaps=native_gaps,scientific_support='unknown',independent_review='pending')
