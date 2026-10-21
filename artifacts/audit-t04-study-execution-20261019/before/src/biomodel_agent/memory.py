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
             model_ref=None, supersedes=None, reason=''):
    """Explicitly record an operator assertion; never extract facts automatically."""
    _scope(store, project_id, model_ref)
    _text(content, 'content', 2000)
    _text(author, 'author', 200)
    _sources(store, source_refs)
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
    links = [*source_refs, *([model_ref] if model_ref else []), *([supersedes] if supersedes else [])]
    return store.put(KIND, {
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


def snapshot(store, *, project_id, source_refs, model_ref=None):
    """Freeze only active, source-authorized memories for one explicit project."""
    _scope(store, project_id, model_ref)
    _sources(store, source_refs)
    allowed = set(source_refs)
    refs = [ref for ref, entry in _active(_records(store, project_id)).items()
            if entry['model_ref'] in (None, model_ref)
            and set(entry['source_refs']).issubset(allowed)]
    if len(refs) > MAX_SNAPSHOT:
        raise ValueError('memory snapshot exceeds 100 entries; narrow or curate the project memory')
    return {'project_id': project_id, 'model_ref': model_ref, 'memory_refs': refs}


def validate_snapshot(store, scope, source_refs):
    """Reject stale/retracted snapshots before another inference or reply write."""
    if not isinstance(scope, dict) or set(scope) != {'project_id', 'model_ref', 'memory_refs'}:
        raise ValueError('invalid memory scope')
    _scope(store, scope['project_id'], scope['model_ref'])
    refs = scope['memory_refs']
    if not isinstance(refs, list) or len(refs) > MAX_SNAPSHOT or len(set(refs)) != len(refs):
        raise ValueError('invalid memory snapshot refs')
    allowed = set(source_refs)
    active = _active(_records(store, scope['project_id']))
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
        used += len(entry['content'])
    return {'method': 'lexical_memory_v1', 'results': hits, 'returned_chars': used,
            'truncated': len(hits) < len(candidates),
            'notice': 'Advisory untrusted memory, not scientific evidence. Recheck against current task sources.'}
