import copy
import json
from pathlib import Path

import numpy as np
import pytest

from biomodel_agent import model_contracts as c
from biomodel_agent import pde_system as pde


def _field(field_id, initial, diffusion=0.0, reaction='0', nonnegative=False):
    return {
        'id': field_id,
        'diffusion': diffusion,
        'initial': np.asarray(initial, dtype=float).tolist(),
        'reaction': reaction,
        'nonnegative': nonnegative,
    }


def _model(shape, lengths, fields, boundary='no_flux', parameters=None):
    return {
        'schema_version': 1,
        'model_type': 'pde',
        'profile': pde.PROFILE,
        'id': 'synthetic_pde_system',
        'units': {
            'time': 'arbitrary time unit',
            'space': 'arbitrary length unit',
            'state': 'arbitrary state unit',
        },
        'grid': {'shape': list(shape), 'lengths': list(lengths)},
        'boundary': boundary,
        'parameters': {} if parameters is None else dict(parameters),
        'fields': fields,
    }


def _settings(**changes):
    values = {
        'duration': 1.0,
        'samples': 5,
        'method': 'BDF',
        'rtol': 1e-9,
        'atol': 1e-11,
        'max_rhs_evaluations': 100000,
    }
    values.update(changes)
    return pde.config(values)


def _assert_all_checks_pass(model, settings, result):
    items = pde.checks(model, settings, result)
    failures = [item for item in items if item['status'] != 'pass']
    assert not failures, failures


def test_logistic_zero_diffusion_1d_matches_analytic():
    initial = np.array([0.20, 0.35, 0.50, 0.65, 0.80, 0.95])
    rate = 0.8
    capacity = 1.7
    model = _model((6,), (2.0,), [
        _field('u', initial, diffusion=0.0,
               reaction='rate*u*(1 - u/capacity)', nonnegative=True),
    ], parameters={'rate': rate, 'capacity': capacity})
    pde.validate(model)
    settings = _settings(duration=1.1, samples=7, method='BDF',
                         rtol=1e-10, atol=1e-12)
    result = pde.execute(model, settings)
    times = np.asarray(result['times'])
    exact = capacity / (1.0 + ((capacity - initial) / initial)
                        * np.exp(-rate * times)[:, None])
    np.testing.assert_allclose(np.asarray(result['values'])[:, 0], exact,
                               rtol=2e-7, atol=1e-9)
    _assert_all_checks_pass(model, settings, result)


def test_logistic_zero_diffusion_2d_matches_analytic():
    initial = np.linspace(0.18, 0.92, 12).reshape(3, 4)
    rate = 0.65
    capacity = 1.4
    model = _model((3, 4), (1.1, 0.8), [
        _field('u', initial, diffusion=0.0,
               reaction='rate*u*(1 - u/capacity)', nonnegative=True),
    ], parameters={'rate': rate, 'capacity': capacity})
    pde.validate(model)
    settings = _settings(duration=0.9, samples=5, method='BDF',
                         rtol=1e-10, atol=1e-12)
    result = pde.execute(model, settings)
    times = np.asarray(result['times'])
    exact = capacity / (1.0 + ((capacity - initial[None, :, :]) / initial[None, :, :])
                        * np.exp(-rate * times)[:, None, None])
    np.testing.assert_allclose(np.asarray(result['values'])[:, 0], exact,
                               rtol=2e-7, atol=1e-9)
    _assert_all_checks_pass(model, settings, result)


