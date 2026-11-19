"""Bounded Europe PMC discovery and official PMC full-text import.

Search results are leads. Raw responses and extraction warnings are retained;
a title, abstract or flattened equation is never certified scientific evidence.
"""
import base64
import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
import httpx

from . import model_contracts as c
from .storage import canonical_bytes
from .background_jats import MAX_NODES, is_review_history
from . import background_html, background_jats

BASE='https://www.ebi.ac.uk/europepmc/webservices/rest'
PMC_HTML='https://pmc.ncbi.nlm.nih.gov/articles'
PMC_CDN='https://cdn.ncbi.nlm.nih.gov/pmc'
REQUEST='research_literature_request'
SEARCH='research_literature_search'
ASSET='research_literature_asset'
TRANSCRIPTION='research_asset_transcription'
TRANSCRIPTION_REQUEST='research_asset_transcription_request'
VERSION='europepmc_research_v1'
MAX_UNRESOLVED_RECORDS=256
# Message form verified empirically on the image-formula assets of PMC3731313
# (segment 17: [image_url, text('OCR')] transcribed cleanly on the validated
# profile, while in-band provenance labels derailed the OCR-specialised
# backend).  Provenance therefore binds in the stored receipt, never in the
# prompt text.
VISION_MESSAGE_FORM='[image_url, text(OCR)]'
TRANSCRIPTION_DIRECTIVE='OCR'
MAX_TRANSCRIPTION_OUTPUT_CHARS=8000
# One bounded generation window per call; finish_reason=length against this
# limit marks the reply as partial in the stored receipt.
TRANSCRIPTION_OUTPUT_TOKEN_LIMIT=1000
_CLOUDPMC_PATH=re.compile(r'<\?cloudpmc-path\s+([^?>]*?/'
                          r'(?P<asset>[^?/>]+))\s*\?>')
# The one visible placeholder the text projection writes for every textless
# formula/graphic location (_project_element below).  The stored page text and
# anything that binds against it (research_background intro blocks) must share
# this single marker syntax; group(1) is the bare asset name.
FORMULA_MARKER=re.compile(r'\[unreadable-formula:([^\]]+)\]')


class LiteratureError(ValueError):
    def __init__(self,message,receipt_ref=None):
        super().__init__(message);self.receipt_ref=receipt_ref


def _failure_receipt(store,project_ref,url,*,params,error_kind,http_status,
                     response_received,bytes_received,elapsed,retained=None):
    """Attempt-bound receipt for a failed read-only literature GET.

    The GET operations here (EuropePMC search, fullTextXML, PMC HTML, PMC CDN
    asset bytes) are public read-only downloads: they create no durable
    provider-side effect, no billing and no project source/asset (those are
    only written after a complete, validated response).  The receipt keeps the
    honest attempt facts (kind/URL/params/elapsed/bytes/response state) without
    any raw transport text, and says explicitly that the failed request itself
    is never replayed automatically while a NEW bounded request within the
    remaining budget is an ordinary planner choice.

    `retained` is None while the attempt stopped before anything was stored.
    A transport failure can also surface while the response context closes
    after a complete transfer, when the response bytes and this attempt's
    request receipt were already stored: the receipt then names them
    (retained_blob_ref/retained_request_ref) instead of claiming that no
    artifact at all exists.
    """
    payload=dict(version=VERSION,project_ref=project_ref,url=url,params=params,
        outcome='failed_attempt',error_kind=error_kind,
        **({'http_status':http_status} if type(http_status) is int and 100<=http_status<=599 else {}),
        response_received=response_received,bytes_received=bytes_received,elapsed_s=elapsed)
    if retained is None:
        refs=(project_ref,)
        payload['notice']='Failed read-only literature GET attempt, receipted for this attempt only. No project ' \
            'artifact came from this request and a public literature GET has no durable provider-side ' \
            'effect. This exact request is never replayed automatically; within the remaining ' \
            'literature budget a NEW counted request (same target, another format or another source) ' \
            'is an ordinary planner action and needs no operator recovery.'
    else:
        blob_ref,request_ref=retained
        refs=(project_ref,blob_ref,request_ref)
        payload['retained_blob_ref']=blob_ref
        payload['retained_request_ref']=request_ref
        payload['notice']='Failed read-only literature GET attempt, receipted for this attempt only. The transfer ' \
            'had already completed, so the response bytes (retained_blob_ref) and this attempt\'s ' \
            'request receipt (retained_request_ref) stay stored and are referenced here; no project ' \
            'source or asset was admitted from the attempt and a public literature GET has no durable ' \
            'provider-side effect. This exact request is never replayed automatically; within the ' \
            'remaining literature budget a NEW counted request (same target, another format or ' \
            'another source) is an ordinary planner action and needs no operator recovery.'
    return store.put(REQUEST,payload,refs=refs)


