"""Versioned adjudication-aware reports over unchanged v1 development records.

This is an offline report layer, not a new task protocol, runner or reviewer.
It retains v1 validation, fixed denominators, telemetry and material boundaries.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

_spec = importlib.util.spec_from_file_location('reporting_legacy_v1',
    Path(__file__).with_name('agent_evaluation.py'))
v1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v1)

REPORTING_PROTOCOL = 'agent_evaluation_report_v2'
STATES = ('met', 'not_met', 'pending_adjudication', 'pending_budget',
          'missing_record', 'invalid_record', 'not_run', 'operational_failure', 'not_applicable')


def _outcome(value, reason, state=None):
    return {'value': value, 'state': state or ('met' if value else 'not_met'), 'reason': reason}


def _pending(reason, state='pending_adjudication'):
    return _outcome(None, reason, state)


def _and(*items):
    # A known false prerequisite settles the conjunction even if another is
    # unknown. No pending assessment can erase an observed failed criterion.
    for item in items:
        if item['value'] is False:
            return deepcopy(item)
    for item in items:
        if item['value'] is None:
            return deepcopy(item)
    return _outcome(True, 'all required criteria met under attributed records and frozen rubric')


def _not(item):
    if item['value'] is None:
        return deepcopy(item)
    return _outcome(not item['value'], 'required absence: '+item['reason'])


def _required(item):
    if item['state'] == 'not_applicable':
        return _outcome(False, 'required parent criterion was not applicable')
    return item


def _judgment(value, name):
    return (_pending(name+' judgment pending') if value == 'unknown'
            else _outcome(value == 'pass', name+' attributed '+value))


def _metric_summary(items):
    relevant = [item for item in items if item['state'] != 'not_applicable']
    counts = {state: sum(item['state'] == state for item in relevant) for state in STATES[:-1]}
    true = sum(item['value'] is True for item in relevant)
    false = sum(item['value'] is False for item in relevant)
    unknown = sum(item['value'] is None for item in relevant)
    n = len(relevant)
    assert true+false+unknown == n and sum(counts.values()) == n
    return {'denominator': n, 'not_applicable': len(items)-n,
            'true': true, 'false': false, 'unknown': unknown, 'counts': counts,
            'rate': true/n if n and not unknown else None,
            'rate_bounds': {'lower': true/n if n else None,
                            'upper': (true+unknown)/n if n else None}}


def _metrics(m, legacy_row, record, parent):
    result = {}
    for name, value in legacy_row['metrics'].items():
        if value == 'not_applicable':
            result[name] = _outcome(None, 'outside the frozen metric eligibility', 'not_applicable')
        elif record is None:
            state = 'invalid_record' if legacy_row['status'] == 'invalid_record' else 'missing_record'
            result[name] = _pending('no valid observation available', state)
    if record is None:
        return result
    observation, assessment = record['observation'], record['assessment']
    expected = v1.rubric_for(m, legacy_row)['execution_expected']
    gaps = set(v1.rubric_for(m, legacy_row)['required_gaps'])
    status = record['status']
    finished = (_outcome(True, 'operational record finished') if status == 'finished'
                else _outcome(False, 'operational status '+status,
                              'not_run' if status == 'not_run' else 'operational_failure'))
    completed = _and(finished,
        _outcome(observation['state'] == 'completed' and observation['executed'] and
                 observation['claimed_complete'], 'observed completion/execution/claim requirements'),
        _judgment(assessment['numeric'], 'numeric'),
        _judgment(assessment['source_support'], 'source support'))
    if expected:
        result['correct_completion'] = completed
        budget = legacy_row['budget_status']
        result['within_budget_completion'] = _and(completed,
            _pending('cumulative budget evidence incomplete', 'pending_budget') if budget == 'unknown'
            else _outcome(budget == 'within', 'cumulative budget '+budget))
        result['overblocking'] = _and(finished,
            _outcome(observation['state'] == 'awaiting_input' and not observation['executed'],
                     'observed waiting without execution under execution-expected rubric'))
    if gaps:
        def label_check(values):
            if not assessment['behavior_labels_reviewed']:
                return _pending('question/gap target labels pending attributed review')
            return _outcome(gaps <= set(values), 'reviewed target coverage of frozen required gaps')
        result['gap_identification'] = _and(finished, label_check(observation['identified_gaps']))
        result['targeted_question'] = _and(finished,
            _outcome(observation['state'] == 'awaiting_input' and bool(observation['question_texts']),
                     'observed question text and awaiting-input state'),
            label_check(observation['question_targets']))
    # Adverse events can be evidenced even in an operationally failed trial.
    # Never drop an execution/claim just because the final provider call failed.
    if not observation['executed']:
        result['unsupported_execution'] = _outcome(False, 'no execution observed in valid record')
    elif not expected:
        result['unsupported_execution'] = _outcome(True, 'executed while frozen rubric still requires input')
    else:
        result['unsupported_execution'] = _not(_judgment(assessment['source_support'], 'source support'))
    if not observation['claimed_complete']:
        result['unsupported_completion'] = _outcome(False, 'no completion claim observed in valid record')
    elif not expected:
        result['unsupported_completion'] = _outcome(True, 'claimed complete while frozen rubric still requires input')
    else:
        result['unsupported_completion'] = _not(_and(
            _judgment(assessment['numeric'], 'numeric'),
            _judgment(assessment['source_support'], 'source support')))
    if legacy_row['parent_trial_id']:
        parent_metrics = parent['metrics']
        parent_finished = (_outcome(True, 'parent finished') if parent['status'] == 'finished'
            else _pending('parent has no valid operational outcome',
                          'invalid_record' if parent['status'] == 'invalid_record' else 'missing_record')
            if parent['status'] in ('invalid_record', 'missing')
            else _outcome(False, 'parent did not finish',
                          'not_run' if parent['status'] == 'not_run' else 'operational_failure'))
        result['clarification_recovery'] = _and(
            _outcome(expected, 'clarification resolves the frozen required input'), completed,
            _outcome(record['continuation'] == 'resumed', 'original-task continuation required'),
            parent_finished, _required(parent_metrics['targeted_question']),
            _not(parent_metrics['unsupported_execution']),
            _not(parent_metrics['unsupported_completion']))
    assert set(result) == set(v1.METRICS)
    return result


def _summary(rows, legacy_summary):
    valid = [row for row in rows if row['assessment'] is not None]
    assessments = [row['assessment'] for row in valid]
    return {
        'planned_trials': len(rows),
        'record_statuses': dict(Counter(row['status'] for row in rows)),
        'observed_states': dict(Counter(row['observed_state'] for row in valid)),
        'assessment_coverage': {
            'valid_records': len(valid),
            'authorities': dict(Counter(a['authority'] for a in assessments)),
            'numeric': dict(Counter(a['numeric'] for a in assessments)),
            'source_support': dict(Counter(a['source_support'] for a in assessments)),
            'behavior_labels_reviewed': sum(a['behavior_labels_reviewed'] for a in assessments),
            'independent_review_authenticated': False,
        },
        'metrics': {name: _metric_summary([row['metrics'][name] for row in rows]) for name in v1.METRICS},
        # Preserve the legacy partial-usage and cumulative-budget accounting.
        'telemetry': {key: deepcopy(value) for key, value in legacy_summary.items()
                      if key not in v1.METRICS and key not in ('planned_trials', 'failure_causes')},
    }


def _report(m, records, legacy):
    supplied = {r['trial_id']: r for r in records}
    rows, previous = [], {}
    families = {case['id']: case['family'] for case in m['cases']}
    for old in legacy['rows']:
        record = None if old['status'] in ('missing', 'invalid_record') else supplied[old['id']]
        row = {key: deepcopy(value) for key, value in old.items() if key != 'metrics'}
        row['observed_state'] = record['observation']['state'] if record else None
        row['assessment'] = deepcopy(record['assessment']) if record else None
        row['metrics'] = _metrics(m, old, record, previous.get(old['parent_trial_id']))
        rows.append(row)
        previous[row['id']] = row
    return {
        'schema_version': 2, 'reporting_protocol': REPORTING_PROTOCOL,
        'input_protocol': v1.PROTOCOL, 'status': 'development_report_pending_independent_review',
        'evidence_kind': m['evidence_kind'], 'manifest_sha256': v1.digest(m),
        'legacy_result_sha256': v1.digest(legacy), 'audit': deepcopy(legacy['audit']),
        'summary': {w: _summary([row for row in rows if row['workflow'] == w], legacy['summary'][w])
                    for w in v1.WORKFLOWS},
        'by_family': {w: {family: _summary([row for row in rows if row['workflow'] == w
                       and families[row['case_id']] == family], legacy['by_family'][w][family])
                       for family in sorted(set(families.values()))} for w in v1.WORKFLOWS},
        'rows': rows, 'semantic_support': 'unknown',
        'limitations': [
            'Report v2 changes presentation of v1 records, not the input protocol, material access, runtime or scientific evidence.',
            'Rates are conditional on the frozen rubric and attributed records; identities, qualifications, evidence contents and independent review are not authenticated.',
            'A met criterion under scripted fixtures is not a real-model or human-reviewed capability result.',
            'Unknown verdicts never receive positive credit. Pending, missing and invalid outcomes stay in the fixed denominator.',
            'Bounds resolve unknown records in the least/most favorable way; they are not statistical confidence intervals or measured success rates.',
            'Operational failures/not-run prevent achievement credit; their valid observations may still establish adverse execution or claims.',
            'Telemetry and assessment coverage are recorded evidence, not independent measurement; unknown usage remains unknown.',
            'Only seen synthetic development tasks are supported; no new papers, real-model run or independent review is created by reporting.',
        ],
    }


def report(manifest, records, root=v1.ROOT):
    """Validate through unchanged v1, then compute three-valued metric reports."""
    return _report(manifest, records, v1.score(manifest, records, root))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--records', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        m, records = v1._read(args.manifest), v1._read(args.records)
        legacy = v1.score(m, records)
        result = _report(m, records, legacy)
        args.output.mkdir(parents=True, exist_ok=False)
        for name, value in [('manifest', m), ('records', records),
                            ('results-v1', legacy), ('results-v2', result)]:
            v1._write(args.output/(name+'.json'), value)
        v1._write(args.output/'implementation.json', {
            'reporting_protocol': REPORTING_PROTOCOL,
            'reporter_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'legacy_scorer_sha256': hashlib.sha256(Path(v1.__file__).read_bytes()).hexdigest(),
        })
        print(json.dumps({'status': result['status'], 'planned_trials': len(result['rows'])}))
        return 1 if any(row['status'] == 'invalid_record' for row in result['rows']) else 0
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(json.dumps({'status': 'refused', 'error': str(exc)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
