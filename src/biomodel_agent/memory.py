"""Operator-authored, scoped advisory memory on the immutable artifact store.

Memory is never a scientific source or a substitute for evidence validation.
Updates/retractions append records; task snapshots freeze the readable corpus.
"""
from __future__ import annotations

import re
from uuid import uuid4

KIND = 'research_memory'
CATEGORIES = ('preference', 'experience', 'decision')
MAX_SNAPSHOT = 100
INVALIDATIONS = 'memory_dependency_invalidation'


def conditions(value):
    """Declared exact-match labels; no inference or scientific unit conversion."""
    if value is None:
        return None
    if not isinstance(value, dict) or not 1 <= len(value) <= 16:
        raise ValueError('memory conditions require 1..16 explicit labels')
    for key, item in value.items():
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', key):
            raise ValueError('invalid memory condition key')
        _text(item, 'memory condition', 128)
    return dict(sorted(value.items()))


def _invalidated(store, project_id):
    result = set()
    for ref in store.list(INVALIDATIONS):
        p = store.get(ref)['payload']
        if p['project_id'] == project_id:
            result.add(p['dependency_ref'])
    return result


def invalidate_dependency(store, project_id, dependency_ref, *, author, reason):
    """Append an operator invalidation, never reinterpret a new source as one."""
    _scope(store, project_id, None)
    _text(author, 'author', 200); _text(reason, 'invalidation reason', 1000)
    obj = store.get(dependency_ref)
    if obj['kind'] not in ('source', 'model', KIND):
        raise ValueError('memory invalidation requires a source, ODE model or memory')
    if obj['kind'] == KIND and obj['payload']['project_id'] != project_id:
        raise ValueError('memory invalidation cannot cross projects')
    return store.put(INVALIDATIONS, dict(version=1, project_id=project_id,
        dependency_ref=dependency_ref, author=author, reason=reason,
        authority='operator_invalidation', semantic_support='unknown'), refs=(dependency_ref,))


def _eligible(store, project_id, source_refs, model_ref, context):
    records = _records(store, project_id); active = _active(records)
    invalid = _invalidated(store, project_id); allowed = set(source_refs)
    good = {}; visiting = set()
    def check(ref):
        if ref in good:
            return good[ref]
        if ref in visiting:
            raise ValueError('cyclic advisory memory dependencies')
        p = active.get(ref)
        if p is None:
            return False
        visiting.add(ref)
        declared = conditions(p.get('conditions'))
        deps = p.get('depends_on', [])
        if not isinstance(deps, list) or len(deps) > 16 or len(set(deps)) != len(deps):
            raise ValueError('invalid advisory memory dependencies')
        linked = set(p['source_refs']) | set(deps) | {ref} | ({p['model_ref']} if p['model_ref'] else set())
        if context is None:
            matches = declared is None
        elif declared is None:
            matches = p['category'] == 'preference'
        else:
            matches = all(context.get(k) == v for k, v in declared.items())
        ok = (p['model_ref'] in (None, model_ref) and set(p['source_refs']).issubset(allowed)
              and not (linked & invalid) and matches and all(check(d) for d in deps))
        visiting.remove(ref); good[ref] = ok
        return ok
    return {ref: entry for ref, entry in active.items() if check(ref)}


def validate_advice(store, *, project_id, source_refs, model_ref=None, conditions_=None, depends_on=(), supersedes=None):
    """Validate applicability and current lineage before an operator write."""
    _scope(store, project_id, model_ref); _sources(store, source_refs)
    declared = conditions(conditions_)
    if not isinstance(depends_on, (tuple, list)) or len(depends_on)>16 or len(set(depends_on))!=len(depends_on):
        raise ValueError('invalid bounded memory depends_on')
    invalid = _invalidated(store, project_id)
    if (set(source_refs) | ({model_ref} if model_ref else set())) & invalid:
        raise ValueError('memory source/model dependency was invalidated')
    eligible = _eligible(store, project_id, source_refs, model_ref, declared)
    for ref in depends_on:
        if ref not in eligible:
            raise ValueError('memory dependency is stale, invalidated or outside declared conditions')
        def uses(current):
            if current == supersedes:
                return True
            return any(uses(d) for d in eligible[current].get('depends_on', []))
        if supersedes is not None and uses(ref):
            raise ValueError('new memory cannot depend on the entry it supersedes')
    return declared, list(depends_on)