def test_local_a_to_b_conversion_conserves_total_and_matches_analytic():
    initial_a = np.linspace(0.30, 1.10, 12).reshape(3, 4)
    initial_b = np.linspace(0.05, 0.20, 12).reshape(3, 4)
    rate = 0.7
    model = _model((3, 4), (0.9, 0.7), [
        _field('A', initial_a, diffusion=0.0, reaction='-rate*A', nonnegative=True),
        _field('B', initial_b, diffusion=0.0, reaction='rate*A', nonnegative=True),
    ], parameters={'rate': rate})
    pde.validate(model)
    settings = _settings(duration=1.2, samples=8, method='BDF',
                         rtol=1e-10, atol=1e-12)
    result = pde.execute(model, settings)
    times = np.asarray(result['times'])
    values = np.asarray(result['values'])
    decay = np.exp(-rate * times)
    exact_a = initial_a[None, :, :] * decay[:, None, None]
    exact_b = (initial_b[None, :, :]
               + initial_a[None, :, :] * (1.0 - decay)[:, None, None])
    np.testing.assert_allclose(values[:, 0], exact_a, rtol=2e-7, atol=1e-9)
    np.testing.assert_allclose(values[:, 1], exact_b, rtol=2e-7, atol=1e-9)
    total = np.asarray(result['mass']).sum(axis=1)
    np.testing.assert_allclose(total, total[0], rtol=1e-10, atol=1e-10)
    _assert_all_checks_pass(model, settings, result)


def _fourier_problem(boundary, nx, ny):
    lx, ly = 0.7, 1.3
    x = (np.arange(nx) + 0.5) * lx / nx
    y = (np.arange(ny) + 0.5) * ly / ny
    if boundary == 'periodic':
        initial = np.cos(2.0 * np.pi * x / lx)[:, None] \
            * np.cos(2.0 * np.pi * y / ly)[None, :]
        kx, ky = 2.0 * np.pi / lx, 2.0 * np.pi / ly
    elif boundary == 'no_flux':
        initial = np.cos(np.pi * x / lx)[:, None] \
            * np.cos(np.pi * y / ly)[None, :]
        kx, ky = np.pi / lx, np.pi / ly
    else:
        initial = np.sin(np.pi * x / lx)[:, None] \
            * np.sin(np.pi * y / ly)[None, :]
        kx, ky = np.pi / lx, np.pi / ly
    model = _model((nx, ny), (lx, ly), [
        _field('u', initial, diffusion=0.1, reaction='0', nonnegative=False),
    ], boundary=boundary)
    return model, initial, kx * kx + ky * ky


@pytest.mark.parametrize('boundary', ['periodic', 'no_flux', 'dirichlet_zero'])
def test_2d_nonsquare_fourier_modes_show_second_order_refinement(boundary):
    duration = 0.05
    settings = _settings(duration=duration, samples=3, method='BDF',
                         rtol=1e-10, atol=1e-12)
    errors = {}
    results = {}
    for nx, ny in ((8, 12), (16, 24)):
        model, initial, k_squared = _fourier_problem(boundary, nx, ny)
        pde.validate(model)
        axes, spacing = pde.grid(model)
        assert axes['x'].shape == (nx,)
        assert axes['y'].shape == (ny,)
        dx, dy = 0.7 / nx, 1.3 / ny
        np.testing.assert_array_equal(spacing, [dx, dy])
        np.testing.assert_array_equal(axes['x'], (np.arange(nx) + 0.5) * dx)
        np.testing.assert_array_equal(axes['y'], (np.arange(ny) + 0.5) * dy)
        result = pde.execute(model, settings)
        final = np.asarray(result['values'])[-1, 0]
        exact = np.exp(-0.1 * k_squared * duration) * initial
        errors[(nx, ny)] = np.max(np.abs(final - exact))
        results[(nx, ny)] = result
        _assert_all_checks_pass(model, settings, result)
        codes = {item['code']: item['status']
                 for item in pde.checks(model, settings, result)}
        if boundary in ('periodic', 'no_flux'):
            assert codes['mass_conservation'] == 'pass'
        else:
            assert 'mass_conservation' not in codes
    ratio = errors[(8, 12)] / errors[(16, 24)]
    assert 3.2 < ratio < 5.0, (boundary, errors, ratio)
    if boundary == 'dirichlet_zero':
        # The face is zero; the nearest cell centre is not forced to zero.
        final = np.asarray(results[(8, 12)]['values'])[-1, 0]
        assert abs(final[0, 0]) > 1e-3