def _request(store,project_ref,path,params,transport,*,base=BASE,
             accept='application/json, application/xml;q=0.9'):
    start=time.monotonic();url=base+path;data=bytearray();status=None;retained=None
    try:
        try:
            with httpx.Client(transport=transport,timeout=15.,follow_redirects=False,trust_env=False) as client:
                with client.stream('GET',url,params=params,headers={'Accept':accept}) as response:
                    status=response.status_code
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data)>2_000_000 or time.monotonic()-start>20:raise LiteratureError('literature response exceeded byte/time budget')
                    blob=store.put_blob(bytes(data))
                    receipt=store.put(REQUEST,dict(version=VERSION,project_ref=project_ref,url=url,params=params,
                        http_status=response.status_code,blob_ref=blob,elapsed_s=time.monotonic()-start,
                        response_received=True),refs=(project_ref,blob))
                    retained=(blob,receipt)
                    if response.status_code!=200:raise LiteratureError('literature HTTP '+str(response.status_code),receipt)
                    return bytes(data),receipt
        except LiteratureError as exc:
            # Mid-GET byte/time budget stop: the response was received (at
            # least its head), no artifact was written, so the attempt is a
            # confirmed rejected read, not an unknown effect.
            if exc.receipt_ref is None:
                exc.receipt_ref=_failure_receipt(store,project_ref,url,params=params,
                    error_kind='byte_time_budget_exceeded',http_status=status,response_received=status is not None,
                    bytes_received=len(data),elapsed=time.monotonic()-start)
            raise
    except httpx.HTTPError as exc:
        # Only the sanitized transport class name is retained, never raw
        # exception text (which can carry local configuration details).
        receipt=_failure_receipt(store,project_ref,url,params=params,error_kind=type(exc).__name__,
            http_status=status,response_received=status is not None,bytes_received=len(data),
            elapsed=time.monotonic()-start,retained=retained)
        if retained is None:
            message='literature read-only GET failed (%s) after %.1fs with %d byte(s) received; failed attempt ' \
                'receipt %s. No project artifact exists from this request and it is never replayed ' \
                'automatically; within the remaining literature budget you may issue a NEW counted request ' \
                'for another format or source. This read-only GET needs no operator recovery.'
        else:
            message='literature read-only GET failed (%s) after %.1fs with %d byte(s) received; failed attempt ' \
                'receipt %s. The transfer had already completed, so the retained response bytes and request ' \
                'receipt stay stored and are referenced by the failed attempt receipt; no project source or ' \
                'asset exists from this request and it is never replayed automatically; within the remaining ' \
                'literature budget you may issue a NEW counted request for another format or source. This ' \
                'read-only GET needs no operator recovery.'
        raise LiteratureError(message%(type(exc).__name__,time.monotonic()-start,len(data),receipt),receipt) from exc


def _localname(node):
    return node.tag.split('}')[-1]


def _href(node):
    return (node.get('{http://www.w3.org/1999/xlink}href') or node.get('href') or '').strip()


def _asset_name(href):
    name = href.replace('\\', '/').rsplit('/', 1)[-1].strip()
    if not name or len(name) > 200 or '/' in name or '\x00' in name:
        return None
    return name


def _unreadable_formula(node):
    """True when a formula subtree carries no textual content at all.

    MathML with mn/mi text or an inline-formula wrapping graphic-marked
    content is handled by the caller; only subtrees that would project to
    empty text (silent content loss) are marked here.
    """
    if any((child.text or '').strip() for child in node.iter()):
        return False
    return True


