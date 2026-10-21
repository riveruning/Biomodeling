"""Codex-owned input boundary checks; providers are never called."""
from copy import deepcopy
from decimal import Decimal
import json
import math
import pickle

import pytest

from biomodel_agent import validation
from biomodel_agent.domain import contracts as c
from biomodel_agent.domain.serialization import from_dict, to_dict


CASES = {
    'parameter': (c.Parameter, {'id':'k','value':1.}, ('value',)),
    'initial': (c.State, {'id':'x','initial':1.}, ('initial',)),
    'volume': (c.Compartment, {'size':1.}, ('size',)),
    'segment_time': (c.InputSegment, {'time':1.,'values':{'k':2.}}, ('time',)),
    'experiment_time': (c.ExperimentSpec, {'times':[0.,2.]}, ('times',0)),
    'rtol': (c.SolverConfig, {'rtol':1.}, ('rtol',)),
    'atol': (c.SolverConfig, {'atol':1.}, ('atol',)),
    'timeout': (c.SolverConfig, {'timeout_s':1.}, ('timeout_s',)),
    'result_time': (c.SimulationResult, {'status':'succeeded','times':[0.,2.],'values':{'x':[1.,.5]}}, ('times',0)),
    'elapsed': (c.SimulationResult, {'status':'failed','elapsed_s':1.}, ('elapsed_s',)),
    'result_column': (c.SimulationResult, {'status':'succeeded','times':[0.,2.],'values':{'x':[1.,.5]}}, ('values','x',0)),
    'initial_override': (c.ExperimentSpec, {'times':[0.,2.],'initial_overrides':{'x':1.}}, ('initial_overrides','x')),
    'stoichiometry': (c.Reaction, {'id':'r','reactants':{'x':1.},'products':{},'rate':'k*x'}, ('reactants','x')),
}


def modified(case, value):
    cls, payload, path = CASES[case]
    payload = deepcopy(payload)
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return cls, payload


@pytest.mark.parametrize('case', list(CASES))
@pytest.mark.parametrize('value', [True, '1.0', 10**400], ids=['bool','string','overflowing_integer'])
def test_invalid_numbers_rejected_by_python_and_json(case, value):
    cls, payload = modified(case, value)
    for constructor in (lambda:cls(**payload), lambda:from_dict(cls,payload)):
        with pytest.raises(ValueError):
            constructor()


@pytest.mark.parametrize('value', [True, False, 1., '1', None])
def test_step_count_never_accepts_bool_float_or_string(value):
    for constructor in (lambda:c.SolverConfig(max_steps=value),
                        lambda:from_dict(c.SolverConfig,{'max_steps':value})):
        with pytest.raises(ValueError):
            constructor()


def test_no_implicit_float_protocol_or_decimal_conversion():
    converted = []
    class Convertible:
        def __float__(self):
            converted.append(True)
            return 1.
    for value in (Decimal('1'), Convertible()):
        with pytest.raises(ValueError):
            c.Parameter('k',value)
        with pytest.raises(ValueError):
            c.SimulationResult('succeeded',(0.,),{'x':[value]})
    assert converted == []


@pytest.mark.parametrize('case', list(CASES))
def test_legal_int_and_float_paths_match_and_roundtrip(case):
    for value in (1,1.):
        cls, payload = modified(case,value)
        direct = cls(**payload)
        decoded = from_dict(cls,payload)
        assert direct == decoded
        encoded = to_dict(direct)
        assert from_dict(cls,json.loads(json.dumps(encoded,allow_nan=False))) == direct
        assert pickle.loads(pickle.dumps(direct)) == direct


def test_missing_values_and_generic_metadata_keep_their_types():
    for cls,payload in ((c.Parameter,{'id':'k','value':None}), (c.State,{'id':'x','initial':None})):
        assert cls(**payload) == from_dict(cls,payload)
    metadata = c.FrozenMap({'accepted':True,'label':'1.0','unknown':None})
    assert metadata['accepted'] is True and metadata['label']=='1.0' and metadata['unknown'] is None
    assert c.SolverConfig(max_steps=10**400).max_steps == 10**400


@pytest.mark.parametrize('value', [float('nan'),float('inf'),-float('inf')])
def test_abnormal_solver_columns_are_preserved_but_never_numeric_evidence(value):
    payload={'status':'succeeded','times':[0.,1.],'values':{'x':[1.,value]}}
    for result in (c.SimulationResult(**payload),from_dict(c.SimulationResult,payload)):
        actual = result.values['x'][1]
        assert actual == value or (math.isnan(actual) and math.isnan(value))
        report = validation.validate_numeric(result)
        assert any(check.code=='finite' and check.status=='fail' for check in report.checks)
        with pytest.raises(ValueError):
            to_dict(result)


def test_t01_persisted_inputs_still_decode_without_rewriting():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    frozen=json.loads((root/'benchmarks/inputs/contracts-compatibility/t01-negative-run.json').read_text())
    assert from_dict(c.ExperimentSpec,frozen['experiment']).times == (0.,2.,4.)
    assert from_dict(c.SolverConfig,frozen['solver']).max_steps == 100000
    assert from_dict(c.SimulationResult,frozen['simulation']).values['x'][-1] < -.5