def test_time_and_cartesian_coordinate_source_is_analytic_and_signed():
    initial = np.linspace(-0.6, 0.8, 20).reshape(4, 5)
    model = _model((4, 5), (1.1, 0.6), [
        _field('potential', initial, diffusion=0.0,
               reaction='time + x*y', nonnegative=False),
    ], boundary='no_flux')
    pde.validate(model)
    settings = _settings(duration=0.9, samples=6, method='RK45',
                         rtol=1e-10, atol=1e-12)
    result = pde.execute(model, settings)
    times = np.asarray(result['times'])
    x = (np.arange(4) + 0.5) * 1.1 / 4
    y = (np.arange(5) + 0.5) * 0.6 / 5
    exact = (initial[None, :, :]
             + 0.5 * times[:, None, None] ** 2
             + times[:, None, None] * (x[:, None] * y[None, :])[None, :, :])
    np.testing.assert_allclose(np.asarray(result['values'])[:, 0], exact,
                               rtol=2e-8, atol=1e-10)
    _assert_all_checks_pass(model, settings, result)


def test_config_defaults_and_strict_bounds():
    defaults = pde.config()
    assert defaults == {
        'timeout_s': 60.0,
        'duration': 1.0,
        'samples': 21,
        'method': 'BDF',
        'rtol': 1e-6,
        'atol': 1e-9,
        'max_rhs_evaluations': 10000,
    }
    assert pde.config({'timeout_s': 0.1})['timeout_s'] == 0.1
    assert pde.config({'timeout_s': 600.0})['timeout_s'] == 600.0

    invalid = [
        {'timeout_s': 0.09},
        {'timeout_s': 600.01},
        {'duration': 0.0},
        {'duration': -1.0},
        {'duration': '1.0'},
        {'samples': 2.0},
        {'samples': True},
        {'samples': 1},
        {'samples': 1002},
        {'method': 'LSODA'},
        {'method': 'bdf'},
        {'rtol': 0.0},
        {'rtol': 0.1},
        {'atol': 1e-13},
        {'atol': 1e-2},
        {'max_rhs_evaluations': 0},
        {'max_rhs_evaluations': 100001},
        {'max_rhs_evaluations': False},
        {'unknown': 1},
    ]
    for value in invalid:
        with pytest.raises(ValueError):
            pde.config(value)


def test_validation_rejects_unknown_reserved_conflicting_and_bad_values():
    base = _model((5,), (1.0,), [
        _field('u', np.ones(5), diffusion=0.0, reaction='0', nonnegative=True),
    ])

    bad = copy.deepcopy(base)
    bad['unexpected'] = 1
    with pytest.raises(ValueError):
        pde.validate(bad)

    bad = copy.deepcopy(base)
    bad['profile'] = 'another_profile'
    with pytest.raises(ValueError):
        pde.validate(bad)

    bad = copy.deepcopy(base)
    bad['fields'][0]['extra'] = 1
    with pytest.raises(ValueError):
        pde.validate(bad)

    bad = copy.deepcopy(base)
    bad['fields'][0]['id'] = 'x'
    with pytest.raises(ValueError):
        pde.validate(bad)

    bad = copy.deepcopy(base)
    bad['parameters'] = {'time': 1.0}
    with pytest.raises(ValueError):
        pde.validate(bad)

    bad = copy.deepcopy(base)
    bad['parameters'] = {'u': 1.0}
    with pytest.raises(ValueError):
        pde.validate(bad)

    bad = copy.deepcopy(base)
    bad['fields'].append(copy.deepcopy(bad['fields'][0]))
    with pytest.raises(ValueError):
        pde.validate(bad)

    bad = copy.deepcopy(base)
    bad['fields'][0]['diffusion'] = True
    with pytest.raises(ValueError):
        pde.validate(bad)

    bad = copy.deepcopy(base)
    bad['parameters'] = {'rate': float('nan')}
    with pytest.raises(ValueError):
        pde.validate(bad)

    bad = copy.deepcopy(base)
    bad['fields'][0]['initial'] = [1.0, 1.0, 1.0, 1.0]
    with pytest.raises(ValueError):
        pde.validate(bad)

    bad = copy.deepcopy(base)
    bad['fields'][0]['initial'][2] = -1.0
    with pytest.raises(ValueError):
        pde.validate(bad)

    bad = copy.deepcopy(base)
    bad['fields'][0]['reaction'] = 'y'
    with pytest.raises(ValueError):
        pde.validate(bad)

    bad = copy.deepcopy(base)
    bad['fields'][0]['reaction'] = '__import__("os")'
    with pytest.raises(ValueError):
        pde.validate(bad)

    bad = copy.deepcopy(base)
    bad['grid'] = {'shape': [5, 5, 5], 'lengths': [1.0, 1.0, 1.0]}
    with pytest.raises(ValueError):
        pde.validate(bad)

    bad = copy.deepcopy(base)
    bad['grid'] = {'shape': [2], 'lengths': [1.0]}
    with pytest.raises(ValueError):
        pde.validate(bad)

    bad = copy.deepcopy(base)
    bad['grid'] = {'shape': [128, 33], 'lengths': [1.0, 1.0]}
    with pytest.raises(ValueError):
        pde.validate(bad)