def _project_element(node, unresolved, assets):
    """Flatten one block to text, marking unreadable formula/graphic content.

    Silent loss is an integrity failure: an image-only equation previously
    vanished from the projection, leaving prose that reads as complete with
    invisible holes (PMC3731313 Fig 2B parameter values).  Every graphic or
    textless formula is replaced by a visible [unreadable-formula:asset]
    marker and recorded for asset retrieval.  Whitespace is never content:
    a pretty-printed but empty formula must still surface as a gap.  In an
    <alternatives> wrapper a readable sibling (MathML/TeX text) wins over a
    parallel graphic: the text is projected and the graphic stays recorded as
    an UNREAD asset (fetchable, honest about its status) instead of turning a
    readable formula into a fake textless hole.  The whole tree is already
    node/depth-budgeted by the walk in _extract before projection runs.
    """
    parts = []

    def record(kind, asset):
        # Every gap location is recorded; honesty about the real total comes
        # first, payload bounding happens at aggregation in extract_projection
        # (silently capping here reproduced the original integrity root cause
        # for articles with more than MAX_UNRESOLVED_RECORDS gap locations).
        unresolved.append(dict(kind=kind, asset=asset))

    def record_asset(asset):
        if asset and all(entry['asset'] != asset for entry in assets):
            assets.append(dict(asset=asset, status='unread_asset_text_available'))

    def readable(subtree):
        return any((child.text or '').strip() for child in subtree.iter())

    def walk(current, text_available=False):
        tag = _localname(current)
        if tag == 'alternatives':
            # Decide readability from the WHOLE wrapper before projecting any
            # child, so the outcome does not depend on sibling document order.
            available = any(readable(child) for child in current)
            for child in current:
                walk(child, available)
                if (child.tail or '').strip():
                    parts.append(child.tail)
            return
        if tag in ('graphic', 'inline-graphic'):
            name = _asset_name(_href(current))
            if text_available:
                record_asset(name)
                return
            record('graphic_image', name)
            parts.append('[unreadable-formula:%s]' % (name or 'unidentified'))
            return
        if tag in ('math', 'inline-formula', 'disp-formula'):
            before = len(parts)
            if (current.text or '').strip():
                parts.append(current.text)
            for child in current:
                walk(child, text_available)
                if (child.tail or '').strip():
                    parts.append(child.tail)
            if len(parts) == before:
                if _unreadable_formula(current):
                    if text_available:
                        return
                    record('formula_without_text', None)
                    parts.append('[unreadable-formula:unidentified]')
            return
        if current.text:
            parts.append(current.text)
        for child in current:
            walk(child, text_available)
            if child.tail:
                parts.append(child.tail)

    walk(node)
    return ' '.join(''.join(parts).split())


def extract_projection(raw):
    """Project JATS XML to text with unresolved formula content made visible.

    Returns {text, warnings, unresolved_formula, unresolved_formula_count,
    unresolved_formula_records, unresolved_formula_truncated, formula_assets}:
    unresolved_formula_count is ALWAYS the true number of gap locations (it
    equals the marker count in the text); unresolved_formula is the
    deduplicated per-asset record list bounded to MAX_UNRESOLVED_RECORDS for
    payload size.  When the bound truncates the list the projection says so
    explicitly (count/records/truncated fields plus a warning) and asset
    retrieval reports truncation instead of pretending every asset was
    enumerated; assets visible as text markers stay retrievable through the
    retained-source route check.
    """
    unresolved = []
    assets = []
    text, warnings = _extract(raw, unresolved, assets)
    by_asset = {}
    for item in unresolved:
        key = (item['kind'], item['asset'])
        entry = by_asset.setdefault(key, dict(kind=item['kind'], asset=item['asset'],
                                              occurrences=0))
        entry['occurrences'] += 1
    unique = sorted(by_asset.values(),
                    key=lambda e: (e['asset'] is None, e['asset'] or ''))
    truncated = len(unique) > MAX_UNRESOLVED_RECORDS
    entries = unique[:MAX_UNRESOLVED_RECORDS]
    total = len(unresolved)
    if unresolved:
        named = sorted({item['asset'] for item in unresolved if item['asset']})
        shown = ', '.join(named[:12])
        more = '; +%d more assets' % (len(named) - 12) if len(named) > 12 else ''
        warning = ('%d formula/graphic location(s) have no textual content in this '
                   'text projection and are marked [unreadable-formula:...] in the '
                   'pages text; unresolved assets: %s%s'
                   % (total, shown or 'unidentified', more))
        if truncated:
            warning += ('; WARNING: the structured record list is truncated to %d of %d '
                        'distinct gap records (markers in the pages text remain complete); '
                        'unlisted assets are marked in the text but not enumerated'
                        % (MAX_UNRESOLVED_RECORDS, len(unique)))
        warnings.insert(0, warning)
    return dict(text=text, warnings=warnings, unresolved_formula=entries,
                unresolved_formula_count=total, unresolved_formula_records=len(entries),
                unresolved_formula_truncated=truncated, formula_assets=assets)


