"""Bounded Europe PMC discovery and full-text import for research projects.

Search results are leads. Raw responses and extraction warnings are retained;
a title, abstract or flattened equation is never certified scientific evidence.
"""
import json
import re
import time
import xml.etree.ElementTree as ET
import httpx

from . import model_contracts as c
from .storage import canonical_bytes

BASE='https://www.ebi.ac.uk/europepmc/webservices/rest'
REQUEST='research_literature_request'
SEARCH='research_literature_search'
VERSION='europepmc_research_v1'


class LiteratureError(ValueError):
    def __init__(self,message,receipt_ref=None):
        super().__init__(message);self.receipt_ref=receipt_ref


def _request(store,project_ref,path,params,transport):
    start=time.monotonic()
    try:
        with httpx.Client(transport=transport,timeout=15.,follow_redirects=False,trust_env=False) as client:
            with client.stream('GET',BASE+path,params=params,headers={'Accept':'application/json, application/xml;q=0.9'}) as response:
                data=bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data)>2_000_000 or time.monotonic()-start>20:raise LiteratureError('literature response exceeded byte/time budget')
                blob=store.put_blob(bytes(data))
                receipt=store.put(REQUEST,dict(version=VERSION,project_ref=project_ref,url=BASE+path,params=params,
                    http_status=response.status_code,blob_ref=blob,elapsed_s=time.monotonic()-start),refs=(project_ref,blob))
                if response.status_code!=200:raise LiteratureError('literature HTTP '+str(response.status_code),receipt)
                return bytes(data),receipt
    except httpx.HTTPError as exc:
        raise LiteratureError('literature transport failed; no automatic retry') from exc


def _extract(raw):
    text=raw.decode('utf-8')
    if re.search(r'<!\s*ENTITY|<!DOCTYPE[^>]*\[',text,re.I|re.S) or '\x00' in text:
        raise ValueError('XML entities and internal subsets are unsupported')
    root=ET.fromstring(text);nodes=0;warnings=[];parts=[]
    def walk(node,depth=0):
        nonlocal nodes
        nodes+=1
        if nodes>20000 or depth>64:raise ValueError('article XML tree budget exceeded')
        tag=node.tag.split('}')[-1]
        if node.tag=='{http://www.w3.org/2001/XInclude}include':raise ValueError('external XML inclusions are unsupported')
        if tag in ('math','inline-formula','disp-formula'):
            warnings.append('Formula present; XML text projection is not a verified mathematical transcription.')
        if tag in ('table','table-wrap'):
            warnings.append('Table present; reading order and cell interpretation require verification.')
        for child in node:walk(child,depth+1)
    walk(root)
    # Keep blocks once; avoid repeating a paragraph for every enclosing section.
    tags={'article-title','title','p','label','table','disp-formula'}
    def blocks(node):
        tag=node.tag.split('}')[-1]
        if tag in tags:
            value=' '.join(''.join(node.itertext()).split())
            if value:parts.append(value)
            return
        for child in node:blocks(child)
    blocks(root)
    if not parts:raise ValueError('article contains no supported text blocks')
    return '\n'.join(parts),list(dict.fromkeys(warnings))


def validate(store,project_ref,operation,args):
    from . import research_projects as projects
    if operation=='search_literature':
        c.fields(args,{'query'},{'limit'});projects.text(args['query'],256);c.integer(args.get('limit',5),1,10,'limit');return
    c.fields(args,{'search_ref','pmcid'})
    search=projects.own(store,args['search_ref'],SEARCH,project_ref)
    if not isinstance(args['pmcid'],str) or not re.fullmatch(r'PMC[1-9][0-9]{0,11}',args['pmcid']):raise ValueError('invalid PMCID')
    if not any(r.get('pmcid')==args['pmcid'] and r.get('isOpenAccess')=='Y' for r in search['results']):raise ValueError('fetch only an open-access PMCID from this project search')
    if len(projects.inspect(store,project_ref)['source_refs'])>=32:raise ValueError('project source limit exceeded')


def perform(store,project_ref,operation,args,*,transport=None):
    from . import research_projects as projects
    validate(store,project_ref,operation,args)
    if operation=='search_literature':
        c.fields(args,{'query'},{'limit'});projects.text(args['query'],256)
        limit=c.integer(args.get('limit',5),1,10,'limit')
        raw,receipt=_request(store,project_ref,'/search',dict(query=args['query'],format='json',resultType='core',pageSize=limit),transport)
        try:
            body=json.loads(raw);results=body['resultList']['result']
            if not isinstance(results,list) or type(body['hitCount']) is not int:raise ValueError('invalid search response')
            records=[]
            for row in results[:limit]:
                if not isinstance(row,dict):raise ValueError('invalid literature record')
                records.append({k:row.get(k) for k in ('id','source','pmcid','doi','title','authorString','pubYear','isOpenAccess','abstractText')})
            if len(canonical_bytes(records))>64000:raise ValueError('literature metadata exceeds context budget')
        except (ValueError,KeyError,TypeError) as exc:raise LiteratureError('unusable literature search response',receipt) from exc
        p=dict(version=VERSION,project_ref=project_ref,query=args['query'],hit_count=body['hitCount'],
               results=records,request_ref=receipt,notice='Discovery leads; abstracts are not full-text evidence.')
        ref=store.put(SEARCH,p,refs=(project_ref,receipt))
        return dict(search_ref=ref,**p)
    c.fields(args,{'search_ref','pmcid'})
    search=projects.own(store,args['search_ref'],SEARCH,project_ref)
    if not isinstance(args['pmcid'],str) or not re.fullmatch(r'PMC[1-9][0-9]{0,11}',args['pmcid']):
        raise ValueError('invalid PMCID')
    matches=[r for r in search['results'] if r.get('pmcid')==args['pmcid'] and r.get('isOpenAccess')=='Y']
    if not matches:raise ValueError('fetch only an open-access PMCID from this project search')
    if len(projects.inspect(store,project_ref)['source_refs'])>=32:raise ValueError('project source limit exceeded')
    raw,receipt=_request(store,project_ref,'/'+args['pmcid']+'/fullTextXML',{},transport)
    try:text,warnings=_extract(raw)
    except (ValueError,ET.ParseError,UnicodeError) as exc:
        raise LiteratureError('full text requires unsupported extraction; raw response retained',receipt) from exc
    blob=store.put_blob(raw)
    p=dict(blob_ref=blob,filename=args['pmcid']+'.xml',url=BASE+'/'+args['pmcid']+'/fullTextXML',
           parser=VERSION,parser_version='1',pages=[dict(number=1,text=text)],warnings=warnings,
           project_ref=project_ref,literature_search_ref=args['search_ref'],request_ref=receipt,
           capabilities=dict(full_text_projection=True,formula_transcription='unverified',table_semantics='unverified'))
    ref=store.put('source',p,refs=(project_ref,blob,args['search_ref'],receipt))
    return dict(source_ref=ref,pmcid=args['pmcid'],characters=len(text),warnings=warnings)

