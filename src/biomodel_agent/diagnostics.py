"""Actionable diagnostics derived from tool facts, never scientific approval."""
from __future__ import annotations


def issue(category, code, message, *, target=None, source=None, blocking=True):
    routes = {
        'citation': ('repair', 'Inspect the cited page; submit an exact unique quote or explicit offsets.'),
        'binding': ('repair', 'Compare the named target with the original declaration; correct the draft or citation.'),
        'missing_information': ('retrieve_or_ask', 'Search authorized sources for this target; if unresolved, ask a targeted question. Keep unknown values null.'),
        'source_conflict': ('clarify', 'Present the conflicting source locations and request a target-specific clarification; do not choose a value silently.'),
        'units': ('clarify', 'Check declared units and experiment conditions; request missing information. Do not silently convert or assume units.'),
        'model': ('repair', 'Repair the model using the original check and authorized evidence, within the remaining repair budget.'),
        'numerical': ('diagnose', 'Inspect the saved numerical checks. A consumed scientific run cannot be repeated in this task.'),
        'service': ('bounded_retry', 'Use only the existing provider retry policy and remaining budget.'),
        'unknown': ('stop_or_inspect', 'Inspect the original diagnostic; do not assume a retry is safe.'),
    }
    route, suggestion = routes[category]
    return dict(category=category, code=code, target=target, source=source,
                blocking=blocking, route=route, suggestion=suggestion, message=message)


def draft_feedback(result):
    items = []
    for diagnostic in (result.get('decision_feedback') or {}).get('issues', []):
        code = diagnostic['code']
        category = ('binding' if code == 'source_binding_mismatch' else
                    'source_conflict' if code in ('source_review', 'condition_mismatch') else
                    'model' if code == 'invalid_model' else 'missing_information')
        items.append(issue(category, code, diagnostic['question'], target=diagnostic.get('target')))
    for diagnostic in result.get('parameter_diagnostics', []):
        if diagnostic['status'] != 'declared_fields_match':
            category = ('units' if diagnostic['status'] == 'conversion_required' else
                        'source_conflict' if diagnostic['status'] in ('mismatch', 'source_conflict') else
                        'missing_information' if diagnostic['status'] == 'unknown' else 'model')
            items.append(issue(category, 'parameter_' + diagnostic['status'], diagnostic['reason'],
                               target=diagnostic['target'], source={'assessment_ref': diagnostic.get('assessment_ref')}))
    for diagnostic in result.get('citation_diagnostics', []):
        items.append(issue('citation', diagnostic['kind'], diagnostic.get('instruction', ''),
                           target=diagnostic.get('target'),
                           source={k: diagnostic[k] for k in ('source_ref', 'page') if k in diagnostic}))
    for diagnostic in result.get('binding_diagnostics', []):
        if diagnostic.get('status') != 'matched':
            category = 'binding' if diagnostic.get('status') == 'mismatch' else 'missing_information'
            items.append(issue(category, 'binding_' + diagnostic.get('status', 'unknown'),
                               diagnostic.get('reason', ''), target=diagnostic.get('target'),
                               source=diagnostic.get('source')))
    for diagnostic in (result.get('source_review') or {}).get('issues', []):
        kind = diagnostic['kind']
        category = ('units' if 'unit' in kind else 'missing_information' if 'missing' in kind else 'source_conflict')
        items.append(issue(category, kind, diagnostic['question'], target=diagnostic.get('target'),
                           source=diagnostic.get('evidence')))
    for check in result.get('checks', []):
        if check.get('status') in ('pass', 'not_applicable'):
            continue
        code = check.get('code', 'model_check')
        items.append(issue('units' if 'unit' in code or 'time' in code else 'model', code,
                           check.get('message', ''), blocking=check.get('status') == 'fail'))
    # Preserve unstructured legacy problems too; none are silently discarded.
    for problem in result.get('problems', []):
        target = problem.split(':', 1)[0] if '/' in problem.split(':', 1)[0] else None
        if any(i['message'] == problem for i in items):
            continue
        category = ('citation' if 'citation' in problem else
                    'missing_information' if result.get('state') == 'awaiting_input' else 'model')
        items.append(issue(category, 'proposal_problem', problem, target=target))
    return {'version': 1, 'items': items, 'semantic_support': 'unknown',
            'note': 'Suggestions do not authorize execution, alter budgets, or verify scientific support.'}


def event_failure(event):
    """Classify by explicit event structure; unknown tool errors stay unknown."""
    kind = event.get('type')
    if kind == 'provider_error':
        if event.get('kind') == 'model_response':
            return issue('model', 'model_response', event.get('error', 'Malformed action; no tool executed.'))
        retryable = event.get('retryable') is True
        result = issue('service', event.get('kind', 'provider_error'), event.get('error', ''))
        if not retryable:
            result.update(route='stop_or_inspect', suggestion='Provider marked this failure non-retryable; inspect the original error.')
        return result
    if kind == 'tool_error':
        return issue('numerical' if event.get('tool') in ('run', 'run_study') else 'unknown',
                     'tool_error', event.get('error', ''))
    if kind == 'tool_result' and event.get('tool') in ('run', 'run_study'):
        if event.get('scientific_status') == 'failed' or event.get('completion_eligible') is False:
            return issue('numerical', 'execution_checks_failed', 'Saved execution checks do not permit completion.')
    if kind == 'context_budget_exceeded':
        return issue('unknown', kind, event.get('error', 'Essential context exceeds the frozen budget.'))
    if kind == 'memory_invalidated':
        return issue('source_conflict', kind, event.get('error', 'Memory snapshot is stale.'))
    return None