def _extract(raw, unresolved=None, assets=None):
    text=raw.decode('utf-8')
    if re.search(r'<!\s*ENTITY|<!DOCTYPE[^>]*\[',text,re.I|re.S) or '\x00' in text:
        raise ValueError('XML entities and internal subsets are unsupported')
    root=ET.fromstring(text);nodes=0;warnings=[];parts=[]
    def walk(node,depth=0,primary=True):
        nonlocal nodes
        nodes+=1
        if nodes>MAX_NODES or depth>64:raise ValueError('article XML tree budget exceeded')
        tag=node.tag.split('}')[-1]
        primary=primary and not is_review_history(node)
        if node.tag=='{http://www.w3.org/2001/XInclude}include':raise ValueError('external XML inclusions are unsupported')
        if primary and tag in ('math','inline-formula','disp-formula'):
            warnings.append('Formula present; XML text projection is not a verified mathematical transcription.')
        if primary and tag in ('table','table-wrap'):
            warnings.append('Table present; reading order and cell interpretation require verification.')
        for child in node:walk(child,depth+1,primary)
    walk(root)
    # Keep blocks once; avoid repeating a paragraph for every enclosing section.
    tags={'article-title','title','p','label','table','disp-formula'}
    def blocks(node):
        if is_review_history(node):
            warnings.append('Peer-review correspondence excluded from research text projection: '+node.get('article-type',''))
            return
        tag=node.tag.split('}')[-1]
        if tag in tags:
            if unresolved is None:
                value=' '.join(''.join(node.itertext()).split())
            else:
                value=_project_element(node,unresolved,assets)
            if value:parts.append(value)
            return
        for child in node:blocks(child)
    blocks(root)
    if not parts:raise ValueError('article contains no supported text blocks')
    return '\n'.join(parts),list(dict.fromkeys(warnings))


def _check_publication_identity(identity, pmcid, discovered):
    """Reject conflicting known identifiers while preserving ID-incomplete imports."""
    actual = {}
    for entry in identity['article_ids']:
        if entry['type'] in background_jats.ID_TYPES and entry['value'] is not None:
            actual.setdefault(entry['type'], set()).add(entry['value'])
    expected = {'pmcid': {pmcid}}
    for row in discovered:
        fields = [('pmcid', row.get('pmcid')), ('doi', row.get('doi'))]
        if row.get('source') == 'MED':
            fields.append(('pmid', row.get('id')))
        for kind, raw in fields:
            normalized_kind, value = background_jats._normalize_identifier(raw, kind)
            if normalized_kind is not None and value is not None:
                expected.setdefault(normalized_kind, set()).add(value)
    if any(len(values) > 1 for values in [*actual.values(), *expected.values()]):
        raise ValueError('ambiguous full-text or discovered publication identifiers')
    if any(actual[kind] != expected[kind] for kind in actual.keys() & expected.keys()):
        raise ValueError('full-text publication identifiers conflict with discovery')


def _jats_source(store, project_ref, source_ref):
    from . import research_projects as projects
    source = projects.own(store, source_ref, 'source', project_ref)
    if source.get('parser') != VERSION or not source.get('blob_ref'):
        raise ValueError('formula assets are retrievable only from Europe PMC JATS full-text sources')
    return source


def _resolve_asset_route(store, source, asset):
    """Derive the official NCBI PMC blob URL for one unresolved formula asset.

    Every retained fullTextXML carries <?cloudpmc-path blobs/.../asset?> hints
    next to its graphics; the public mirror is https://cdn.ncbi.nlm.nih.gov/pmc/
    <path>.  The route is derived from the retained official source, never
    guessed, and restricted to the exact assets this projection reported.
    """
    pmcid = source.get('pmcid')
    if not pmcid:
        match = re.fullmatch(r'(PMC[1-9][0-9]{0,11})\.xml', source.get('filename') or '')
        pmcid = match.group(1) if match else None
    if not pmcid:
        raise ValueError('JATS source lacks a PMCID for asset retrieval')
    try:
        raw = store.get_blob(source['blob_ref']).decode('utf-8')
    except (ValueError, UnicodeDecodeError):
        raise ValueError('unusable JATS blob for asset retrieval') from None
    paths = []
    for match in _CLOUDPMC_PATH.finditer(raw):
        path = match.group(1).strip()
        if match.group('asset') != asset or '..' in path or not path.startswith('blobs/'):
            continue
        if path not in paths:
            paths.append(path)
    if not paths:
        raise ValueError('no official PMC blob route is recorded in the retained source for asset %r'
                         % asset)
    if len(paths) > 1:
        raise ValueError('ambiguous PMC blob routes recorded for asset %r' % asset)
    return PMC_CDN + '/' + paths[0], pmcid


