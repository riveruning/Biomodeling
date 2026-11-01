"""Bounded local parameter investigation; retrieval never adopts a value."""
import re

from .retrieval import search_sources
from .source_review import review_sources


def investigate(store, source_refs, target, query, *, method='lexical_v1'):
    if not isinstance(target, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}/(?:value|initial)', target):
        raise ValueError('target must be <symbol>/value or <symbol>/initial')
    # Retrieval enforces the existing source/page/character budgets before review.
    retrieved = search_sources(store, source_refs, query, method=method)
    review = review_sources(store, source_refs, [target])
    payload = {'version': 1, 'target': target, 'query': query,
               'source_refs': list(source_refs), 'retrieval': retrieved, 'source_review': review,
               'selected_value': None, 'semantic_support': 'unknown',
               'conditions_status': 'not_verified', 'units_status': 'unknown',
               'next_action': ('Present the source issue and ask for target-specific clarification.'
                               if review['issues'] else
                               'Inspect candidate excerpts, units and conditions; cite an exact declaration in a draft.'
                               if retrieved['results'] else
                               'No local match. Keep the value unknown; ask for supplementary material or an explicit exploration assumption.'),
               'note': 'No numeric estimate or value adoption was performed. Matches are untrusted leads, not parameter confirmation.'}
    ref = store.put('parameter_investigation', payload, refs=tuple(source_refs))
    return {'investigation_ref': ref, **payload}
