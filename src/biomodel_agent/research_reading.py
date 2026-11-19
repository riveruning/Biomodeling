"""Project-scoped experimental full-text reading through normal agent actions."""

from . import background_reading as chunks, model_contracts as c

PLAN = 'research_background_reading_plan'
READING = 'research_background_reading'
OPERATIONS = ('start_expt_reading', 'record_expt_chunk', 'inspect_expt_reading')


def segments(chunk):
    result = []
    cursor = chunk['start']
    for line in chunk['quote'].splitlines(keepends=True):
        text = line.rstrip('\r\n')
        for offset in range(0, len(text), 800):
            start = cursor + offset
            quote = text[offset:offset + 800]
            result.append(dict(id=f's{len(result)}', page=chunk['page'], start=start,
                               end=start + len(quote), text=quote))
        cursor += len(line)
    return result


def _load(store, project_ref, reading_ref, view):
    from . import research_projects as projects
    if reading_ref not in view.get('background_reading_refs', []):
        raise ValueError('reading outside successful project history')
    reading = projects.own(store, reading_ref, READING, project_ref)
    plan = projects.own(store, reading['plan_ref'], PLAN, project_ref)
    from . import research_background as background
    source = background._source(store, view, plan['source_ref'])
    if (plan['plan']['source_ref'] != plan['source_ref'] or
            [(page['page'], page['text']) for page in plan['plan']['pages']] !=
            [(page['number'], page['text']) for page in source['pages']]):
        raise ValueError('reading plan must bind the complete admitted source pages')
    chunks.summarize(plan['plan'], reading['receipts'])
    return reading, plan


def _next(plan, receipts):
    summary = chunks.summarize(plan, receipts)
    if summary['status'] == 'excluded_agent_assessed':
        return None
    recorded = receipts['receipts'] if receipts else {}
    return next((chunk for chunk in plan['chunks'] if chunk['chunk_id'] not in recorded), None)


def _summary(plan, receipts):
    summary = chunks.summarize(plan, receipts)
    return {key: summary[key] for key in ('status', 'total_chunks', 'reviewed_chunks',
                                         'fulltext_reviewed', 'scientific_support')}


def _packet(reading_ref, reading, plan):
    chunk = _next(plan['plan'], reading['receipts'])
    result = dict(reading_ref=reading_ref, source_ref=plan['source_ref'],
                  summary=_summary(plan['plan'], reading['receipts']),
                  recorded_chunks=len(reading['receipts']['receipts']) if reading['receipts'] else 0,
                  discovery_options=plan['discovery_options'],
                  source_warnings=plan['source_warnings'][:8],
                  source_warnings_truncated=len(plan['source_warnings']) > 8,
                  curator_only=True, task_b_packet_ready=False)
    result['next_chunk'] = (dict(chunk_id=chunk['chunk_id'], page=chunk['page'],
                                 start=chunk['start'], end=chunk['end'], segments=segments(chunk))
                            if chunk else None)
    return result


def _discovery_options(store, source_ref, source, document):
    from . import research_background as background, research_literature as literature
    search_ref = source.get('literature_search_ref')
    if not search_ref or search_ref not in store.get(source_ref)['refs'] or document is None:
        return []
    search = store.get(search_ref)
    if search['kind'] != literature.SEARCH:
        return []
    options = []
    for row in search['payload'].get('results', [])[:10]:
        option = dict(search_ref=search_ref, publication_source=row.get('source'),
                      publication_id=row.get('id'))
        try:
            _, expected = background._discovery_binding(store, source_ref, source, option)
        except ValueError:
            continue
        if background._identity_status(dict(identifiers=expected), document) == 'matched':
            options.append(option)
    return options


