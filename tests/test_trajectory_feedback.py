import pytest

from biomodel_agent import agent, validation


@pytest.mark.parametrize('values', [
    {'Y':[1.,1.,1.,1.]},
    {'Y':[0.,0.,0.,0.],'X':[2.,2.,2.,2.]},
    {'Y':[1.,1.+1e-11,1.,1.]},
])
def test_flat_trajectory_is_visible_without_certifying_scientific_failure(values):
    simulation=dict(status='succeeded',times=[0.,1.,2.,3.],values=values)
    diagnostic=validation.trajectory_diagnostic(simulation)
    assert diagnostic['status']=='suspicious'
    assert set(diagnostic['flat_columns'])==set(values)
    assert diagnostic['scientific_support']=='unknown'
    assert 'valid steady state' in diagnostic['suggested_fix']
    facts=agent._run_facts({'simulation':simulation})
    assert facts['trajectory_diagnostic']==diagnostic
    assert facts['scientific_status']==agent._run_facts({})['scientific_status']


def test_varying_trajectory_does_not_claim_oscillation():
    diagnostic=validation.trajectory_diagnostic(dict(status='succeeded',times=[0.,1.,2.],
        values={'Y':[1.,2.,3.],'constant':[5.,5.,5.]}))
    assert diagnostic['status']=='nonconstant' and diagnostic['flat_columns']==['constant']
    assert diagnostic['scientific_support']=='unknown'


@pytest.mark.parametrize('simulation', [None, {}, {'status':'failed'},
    dict(status='succeeded',times=[0.,1.],values={'Y':[1.,1.]}),
    dict(status='succeeded',times=[0.,1.,2.],values={'Y':[1.,float('nan'),1.]})])
def test_incomplete_or_invalid_data_does_not_become_flat_trajectory_evidence(simulation):
    assert validation.trajectory_diagnostic(simulation)['status']=='not_available'


def test_large_finite_values_do_not_overflow_descriptive_diagnostic():
    result=validation.trajectory_diagnostic(dict(status='succeeded',times=[0.,1.,2.],
        values={'Y':[-1e308,0.,1e308]}))
    assert result['status']=='nonconstant'
