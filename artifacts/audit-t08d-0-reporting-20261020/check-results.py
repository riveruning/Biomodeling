"""Verify actual saved-record reports independently of new test fixtures."""
import hashlib
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
OLD = ROOT/'artifacts/audit-t08c-http-transport-20261019/http-mock-execution'
RUN = OUT/'actual-saved-records-report'


def read(path):
    return json.loads(path.read_text())


report = read(RUN/'results-v2.json')
legacy = read(RUN/'results-v1.json')
assert legacy == read(OLD/'results.json') == read(OUT/'legacy-v1-results-before.json')
assert read(RUN/'manifest.json') == read(OLD/'manifest.json')
assert read(RUN/'records.json') == read(OLD/'records.json')
assert len(report['rows']) == 30
assert report['semantic_support'] == 'unknown' and report['evidence_kind'] == 'scripted_control'
assert all(r['assessment']['authority'] == 'unreviewed' for r in report['rows'])
total_pending_completion = 0
for workflow, summary in report['summary'].items():
    assert summary['planned_trials'] == 10
    assert summary['observed_states'] == {'completed': 6, 'awaiting_input': 4}
    completion = summary['metrics']['correct_completion']
    assert completion['counts']['pending_adjudication'] == 6
    assert completion['true'] == completion['false'] == 0
    assert completion['denominator'] == completion['unknown'] == 6
    assert completion['rate'] is None and completion['rate_bounds'] == {'lower': 0.0, 'upper': 1.0}
    total_pending_completion += completion['counts']['pending_adjudication']
    for name in ('gap_identification', 'targeted_question', 'clarification_recovery'):
        metric = summary['metrics'][name]
        assert metric['denominator'] == metric['counts']['pending_adjudication'] == 4
        assert metric['rate'] is None
    assert summary['telemetry']['input_tokens']['total_if_complete'] is None
    assert summary['telemetry']['output_tokens']['total_if_complete'] is None
    assert summary['assessment_coverage']['behavior_labels_reviewed'] == 0
    assert summary['assessment_coverage']['independent_review_authenticated'] is False

result = {
    'status': 'saved_record_report_invariants_verified',
    'planned_phases': 30, 'pending_completion_adjudications': total_pending_completion,
    'initial_gap_phases': 12, 'all_behavior_target_labels_pending': True,
    'legacy_json_results_identical': True, 'manifest_and_records_unchanged': True,
    'bounds_are_confidence_intervals': False, 'model_capability_change_claimed': False,
    'new_scientific_runs': 0, 'online_inference_calls': 0,
    'actual_independent_review_decisions': 0,
}
(OUT/'report-invariants.json').write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps(result))