def _text(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{name} must contain 1..{limit} characters')
    return value


def _scope(store, project_id, model_ref):
    _text(project_id, 'project_id', 128)
    if model_ref is not None and store.get(model_ref)['kind'] != 'model':
        raise ValueError('model_ref must identify a model artifact')


def _sources(store, refs):
    if not isinstance(refs, (list, tuple)) or len(refs) > 64:
        raise ValueError('source_refs must be a sequence of at most 64 sources')
    if len(set(refs)) != len(refs):
        raise ValueError('source_refs must be distinct')
    for ref in refs:
        if store.get(ref)['kind'] != 'source':
            raise ValueError('memory source_refs must identify source artifacts')


def _records(store, project_id):
    records = {}
    for ref in store.list(KIND):
        entry = store.get(ref)['payload']
        if entry['project_id'] == project_id:
            records[ref] = entry
    return records


def _heads(records):
    superseded = {entry['supersedes'] for entry in records.values() if entry['supersedes']}
    return {ref: entry for ref, entry in records.items()
            if ref not in superseded and entry['operation'] == 'remember'}


def _active(records):
    heads = _heads(records)
    roots = set()
    for ref in heads:
        root, seen = ref, set()
        while records[root]['supersedes']:
            if root in seen:
                raise ValueError('cyclic memory history')
            seen.add(root)
            root = records[root]['supersedes']
            if root not in records:
                raise ValueError('incomplete memory history')
        if root in roots:
            raise ValueError('memory has conflicting update branches; retract an unwanted branch before use')
        roots.add(root)
    return heads


def remember(store, *, project_id, content, category, author, source_refs=(),
             model_ref=None, supersedes=None, reason='', candidate_review_ref=None,
             conditions=None, depends_on=()):
    """Explicitly record an operator assertion; never extract facts automatically."""
    _scope(store, project_id, model_ref)
    _text(content, 'content', 2000)
    _text(author, 'author', 200)
    _sources(store, source_refs)
    declared, dependencies = validate_advice(store, project_id=project_id, source_refs=source_refs,
        model_ref=model_ref, conditions_=conditions, depends_on=depends_on, supersedes=supersedes)
    if category not in CATEGORIES:
        raise ValueError('unknown memory category')
    if category == 'decision' and (model_ref is None or not source_refs):
        raise ValueError('a decision requires model_ref and attributed source_refs')
    if not isinstance(reason, str) or len(reason) > 1000:
        raise ValueError('reason must be a string of at most 1000 characters')
    if supersedes is not None:
        _text(reason, 'update reason', 1000)
        parent = _active(_records(store, project_id)).get(supersedes)
        if parent is None or (parent['model_ref'], parent['category']) != (model_ref, category):
            raise ValueError('update must supersede an active memory in the same project/model/category')
    links = [*source_refs, *dependencies, *([model_ref] if model_ref else []), *([supersedes] if supersedes else [])]
    reviewed = {}
    if candidate_review_ref is not None:
        review = store.get(candidate_review_ref)
        if review['kind'] != 'memory_candidate_review' or review['payload'].get('decision') != 'approved':
            raise ValueError('candidate memory requires an approved review')
        candidate = store.get(review['payload']['candidate_ref'])
        p = candidate['payload']
        if (candidate['kind'] != 'memory_candidate' or supersedes is not None
                or (project_id, model_ref, list(source_refs), category, author, content) !=
                (p['project_id'], p['model_ref'], p['source_refs'], p['category'],
                 review['payload']['reviewer'], p['content'] + '\nApplicable when: ' + p['applicability'])):
            raise ValueError('candidate review does not authorize these memory fields')
        if declared != p.get('conditions') or dependencies != p.get('depends_on', []):
            raise ValueError('candidate review does not authorize changed conditions/dependencies')
        links.append(candidate_review_ref)
        reviewed = {'candidate_review_ref': candidate_review_ref}
    return store.put(KIND, {
        **reviewed,
        **({'conditions': declared} if declared is not None else {}),
        **({'depends_on': dependencies} if dependencies else {}),
        'id': uuid4().hex, 'operation': 'remember', 'project_id': project_id,
        'model_ref': model_ref, 'category': category, 'content': content,
        'author': author, 'source_refs': list(source_refs), 'supersedes': supersedes,
        'reason': reason, 'authority': 'operator_assertion', 'semantic_support': 'unknown',
    }, refs=tuple(dict.fromkeys(links)))


def retract(store, ref, *, author, reason):
    _text(author, 'author', 200)
    _text(reason, 'retraction reason', 1000)
    obj = store.get(ref)
    if obj['kind'] != KIND:
        raise ValueError('ref must identify a research memory')
    entry = obj['payload']
    if ref not in _heads(_records(store, entry['project_id'])):
        raise ValueError('only an active memory may be retracted')
    return store.put(KIND, {
        **entry, 'id': uuid4().hex, 'operation': 'retract', 'content': '',
        'author': author, 'reason': reason, 'supersedes': ref,
    }, refs=(ref,))


def snapshot(store, *, project_id, source_refs, model_ref=None, context=None):
    """Freeze only active, source-authorized memories for one explicit project."""
    _scope(store, project_id, model_ref)
    _sources(store, source_refs)
    context = conditions(context)
    refs = list(_eligible(store, project_id, source_refs, model_ref, context))
    if len(refs) > MAX_SNAPSHOT:
        raise ValueError('memory snapshot exceeds 100 entries; narrow or curate the project memory')
    return {'project_id': project_id, 'model_ref': model_ref, 'memory_refs': refs,
            **({'version': 2, 'context': context} if context is not None else {})}


def validate_snapshot(store, scope, source_refs):
    """Reject stale/retracted snapshots before another inference or reply write."""
    legacy = {'project_id', 'model_ref', 'memory_refs'}
    if not isinstance(scope, dict) or set(scope) not in (legacy, legacy | {'version', 'context'}):
        raise ValueError('invalid memory scope')
    context = conditions(scope.get('context'))
    if 'version' in scope and (type(scope['version']) is not int or scope['version'] != 2 or context is None):
        raise ValueError('invalid versioned memory conditions')
    _scope(store, scope['project_id'], scope['model_ref'])
    refs = scope['memory_refs']
    if not isinstance(refs, list) or len(refs) > MAX_SNAPSHOT or len(set(refs)) != len(refs):
        raise ValueError('invalid memory snapshot refs')
    allowed = set(source_refs)
    active = _eligible(store, scope['project_id'], source_refs, scope['model_ref'], context)
    for ref in refs:
        entry = active.get(ref)
        if (entry is None or entry['model_ref'] not in (None, scope['model_ref'])
                or not set(entry['source_refs']).issubset(allowed)):
            raise ValueError('memory snapshot is stale or outside task scope; start a new task')


def recall(store, scope, query, *, source_refs, limit=5, max_chars=3000):
    """Search a frozen memory whitelist; returns advisory data with attribution."""
    _text(query, 'query', 256)
    terms = list(dict.fromkeys(re.findall(r'\w+', query.casefold())))
    if not terms or len(terms) > 16:
        raise ValueError('query must contain 1..16 distinct word tokens')
    if type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError('limit must be an integer in 1..10')
    if type(max_chars) is not int or not 256 <= max_chars <= 8000:
        raise ValueError('max_chars must be an integer in 256..8000')
    validate_snapshot(store, scope, source_refs)
    candidates = []
    for order, ref in enumerate(scope['memory_refs']):
        entry = store.get(ref)['payload']
        score = sum(term in entry['content'].casefold() for term in terms)
        if score:
            candidates.append((-score, order, ref, entry))
    hits, used = [], 0
    for _, _, ref, entry in sorted(candidates):
        if len(hits) >= limit or used + len(entry['content']) > max_chars:
            continue  # Never truncate a decision into a potentially different meaning.
        hits.append({'memory_ref': ref, **{key: entry[key] for key in
            ('content', 'category', 'author', 'source_refs', 'model_ref', 'authority', 'semantic_support')}})
        if scope.get('version') == 2:
            hits[-1].update(conditions=entry.get('conditions'), depends_on=entry.get('depends_on', []))
        used += len(entry['content'])
    return {'method': 'lexical_memory_v1', 'results': hits, 'returned_chars': used,
            **({'context': scope['context'], 'applicability_policy': 'declared_exact_labels_v1'}
               if scope.get('version') == 2 else {}),
            'truncated': len(hits) < len(candidates),
            'notice': 'Advisory untrusted memory, not scientific evidence. Recheck against current task sources.'}