def perform(store, project_ref, operation, args):
    from . import research_background as background, research_projects as projects
    view = projects.inspect(store, project_ref)
    if operation == 'start_expt_reading':
        c.fields(args, {'source_ref'})
        source = background._source(store, view, args['source_ref'])
        if source.get('jats_projection') or not source.get('blob_ref'):
            raise ValueError('reading requires original complete material')
        for ref in reversed(view.get('background_reading_refs', [])):
            previous, existing_plan = _load(store, project_ref, ref, view)
            if existing_plan['source_ref'] == args['source_ref']:
                return _packet(ref, previous, existing_plan)
        try:
            document = background._document(store, source)
        except background.UnsupportedDocument:
            document = None
        plan = chunks.make_plan(args['source_ref'], [dict(page=page['number'], text=page['text'])
                                 for page in source['pages']], max_chars=12000, overlap=200)
        payload = dict(project_ref=project_ref, source_ref=args['source_ref'], plan=plan,
                       source_warnings=source.get('warnings', []),
                       discovery_options=_discovery_options(store, args['source_ref'], source, document))
        plan_ref = store.put(PLAN, payload, refs=(project_ref, args['source_ref']))
        reading = dict(project_ref=project_ref, plan_ref=plan_ref, receipts=None)
        reading_ref = store.put(READING, reading, refs=(project_ref, plan_ref))
        return _packet(reading_ref, reading, payload)
    if operation == 'inspect_expt_reading':
        c.fields(args, {'reading_ref'}, {'offset', 'limit'})
        reading, plan = _load(store, project_ref, args['reading_ref'], view)
        offset = c.integer(args.get('offset', 0), 0, 100000, 'offset')
        limit = c.integer(args.get('limit', 5), 1, 10, 'limit')
        evidence = chunks.summarize(plan['plan'], reading['receipts'])['evidence']
        result = _packet(args['reading_ref'], reading, plan)
        result.update(evidence=[dict(item, evidence_id=f'e{index}') for index, item in
                                 enumerate(evidence) if offset <= index < offset + limit],
                      total_evidence=len(evidence),
                      next_offset=offset + limit if offset + limit < len(evidence) else None)
        return result
    c.fields(args, {'reading_ref', 'chunk_id', 'chunk_reviewed', 'study_type',
                    'mathematical_modeling', 'rationale', 'evidence'})
    reading, plan = _load(store, project_ref, args['reading_ref'], view)
    latest = next(ref for ref in reversed(view['background_reading_refs'])
                  if projects.own(store, ref, READING, project_ref)['plan_ref'] == reading['plan_ref'])
    if latest != args['reading_ref']:
        raise ValueError('record against the latest reading revision')
    chunk = _next(plan['plan'], reading['receipts'])
    if chunk is None or args['chunk_id'] != chunk['chunk_id']:
        raise ValueError('record the next unread chunk of this reading')
    evidence = args['evidence']
    if not isinstance(evidence, list) or len(evidence) > 16:
        raise ValueError('reading evidence needs at most 16 segment selections')
    lookup = {segment['id']: segment for segment in segments(chunk)}
    bound = []
    for item in evidence:
        c.fields(item, {'role', 'segment_id'})
        if not isinstance(item['segment_id'], str) or item['segment_id'] not in lookup:
            raise ValueError('evidence must select a supplied source segment')
        segment = lookup[item['segment_id']]
        bound.append(dict(role=item['role'], page=segment['page'], start=segment['start'],
                          end=segment['end'], quote=segment['text']))
    response = {key: args[key] for key in ('chunk_reviewed', 'study_type',
                                          'mathematical_modeling', 'rationale')}
    receipts = chunks.record_review(plan['plan'], reading['receipts'], chunk['chunk_id'],
                                    dict(response, evidence=bound))
    updated = dict(reading, receipts=receipts)
    ref = store.put(READING, updated, refs=(project_ref, reading['plan_ref'], args['reading_ref']))
    return _packet(ref, updated, plan)


def focused_packet(store, project_ref, view):
    from . import research_projects as projects
    seen = set()
    for ref in reversed(view.get('background_reading_refs', [])):
        current = projects.own(store, ref, READING, project_ref)
        if current['plan_ref'] in seen:
            continue
        seen.add(current['plan_ref'])
        reading, plan = _load(store, project_ref, ref, view)
        if _next(plan['plan'], reading['receipts']) is not None:
            return _packet(ref, reading, plan)
    return None


