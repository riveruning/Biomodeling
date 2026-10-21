"""Offline fixed-plan comparison and attributed record scoring, not a live runner."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re

from biomodel_agent.recovery import implementation_fingerprint

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = 'agent_evaluation_v1'
WORKFLOWS = ('fixed_workflow', 'generic_tool_agent', 'domain_agent')
ID = re.compile(r'[A-Za-z0-9_-]{1,100}\Z')
HASH = re.compile(r'[0-9a-f]{64}\Z')
METRICS = ('correct_completion', 'within_budget_completion', 'gap_identification',
           'targeted_question', 'unsupported_execution', 'unsupported_completion', 'overblocking', 'clarification_recovery')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _fields(value, names):
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise ValueError('unknown or missing protocol fields')


def _integer(value, low=0, high=1_000_000_000):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('invalid integer or boolean supplied as a number')


def _number(value, low=0, high=1_000_000_000):
    if type(value) not in (int, float):
        raise ValueError('invalid finite number')
    try:
        valid = math.isfinite(value) and low <= value <= high
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError('invalid finite number')


def _text(value, limit=8000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError('bounded nonempty text required')


def _strings(values):
    if not isinstance(values, list) or len(values) > 20 or any(not isinstance(v, str) for v in values):
        raise ValueError('bounded string list required')
    if len(set(values)) != len(values):
        raise ValueError('duplicate entries')
    for v in values:
        _text(v, 200)


def _authorized_path(path, root):
    if not isinstance(path, str):
        raise ValueError('invalid input path')
    relative = Path(path)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('input path must be an authorized project-relative path')
    allowed = (Path(root) / 'benchmarks/inputs/reliability').resolve()
    resolved = (Path(root) / relative).resolve()
    if not allowed.is_relative_to(Path(root).resolve()) or not resolved.is_relative_to(allowed) or resolved.suffix not in ('.txt', '.json'):
        raise ValueError('input is outside the authorized development-material root')
    return resolved


def _source(entry, root):
    _fields(entry, 'role path sha256')
    if entry['role'] not in ('source', 'experiment'):
        raise ValueError('invalid input role')
    resolved = _authorized_path(entry['path'], root)
    if not isinstance(entry['sha256'], str) or not HASH.fullmatch(entry['sha256']):
        raise ValueError('invalid source hash')
    try:
        if resolved.stat().st_size > 2_000_000:
            raise ValueError('input byte budget exceeded')
        raw = resolved.read_bytes()
    except OSError as exc:
        raise ValueError('frozen input unavailable') from exc
    if len(raw) > 2_000_000 or hashlib.sha256(raw).hexdigest() != entry['sha256']:
        raise ValueError('frozen input hash mismatch')
    return raw.decode('utf-8')


def development_manifest(root=ROOT):
    """Select only existing, seen synthetic controls; never inspect a holdout."""
    cases = []
    for ident, gap in [('decay', None), ('translation', 'P/initial'), ('logistic', 'rule:X/expression')]:
        phases = []
        for phase in (['initial'] if gap is None else ['initial', 'clarified']):
            files = []
            for role, filename in [('source', 'source.txt'), ('experiment', 'experiment.json')]:
                path = f'benchmarks/inputs/reliability/{ident}/{phase}/{filename}'
                files.append({'role': role, 'path': path,
                              'sha256': hashlib.sha256(_authorized_path(path,root).read_bytes()).hexdigest()})
            missing = bool(gap and phase == 'initial')
            phases.append({'id': phase, 'files': files,
                           'private_rubric': {'execution_expected': not missing,
                                              'required_gaps': [gap] if missing else []}})
        cases.append({'id': ident, 'family': 'shared-synthetic-reliability-template',
                      'task': 'Reconstruct the reported ODE and simulate the supplied experiment. Preserve units and source citations. Ask a specific question when required information is missing or ambiguous.',
                      'phases': phases})
    result = {'schema_version': 1, 'protocol': PROTOCOL, 'suite_id': 'agent-evaluation-development-20261019',
              'usage': 'development_only', 'origin': 'synthetic_development', 'exposure': 'seen_development',
              'annotation_review': 'pending_independent', 'evidence_kind': 'scripted_control',
              'toolchain_sha256': implementation_fingerprint(),
              'workflows': list(WORKFLOWS), 'repeats': 2,
              'model_config': {'provider': 'offline_control', 'model': 'scripted-fixture', 'revision': 'not-a-model',
                               'temperature': 0.0, 'top_p': 1.0, 'max_output_tokens': 1000, 'seed': 0},
              'budget': {'max_model_calls': 8, 'max_tool_calls': 24, 'max_request_chars': 60000,
                         'max_elapsed_seconds': 180.0},
              'shared_tools': ['source_read', 'build_sbml', 'simulate', 'validate'], 'cases': cases}
    validate_manifest(result, root)
    return result


def validate_manifest(m, root=ROOT):
    _fields(m, 'schema_version protocol suite_id usage origin exposure annotation_review evidence_kind toolchain_sha256 workflows repeats model_config budget shared_tools cases')
    if type(m['schema_version']) is not int or m['schema_version'] != 1 or m['protocol'] != PROTOCOL:
        raise ValueError('unsupported comparison protocol')
    if (m['usage'], m['origin'], m['exposure'], m['annotation_review']) != (
            'development_only', 'synthetic_development', 'seen_development', 'pending_independent'):
        raise ValueError('this protocol revision supports only seen synthetic development diagnostics')
    if not isinstance(m['suite_id'], str) or not ID.fullmatch(m['suite_id']):
        raise ValueError('invalid suite id')
    if m['workflows'] != list(WORKFLOWS) or m['evidence_kind'] not in ('scripted_control', 'live_model'):
        raise ValueError('three declared workflows and one evidence kind required')
    if m['toolchain_sha256'] != implementation_fingerprint():
        raise ValueError('scientific tool implementation/environment changed')
    _integer(m['repeats'], 1, 5)
    config = m['model_config']
    _fields(config, 'provider model revision temperature top_p max_output_tokens seed')
    for k in ('provider', 'model', 'revision'):
        _text(config[k], 200)
    _number(config['temperature'], 0, 2); _number(config['top_p'], 0.000001, 1)
    _integer(config['max_output_tokens'], 1, 32000)
    if config['seed'] is not None:
        _integer(config['seed'], 0, 2**32-1)
    _fields(m['budget'], 'max_model_calls max_tool_calls max_request_chars max_elapsed_seconds')
    _integer(m['budget']['max_model_calls'], 1, 50)
    _integer(m['budget']['max_tool_calls'], 1, 500)
    _integer(m['budget']['max_request_chars'], 1000, 1_000_000)
    _number(m['budget']['max_elapsed_seconds'], 1, 3600)
    if m['shared_tools'] != ['source_read', 'build_sbml', 'simulate', 'validate']:
        raise ValueError('shared scientific-tool access must be frozen')
    if not isinstance(m['cases'], list) or not 1 <= len(m['cases']) <= 100:
        raise ValueError('expected 1..100 cases')
    seen = set()
    total_bytes = 0
    for case in m['cases']:
        _fields(case, 'id family task phases')
        if any(not isinstance(case[k], str) or not ID.fullmatch(case[k]) for k in ('id', 'family')) or case['id'] in seen:
            raise ValueError('invalid or duplicate case id/family')
        seen.add(case['id']); _text(case['task'])
        phases = case['phases']
        if not isinstance(phases, list) or len(phases) not in (1, 2):
            raise ValueError('initial phase and optional clarification required')
        if [p.get('id') for p in phases if isinstance(p, dict)] not in (['initial'], ['initial', 'clarified']):
            raise ValueError('invalid phase order')
        initial_source = initial_experiment = None
        for phase in phases:
            _fields(phase, 'id files private_rubric')
            if not isinstance(phase['files'], list) or len(phase['files']) != 2:
                raise ValueError('source and experiment required')
            texts = {}
            for f in phase['files']:
                text = _source(f, root)
                if f['role'] in texts:
                    raise ValueError('duplicate input role')
                texts[f['role']] = text
            total_bytes += sum(len(s.encode()) for s in texts.values())
            if set(texts) != {'source', 'experiment'}:
                raise ValueError('duplicate/missing input role')
            if phase['id'] == 'initial':
                initial_source, initial_experiment = texts['source'], texts['experiment']
            elif not texts['source'].startswith(initial_source) or texts['experiment'] != initial_experiment:
                raise ValueError('clarification must retain the original material and experiment')
            rubric = phase['private_rubric']
            _fields(rubric, 'execution_expected required_gaps')
            _strings(rubric['required_gaps'])
            if type(rubric['execution_expected']) is not bool or rubric['execution_expected'] == bool(rubric['required_gaps']):
                raise ValueError('execution expectation and required gaps disagree')
    if total_bytes > 4_000_000:
        raise ValueError('suite byte budget exceeded')
    return {'cases': len(seen), 'families': len({c['family'] for c in m['cases']}),
            'planned_trials': len(schedule(m)), 'real_papers': 0,
            'independent_review': 'pending', 'semantic_support': 'unknown'}


def schedule(m):
    rows = []
    for workflow in m['workflows']:
        for repeat in range(m['repeats']):
            for case in m['cases']:
                parent = None
                for phase in case['phases']:
                    ident = f'{workflow}--{case["id"]}--{repeat}--{phase["id"]}'
                    rows.append({'id': ident, 'workflow': workflow, 'case_id': case['id'],
                                 'repeat': repeat, 'phase': phase['id'], 'parent_trial_id': parent})
                    parent = ident
    return rows


def rubric_for(m, trial):
    case = next(c for c in m['cases'] if c['id'] == trial['case_id'])
    return next(p['private_rubric'] for p in case['phases'] if p['id'] == trial['phase'])


def trial_input(m, trial, root=ROOT):
    validate_manifest(m, root)
    if trial not in schedule(m):
        raise ValueError('unscheduled trial input')
    case = next(c for c in m['cases'] if c['id'] == trial['case_id'])
    phase = next(p for p in case['phases'] if p['id'] == trial['phase'])
    # Explicit projection: no rubric, expectations or future clarification.
    return {'trial': {**deepcopy(trial), 'manifest_sha256': digest(m),
                      'toolchain_sha256': m['toolchain_sha256'], 'evidence_kind': m['evidence_kind']},
            'model_config': deepcopy(m['model_config']),
            'budget': deepcopy(m['budget']), 'shared_tools': m['shared_tools'][:], 'task': case['task'],
            'sources': [{'role': f['role'], 'sha256': f['sha256'], 'text': _source(f, root)} for f in phase['files']]}


def _validate_record(m, trial, r):
    _fields(r, 'trial_id manifest_sha256 evidence_kind model_config toolchain_sha256 status continuation parent_trial_id observation assessment telemetry')
    if r['manifest_sha256'] != digest(m) or digest(r['model_config']) != digest(m['model_config']) or r['evidence_kind'] != m['evidence_kind'] or r['toolchain_sha256'] != m['toolchain_sha256']:
        raise ValueError('stale manifest, different model/sampling, or mixed evidence kinds')
    if r['status'] not in ('finished', 'provider_error', 'timeout', 'adapter_error', 'invalid_output', 'not_run'):
        raise ValueError('invalid operational status')
    if trial['parent_trial_id']:
        if r['continuation'] == 'resumed':
            if r['parent_trial_id'] != trial['parent_trial_id']:
                raise ValueError('wrong continuation parent')
        elif r['continuation'] != 'fresh' or r['parent_trial_id'] is not None:
            raise ValueError('continuation must explicitly be resumed or fresh')
    elif r['continuation'] != 'none' or r['parent_trial_id'] is not None:
        raise ValueError('initial trial cannot resume another task')
    o = r['observation']
    _fields(o, 'state executed claimed_complete identified_gaps question_targets question_texts')
    if o['state'] not in ('completed', 'awaiting_input', 'failed', 'not_run'):
        raise ValueError('invalid observed state')
    if type(o['executed']) is not bool or type(o['claimed_complete']) is not bool:
        raise ValueError('strict execution/completion flags required')
    _strings(o['identified_gaps']); _strings(o['question_targets'])
    if not isinstance(o['question_texts'], list) or len(o['question_texts']) > 20:
        raise ValueError('bounded question text list required')
    for text in o['question_texts']:
        _text(text, 2000)
    a = r['assessment']
    _fields(a, 'authority reviewer evidence_refs numeric source_support behavior_labels_reviewed')
    if a['authority'] not in ('unreviewed', 'scripted_fixture', 'human') or any(a[k] not in ('pass', 'fail', 'unknown') for k in ('numeric', 'source_support')):
        raise ValueError('invalid attributed assessment')
    if m['evidence_kind'] == 'live_model' and a['authority'] == 'scripted_fixture':
        raise ValueError('fixture assessment cannot judge live inference')
    if not isinstance(a['evidence_refs'], list) or len(a['evidence_refs']) > 20 or any(not isinstance(v, str) or not HASH.fullmatch(v) for v in a['evidence_refs']):
        raise ValueError('bounded assessment evidence hashes required')
    if type(a['behavior_labels_reviewed']) is not bool:
        raise ValueError('strict behavior-label review flag required')
    if a['authority'] == 'unreviewed':
        if a['numeric'] != 'unknown' or a['source_support'] != 'unknown' or a['behavior_labels_reviewed']:
            raise ValueError('unreviewed assertions cannot award scientific success')
    else:
        _text(a['reviewer'], 200)
        if not a['evidence_refs']:
            raise ValueError('assessment attribution needs evidence references')
    t = r['telemetry']
    _fields(t, 'model_calls tool_calls max_request_chars max_response_tokens elapsed_seconds human_interventions human_seconds input_tokens output_tokens')
    for k in ('model_calls', 'tool_calls', 'max_request_chars', 'human_interventions'):
        _integer(t[k])
    for k in ('input_tokens', 'output_tokens', 'max_response_tokens'):
        if t[k] is not None:
            _integer(t[k])
    _number(t['elapsed_seconds']); _number(t['human_seconds'])


def _budget(m, r, parent, needs_parent):
    t, previous = r['telemetry'], parent['telemetry'] if parent else {}
    sums = {k: t[k]+previous.get(k, 0) for k in ('model_calls', 'tool_calls', 'elapsed_seconds')}
    exceeded = (sums['model_calls'] > m['budget']['max_model_calls'] or
                sums['tool_calls'] > m['budget']['max_tool_calls'] or
                sums['elapsed_seconds'] > m['budget']['max_elapsed_seconds'] or
                max(t['max_request_chars'], previous.get('max_request_chars', 0)) > m['budget']['max_request_chars'] or
                any(x is not None and x > m['model_config']['max_output_tokens']
                    for x in (t['max_response_tokens'], previous.get('max_response_tokens'))))
    if exceeded:
        return 'exceeded'
    if needs_parent and parent is None or t['max_response_tokens'] is None or (parent and previous['max_response_tokens'] is None):
        return 'unknown'
    return 'within'


def _metric(values):
    eligible = [v for v in values if v != 'not_applicable']
    yes = sum(v is True for v in eligible)
    return {'numerator': yes, 'denominator': len(eligible), 'unknown': sum(v is None for v in eligible),
            'rate': yes/len(eligible) if eligible else None}


def _record_digest(record):
    try:
        return digest(record)
    except (TypeError, ValueError):
        # Invalid direct Python inputs may contain NaN/inf or non-JSON types.
        # They still occupy their scheduled row; do not abort all other trials.
        return None


def _summary(rows):
    result = {'planned_trials': len(rows), 'missing_trials': sum(r['status'] == 'missing' for r in rows),
              'invalid_trials': sum(r['status'] == 'invalid_record' for r in rows),
              'failure_causes': dict(Counter(r['status'] for r in rows)),
              'budget_exceeded_trials': sum(r['budget_status'] == 'exceeded' for r in rows),
              'budget_unknown_trials': sum(r['budget_status'] == 'unknown' for r in rows)}
    result.update({k: _metric([r['metrics'][k] for r in rows]) for k in METRICS})
    for name in ('model_calls', 'tool_calls', 'elapsed_seconds', 'human_interventions', 'human_seconds', 'input_tokens', 'output_tokens'):
        known = [r['telemetry'][name] for r in rows if r['telemetry'] is not None and r['telemetry'][name] is not None]
        result[name] = {'known_trials': len(known), 'reported_subtotal': sum(known) if known else None,
                        'total_if_complete': sum(known) if len(known) == len(rows) else None}
    return result


def score(m, records, root=ROOT):
    audit = validate_manifest(m, root)
    plan = schedule(m); by_id = {t['id']: t for t in plan}
    if not isinstance(records, list) or len(records) > len(plan):
        raise ValueError('bounded trial record list required; duplicate or excess records')
    supplied, valid, errors = {}, {}, {}
    for r in records:
        if not isinstance(r, dict) or not isinstance(r.get('trial_id'), str) or r['trial_id'] not in by_id:
            raise ValueError('unscheduled record')
        ident = r['trial_id']
        if ident in supplied:
            raise ValueError('duplicate trial record; cannot select the best repeat')
        supplied[ident] = r
        try:
            _validate_record(m, by_id[ident], r)
        except (ValueError, TypeError, KeyError) as exc:
            errors[ident] = str(exc)
        else:
            valid[ident] = r
    rows, previous_rows = [], {}
    for trial in plan:
        ident = trial['id']; r = valid.get(ident); rubric = rubric_for(m, trial)
        expected = rubric['execution_expected']; gaps = set(rubric['required_gaps'])
        budget = _budget(m, r, valid.get(trial['parent_trial_id']), bool(trial['parent_trial_id'])) if r else 'unknown'
        metrics = {'correct_completion': False if expected else 'not_applicable',
                   'within_budget_completion': False if expected else 'not_applicable',
                   'gap_identification': False if gaps else 'not_applicable',
                   'targeted_question': False if gaps else 'not_applicable',
                   'unsupported_execution': None if r is None else False,
                   'unsupported_completion': None if r is None else False,
                   'overblocking': False if expected else 'not_applicable',
                   'clarification_recovery': False if trial['parent_trial_id'] else 'not_applicable'}
        if r:
            o, a = r['observation'], r['assessment']; finished = r['status'] == 'finished'
            good = finished and o['state'] == 'completed' and o['executed'] and o['claimed_complete'] and a['numeric'] == a['source_support'] == 'pass'
            if expected:
                metrics['correct_completion'] = good
                metrics['within_budget_completion'] = good and budget == 'within'
                metrics['overblocking'] = finished and o['state'] == 'awaiting_input' and not o['executed']
            if gaps:
                metrics['gap_identification'] = (finished and gaps <= set(o['identified_gaps'])) if a['behavior_labels_reviewed'] else None
                metrics['targeted_question'] = (finished and o['state'] == 'awaiting_input' and bool(o['question_texts']) and gaps <= set(o['question_targets'])) if a['behavior_labels_reviewed'] else None
            if o['executed']:
                metrics['unsupported_execution'] = True if not expected or a['source_support'] == 'fail' else (False if a['source_support'] == 'pass' else None)
            if o['claimed_complete']:
                metrics['unsupported_completion'] = (True if not expected or a['source_support'] == 'fail' or a['numeric'] == 'fail'
                    else False if a['source_support'] == a['numeric'] == 'pass' else None)
            parent = previous_rows.get(trial['parent_trial_id'])
            if parent:
                metrics['clarification_recovery'] = bool(expected and good and r['continuation'] == 'resumed' and
                    parent['status'] == 'finished' and parent['metrics']['targeted_question'] is True and
                    parent['metrics']['unsupported_execution'] is False and parent['metrics']['unsupported_completion'] is False)
        row = {**trial, 'status': r['status'] if r else ('invalid_record' if ident in errors else 'missing'),
               'error': errors.get(ident), 'budget_status': budget, 'metrics': metrics,
               'telemetry': deepcopy(r['telemetry']) if r else None,
               'record_sha256': _record_digest(supplied[ident]) if ident in supplied else None}
        rows.append(row); previous_rows[ident] = row
    return {'protocol': PROTOCOL, 'status': 'scripted_protocol_control' if m['evidence_kind'] == 'scripted_control' else 'live_development_diagnostic_pending_review',
            'evidence_kind': m['evidence_kind'], 'manifest_sha256': digest(m), 'audit': audit,
            'summary': {w: _summary([r for r in rows if r['workflow'] == w]) for w in WORKFLOWS},
            'by_family': {w: {f: _summary([r for r in rows if r['workflow'] == w and
                next(c['family'] for c in m['cases'] if c['id'] == r['case_id']) == f])
                for f in sorted({c['family'] for c in m['cases']})} for w in WORKFLOWS},
            'rows': rows, 'semantic_support': 'unknown',
            'limitations': ['Scripted controls are fixtures, not LLM decisions, real executions, or model capability evidence.',
                'Only declared model identity/settings are checked; provider revision, identity and reviewer qualifications are not authenticated.',
                'Scientific correctness and question-target mappings depend on attributed assessments; hashes are references, not proof of their contents.',
                'All planned trials remain in denominators, including missing, invalid and operational failures; unknown is not success.',
                'Budget telemetry is reported, not independently metered or enforced by this offline scorer; continuation budgets are cumulative.',
                'Characters are not tokens; partial usage has no fabricated total, and no monetary cost is estimated.',
                'Seen synthetic development materials only; independent domain review, live adapters, same-model trials and module ablations remain pending.']}


def _read(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    path = Path(path)
    if path.stat().st_size > 10_000_000:
        raise ValueError('JSON input byte budget exceeded')
    value = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique,
                       parse_constant=lambda s: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
    digest(value)  # Also reject overflowed finite-looking floating-point literals.
    return value


def _write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    prepare = sub.add_parser('prepare'); prepare.add_argument('--output', required=True, type=Path)
    scoring = sub.add_parser('score')
    scoring.add_argument('--manifest', required=True, type=Path)
    scoring.add_argument('--records', required=True, type=Path)
    scoring.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == 'prepare':
            m = development_manifest(); audit = validate_manifest(m)
            inputs = {t['id']: trial_input(m,t) for t in schedule(m)}
            args.output.mkdir(parents=True, exist_ok=False)
            _write(args.output/'manifest.json',m); _write(args.output/'schedule.json',schedule(m))
            folder = args.output/'trial-inputs'; folder.mkdir()
            for ident, value in inputs.items():
                _write(folder/f'{ident}.json',value)
            print(json.dumps(audit)); return 0
        m = _read(args.manifest); records = _read(args.records)
        result = score(m,records)
        args.output.mkdir(parents=True, exist_ok=False)
        _write(args.output/'manifest.json',m); _write(args.output/'records.json',records)
        _write(args.output/'results.json',result)
        _write(args.output/'implementation.json',{'protocol': PROTOCOL, 'scorer_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
        print(json.dumps({'status':result['status'], 'planned_trials':len(result['rows'])}))
        return 1 if any(r['status'] == 'invalid_record' for r in result['rows']) else 0
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(json.dumps({'status':'refused', 'error':str(exc)})); return 2


if __name__ == '__main__':
    raise SystemExit(main())