def test_output_budget_is_enforced():
    initial = np.zeros((32, 128))
    model = _model((32, 128), (2.0, 3.0), [
        _field('u', initial, diffusion=0.0, reaction='0', nonnegative=True),
    ])
    pde.validate(model)
    with pytest.raises(ValueError, match='output scalar budget'):
        pde.compatible(model, pde.config({'samples': 1001}))


def test_rhs_work_budget_raises_work_limit():
    model = _model((5,), (1.0,), [
        _field('u', np.full(5, 0.4), diffusion=0.0,
               reaction='0.6*u', nonnegative=True),
    ])
    pde.validate(model)
    settings = _settings(duration=1.0, samples=4, method='RK45',
                         max_rhs_evaluations=1)
    with pytest.raises(c.WorkLimit):
        pde.execute(model, settings)


def test_failed_and_incomplete_solver_outputs_are_rejected(monkeypatch):
    model = _model((5,), (1.0,), [
        _field('u', np.full(5, 0.4), diffusion=0.0,
               reaction='0.6*u', nonnegative=True),
    ])
    pde.validate(model)
    settings = _settings(duration=1.0, samples=4, method='BDF')

    class Failed:
        success = False
        status = -1
        message = 'forced failure'
        t = np.linspace(0.0, 1.0, 4)
        y = np.full((5, 4), 0.4)
        nfev = 1
        njev = 0
        nlu = 0

    monkeypatch.setattr(pde, 'solve_ivp', lambda **kwargs: Failed())
    with pytest.raises(RuntimeError, match='forced failure'):
        pde.execute(model, settings)

    class Incomplete:
        success = True
        status = 0
        message = 'forced incomplete'
        t = np.array([0.0])
        y = np.full((5, 1), 0.4)
        nfev = 1
        njev = 0
        nlu = 0

    monkeypatch.setattr(pde, 'solve_ivp', lambda **kwargs: Incomplete())
    with pytest.raises(RuntimeError, match='incomplete'):
        pde.execute(model, settings)


def test_runtime_expression_domain_error_is_an_explicit_failure():
    model = _model((4,), (1.0,), [
        _field('u', np.ones(4), diffusion=0.0,
               reaction='sqrt(u - 2)', nonnegative=False),
    ])
    pde.validate(model)
    settings = _settings(duration=0.1, samples=3, method='BDF')
    with pytest.raises((ValueError, RuntimeError)):
        pde.execute(model, settings)