def section_document(store, project_ref, reading_ref, view, document):
    """Bind HTML body findings to completed primary-source reading judgments."""
    from . import research_background as background
    if document is None or document.get('format') != 'pmc_html':
        return document
    reading, plan = _load(store, project_ref, reading_ref, view)
    summary = chunks.summarize(plan['plan'], reading['receipts'])
    if summary['status'] != 'candidate_agent_assessed':
        return document
    anchors = []
    for item in summary['evidence']:
        if item['role'] != 'results':
            continue
        anchor = {key: item[key] for key in ('page', 'start', 'end', 'quote')}
        anchor['source_ref'] = plan['source_ref']
        if background._reading_result_anchor(document, anchor) and anchor not in anchors:
            anchors.append(anchor)
    return dict(document, reading_results_evidence=anchors)


def expand_review(store, project_ref, review, view):
    """Compile a compact reading-backed assessment into the existing review gates."""
    from . import research_background as background
    c.fields(review, {'reading_ref'}, {'reference_id', 'discovery', 'coverage'})
    reading, plan = _load(store, project_ref, review['reading_ref'], view)
    summary = chunks.summarize(plan['plan'], reading['receipts'])
    source_ref = plan['source_ref']
    source = background._source(store, view, source_ref)
    try:
        document = background._document(store, source)
    except background.UnsupportedDocument:
        document = None
    document = section_document(store, project_ref, review['reading_ref'], view, document)
    by_role = {}
    for role in ('methods', 'results', 'modeling'):
        anchors = []
        for item in summary['evidence']:
            anchor = {key: item[key] for key in ('page', 'start', 'end', 'quote')}
            anchor['source_ref'] = source_ref
            if item['role'] == role and (role == 'modeling' or
                    background._has_section_evidence(document, [anchor], role)) and anchor not in anchors:
                anchors.append(anchor)
        by_role[role] = anchors[:8]
    claims = [receipt['response'] for receipt in (reading['receipts'] or {}).get('receipts', {}).values()]
    modeling = ('present' if any(claim['mathematical_modeling'] == 'present' for claim in claims)
                else 'absent' if claims and all(claim['mathematical_modeling'] == 'absent' for claim in claims)
                else 'uncertain')
    study = ('review' if any(claim['study_type'] == 'review' for claim in claims)
             else 'mathematical_modeling' if any(claim['study_type'] == 'mathematical_modeling' for claim in claims)
             else 'experimental' if any(claim['study_type'] == 'experimental' for claim in claims)
             else 'uncertain')
    result = dict(source_ref=source_ref, reading_ref=review['reading_ref'], study_type=study,
                  mathematical_modeling=modeling, fulltext_reviewed=summary['fulltext_reviewed'],
                  rationale='Source-bound chunk assessments. ' + ' '.join(claim['rationale'] for claim in claims)[:3900],
                  methods_evidence=by_role['methods'], results_evidence=by_role['results'],
                  modeling_evidence=by_role['modeling'], coverage=[])
    result['coverage'] = []
    lookup = {f'e{index}': item for index, item in enumerate(summary['evidence'])}
    coverage = review.get('coverage', [])
    if not isinstance(coverage, list) or len(coverage) > 16:
        raise ValueError('reading-backed coverage supports at most 16 requirements')
    for item in coverage:
        c.fields(item, {'requirement_id', 'explanation', 'evidence_ids'}, {'support'})
        ids = item['evidence_ids']
        if not isinstance(ids, list) or not 1 <= len(ids) <= 8:
            raise ValueError('coverage needs 1..8 reading evidence IDs')
        evidence = []
        for ident in ids:
            if not isinstance(ident, str) or ident not in lookup:
                raise ValueError('coverage must select this reading revision evidence IDs')
            anchor = {key: lookup[ident][key] for key in ('page', 'start', 'end', 'quote')}
            evidence.append(dict(anchor, source_ref=source_ref))
        normalized = {key: value for key, value in item.items() if key != 'evidence_ids'}
        result['coverage'].append(dict(normalized, evidence=evidence))
    for key in ('reference_id', 'discovery'):
        if key in review:
            result[key] = review[key]
    return result
