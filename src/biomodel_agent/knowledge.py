"""Bounded public database access; fetched records are unverified sources.

No credentials, arbitrary URLs, automatic model execution or scientific approval.
Agent search is advisory; operators explicitly import sources into a new task.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import re
import time

import httpx

DATABASES = ('biomodels', 'sabiork')
VERSION = 'public_knowledge_v1'
MAX_BYTES = 4 * 1024 * 1024
MAX_SECONDS = 30
BIO = 'https://www.biomodels.org'
SABIO = 'https://sabiork.h-its.org'
SABIO_FIELDS = 'EntryID,ReactionEquation,ECNumber,EnzymeName,Organism,Tissue,Substrate,Product,PubMedID,pH,Temperature,Json'


def databases(values):
    if not isinstance(values, (tuple, list)) or any(v not in DATABASES for v in values):
        raise ValueError('knowledge databases must be biomodels and/or sabiork')
    return list(dict.fromkeys(values))


def _identifier(database, identifier):
    databases([database])
    pattern = r'(?:BIOMD\d{10}|MODEL\d{10})' if database == 'biomodels' else r'[1-9]\d{0,9}'
    if not isinstance(identifier, str) or not re.fullmatch(pattern, identifier):
        raise ValueError('invalid database record identifier')
    return identifier


def _query(query, limit):
    if not isinstance(query, str) or not query.strip() or len(query) > 256 or any(ord(c) < 32 for c in query):
        raise ValueError('query must contain 1..256 characters without control characters')
    if type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError('limit must be an integer in 1..10')


def _json(data):
    def reject(value):
        raise ValueError('nonfinite JSON value')
    def finite(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('nonfinite JSON number')
        return number
    try:
        return json.loads(data, parse_constant=reject, parse_float=finite)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError('database returned invalid JSON') from exc


def _request(store, url, params, *, transport=None):
    # All callers build fixed endpoints; never follow server-provided URLs.
    started = time.monotonic()
    raw_ref = None
    try:
        with httpx.Client(timeout=httpx.Timeout(20, connect=5), follow_redirects=False,
                          trust_env=False, transport=transport) as client:
            with client.stream('GET', url, params=params,
                               headers={'User-Agent': 'BioModel-Agent/0.1 public-knowledge',
                                        'Accept': 'application/json, application/xml;q=0.9'}) as response:
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES or time.monotonic() - started > MAX_SECONDS:
                        raise ValueError('database response exceeded size or time limit')
                    chunks.append(chunk)
                data = b''.join(chunks)
                raw_ref = store.put_blob(data)
                receipt = {'adapter': VERSION, 'url': str(response.url),
                           'retrieved_at': datetime.now(timezone.utc).isoformat(),
                           'http_status': response.status_code,
                           'content_type': response.headers.get('content-type', ''),
                           'blob_ref': raw_ref}
                request_ref = store.put('knowledge_request', receipt, refs=(raw_ref,))
                if response.status_code != 200:
                    raise ValueError(f'database HTTP {response.status_code}; request_ref={request_ref}')
                if not data or data.lstrip().lower().startswith((b'<!doctype html', b'<html')):
                    raise ValueError(f'database returned empty content or HTML; request_ref={request_ref}')
                return data, request_ref, str(response.url)
    except (httpx.HTTPError, ValueError) as exc:
        refs = (raw_ref,) if raw_ref else ()
        failure = store.put('knowledge_failure', {
            'adapter': VERSION, 'url': url, 'params': params,
            'error_type': type(exc).__name__, 'message': str(exc)[:500],
            'retrieved_at': datetime.now(timezone.utc).isoformat(),
        }, refs=refs)
        raise ValueError(f'knowledge request failed: {exc}; failure_ref={failure}') from exc


def _solr(body):
    response = body.get('response') if isinstance(body, dict) else None
    if not isinstance(response, dict) or not isinstance(response.get('docs'), list) or type(response.get('numFound')) is not int:
        raise ValueError('unexpected SABIO-RK response schema')
    if response['numFound'] < 0 or any(not isinstance(d, dict) for d in response['docs']):
        raise ValueError('invalid SABIO-RK result records')
    return response


def _sabio_summary(doc):
    nested = doc.get('Json')
    if isinstance(nested, list):
        nested = nested[0] if nested else None
    details = _json(nested) if isinstance(nested, str) else nested
    if details is not None and not isinstance(details, dict):
        raise ValueError('invalid SABIO-RK record details')
    details = details or {}
    law = details.get('kineticlaw', {})
    if not isinstance(law, dict) or not isinstance(law.get('parameter', []), list):
        raise ValueError('invalid SABIO-RK kinetic parameters')
    params = law.get('parameter', [])
    return {'id': str(doc.get('EntryID')), 'reaction': doc.get('ReactionEquation'),
            'organism': doc.get('Organism'), 'ec_number': doc.get('ECNumber'),
            'pubmed_id': doc.get('PubMedID'),
            'conditions': details.get('experimental_conditions', {}),
            'parameters': [{'name': p.get('name'), 'type': p.get('parameter_type'),
                            'value': p.get('start_value'), 'unit': p.get('unit'),
                            'species': p.get('species'),
                            'normalized_value': p.get('n_start_value')}
                           for p in params if isinstance(p, dict) and p.get('role') == 'Constant'
                           and p.get('start_value') is not None]}


def search(store, database, query, limit=5, *, transport=None):
    databases([database])
    _query(query, limit)
    if database == 'biomodels':
        data, request_ref, url = _request(store, BIO + '/search',
            {'query': query, 'numResults': limit, 'offset': 0, 'format': 'json'}, transport=transport)
        body = _json(data)
        if not isinstance(body, dict) or not isinstance(body.get('models'), list) or type(body.get('matches')) is not int:
            raise ValueError('unexpected BioModels search response schema')
        if any(not isinstance(m, dict) for m in body['models']):
            raise ValueError('invalid BioModels result records')
        results = [{k: model.get(k) for k in ('id', 'name', 'format', 'submissionDate')}
                   for model in body['models'][:limit]]
        total = body['matches']
    else:
        data, request_ref, url = _request(store, SABIO + '/api/ft/proxy-select',
            {'q': query, 'df': 'Everything', 'rows': limit, 'start': 0, 'wt': 'json',
             'fl': SABIO_FIELDS}, transport=transport)
        response = _solr(_json(data))
        results = [_sabio_summary(d) for d in response['docs'][:limit]]
        total = response['numFound']
    payload = {'adapter': VERSION, 'database': database, 'query': query, 'url': url,
               'total': total, 'results': results, 'request_ref': request_ref,
               'notice': 'Unverified database results; not proof of applicability or scientific truth.'}
    ref = store.put('knowledge_search', payload, refs=(request_ref,))
    return {'search_ref': ref, **payload}


def model_files(store, identifier, *, transport=None):
    _identifier('biomodels', identifier)
    data, request_ref, _ = _request(store, BIO + '/model/files/' + identifier,
                                   {'format': 'json'}, transport=transport)
    body = _json(data)
    if not isinstance(body, dict) or not isinstance(body.get('main'), list) or not isinstance(body.get('additional', []), list):
        raise ValueError('unexpected BioModels file-list schema')
    if any(not isinstance(f, dict) or not isinstance(f.get('name'), str)
           for f in body['main'] + body.get('additional', [])):
        raise ValueError('invalid BioModels file records')
    return {'id': identifier, 'main': body['main'], 'additional': body.get('additional', []),
            'request_ref': request_ref}


def _source(store, database, identifier, text, request_ref, url, blob_ref):
    return store.put('source', {
        'blob_ref': blob_ref, 'filename': f'{database}-{identifier}.txt', 'url': url,
        'parser': VERSION, 'parser_version': '1', 'database': database,
        'record_id': identifier, 'request_ref': request_ref,
        'pages': [{'number': 1, 'text': text}],
        'warnings': ['External record, unverified. Database membership is not scientific approval.',
                     'XML/JSON representation; binding grammar may require explicit clarification.'],
    }, refs=(blob_ref, request_ref))


def fetch(store, database, identifier, filename=None, *, transport=None):
    _identifier(database, identifier)
    if database == 'sabiork':
        if filename is not None:
            raise ValueError('filename is only supported for BioModels')
        data, request_ref, url = _request(store, SABIO + '/api/ft/proxy-select',
            {'q': 'EntryID:' + identifier, 'rows': 1, 'wt': 'json', 'fl': SABIO_FIELDS}, transport=transport)
        response = _solr(_json(data))
        if response['numFound'] != 1 or len(response['docs']) != 1 or str(response['docs'][0].get('EntryID')) != identifier:
            raise ValueError('SABIO-RK record missing or identifier mismatch')
        doc = response['docs'][0]
        # Preserve full native response as evidence, expose a readable JSON representation.
        text = json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False)
        source_ref = _source(store, database, identifier, text, request_ref, url, store.put_blob(data))
        return {'database': database, 'id': identifier, 'source_ref': source_ref,
                'request_ref': request_ref, 'record': _sabio_summary(doc)}
    files = model_files(store, identifier, transport=transport)
    eligible = [f for f in files['main'] if f['name'].lower().endswith(('.xml', '.sbml'))]
    if filename is None:
        if len(eligible) != 1:
            raise ValueError('choose an explicit XML/SBML filename using knowledge-files')
        selected = eligible[0]
        filename = selected['name']
    else:
        candidates = [f for f in files['main'] + files['additional'] if f['name'] == filename]
        if len(candidates) != 1 or not filename.lower().endswith(('.xml', '.sbml')):
            raise ValueError('filename must identify a listed XML/SBML file')
        selected = candidates[0]
    if not filename or len(filename) > 256 or any(c in filename for c in ('/', '\\', '\x00')):
        raise ValueError('invalid model filename')
    data, request_ref, url = _request(store, BIO + '/model/download/' + identifier,
                                    {'filename': filename}, transport=transport)
    expected = selected.get('sha256sum')
    if expected and hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('downloaded model does not match listed SHA-256')
    try:
        text = data.decode('utf-8-sig')
    except UnicodeError as exc:
        raise ValueError('model is not UTF-8 XML') from exc
    if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        raise ValueError('model contains unsupported XML declarations')
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError('download is not well-formed XML') from exc
    if root.tag.split('}')[-1] != 'sbml':
        raise ValueError('download is not SBML')
    from . import modeling
    from .domain.contracts import ModelArtifact
    from .domain.serialization import to_dict
    artifact = ModelArtifact(identifier, text)
    report = modeling.validate_artifact(artifact)
    blob_ref = store.put_blob(data)
    source_ref = _source(store, database, identifier, text, request_ref, url, blob_ref)
    model_ref = store.put('model', to_dict(artifact), refs=(source_ref, files['request_ref']))
    result = {'database': database, 'id': identifier, 'filename': filename,
              'source_ref': source_ref, 'model_ref': model_ref, 'request_ref': request_ref,
              'can_execute': report.can_execute, 'checks': [to_dict(c) for c in report.checks],
              'scientific_status': 'not_verified'}
    store.put('knowledge_import', result, refs=(source_ref, model_ref, request_ref))
    return result
