"""Checks for neutral-output and scientific-output pilot scoring boundaries."""
import importlib.util
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('comparison_score',ROOT/'benchmarks/development/score_comparison.py')
comparison=importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


def test_neutral_expression_and_null_initial_are_preserved():
    normalized=comparison.normalize({'status':'needs_input','states':[
        {'id':'X','initial':None,'quote':None}], 'equations':[
        {'state':'X','expression':'k*x','quote':'dx/dt=k*x'}]})
    assert normalized['facts']['X/initial']['value'] is None
    assert normalized['facts']['rule:X/expression']['evidence']==[
        {'source_id':'case','quote':'dx/dt=k*x'}]


def test_duplicate_targets_are_reported_instead_of_silent_acceptance():
    normalized=comparison.normalize({'parameters':[
        {'id':'k','value':1.},{'id':'k','value':2.}]})
    assert normalized['duplicate_targets']==['k/value']


@pytest.mark.parametrize('values',[
    {'X':[.1,.1],'Y':[.2,.2],'Z':[.3,float('nan')]},
    {'X':[.1],'Y':[.2],'Z':[.3]},
])
def test_invalid_scientific_outputs_are_rejected(values):
    with pytest.raises(ValueError):
        comparison.compare_trajectory([0.,2.],values,[0.,2.])


def test_wrong_sampling_cannot_pass_even_if_values_have_correct_shape():
    with pytest.raises(ValueError,match='sampling'):
        comparison.compare_trajectory([0.,3.],{'X':[.1,.1],'Y':[.2,.2],'Z':[.3,.3]},[0.,2.])


def test_constant_wrong_trajectory_fails_independent_numeric_comparison():
    result=comparison.compare_trajectory([0.,2.],{'X':[.1,.1],'Y':[.2,.2],'Z':[.3,.3]},[0.,2.])
    assert result['status']=='fail'


def test_unknown_program_hash_is_not_executed(tmp_path,monkeypatch):
    script=tmp_path/'simulate.py';script.write_text("raise RuntimeError('must not run')")
    monkeypatch.setattr(comparison,'ROOT',tmp_path)
    assert comparison.run_reviewed_program(tmp_path,{}, {})['status']=='awaiting_code_review'
