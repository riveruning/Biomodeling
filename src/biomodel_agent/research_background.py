"""Versioned MM citation and experimental-background reviews for research projects.

Literal bindings and identity checks are deterministic. Study-type, absence of
mechanistic modelling and biological coverage remain explicit agent judgments.
These MM-linked artifacts stay on the reproduction/curator side of an A/B study.
"""
from copy import deepcopy
from collections import Counter
from itertools import zip_longest
import json
import re

from . import background_html, background_jats, background_pdf, model_contracts as c
from .storage import canonical_bytes

INDEX = 'research_background_index'
REVIEW = 'research_background_review'
OPERATIONS = ('index_mm_background', 'inspect_mm_background',
              'review_expt_background', 'inspect_background_review')
SUPPORT = ('full', 'partial', 'uncertain')


class UnsupportedDocument(ValueError):
    """The admitted material needs another complete-document representation."""


def _bind_intro_contexts(store, index):
    """Bind every indexed introduction block to a unique literal anchor.

    Each entry keeps the original text, source page offsets, citation reference
    IDs and a stable context_id scoped to the index (i0, i1, ...). The IDs follow
    the original block order, with long blocks contributing one ID per segment.
    Returns None when any block is empty or does not bind to exactly one place.
    """
    from .research_reading import segments
    document = index.get('document')
    blocks = document.get('intro_blocks', []) if document else []
    if not blocks or len(blocks) > 64 or sum(len(block['text']) for block in blocks) > 12000:
        return None
    source_ref = index['source_ref']
    source = store.get(source_ref)['payload']
    contexts = []
    for block in blocks:
        quote = block['text']
        if not quote:
            return None
        matches = []
        for page in source['pages']:
            start = page['text'].find(quote)
            if start >= 0:
                if page['text'].find(quote, start + 1) >= 0:
                    return None
                matches.append(dict(source_ref=source_ref, page=page['number'],
                                    start=start, end=start + len(quote), quote=quote))
        if len(matches) != 1:
            return None
        anchor = matches[0]
        anchors = [anchor]
        if len(quote) > 4000:
            anchors = [dict(source_ref=source_ref, page=part['page'], start=part['start'],
                            end=part['end'], quote=part['text']) for part in segments(anchor)]
        ids = list(dict.fromkeys(rid for citation in block.get('citations', [])
                                 for rid in citation.get('rids', [])))
        for part in anchors:
            contexts.append(dict(context_id=f'i{len(contexts)}', mm_context=part, reference_ids=ids))
    return dict(source_ref=source_ref,
                identity=document.get('identity'), intro_contexts=contexts,
                all_indexed_intro_blocks_supplied=True, indexed_intro_blocks=len(blocks),
                document_issues=document.get('issues', []),
                source_warnings=source.get('warnings', []), scientific_support='unknown')


def intro_packet(store, project_ref, index_ref):
    """Stable, index-scoped compact selectors for one project-owned MM index."""
    from . import research_projects as projects
    index = projects.own(store, index_ref, INDEX, project_ref)
    if index.get('document') is None:
        raise ValueError('MM source format needs a supported full-text index before review')
    bound = _bind_intro_contexts(store, index)
    if bound is None:
        return None
    return dict(index_ref=index_ref, **bound)


def requirements_packet(store, project_ref, view):
    """Supply every indexed introduction block when all bind within a small packet."""
    if view.get('mode') != 'reproduction' or view.get('frame_ref'):
        return None
    from . import research_projects as projects
    reviewed = {projects.own(store, ref, REVIEW, project_ref)['index_ref']
                for ref in view.get('background_review_refs', [])}
    pending = [ref for ref in view.get('background_index_refs', []) if ref not in reviewed]
    if not pending:
        return None
    packet = intro_packet(store, project_ref, pending[-1])
    if packet is None:
        return None
    return dict(packet)


def _source(store, view, source_ref):
    if source_ref not in view['source_refs']:
        raise ValueError('background source outside project scope')
    obj = store.get(source_ref)
    source = obj['payload']
    if (obj['kind'] != 'source' or source.get('reply_ref')
            or source.get('research_input_role') in ('scenario', 'researcher_reply')):
        raise ValueError('background evidence requires a full-text material source')
    return source