def _reported_assets(source):
    """Assets this source's projection made discoverable (bounded, honest).

    Enumerated unresolved records plus readable-alternatives assets (unread
    status preserved).  When the record list was truncated, every asset that
    the projection marked in the pages text stays retrievable too: the text
    markers are the authoritative gap list, the record list is a bounded
    index that says so explicitly.
    """
    known = {entry['asset'] for entry in source.get('unresolved_formula', []) if entry.get('asset')}
    known |= {entry['asset'] for entry in source.get('formula_assets', []) if entry.get('asset')}
    if source.get('unresolved_formula_truncated'):
        for page in source.get('pages', []):
            known |= set(FORMULA_MARKER.findall(page.get('text', '')))
    known.discard('unidentified')
    return known


def _check_asset_reported(source, asset):
    if asset in _reported_assets(source):
        return
    if source.get('unresolved_formula_truncated'):
        raise ValueError('asset is neither in the bounded record list nor marked in this '
                         'source text; the record list was truncated, inspect the page markers')
    raise ValueError('asset must be one of the unresolved formula assets reported for this source')


def _owned_asset(store, project_ref, asset_ref):
    """The asset record must belong to THIS project and its retained source."""
    from . import research_projects as projects
    if not isinstance(asset_ref, str):
        raise ValueError('asset_ref must be a fetched research_literature_asset reference')
    obj = store.get(asset_ref)
    if obj['kind'] != ASSET:
        raise ValueError('asset_ref must reference a fetched research_literature_asset')
    payload = obj['payload']
    if payload.get('project_ref') != project_ref:
        raise ValueError('asset belongs to another project')
    source = projects.own(store, payload['source_ref'], 'source', project_ref)
    return payload, source


def _sanitized_usage(provider):
    value = getattr(provider, 'last_usage', None)
    if not isinstance(value, dict):
        return {}
    return {key: usage for key, usage in value.items()
            if key in ('prompt_tokens', 'completion_tokens', 'total_tokens')
            and type(usage) is int and 0 <= usage <= 10 ** 12}


def _transcription_profile(store, project_ref, provider):
    """Configurable vision profile: the project policy selects the model id;
    the endpoint/credential stay on the provider the run already carries."""
    from . import research_projects as projects
    from .provider_identity import identity
    profile = projects.effective_policy(projects.load(store, project_ref)['policy'])['transcription_model']
    ident = identity(provider)
    ident['model'] = profile
    ident['planner_model'] = getattr(provider, 'model', None)
    ident['message_form'] = VISION_MESSAGE_FORM
    return profile, ident


