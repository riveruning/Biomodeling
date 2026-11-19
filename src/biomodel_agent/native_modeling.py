"""Opt-in native Agent adapter for immutable ODE decision proposals.

Only this adapter assigns source offsets and plan lineage. It executes no
simulation; ordinary Agent run/finish retain their journal and budget.
"""
from copy import deepcopy

from . import modeling_plan as plans
from .domain.serialization import to_dict
from .model_contracts import fields
from .storage import canonical_bytes

SUBMISSION = 'agent_modeling_submission'
PROMPT = '''
This task explicitly enables modeling_decisions. Use submit_plan instead of
submit_draft; generic skill text about submitting drafts refers to this checked
proposal stage. submit_plan args are {plan: <complete decision-plan object>,
reason?: <specific revision reason>}. Keep plan.id stable after the first saved
proposal and provide reason for revisions. The task freezes mode=reproduction,
model_type=ode and context; never change them. A qualitative relation does not
select a rate law. Unresolved choices and missing numbers stay unresolved/null.
The plan schema has schema_version:1, id, model_type, mode, context, entities:
[{id,label}], evidence:[{id,source_ref,page,quote,interpretation,conditions}],
mechanisms:[{id,subject,relation,object,evidence_ids,conditions,rationale}],
candidates:[{id,label,variable_map:<entity to state mapping>,draft:<normal ODE
draft>,decisions:[{target,origin,evidence_ids,mechanism_ids,rationale,alternatives}]}],
selected_candidate:<explicit candidate id or null>. Every numeric/ODE target
requires a decision; origin is reported, assumed or unresolved. Assumptions
cannot complete reproduction. Evidence quotes must be literal unique source
substrings; tools resolve offsets, or accept explicit start/end for a repeated
quote. Mechanisms are unverified interpretations, not equations. Their relation
is promotes/inhibits/production/degradation/conversion/association/unspecified.
Use explicit rate rules, a default unit compartment, no reactions/derived.
Inspect returned decision_feedback and inspect_progress for gaps. Clarification
keeps the same task/budget; resubmit against the updated sources before run.
run is still the single scientific execution and finish still uses saved checks.
'''


def options(task):
    value = task.get('modeling_decisions')
    if value is None:
        raise ValueError('modeling decisions are not enabled for this task')
    fields(value, {'version', 'context'})
    if (type(value['version']) is not int or value['version'] != 1 or task.get('mode') != 'reproduction'
            or task.get('study_plan_ref') or task.get('estimation_plan_ref') or task.get('parameter_requirements')):
        raise ValueError('unsupported modeling-decision task configuration')
    plans._context(value['context'])
    return value


def latest_ref(events):
    return next((e['modeling_plan_ref'] for e in reversed(events)
                 if e.get('tool') == 'submit_plan' and e.get('modeling_plan_ref')), None)


def normalize(store, args, source_refs, task, parent_ref):
    fields(args, {'plan'}, {'reason'})
    config = options(task)
    spec = deepcopy(args['plan'])
    if not isinstance(spec, dict) or spec.get('mode') != 'reproduction' or spec.get('context') != config['context']:
        raise ValueError('proposal must retain the operator-frozen reproduction mode and conditions')
    for anchor in plans._items(spec.get('evidence'), 64, 'evidence'):
        fields(anchor, {'id', 'source_ref', 'page', 'quote', 'interpretation', 'conditions'}, {'start', 'end'})
        if anchor['source_ref'] not in source_refs:
            raise ValueError('proposal citation outside current authorized task sources')
        if type(anchor['page']) is not int or anchor['page'] < 1:
            raise ValueError('citation page must be a positive integer')
        quote = anchor['quote']
        if not isinstance(quote, str) or not quote or len(quote) > 8000:
            raise ValueError('citation quote must contain 1..8000 source characters')
        if ('start' in anchor) != ('end' in anchor):
            raise ValueError('citation needs both offsets or a unique literal quote')
        if 'start' not in anchor:
            from .evidence import _source_page_text
            text = _source_page_text(store, anchor['source_ref'], anchor['page'])
            start = text.find(quote)
            if start < 0 or text.find(quote, start + 1) >= 0:
                raise ValueError('citation quote must be exact and unique; inspect source or supply explicit offsets')
            anchor.update(start=start, end=start + len(quote))
    spec = plans.validate(store, spec, source_refs)
    if parent_ref is not None:
        parent = plans.load(store, parent_ref)
        if parent['spec']['id'] != spec['id'] or not set(parent['source_refs']) <= set(source_refs):
            raise ValueError('revision must retain identity and authorized source lineage')
        plans._text(args.get('reason'))
    elif args.get('reason') is not None:
        plans._text(args['reason'])
    return spec


def _empty():
    return dict(state='invalid', artifact=None, model_ref=None, draft_ref=None, hypothesis_ref=None,
        checks=[], problems=[], source_review=None, source_review_ref=None, citation_diagnostics=[],
        binding_diagnostics=[], parameter_diagnostics=[], assessment_refs=[], modeling_plan_ref=None,
        compilation_ref=None, decision_feedback=None)


