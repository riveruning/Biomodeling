"""Meaningful offline checks of gap handling, independent references and metric denominators."""
import json
from pathlib import Path
import sys
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'benchmarks/development'))
from prepare_reliability import models, reference
from score_reliability import aggregate, compare, gap_identified, unresolved_numeric_prediction
from run_reliability import verify_freeze


def test_references_against_closed_form_and_conservation():
    t=np.linspace(0,10,21)
    cases={m[0]:m for m in models()}
    def ref(name):
        _,_,s,p,e,_,_=cases[name];return reference(s,p,e,t)['values']
    assert np.allclose(ref('decay')['A'],2*np.exp(-.4*t),rtol=1e-10,atol=1e-11)
    assert np.allclose(ref('turnover')['M'],4+(.5-4)*np.exp(-.3*t),rtol=1e-10,atol=1e-11)
    assert np.allclose(np.array(ref('reversible')['A'])+ref('reversible')['B'],1.25,atol=1e-11)
    b=ref('binding')
    assert np.allclose(np.array(b['A'])+b['C'],1.5,atol=1e-11)
    assert np.allclose(np.array(b['B'])+b['C'],1.,atol=1e-11)
    assert np.allclose(ref('logistic')['X'],2/(1+9*np.exp(-.8*t)),rtol=1e-10,atol=1e-11)


@pytest.mark.parametrize('data',[
    {'times':[0,2],'values':{'A':[1,0]}},
    {'times':[0,1],'values':{'A':[1,float('nan')]}},
    {'times':[0,1],'values':{'A':[1]}}])
def test_wrong_grids_nonfinite_and_truncated_outputs_cannot_pass(data):
    with pytest.raises(ValueError):compare(data,{'times':[0,1],'values':{'A':[1,.5]}},{'A':1})


def test_gap_detection_requires_specific_target_not_generic_refusal():
    assert gap_identified('Please provide initial B.','B/initial','missing_initial')
    assert not gap_identified('Please provide initial A.','B/initial','missing_initial')
    assert not gap_identified('Information missing.','B/initial','missing_initial')
    assert gap_identified('k has contradictory second/minute units.','k/unit','unit_conflict')
    assert not gap_identified('k is absent.','k/unit','unit_conflict')


def test_zero_default_is_unsupported_but_null_is_not():
    assert unresolved_numeric_prediction({'B/initial':{'value':0}},'B/initial')
    assert not unresolved_numeric_prediction({'B/initial':{'value':None}},'B/initial')


def test_conditional_rebuild_does_not_count_unasked_clarifications():
    rows=[{'system':'biomodel','case':'a','phase':'initial','category':'missing_initial','expected_execution':False,'gap_detected':True},
          {'system':'biomodel','case':'a','phase':'clarified','category':'missing_initial','expected_execution':True,'successful_execution':False},
          {'system':'biomodel','case':'b','phase':'initial','category':'missing_initial','expected_execution':False,'gap_detected':False},
          {'system':'biomodel','case':'b','phase':'clarified','category':'missing_initial','expected_execution':True,'successful_execution':True}]
    for row in rows:row['status']='test'
    result=aggregate(rows)['biomodel']
    assert result['clarified_rebuild']=={'count':1,'denominator':2}
    assert result['conditional_rebuild']=={'count':0,'denominator':1}


@pytest.mark.skipif(not (ROOT/'artifacts/reliability-20261017/freeze.json').exists(),reason='optional development benchmark not prepared')
def test_frozen_inputs_and_reference_files_are_unchanged():verify_freeze()
