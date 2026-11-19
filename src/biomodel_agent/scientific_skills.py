"""Frozen scientific procedure contracts with read-only artifact-based audits."""
from copy import deepcopy
import json
import hashlib

from .storage import canonical_bytes

_CONTRACTS = {
    'parameter_adoption_v1': dict(
        purpose='Trace a parameter from authorized declaration to a checked ODE draft.',
        required_tools=['investigate_parameter', 'inspect_source', 'assess_parameter', 'submit_draft', 'inspect_progress'],
        inputs=['Authorized source refs', 'Frozen parameter_requirements with units and conditions'],
        steps=['Investigate the target in current authorized sources and inspect exact declarations.',
               'Use assess_parameter on the complete declaration and metadata span; inspect native derivation if present.',
               'Submit a cited draft with parameter_assessments; use inspect_progress to inspect recomputed checks.'],
        outputs=['parameter_assessment', 'model_draft with original evidence and assessment refs', 'validated model'],
        acceptance=['Recomputed assessment matches frozen requirements and current source scope.',
                    'Submitted value, unit and clock match the assessed declaration; model is ready.'],
        stop_conditions=['Missing or conflicting units/conditions: keep unknown and ask.',
                         'No supported explicit conversion: do not invent one.',
                         'Source scope changed: reassess; consumed repair/call budgets are not refunded.']),
    'reference_comparison_v1': dict(
        purpose='Interpret the actual ODE result against an operator-frozen reference.',
        required_tools=['submit_draft', 'run', 'inspect_progress', 'finish'],
        inputs=['Authorized source refs and a ready cited ODE', 'Internally frozen reference and fixed scales'],
        steps=['Build and validate the cited draft without inspecting evaluator-only reference values.',
               'Run once with the frozen experiment, reference and scales.',
               'Inspect progress: report comparison, execution and scientific support separately.'],
        outputs=['run with simulation_input and actual comparison checks'],
        acceptance=['The actual run is bound to the current ready model and frozen reference.',
                    'Technical comparison passes; unknown scientific support remains unknown.'],
        stop_conditions=['No reference/scales: this procedure is unavailable.',
                         'Failed comparison or numerical checks: diagnose and stop, never tune against reference.',
                         'Do not change thresholds, scales, time units or rerun after evaluation.']),
    'failure_diagnosis_v1': dict(
        purpose='Inspect a bounded failure decision and its original diagnostic artifacts.',
        required_tools=['inspect_progress', 'submit_draft', 'investigate_parameter', 'ask_researcher'],
        inputs=['Enabled frozen failure_policy', 'An actual tool failure or validation diagnostic'],
        steps=['Read the original validation feedback or saved run checks.',
               'Follow the bounded route: repair, local investigation, targeted clarification, or stop.',
               'Inspect progress and preserve the decision ref, remaining budgets and unresolved issues.'],
        outputs=['failure_decision referencing original source/draft/run diagnostics', 'Revised draft or recorded question/stop'],
        acceptance=['Decision recomputes from the frozen policy and original failure facts.',
                    'A stop/clarification is a recorded disposition, not a successful scientific repair.'],
        stop_conditions=['Repeated failure, exhausted budgets or unknown effects: no tool retry.',
                         'Units or source conflict: ask; never choose a value silently.',
                         'Scientific run consumed: no new solver, model, threshold or execution.']),
}


def contract(name):
    if name not in _CONTRACTS:
        raise ValueError('unknown scientific skill')
    return dict(name=name, version=1, contract_version=1, **deepcopy(_CONTRACTS[name]),
                limitations=['Procedure guidance and engineering checks only; scientific support remains unknown.',
                             'Does not grant tools, sources, retries or permission to change a frozen protocol.'])


def prepare(store, task, available_tools):
    entries = {}
    for name in _CONTRACTS:
        definition = contract(name)
        missing = sorted(set(definition['required_tools']) - set(available_tools))
        reasons = ['Missing tool: '+tool for tool in missing]
        if task['mode'] != 'reproduction' or task.get('study_plan_ref'):
            reasons.append('Only ODE reproduction is supported.')
        if name == 'parameter_adoption_v1' and not task.get('parameter_requirements'):
            reasons.append('Frozen parameter requirements are required.')
        if name == 'reference_comparison_v1' and (task.get('reference') is None or not task.get('scales')):
            reasons.append('A frozen reference and fixed scales are required.')
        if name == 'failure_diagnosis_v1' and not task.get('failure_policy'):
            reasons.append('Frozen failure handling must be enabled.')
        ref = store.put('scientific_skill', definition)
        entries[name] = dict(skill_ref=ref, available=not reasons, reasons=reasons)
    return dict(version=1, entries=entries, requirements=deepcopy(task.get('parameter_requirements', {})),
                scientific_input_digest=hashlib.sha256(canonical_bytes({k: task[k] for k in
                    ('experiment', 'solver', 'observations', 'reference', 'scales')})).hexdigest())