def _perform_transcription(store, project_ref, args, provider):
    from .llm import ProviderError
    payload, source = _owned_asset(store, project_ref, args['asset_ref'])
    if provider is None or not hasattr(provider, 'respond_content'):
        # Explicit actionable branch: no provider request was made (receipted
        # as such), the gap stays visible and recorded; no silent recitation
        # from model priors, no fabricated transcription.
        receipt = store.put(TRANSCRIPTION_REQUEST, dict(version=1, project_ref=project_ref,
            asset_ref=args['asset_ref'], source_ref=payload['source_ref'], asset=payload['asset'],
            blob_ref=payload['blob_ref'], error_kind='capability_unavailable', request_sent=False,
            usage={}, notice='No transcription request was made: this run provides no chat provider '
                             'with a content channel for the configured vision profile'),
            refs=(project_ref, args['asset_ref'], payload['source_ref'], payload['blob_ref']))
        failure = ValueError('transcription capability unavailable: this run provides no chat provider with '
                             'a content channel for the configured vision profile. Keep the '
                             '[unreadable-formula:...] markers unresolved, record the gap in the frame '
                             'or ask_alignment, and never invent formula content')
        failure.receipt_ref = receipt
        raise failure
    profile, ident = _transcription_profile(store, project_ref, provider)
    raw = store.get_blob(payload['blob_ref'])
    if hashlib.sha256(raw).hexdigest() != payload['blob_ref']:
        raise ValueError('asset blob no longer matches its content-addressed reference')
    data_url = 'data:%s;base64,%s' % (payload['media_type'], base64.b64encode(raw).decode())
    messages = [dict(role='user', content=[dict(type='image_url', image_url={'url': data_url}),
                                           dict(type='text', text=TRANSCRIPTION_DIRECTIVE)])]
    started = time.monotonic()
    try:
        content = provider.respond_content(messages, model=profile,
                                           max_tokens=TRANSCRIPTION_OUTPUT_TOKEN_LIMIT)
    except ProviderError as exc:
        elapsed = time.monotonic() - started
        diagnostic = getattr(provider, 'last_response_diagnostic', {})
        status = diagnostic.get('http_status') if isinstance(diagnostic, dict) else None
        confirmed = type(status) is int and 100 <= status <= 599
        receipt = store.put(TRANSCRIPTION_REQUEST, dict(version=1, project_ref=project_ref,
            asset_ref=args['asset_ref'], source_ref=payload['source_ref'], asset=payload['asset'],
            blob_ref=payload['blob_ref'], profile=ident, error_kind=str(getattr(exc, 'kind', 'provider_error')),
            **({'http_status': status} if confirmed else {}), usage=_sanitized_usage(provider),
            elapsed_s=elapsed, response_received=confirmed,
            notice='Failed bounded transcription attempt; the outcome is receipted and never auto-replayed'),
            refs=(project_ref, args['asset_ref'], payload['source_ref'], payload['blob_ref']))
        # Every failed attempt carries ITS OWN receipt on the exception, so
        # the operation return never references an orphan diagnostic.  The
        # receipt's response_received field keeps the distinction: a received
        # protocol failure gives the planner an actionable rejected branch,
        # while an unresponded attempt stays unknown and is never replayed.
        exc.receipt_ref = receipt
        raise
    elapsed = time.monotonic() - started
    if not isinstance(content, str):
        raise LiteratureError('transcription provider returned no text content')
    diagnostic = getattr(provider, 'last_response_diagnostic', {})
    finish_reason = diagnostic.get('finish_reason') if isinstance(diagnostic, dict) else None
    # Completeness comes from the actual finish state, never from the mere
    # presence of a string: a reply cut at the output token limit (length) or
    # stopped by a filter is partial/ambiguous input, not a full formula.
    # Token counts are never treated as proof that the image was read.
    if not content.strip():
        status = 'unresolved_empty_output'
    elif finish_reason == 'stop':
        status = 'transcribed'
    elif finish_reason == 'length':
        status = 'partial_truncated_output'
    else:
        status = 'unresolved_incomplete_output'
    if status == 'transcribed':
        status_notice = ('Agent-assessed transcription by the configured vision profile. Compare against '
                         'the cited source before scientific use and keep scientific_support unknown')
    elif status == 'partial_truncated_output':
        status_notice = ('The reply hit the bounded output-token limit: this content is PARTIAL and may '
                         'cut a formula mid-expression. Do not treat it as a complete equation; keep the '
                         '[unreadable-formula:...] marker qualified against this partial text, record '
                         'the ambiguity in the frame or ask_alignment, or spend another call within the '
                         'remaining transcription budget. The retained raw output and finish_reason are '
                         'authoritative about what was actually returned')
    else:
        status_notice = ('The provider stopped before completion (finish_reason %s) or returned no usable '
                         'text: this transcription is UNRESOLVED, not a complete equation. Keep the '
                         '[unreadable-formula:...] marker visible, record the gap in the frame or '
                         'ask_alignment, and treat any retained text as ambiguous input only'
                         % finish_reason)
    record = dict(version=1, project_ref=project_ref, source_ref=payload['source_ref'],
        asset_ref=args['asset_ref'], asset=payload['asset'], blob_ref=payload['blob_ref'],
        media_type=payload['media_type'], url=payload.get('url'), profile=ident,
        message_form=VISION_MESSAGE_FORM, finish_reason=finish_reason,
        output_token_limit=TRANSCRIPTION_OUTPUT_TOKEN_LIMIT,
        raw_output=content[:64000], output_truncated=len(content) > 64000,
        output_characters=len(content), usage=_sanitized_usage(provider), elapsed_s=elapsed,
        status=status, output_authority='agent_assessed_visual_transcription_not_independent_verification',
        notice='Model output bound to this asset blob and profile; readable, unresolved or ambiguous '
               'content is agent-assessed input for the research decision, never independent '
               'scientific verification')
    ref = store.put(TRANSCRIPTION, record,
        refs=(project_ref, args['asset_ref'], payload['source_ref'], payload['blob_ref']))
    bounded = content[:MAX_TRANSCRIPTION_OUTPUT_CHARS]
    return dict(transcription_ref=ref, source_ref=payload['source_ref'], asset_ref=args['asset_ref'],
        asset=payload['asset'], blob_sha256=payload['blob_ref'], model_profile=profile, status=status,
        finish_reason=finish_reason, output_token_limit=TRANSCRIPTION_OUTPUT_TOKEN_LIMIT,
        usage=_sanitized_usage(provider), transcription=bounded,
        transcription_truncated=len(bounded) < len(content),
        notice=status_notice)