def _compiled_result(store, plan_ref, compilation_ref):
    obj = store.get(compilation_ref); c = obj['payload']; view = plans.inspect(store, plan_ref)
    from .recovery import implementation_fingerprint
    if (obj['kind'] != plans.COMPILATION or c.get('plan_ref') != plan_ref
            or type(c.get('version')) is not int or c['version'] != 1 or c.get('scientific_support') != 'unknown'
            or c.get('profile') != plans.PROFILE or c.get('implementation_sha256') != implementation_fingerprint()
            or plan_ref not in obj['refs']):
        raise ValueError('submission compilation does not bind the active plan/compiler')
    result = _empty()
    result.update(state=c['state'] if c['state'] in ('ready', 'invalid') else 'awaiting_input',
        modeling_plan_ref=plan_ref, compilation_ref=compilation_ref, decision_feedback=view,
        checks=deepcopy(c['checks']), binding_diagnostics=view['binding_diagnostics'],
        problems=[i['question'] for i in c['issues']])
    for key in ('draft_ref', 'model_ref', 'hypothesis_ref', 'source_review_ref'):
        result[key] = c[key]
    if c['source_review_ref']:
        result['source_review'] = store.get(c['source_review_ref'])['payload']
    if result['state'] == 'ready':
        _, _, result['artifact'] = plans.compiled_model(store, compilation_ref)
    elif c['model_ref'] is not None:
        raise ValueError('blocked decision submission cannot expose an executable model')
    return result


def refs(result):
    return [result[k] for k in ('modeling_plan_ref', 'compilation_ref', 'submission_ref',
        'draft_ref', 'model_ref', 'hypothesis_ref', 'source_review_ref') if result.get(k)]


def submit(store, args, source_refs, *, task_ref, parent_ref=None):
    task = store.get(task_ref)
    if task['kind'] != 'agent_task':
        raise ValueError('submission requires an Agent task')
    options(task['payload'])
    result = _empty()
    try:
        spec = normalize(store, args, source_refs, task['payload'], parent_ref)
        plan_ref = plans.prepare(store, spec, source_refs, parent_ref=parent_ref,
                                 reason=args['reason'] if parent_ref else None)
        compilation_ref = plans.compile_plan(store, plan_ref)
        result = _compiled_result(store, plan_ref, compilation_ref)
    except (ValueError, TypeError, KeyError) as exc:
        result['problems'] = [str(exc)[:2000]]
    saved = {k: v for k, v in result.items() if k != 'artifact'}
    result['submission_ref'] = store.put(SUBMISSION, dict(version=1, task_ref=task_ref,
        source_refs=list(source_refs), parent_modeling_plan_ref=parent_ref, args=deepcopy(args), result=saved),
        refs=tuple(dict.fromkeys([task_ref, *source_refs, *([parent_ref] if parent_ref else []), *refs(result)])))
    return result


def verify_return(store, task_ref, scope, details):
    """Read-only validation of an artifact-write return before recovery adoption."""
    obj = store.get(details.get('submission_ref')); p = obj['payload']
    if (obj['kind'] != SUBMISSION or type(p.get('version')) is not int or p['version'] != 1
            or p.get('task_ref') != task_ref or p.get('source_refs') != scope.get('source_refs')
            or p.get('args') != scope.get('args') or p.get('parent_modeling_plan_ref') != scope.get('parent_modeling_plan_ref')):
        raise ValueError('submission return differs from reserved task/arguments/source scope')
    saved = p['result']; task = store.get(task_ref)['payload']; options(task)
    if canonical_bytes({k: v for k, v in details.items() if k != 'submission_ref'}) != canonical_bytes(saved):
        raise ValueError('returned submission snapshot differs from its immutable receipt')
    if saved['modeling_plan_ref']:
        expected_spec = normalize(store, p['args'], p['source_refs'], task, p['parent_modeling_plan_ref'])
        plan = plans.load(store, saved['modeling_plan_ref'])
        if (plan['spec'] != expected_spec or plan['source_refs'] != p['source_refs']
                or plan['parent_ref'] != p['parent_modeling_plan_ref']
                or plan['revision_reason'] != (p['args'].get('reason') if plan['parent_ref'] else None)):
            raise ValueError('plan differs from the submitted mathematical choices')
        expected = _compiled_result(store, saved['modeling_plan_ref'], saved['compilation_ref'])
        if canonical_bytes({k: v for k, v in expected.items() if k != 'artifact'}) != canonical_bytes(saved):
            raise ValueError('submission differs from checked compiler artifacts')
    elif saved['state'] != 'invalid' or any(saved.get(k) for k in ('model_ref', 'draft_ref', 'compilation_ref')):
        raise ValueError('invalid submission cannot have compiled artifacts')
    expected_refs = {task_ref, *p['source_refs'], *([p['parent_modeling_plan_ref']] if p['parent_modeling_plan_ref'] else []), *refs(saved)}
    if set(obj['refs']) != expected_refs:
        raise ValueError('submission dependency closure mismatch')
    return saved


def active_model(store, task_ref, events, source_refs, model_ref, draft_ref):
    latest = next((e for e in reversed(events) if e.get('tool') == 'submit_plan'), None)
    if not latest or latest.get('state') != 'ready' or not latest.get('submission_ref'):
        raise ValueError('run requires the latest decision submission to be ready')
    receipt = store.get(latest['submission_ref'])['payload']
    if receipt['source_refs'] != list(source_refs):
        raise ValueError('source scope changed; submit a revised decision plan before execution')
    details = {**deepcopy(receipt['result']), 'submission_ref': latest['submission_ref']}
    scope = dict(args=receipt['args'], source_refs=list(source_refs),
                 parent_modeling_plan_ref=receipt['parent_modeling_plan_ref'])
    saved = verify_return(store, task_ref, scope, details)
    if saved['model_ref'] != model_ref or saved['draft_ref'] != draft_ref:
        raise ValueError('active model differs from the latest checked mathematical decisions')
    return plans.compiled_model(store, saved['compilation_ref'])[2]
