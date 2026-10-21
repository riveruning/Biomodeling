"""Frozen independent reference track, not automatic literature reconstruction."""
import json
from pathlib import Path
import pytest
from biomodel_agent.domain.contracts import ModelArtifact,ExperimentSpec,SimulationResult
from biomodel_agent.simulation import simulate
from biomodel_agent.validation import compare_series

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('mid',['BIOMD0000000012','BIOMD0000000010','BIOMD0000000507','BIOMD0000000027'])
def test_curated_sbml_matches_independent_scipy_reference(mid):
    suffix='.render-fixed.xml' if mid.endswith('0507') else '.xml'
    model=ModelArtifact(mid,(ROOT/'benchmarks/inputs/public'/f'{mid}{suffix}').read_text())
    gold=json.loads((ROOT/'benchmarks/development/references'/f'{mid}.json').read_text())
    result=simulate(model,ExperimentSpec(gold['times']))
    assert result.status=='succeeded',result.diagnostics
    reference=SimulationResult('succeeded',gold['times'],gold['values'])
    comparison=compare_series(result,reference,gold['scales'],gold['threshold'])
    assert comparison['status']=='pass',comparison