def validate(store,project_ref,operation,args):
    from . import research_projects as projects
    if operation=='search_literature':
        c.fields(args,{'query'},{'limit'});projects.text(args['query'],256);c.integer(args.get('limit',5),1,10,'limit');return
    if operation=='fetch_literature_asset':
        c.fields(args,{'source_ref','asset'})
        source=_jats_source(store,project_ref,args['source_ref'])
        asset=args['asset']
        if not isinstance(asset,str) or _asset_name(asset)!=asset:
            raise ValueError('asset must be a bare filename reported by this source projection')
        _check_asset_reported(source,asset)
        _resolve_asset_route(store,source,asset)
        # No 32-source gate here: an asset ATTACHES TO AN EXISTING source and
        # never adds a project source; its real limits are the shared
        # literature-request budget and the no-replay reservation.
        return
    if operation=='transcribe_literature_asset':
        c.fields(args,{'asset_ref'})
        # Ownership/identity checks are deterministic and run before any
        # provider request; the call itself is limited by the project's
        # bounded transcription budget.
        _owned_asset(store,project_ref,args['asset_ref'])
        return
    c.fields(args,{'search_ref','pmcid'},{'format','publication_id'})
    source_format=args.get('format','jats')
    if source_format not in ('jats','pmc_html'):raise ValueError('format must be jats or pmc_html')
    search=projects.own(store,args['search_ref'],SEARCH,project_ref)
    if not isinstance(args['pmcid'],str) or not re.fullmatch(r'PMC[1-9][0-9]{0,11}',args['pmcid']):raise ValueError('invalid PMCID')
    publication_id=args.get('publication_id')
    if publication_id is not None:
        if source_format!='pmc_html' or not isinstance(publication_id,str) or not re.fullmatch(r'[1-9][0-9]{0,11}',publication_id):
            raise ValueError('publication_id requires a positive PMID and pmc_html format')
        if not any(r.get('source')=='MED' and r.get('id')==publication_id for r in search['results']):
            raise ValueError('publication_id must identify a MED result in this project search')
    if not any(r.get('pmcid')==args['pmcid'] and
               (source_format=='pmc_html' or r.get('isOpenAccess')=='Y') for r in search['results']) and publication_id is None:
        raise ValueError('fetch only an open-access PMCID from this project search'
                         if source_format=='jats' else 'fetch only a PMCID from this project search')
    if len(projects.inspect(store,project_ref)['source_refs'])>=32:raise ValueError('project source limit exceeded')