def _document(store, source):
    if source.get('jats_projection'):
        raise UnsupportedDocument('a selected JATS section does not admit the complete article')
    if not source.get('blob_ref'):
        raise UnsupportedDocument('background source has no original document blob')
    raw = store.get_blob(source['blob_ref'])
    try:
        if source.get('parser') == background_html.FORMAT:
            return background_html.extract_background(raw)
        if raw.startswith(b'%PDF-') and source.get('parser') in ('pypdf', 'pymupdf'):
            return background_pdf.extract_pdf_background(source['pages'])
        return background_jats.extract_background(raw)
    except ValueError as exc:
        raise UnsupportedDocument(str(exc)) from exc


def _anchor(store, source_ref, value):
    from .task_proposals import _anchor as bind
    c.fields(value, {'source_ref', 'page', 'quote'}, {'start', 'end'})
    if value['source_ref'] != source_ref:
        raise ValueError('background quote belongs to a different source')
    bound = bind(store, dict(value, id='background', kind='source'),
                 {'spec': {'source_refs': [source_ref], 'goal': ''}})
    return {key: val for key, val in bound.items() if key not in ('id', 'kind')}


def _anchors(store, source_ref, values):
    if not isinstance(values, list) or len(values) > 8:
        raise ValueError('background evidence needs at most 8 literal anchors')
    return [_anchor(store, source_ref, item) for item in values]


def _identity_values(entries):
    values = {}
    for entry in entries:
        if entry['type'] in background_jats.ID_TYPES and entry['value'] is not None:
            values.setdefault(entry['type'], set()).add(entry['value'])
    return values


def _identity_status(reference, document):
    if document is None:
        return 'pending_identity'
    expected = _identity_values(reference['identifiers'])
    actual = _identity_values(document['identity']['article_ids'])
    if any(len(values) > 1 for values in [*expected.values(), *actual.values()]):
        return 'identity_conflict'
    shared = expected.keys() & actual.keys()
    if any(expected[kind] != actual[kind] for kind in shared):
        return 'identity_mismatch'
    return 'matched' if shared else 'pending_identity'


