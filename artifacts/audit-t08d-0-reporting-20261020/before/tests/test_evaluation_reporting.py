"""Adjudication-aware development reports, never model capability evidence."""
from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SAVED = ROOT/'artifacts/audit-t08c-http-transport-20261019/http-mock-execution'


@pytest.fixture
def reporting():
    spec = importlib.util.spec_from_file_location('evaluation_reporting_test',
        ROOT/'benchmarks/development/evaluation_reporting.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def saved(reporting):
    return reporting.v1._read(SAVED/'manifest.json'), reporting.v1._read(SAVED/'records.json')


def test_actual_saved_unreviewed_completions_stay_pending(reporting):
    m, records = saved(reporting)
    before = deepcopy((m, records))
    result = reporting.report(m, records, ROOT)
    assert result['reporting_protocol'] == 'agent_evaluation_report_v2'
    for summary in result['summary'].values():
        metric = summary['metrics']['correct_completion']
        assert metric['denominator'] == 6
        assert metric['counts']['pending_adjudication'] == 6
        assert metric['true'] == metric['false'] == 0
        assert metric['unknown'] == 6 and metric['rate'] is None
        assert metric['rate_bounds'] == {'lower': 0.0, 'upper': 1.0}
        assert summary['metrics']['targeted_question']['denominator'] == 4
        assert summary['metrics']['targeted_question']['counts']['pending_adjudication'] == 4
    assert (m, records) == before


def test_actual_legacy_scores_stay_exact(reporting):
    m, records = saved(reporting)
    old = reporting.v1._read(SAVED/'results.json')
    assert reporting.v1.score(m, records, ROOT) == old
    assert reporting.report(m, records, ROOT)['legacy_result_sha256'] == reporting.v1.digest(old)
