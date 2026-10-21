"""Additional independent analytical checks; do not alter frozen scoring criteria."""
from pathlib import Path
import sys
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'benchmarks/development'))
from prepare_reliability import models, reference


def test_remaining_reference_models_against_analytic_identities():
    t=np.linspace(0,10,21); cases={m[0]:m for m in models()}
    def values(name):
        _,_,states,parameters,equations,_,_=cases[name]
        return {k:np.array(v) for k,v in reference(states,parameters,equations,t)['values'].items()}
    binding=values('binding'); decay=np.exp(-.5*.15*t)
    b=.5*decay/(.5+1-decay)
    assert np.allclose(binding['B'],b,rtol=1e-10,atol=1e-11)
    translation=values('translation')
    m=2-np.exp(-.4*t)
    p=.2*np.exp(-.2*t)+6*(1-np.exp(-.2*t))-3*(np.exp(-.2*t)-np.exp(-.4*t))
    assert np.allclose(translation['M'],m,rtol=1e-10,atol=1e-11)
    assert np.allclose(translation['P'],p,rtol=1e-10,atol=1e-11)
    s=values('saturation')['S']
    assert np.allclose(s+.7*np.log(s),2+.7*np.log(2)-.9*t,rtol=1e-8,atol=1e-8)
    assert np.allclose(values('unit_decay')['D'],np.exp(-.3*t),rtol=1e-10,atol=1e-11)
    chain=values('unit_chain')
    assert np.allclose(chain['U'],np.exp(-.2*t),rtol=1e-10,atol=1e-11)
    assert np.allclose(chain['V'],.1*np.exp(-.15*t)+4*(np.exp(-.15*t)-np.exp(-.2*t)),rtol=1e-10,atol=1e-11)


def test_feedback_reference_against_independent_stiff_solver():
    from scipy.integrate import solve_ivp
    t=np.linspace(0,10,21)
    # Independently written from the fixed source rather than evaluator expressions.
    def rhs(time,y):return [1/(1+y[1]**2)-.4*y[0],.7*y[0]-.3*y[1]]
    alternate=solve_ivp(rhs,(0,10),[.2,.1],t_eval=t,method='Radau',rtol=1e-10,atol=1e-12)
    _,_,states,parameters,equations,_,_=next(m for m in models() if m[0]=='feedback')
    primary=reference(states,parameters,equations,t)
    assert alternate.success
    assert np.allclose(alternate.y,np.array([primary['values']['R'],primary['values']['P']]),rtol=1e-8,atol=1e-9)