def _discovery_binding(store, source_ref, source, discovery):
    """Verify a discovery binding against the source's own literature receipts.

    The search_ref is never accepted from the caller alone: it must equal the
    literature_search_ref recorded on the fetched source and appear among that
    source's immutable refs. The receipt must be a real research_literature_search
    of the source's original fetch project and cite the raw request receipt of
    the same project, so an operator-imported source keeps its original search
    in the closure. publication_source/publication_id must then select exactly
    one structured result row; its PMCID, MED PMID and DOI become the expected
    publication identity. Titles and abstracts never stand in for identity.
    """
    from . import research_literature as literature, research_projects as projects
    search_ref = discovery['search_ref']
    publication_source = discovery['publication_source']
    publication_id = discovery['publication_id']
    if not isinstance(search_ref, str) or not search_ref:
        raise ValueError('discovery search_ref must be the literature search receipt ref of the fetched source')
    if search_ref != source.get('literature_search_ref') or search_ref not in store.get(source_ref)['refs']:
        raise ValueError('discovery search_ref must equal the immutable search receipt recorded on the '
                         'fetched source; caller-chosen replacements are rejected')
    origin = source.get('project_ref')
    if not isinstance(origin, str) or not origin or store.get(origin)['kind'] != projects.PROJECT:
        raise ValueError('discovery source does not record the project its search and fetch belonged to')
    if not isinstance(publication_source, str) or not publication_source or len(publication_source) > 16:
        raise ValueError('discovery publication_source must be a short structured source tag such as MED')
    if not isinstance(publication_id, str) or not publication_id or len(publication_id) > 32:
        raise ValueError('discovery publication_id must be the structured record id of one search result row')
    if publication_source == 'MED' and not re.fullmatch(r'[1-9][0-9]{0,11}', publication_id):
        raise ValueError('a MED discovery publication_id must be a positive PMID')
    search = store.get(search_ref)
    payload = search['payload']
    if (search['kind'] != literature.SEARCH or not isinstance(payload, dict)
            or payload.get('project_ref') != origin or origin not in search['refs']):
        raise ValueError('discovery search receipt must be a literature search of the source '
                         'original fetch project')
    request = store.get(payload['request_ref']) if isinstance(payload.get('request_ref'), str) else None
    if (not isinstance(request, dict) or request['kind'] != literature.REQUEST
            or payload['request_ref'] not in search['refs']
            or origin not in request['refs']
            or request['payload'].get('project_ref') != origin
            or request['payload'].get('http_status') != 200
            or request['payload'].get('blob_ref') not in request['refs']):
        raise ValueError('discovery search receipt must cite the raw literature request of the same project')
    fetch_ref = source.get('request_ref')
    if not isinstance(fetch_ref, str) or fetch_ref not in store.get(source_ref)['refs']:
        raise ValueError('discovery source must retain its full-text fetch request')
    fetch = store.get(fetch_ref)
    fetched = fetch['payload']
    if (fetch['kind'] != literature.REQUEST or fetched.get('project_ref') != origin
            or origin not in fetch['refs'] or fetched.get('http_status') != 200
            or fetched.get('blob_ref') != source.get('blob_ref')
            or source.get('blob_ref') not in fetch['refs']
            or fetched.get('url') != source.get('url')):
        raise ValueError('discovery source must match its original successful full-text fetch receipt')
    results = payload.get('results')
    rows = [row for row in results if isinstance(row, dict)
            and row.get('source') == publication_source and row.get('id') == publication_id] \
        if isinstance(results, list) else []
    if len(rows) != 1:
        raise ValueError(f'discovery publication selection must match exactly one structured search row; '
                         f'{len(rows)} rows match {publication_source}/{publication_id}')
    row = rows[0]
    fields = [('pmcid', row.get('pmcid'))]
    if publication_source == 'MED':
        fields.append(('pmid', publication_id))
    fields.append(('doi', row.get('doi')))
    expected = []
    for kind, raw in fields:
        normalized_kind, value = background_jats._normalize_identifier(raw, kind)
        if normalized_kind is not None and value is not None:
            expected.append(dict(type=normalized_kind, value=value))
    if not expected:
        raise ValueError('the selected discovery row carries no structured PMCID, PMID or DOI; titles '
                         'and abstracts cannot establish publication identity')
    return payload, expected


def _section_roles(block):
    patterns = dict(methods=r'method|experiment|protocol|material|方法|实验|材料',
                    results=r'result|finding|discussion|结果|讨论|发现')
    sections = zip_longest(block['section_titles'], block.get('section_sec_types', []),
                           fillvalue='')
    for title, section_type in reversed(list(sections)):
        heading = ' '.join(value for value in (title, section_type) if value)
        roles = {name for name, pattern in patterns.items() if re.search(pattern, heading, re.I)}
        if roles:
            return roles
    return set()


def _reading_result_anchor(document, anchor):
    if document.get('format') != 'pmc_html':
        return False
    inferred_intro = any(issue.get('code') == 'inferred_unheaded_introduction'
                         for issue in document.get('issues', []))
    if not anchor['quote']:
        return False
    for block in document.get('blocks', []):
        titles = block.get('section_titles', [])
        if titles and (block['in_intro'] or _section_roles(block)):
            continue
        if not titles and not inferred_intro:
            continue
        if any(re.search(r'acknowledg|references|funding|author contributions|data availability|致谢|参考文献',
                         title, re.I) for title in titles):
            continue
        path = block.get('locator', {}).get('path', '')
        if (not re.search(r'/p\[[1-9][0-9]*\]$', path)
                or re.search(r'/(?:figure|figcaption|table)\[', path)):
            continue
        for span in block.get('source_spans', []):
            if span['page'] != anchor['page']:
                continue
            if not span['start'] <= anchor['start'] < anchor['end'] <= span['end']:
                continue
            start = anchor['start'] - span['start']
            end = anchor['end'] - span['start']
            if span['quote'][start:end] == anchor['quote']:
                return True
    return False