def test_checks_detect_tampering_and_nonnegative_violation():
    model = _model((5,), (1.0,), [
        _field('u', np.full(5, 0.5), diffusion=0.0,
               reaction='0.2*u', nonnegative=True),
    ])
    pde.validate(model)
    settings = _settings(duration=0.2, samples=4, method='BDF',
                         rtol=1e-9, atol=1e-11)
    result = pde.execute(model, settings)
    _assert_all_checks_pass(model, settings, result)

    tampered_values = copy.deepcopy(result)
    tampered_values['values'][-1][0][0] = -5.0
    codes = {item['code']: item['status']
             for item in pde.checks(model, settings, tampered_values)}
    assert codes['nonnegative'] == 'fail'
    assert codes['spatial_integral'] == 'fail'

    tampered_times = copy.deepcopy(result)
    tampered_times['times'][1] += 0.01
    assert any(item['status'] == 'fail'
               for item in pde.checks(model, settings, tampered_times))

    tampered_mass = copy.deepcopy(result)
    tampered_mass['mass'][-1][0] += 0.25
    assert any(item['status'] == 'fail'
               for item in pde.checks(model, settings, tampered_mass))

    tampered_solver = copy.deepcopy(result)
    tampered_solver['solver']['success'] = False
    assert any(item['status'] == 'fail'
               for item in pde.checks(model, settings, tampered_solver))

    tampered_work = copy.deepcopy(result)
    tampered_work['solver']['rhs_evaluations'] = \
        settings['max_rhs_evaluations'] + 1
    codes = {item['code']: item['status']
             for item in pde.checks(model, settings, tampered_work)}
    assert codes['work_budget'] == 'fail'


def test_gray_scott_example_loads_validates_and_runs():
    path = Path(__file__).resolve().parents[1] / 'examples' / 'model-types' / 'pde-system.json'
    example = json.loads(path.read_text(encoding='utf-8'))
    pde.validate(example)
    assert example['profile'] == pde.PROFILE
    assert example['boundary'] == 'periodic'
    assert len(example['fields']) == 2
    settings = _settings(duration=0.01, samples=3, method='RK45',
                         rtol=1e-6, atol=1e-9)
    result = pde.execute(example, settings)
    np.testing.assert_allclose(result['axes']['x'], (np.arange(16) + 0.5) * 2.5 / 16)
    np.testing.assert_allclose(result['axes']['y'], (np.arange(16) + 0.5) * 2.5 / 16)
    assert np.asarray(result['values']).shape == (3, 2, 16, 16)
    _assert_all_checks_pass(example, settings, result)


def test_reviewed_jacobian_pattern_keeps_self_dependencies_at_unit_spacing():
    model = _model((3,), (3.0,), [
        _field('u', np.ones(3), diffusion=1.0, reaction='-100*u'),
    ])
    info = pde._validated(model)
    spatial = pde._spatial_operator(info)
    pattern = pde._jacobian_sparsity(info, spatial, 3)
    assert np.all(pattern.diagonal() != 0)


@pytest.mark.parametrize('target', ['values', 'mass', 'times'])
def test_reviewed_mixed_boolean_results_fail_numeric_checks(target):
    model = _model((3,), (1.0,), [
        _field('u', np.ones(3), nonnegative=True),
    ])
    settings = _settings(duration=1.0, samples=2, method='RK45')
    result = pde.execute(model, settings)
    _assert_all_checks_pass(model, settings, result)
    if target == 'values':
        result['values'][-1][0][0] = True
    elif target == 'mass':
        result['mass'][-1][0] = True
    else:
        result['times'][-1] = True
    assert any(item['status'] == 'fail'
               for item in pde.checks(model, settings, result))


@pytest.mark.parametrize('length', [1e-200, 1e200])
def test_reviewed_unrepresentable_cell_volume_is_rejected(length):
    model = _model((3, 3), (length, length), [
        _field('u', np.ones((3, 3)), nonnegative=True),
    ])
    with pytest.raises(ValueError, match='grid.*volume'):
        pde.validate(model)