def load(store, protocol, name):
    if not isinstance(name, str) or name not in protocol['entries']:
        raise ValueError('unknown scientific skill')
    entry = protocol['entries'][name]
    if not entry['available']:
        raise ValueError('scientific skill unavailable: '+'; '.join(entry['reasons']))
    obj = store.get(entry['skill_ref'])
    expected = contract(name)
    if obj['kind'] != 'scientific_skill' or canonical_bytes(obj['payload']) != canonical_bytes(expected):
        raise ValueError('frozen scientific skill contract differs; start a new task')
    out = dict(scientific_skill_ref=entry['skill_ref'], **obj['payload'])
    if len(json.dumps(out, ensure_ascii=False)) > 4000:
        raise ValueError('scientific skill exceeds the bounded instruction budget')
    return out


def catalog(store, protocol):
    return [dict(name=name, version=load(store, protocol, name)['version'], purpose=contract(name)['purpose'])
            for name, entry in protocol['entries'].items() if entry['available']]


def project(store, protocol, events, *, source_refs, draft_ref=None, model_ref=None, run_ref=None):
    """Loading is not execution. Derive checks from actual current artifacts."""
    loaded = {e['name'] for e in events if e.get('type') == 'tool_result' and e.get('tool') == 'load_skill'
              and e.get('ok') is True and e.get('scientific_skill_ref')}
    result = []
    for name in sorted(loaded):
        definition = load(store, protocol, name)
        bound = protocol['entries'][name]['skill_ref']
        if any(e.get('scientific_skill_ref') != bound for e in events if e.get('tool') == 'load_skill'
               and e.get('name') == name and e.get('ok') is True):
            raise ValueError('loaded scientific skill reference differs from frozen contract')
        row = dict(name=name, version=definition['version'], skill_ref=bound, status='not_run',
                   artifact_refs=[], checks=[], scientific_support='unknown')
        if name == 'parameter_adoption_v1' and draft_ref:
            from .domain.contracts import ModelDraft
            from .domain.serialization import from_dict
            from .parameter_applicability import validate_proposal
            draft = store.get(draft_ref)
            assessments = {store.get(ref)['payload']['target']: ref for ref in draft['refs']
                           if store.get(ref)['kind'] == 'parameter_assessment'}
            checks = validate_proposal(store, from_dict(ModelDraft, draft['payload']), source_refs,
                                       protocol['requirements'], assessments)
            ready = bool(model_ref and draft_ref in store.get(model_ref)['refs'])
            row.update(status='pass' if ready and checks and all(c['status']=='declared_fields_match' for c in checks) else 'blocked',
                       artifact_refs=[draft_ref, *([model_ref] if ready else []), *assessments.values()], checks=checks)
        elif name == 'reference_comparison_v1' and run_ref:
            from .application import derive_scientific_status
            obj = store.get(run_ref)
            if obj['kind'] != 'run':
                raise ValueError('reference comparison requires an ODE run')
            run = obj['payload']
            inputs = store.get(run['input_ref'])['payload']
            model = store.get(model_ref)['payload'] if model_ref else None
            comparison = run.get('comparison') or {}
            bound_model = model is not None and canonical_bytes(inputs['artifact']) == canonical_bytes(model)
            frozen_match = hashlib.sha256(canonical_bytes({k: inputs[k] for k in
                ('experiment', 'solver', 'observations', 'reference', 'scales')})).hexdigest() == protocol['scientific_input_digest']
            frozen_match = frozen_match and inputs.get('strict') is True and inputs.get('threshold') == 1e-3
            status = ('fail' if derive_scientific_status(run)=='failed' or comparison.get('status')=='fail' else
                      'pass' if run.get('status')=='succeeded' and comparison.get('status')=='pass' and bound_model and frozen_match else 'blocked')
            row.update(status=status, artifact_refs=[run_ref, run['input_ref']],
                       checks=[dict(code='reference_comparison', status=comparison.get('status', 'not_run')),
                               dict(code='current_model_binding', status='pass' if bound_model else 'fail'),
                               dict(code='frozen_inputs', status='pass' if frozen_match else 'fail')])
        elif name == 'failure_diagnosis_v1':
            from .failure_handling import load as load_decision
            decisions = [e for e in events if e.get('tool')=='route_failure' and e.get('decision_ref')]
            if decisions:
                obj = store.get(decisions[-1]['decision_ref'])
                decision = load_decision(store, decisions[-1]['decision_ref'], obj['payload']['policy'])
                row.update(status='recorded', artifact_refs=[decisions[-1]['decision_ref']],
                           checks=[dict(code='failure_route', status=decision['decision']['route'])])
        result.append(row)
    return dict(version=1, procedures=result, availability=deepcopy(protocol['entries']),
                note='Checks describe actual artifacts, not skill efficacy or independent scientific validation.')