def _has_section_evidence(document, anchors, role):
    if document is None or not anchors:
        return False
    for anchor in anchors:
        quote = ' '.join(anchor['quote'].split())
        headed = any(not block['in_intro'] and quote in block['text'] and role in _section_roles(block)
                     for block in document['blocks'])
        reader_assessed = (role == 'results'
                           and anchor in document.get('reading_results_evidence', []))
        if not (headed or reader_assessed):
            return False
    return True


def _joint_reviews(store, requirement_ids, qualified, documents, values):
    """Bind explicit cross-source requirement assessments for this review call.

    Every anchor is re-anchored against its own source pages and checked against
    experimental headings or completed source-reading body judgments. Only sources
    that qualified as experimental within this same call may contribute evidence;
    excluded, pending-identity, unread, out-of-project and MM sources are rejected.
    """
    from . import research_projects as projects
    if not isinstance(values, list) or len(values) > 16:
        raise ValueError('background review supports at most 16 joint requirement reviews')
    normalized = []
    seen = set()
    for item in values:
        if not isinstance(item, dict):
            raise ValueError('joint requirement review must be an object')
        c.fields(item, {'requirement_id', 'support', 'explanation', 'evidence'})
        if item['requirement_id'] not in requirement_ids:
            raise ValueError('joint review cites an unknown background requirement')
        if item['requirement_id'] in seen:
            raise ValueError('duplicate joint background requirement review')
        seen.add(item['requirement_id'])
        if item['support'] not in SUPPORT:
            raise ValueError('joint support must be full, partial or uncertain')
        explanation = item['explanation']
        projects.text(explanation)
        anchors = item['evidence']
        if not isinstance(anchors, list) or len(anchors) > 8:
            raise ValueError('joint requirement evidence needs 0..8 literal anchors')
        evidence = []
        for value in anchors:
            if not isinstance(value, dict) or value.get('source_ref') not in qualified:
                raise ValueError('joint evidence needs an anchor from a source qualified '
                                 'experimental in this same review')
            bound = _anchor(store, value['source_ref'], value)
            document = documents.get(value['source_ref'])
            placed = bool(_has_section_evidence(document, [bound], 'methods')
                          or _has_section_evidence(document, [bound], 'results'))
            evidence.append(dict(bound, in_experimental_section=placed))
        if item['support'] in ('full', 'partial') and not evidence:
            raise ValueError('joint full or partial support needs at least one literal anchor')
        normalized.append(dict(requirement_id=item['requirement_id'], support=item['support'],
                               explanation=explanation,
                               evidence=evidence,
                               evidence_in_experimental_sections=bool(evidence) and all(
                                   anchor['in_experimental_section'] for anchor in evidence)))
    return normalized


def _summary(payload):
    if 'document' in payload:
        document = payload['document']
        return dict(status=payload['status'], source_ref=payload['source_ref'],
                    reference_count=len(document['references']) if document else 0,
                    intro_paragraph_count=len(document['intro_blocks']) if document else 0,
                    issue_count=len(document['issues']) if document else 0,
                    unsupported_reason=payload.get('unsupported_reason'))
    counts = Counter(review['eligibility'] for review in payload['reviews'])
    partial = payload.get('partial_requirement_ids', [])
    return dict(status=payload['status'], qualified_count=counts['qualified_experimental'],
                excluded_count=counts['excluded'], pending_count=counts['pending_review'],
                discovery_review_count=sum(1 for review in payload['reviews']
                                           if isinstance(review, dict) and review.get('discovery')),
                uncovered_requirement_ids=payload['uncovered_requirement_ids'],
                partial_requirement_ids=partial[:32],
                partial_requirement_count=len(partial),
                partial_requirement_ids_truncated=len(partial) > 32,
                joint_requirement_count=len(payload.get('requirement_reviews', [])),
                unreviewed_reference_ids=[rid[:200] if isinstance(rid, str) else None
                                         for rid in payload['unreviewed_reference_ids'][:32]],
                unreviewed_reference_count=len(payload['unreviewed_reference_ids']),
                unreviewed_reference_ids_truncated=(len(payload['unreviewed_reference_ids']) > 32
                    or any(isinstance(rid, str) and len(rid) > 200
                           for rid in payload['unreviewed_reference_ids'][:32])),
                semantic_validation='agent_assessed', scientific_support='unknown')