def perform(store,project_ref,operation,args,*,transport=None,provider=None):
    from . import research_projects as projects
    validate(store,project_ref,operation,args)
    if operation=='transcribe_literature_asset':
        return _perform_transcription(store,project_ref,args,provider)
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
    if operation=='fetch_literature_asset':
        c.fields(args,{'source_ref','asset'})
        source=_jats_source(store,project_ref,args['source_ref'])
        asset=args['asset']
        if not isinstance(asset,str) or _asset_name(asset)!=asset:
            raise ValueError('asset must be a bare filename reported by this source projection')
        _check_asset_reported(source,asset)
        url,pmcid=_resolve_asset_route(store,source,asset)
        raw,receipt=_request(store,project_ref,'',{},transport,base=url,accept='image/*;q=0.9, */*;q=0.1')
        media=(raw[:4]==b'\x89PNG' and 'image/png') or (raw[:3]==b'\xff\xd8\xff' and 'image/jpeg') or (raw[:6] in (b'GIF87a',b'GIF89a') and 'image/gif') or ''
        if not media:
            raise LiteratureError('retrieved asset is not a supported image; bytes retained in the request receipt',receipt)
        blob=store.put_blob(raw)
        payload=dict(version=1,project_ref=project_ref,source_ref=args['source_ref'],pmcid=pmcid,
                     asset=asset,url=url,media_type=media,blob_ref=blob,request_ref=receipt,
                     notice='Retained official PMC blob bytes; image content is not yet a verified transcription.')
        ref=store.put(ASSET,payload,refs=(project_ref,args['source_ref'],blob,receipt))
        return dict(asset_ref=ref,source_ref=args['source_ref'],asset=asset,url=url,
                    media_type=media,bytes=len(raw),request_ref=receipt,
                    notice='Fetch, identity and provenance are deterministic; transcription remains an explicit agent judgment.')
    c.fields(args,{'search_ref','pmcid'},{'format','publication_id'})
    source_format=args.get('format','jats')
    search=projects.own(store,args['search_ref'],SEARCH,project_ref)
    if not isinstance(args['pmcid'],str) or not re.fullmatch(r'PMC[1-9][0-9]{0,11}',args['pmcid']):
        raise ValueError('invalid PMCID')
    matches=[r for r in search['results'] if r.get('pmcid')==args['pmcid'] and
             (source_format=='pmc_html' or r.get('isOpenAccess')=='Y')]
    if not matches and args.get('publication_id') is None:
        raise ValueError('fetch only an open-access PMCID from this project search'
                         if source_format=='jats' else 'fetch only a PMCID from this project search')
    if len(projects.inspect(store,project_ref)['source_refs'])>=32:raise ValueError('project source limit exceeded')
    if source_format=='pmc_html':
        raw,receipt=_request(store,project_ref,'/'+args['pmcid']+'/',{},transport,
                             base=PMC_HTML,accept='text/html')
        try:
            document=background_html.extract_background(raw)
            identities={entry['value'] for entry in document['identity']['article_ids'] if entry['type']=='pmcid'}
            if identities!={args['pmcid']}:raise ValueError('PMC HTML article identity does not match requested PMCID')
            if args.get('publication_id') is not None:
                pmids={entry['value'] for entry in document['identity']['article_ids'] if entry['type']=='pmid'}
                if pmids!={args['publication_id']}:raise ValueError('PMC HTML article does not match discovered PMID')
                matches=[r for r in search['results'] if r.get('source')=='MED'
                         and r.get('id')==args['publication_id']]
            _check_publication_identity(document['identity'], args['pmcid'], matches)
        except ValueError as exc:
            raise LiteratureError('unusable PMC HTML full text; raw response retained',receipt) from exc
        blob=store.put_blob(raw)
        warnings=[issue['code']+': '+issue.get('detail','') for issue in document['issues']]
        payload=dict(blob_ref=blob,filename=args['pmcid']+'.html',url=PMC_HTML+'/'+args['pmcid']+'/',
            parser=background_html.FORMAT,parser_version=background_html.VERSION,pages=document['pages'],
            warnings=warnings,project_ref=project_ref,literature_search_ref=args['search_ref'],request_ref=receipt,
            capabilities=dict(full_text_projection=True,image_transcription='unverified',table_semantics='unverified'))
        ref=store.put('source',payload,refs=(project_ref,blob,args['search_ref'],receipt))
        return dict(source_ref=ref,pmcid=args['pmcid'],characters=len(document['pages'][0]['text']),warnings=warnings)
    raw,receipt=_request(store,project_ref,'/'+args['pmcid']+'/fullTextXML',{},transport)
    try:
        projection=extract_projection(raw)
        text,warnings=projection['text'],projection['warnings']
        _check_publication_identity(background_jats.extract_identity(raw), args['pmcid'], matches)
    except (ValueError,ET.ParseError,UnicodeError) as exc:
        raise LiteratureError('JATS extraction or publication identity check failed, raw response retained',receipt) from exc
    blob=store.put_blob(raw)
    p=dict(blob_ref=blob,filename=args['pmcid']+'.xml',pmcid=args['pmcid'],url=BASE+'/'+args['pmcid']+'/fullTextXML',
           parser=VERSION,parser_version='3',pages=[dict(number=1,text=text)],warnings=warnings,
           unresolved_formula=projection['unresolved_formula'],
           unresolved_formula_count=projection['unresolved_formula_count'],
           unresolved_formula_records=projection['unresolved_formula_records'],
           unresolved_formula_truncated=projection['unresolved_formula_truncated'],
           formula_assets=projection['formula_assets'],
           project_ref=project_ref,literature_search_ref=args['search_ref'],request_ref=receipt,
           capabilities=dict(full_text_projection=True,formula_transcription='transcribe_literature_asset',
                             formula_asset_retrieval='fetch_literature_asset',table_semantics='unverified'))
    ref=store.put('source',p,refs=(project_ref,blob,args['search_ref'],receipt))
    return dict(source_ref=ref,pmcid=args['pmcid'],characters=len(text),warnings=warnings,
                unresolved_formula_count=projection['unresolved_formula_count'])