def _resolve_requirement_contexts(store, project_ref, index_ref, requirements):
    contexts = None
    resolved = []
    for requirement in requirements:
        c.fields(requirement, {'id', 'question'}, {'mm_context', 'mm_context_id'})
        if ('mm_context' in requirement) == ('mm_context_id' in requirement):
            raise ValueError('background requirement needs exactly one MM context binding')
        if 'mm_context' in requirement:
            anchor = requirement['mm_context']
        else:
            context_id = requirement['mm_context_id']
            if not isinstance(context_id, str):
                raise ValueError('MM context selector must be a string')
            if contexts is None:
                packet = intro_packet(store, project_ref, index_ref)
                if packet is None:
                    raise ValueError('MM introduction has no compact context bindings')
                contexts = {row['context_id']: row['mm_context']
                            for row in packet['intro_contexts']}
            if context_id not in contexts:
                raise ValueError('unknown MM context selector for background index')
            anchor = contexts[context_id]
        resolved.append(dict(id=requirement['id'], question=requirement['question'],
                             mm_context=deepcopy(anchor)))
    return resolved


def _review(store, project_ref, args, view):
    from . import research_projects as projects
    c.fields(args, {'index_ref', 'requirements', 'reviews'}, {'requirement_reviews'})
    if args['index_ref'] not in view['background_index_refs']:
        raise ValueError('background index outside successful project history')
    index = projects.own(store, args['index_ref'], INDEX, project_ref)
    if index['document'] is None:
        raise ValueError('MM source format needs a supported full-text index before review')
    requirements = args['requirements']
    reviews = args['reviews']
    if not isinstance(requirements, list) or not 1 <= len(requirements) <= 16:
        raise ValueError('background review needs 1..16 requirements')
    if not isinstance(reviews, list) or len(reviews) > 64:
        raise ValueError('background review supports at most 64 cited references')
    if len(canonical_bytes(args)) > 1_000_000:
        raise ValueError('background review exceeds byte budget')
    requirements = _resolve_requirement_contexts(store, project_ref, args['index_ref'], requirements)
    ids = set()
    normalized_requirements = []
    for requirement in requirements:
        c.fields(requirement, {'id', 'question', 'mm_context'})
        ident = c.identifier(requirement['id'])
        projects.text(requirement['question'])
        if ident in ids:
            raise ValueError('duplicate background requirement')
        ids.add(ident)
        normalized_requirements.append(dict(requirement, mm_context=_anchor(
            store, index['source_ref'], requirement['mm_context'])))
    by_id = {}
    for reference in index['document']['references']:
        by_id.setdefault(reference['reference_id'], []).append(reference)
    normalized = []
    seen = set()
    covered = set()
    source_refs = {index['source_ref']}
    search_refs = set()
    reading_refs = set()
    documents = {}
    for review in reviews:
        if 'reading_ref' in review:
            from .research_reading import expand_review
            review = expand_review(store, project_ref, review, view)
            reading_refs.add(review['reading_ref'])
        c.fields(review, {'study_type', 'mathematical_modeling', 'fulltext_reviewed',
                          'rationale', 'methods_evidence', 'results_evidence',
                          'modeling_evidence', 'coverage'},
                 {'reference_id', 'source_ref', 'discovery', 'reading_ref'})
        rid = review.get('reference_id')
        discovery = review.get('discovery')
        if ('discovery' in review) == ('reference_id' in review):
            raise ValueError('each review needs exactly one direct reference_id or discovery binding')
        if 'discovery' in review and not isinstance(discovery, dict):
            raise ValueError('discovery binding must be an object')
        reference = None
        if discovery is None:
            projects.text(rid, 1000)
            if not isinstance(rid, str) or len(by_id.get(rid, [])) != 1 or rid in seen:
                raise ValueError('review needs a unique, unambiguous MM reference ID')
            seen.add(rid)
            reference = by_id[rid][0]
        else:
            if not isinstance(discovery, dict):
                raise ValueError('discovery binding must be an object with search_ref, '
                                 'publication_source and publication_id')
            c.fields(discovery, {'search_ref', 'publication_source', 'publication_id'})
            if review.get('source_ref') is None:
                raise ValueError('a discovery review must bind the fetched full-text source_ref it '
                                 'actually reviewed')
        projects.text(review['rationale'])
        if review['study_type'] not in ('experimental', 'review', 'mathematical_modeling', 'uncertain'):
            raise ValueError('invalid background study type')
        if review['mathematical_modeling'] not in ('present', 'absent', 'uncertain'):
            raise ValueError('invalid mathematical modelling assessment')
        if type(review['fulltext_reviewed']) is not bool:
            raise ValueError('fulltext_reviewed must be boolean')
        source_ref = review.get('source_ref')
        document = None
        expected_identity = None
        search_payload = None
        if source_ref is not None:
            if source_ref == index['source_ref']:
                raise ValueError('MM itself cannot serve as its experimental background source')
            source = _source(store, view, source_ref)
            source_refs.add(source_ref)
            try:
                document = _document(store, source)
            except UnsupportedDocument:
                document = None
            if 'reading_ref' in review:
                from .research_reading import section_document
                document = section_document(store, project_ref, review['reading_ref'], view, document)
            documents[source_ref] = document
            if discovery is not None:
                search_payload, expected_identity = _discovery_binding(store, source_ref, source, discovery)
                search_refs.add(discovery['search_ref'])
        bound = deepcopy(review)
        for field in ('methods_evidence', 'results_evidence', 'modeling_evidence'):
            bound[field] = _anchors(store, source_ref, review[field])
        coverage = review['coverage']
        if not isinstance(coverage, list) or len(coverage) > 16:
            raise ValueError('invalid background coverage list')
        bound['coverage'] = []
        for item in coverage:
            c.fields(item, {'requirement_id', 'explanation', 'evidence'}, {'support'})
            if item['requirement_id'] not in ids:
                raise ValueError('coverage cites an unknown background requirement')
            projects.text(item['explanation'])
            support = item.get('support', 'full')
            if support not in SUPPORT:
                raise ValueError('coverage support must be full, partial or uncertain')
            evidence = _anchors(store, source_ref, item['evidence'])
            if not evidence:
                raise ValueError('coverage requires literal experimental evidence')
            supported = all(_has_section_evidence(document, [anchor], 'methods')
                            or _has_section_evidence(document, [anchor], 'results')
                            for anchor in evidence)
            bound['coverage'].append(dict(item, evidence=evidence, support=support,
                                          evidence_in_experimental_sections=supported))
        identity = _identity_status(reference if expected_identity is None
                                    else {'identifiers': expected_identity}, document)
        methods = _has_section_evidence(document, bound['methods_evidence'], 'methods')
        results = _has_section_evidence(document, bound['results_evidence'], 'results')
        if document and any(anchor in document.get('reading_results_evidence', [])
                            for anchor in bound['results_evidence']):
            bound['results_section_assessment'] = 'agent_assessed_body_results'
        if review['study_type'] in ('review', 'mathematical_modeling') or review['mathematical_modeling'] == 'present':
            eligibility = 'excluded'
        elif (review['study_type'] == 'experimental' and review['mathematical_modeling'] == 'absent'
              and review['fulltext_reviewed'] and identity == 'matched' and methods and results):
            eligibility = 'qualified_experimental'
            covered.update(item['requirement_id'] for item in bound['coverage']
                           if item['support'] == 'full' and item['evidence_in_experimental_sections'])
        else:
            eligibility = 'pending_review'
        if discovery is not None:
            bound['discovery_project_ref'] = search_payload['project_ref']
            bound['discovery_expected_identity'] = expected_identity
        bound.update(eligibility=eligibility, identity_status=identity,
                     methods_section_evidence=methods, results_section_evidence=results)
        normalized.append(bound)
    qualified = {bound.get('source_ref') for bound in normalized
                 if bound['eligibility'] == 'qualified_experimental'} - {None}
    joint = _joint_reviews(store, ids, qualified, documents, args.get('requirement_reviews', []))
    claimed = {item['requirement_id'] for item in joint}
    # Explicit joint assessments override single-source coverage for their
    # requirement; only full support whose every anchor is verified covers.
    covered = (covered - claimed) | {item['requirement_id'] for item in joint
                                     if item['support'] == 'full'
                                     and item['evidence_in_experimental_sections']}
    partial = {item['requirement_id'] for bound in normalized
               if bound['eligibility'] == 'qualified_experimental'
               for item in bound['coverage']
               if item['support'] == 'partial' and item['evidence_in_experimental_sections']}
    partial = (partial - claimed) | {item['requirement_id'] for item in joint
                                    if item['support'] == 'partial'
                                    and item['evidence_in_experimental_sections']}
    payload = dict(version=1, project_ref=project_ref, index_ref=args['index_ref'],
                   requirements=normalized_requirements, reviews=normalized,
                   requirement_reviews=joint, partial_requirement_ids=sorted(partial - covered),
                   uncovered_requirement_ids=sorted(ids - covered),
                   unreviewed_reference_ids=[r['reference_id'] for r in index['document']['references']
                                             if r['reference_id'] not in seen],
                   status='complete' if ids <= covered else 'incomplete',
                   semantic_validation='agent_assessed', independent_review='pending',
                   scientific_support='unknown', curator_only=True, task_b_packet_ready=False)
    ref = store.put(REVIEW, payload, refs=(project_ref, args['index_ref'], *sorted(source_refs),
                                           *sorted(search_refs), *sorted(reading_refs)))
    return dict(review_ref=ref, **_summary(payload))


def _reference_selection(args, items):
    """Resolve optional explicit reference_ids against immutable index rows.

    Requested IDs match with strict case-sensitive equality against the XML
    reference IDs, and request order is preserved. Every source row sharing a
    requested ID is kept, so an ambiguous ID stays an explicit multi-row
    result instead of posing as a unique match; unmatched IDs are returned
    separately as missing instead of being silently dropped.
    """
    if 'reference_ids' not in args:
        return None, None
    requested = args['reference_ids']
    if not isinstance(requested, list) or not 1 <= len(requested) <= 10:
        raise ValueError('reference_ids needs 1..10 requested reference IDs')
    unique = []
    for rid in requested:
        if not isinstance(rid, str) or not rid or len(rid) > 1000:
            raise ValueError('each requested reference ID must be a non-empty string of at most 1000 characters')
        if rid in unique:
            raise ValueError('reference_ids must not repeat a requested reference ID')
        unique.append(rid)
    rows = {}
    for item in items:
        rows.setdefault(item['reference_id'], []).append(item)
    selected = []
    missing = []
    for rid in unique:
        matches = rows.get(rid, [])
        if matches:
            selected.extend(matches)
        else:
            missing.append(rid)
    return selected, missing


def perform(store, project_ref, operation, args):
    from . import research_projects as projects
    view = projects.inspect(store, project_ref)
    if operation == 'index_mm_background':
        c.fields(args, {'source_ref'})
        source = _source(store, view, args['source_ref'])
        try:
            document = _document(store, source)
            status = document['status']
            reason = None
        except UnsupportedDocument as exc:
            document = None
            status = 'unsupported'
            reason = str(exc)[:500]
        payload = dict(version=1, project_ref=project_ref, source_ref=args['source_ref'],
                       document=document, status=status, unsupported_reason=reason,
                       curator_only=True, task_b_packet_ready=False)
        ref = store.put(INDEX, payload, refs=(project_ref, args['source_ref']))
        return dict(index_ref=ref, **_summary(payload))
    if operation == 'review_expt_background':
        return _review(store, project_ref, args, view)
    kind, key = (INDEX, 'index_ref') if operation == 'inspect_mm_background' else (REVIEW, 'review_ref')
    optional = ({'offset', 'limit', 'section'} if kind == REVIEW else
                {'offset', 'limit', 'reference_ids', 'include_intro_contexts'})
    c.fields(args, {key}, optional)
    allowed = view['background_index_refs'] if kind == INDEX else view['background_review_refs']
    if args[key] not in allowed:
        raise ValueError('background artifact outside successful project history')
    payload = projects.own(store, args[key], kind, project_ref)
    offset = c.integer(args.get('offset', 0), 0, 100000, 'offset')
    limit = c.integer(args.get('limit', 5), 1, 10, 'limit')
    section = args.get('section', 'reviews')
    if kind == REVIEW and section not in ('reviews', 'requirement_reviews'):
        raise ValueError('review section must be reviews or requirement_reviews')
    items = ((payload['document']['references'] if payload['document'] else [])
             if kind == INDEX else payload.get(section, []))
    if kind == INDEX:
        include_contexts = args.get('include_intro_contexts', True)
        if type(include_contexts) is not bool:
            raise ValueError('include_intro_contexts must be boolean')
        selected, missing = _reference_selection(args, items)
    else:
        include_contexts = True
        selected = missing = None
    # Explicit reference_ids page the selected rows directly; the plain path
    # keeps paging the full immutable index. The full table is never truncated
    # before an ID-based selection.
    visible = items if selected is None else selected
    page = []
    page_bytes = 0
    page_budget = 8000 if kind == INDEX else 32000
    for item in visible[offset:offset + limit]:
        preview = deepcopy(item)
        if kind == INDEX:
            preview['citation_text'] = preview['citation_text'][:2000]
            preview['citation_text_truncated'] = len(item['citation_text']) > 2000
            locations = item.get('intro_context_locators', item.get('intro_context_paths', []))
            preview['intro_context_count'] = len(locations)
            if include_contexts:
                contexts = []
                for block in payload['document']['intro_blocks']:
                    location = block.get('locator', block.get('xml_path'))
                    if location in locations:
                        context = dict(text=block['text'][:2000],
                                       text_truncated=len(block['text']) > 2000)
                        context.update(dict(locator=location) if 'locator' in block
                                       else dict(xml_path=location))
                        contexts.append(context)
                preview['intro_contexts'] = contexts[:4]
        size = len(canonical_bytes(preview))
        if size > min(16000, page_budget):
            identity_key = 'requirement_id' if kind == REVIEW and section == 'requirement_reviews' else 'reference_id'
            preview = {identity_key: str(item.get(identity_key) or '')[:1000],
                       'truncated': True,
                       'preview_json': json.dumps(preview, ensure_ascii=False)[:4000]}
            if kind == REVIEW and section == 'reviews' and isinstance(item, dict) and item.get('discovery'):
                # A truncated discovery row keeps its source and search binding
                # identifiable even when the full review no longer fits the page.
                preview['source_ref'] = item.get('source_ref')
                preview['discovery'] = item['discovery']
            size = len(canonical_bytes(preview))
        if page and page_bytes + size > page_budget:
            break
        page.append(preview)
        page_bytes += size
    result = dict(**{key: args[key]}, summary=_summary(payload), offset=offset,
                  items=page, total=len(visible),
                  next_offset=offset + len(page) if offset + len(page) < len(visible) else None)
    if kind == INDEX:
        result['intro_contexts_included'] = include_contexts
        if selected is not None:
            result['index_total'] = len(items)
            result['missing_reference_ids'] = missing
    if kind == REVIEW:
        result['section'] = section
        joint_support = {item.get('requirement_id'): item.get('support')
                         for item in payload.get('requirement_reviews', [])
                         if isinstance(item, dict)}
        result['requirements'] = [dict(id=r['id'], question=r['question'][:500],
                                       question_truncated=len(r['question']) > 500,
                                       joint_support=joint_support.get(r['id']))
                                  for r in payload['requirements']]
    return result
