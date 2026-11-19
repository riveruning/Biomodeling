"""Fixed four-case modeling-pipeline engineering pilot: offline freeze, explicit live.

Engineering trial of the real model service path (research_projects /
research_supervisor over fresh ArtifactStores and real ChatProvider calls).
It is pre-acceptance work for the 100-paper MM/Expt benchmark: it never counts
toward paper A/B completion, reports scientific_mm_a_runs=0, expt_b_runs=0 and
scientific_score=null, and every case is an explicitly synthetic control whose
scientific qualification is never self-certified.

Preparation (default) is fully offline: it generates each case's source copy,
prepared model, frozen settings and an independent continuous analytic
reference from explicit mathematical definitions, then freezes raw-file hashes,
implementation fingerprint, runner hash, provider model/effort, budgets and
expected behavior into manifest.json bound by manifest.sha256.

Live mode needs --run-live plus BIOMODEL_API_KEY in the environment (USTC
endpoint, fixed base_url). The key is read only from the environment at request
time by the existing provider adapter; this runner never reads or writes any
credential file and never logs the key. Each case gets a fresh store and a
fresh provider object; only the frozen manifest is shared. Real calls never use
scripted or fabricated responses; provider failures keep their durable journals
and unknown effects are never retried. An existing output directory is never
overwritten or auto-resumed.

The frozen provider contract fixes json_mode=True: the factory validates the
USTC endpoint, the two allowed models, max reasoning effort and the key
environment name before constructing the real ChatProvider. After each case the
runner exports operation-events.jsonl, a derived export of the SDK's own
persisted operation reservations and return receipts; it is never written
before the calls it describes. Exceptions are logged as error_type plus a
message redacted in memory against the run-environment key value, so no
credential material reaches disk.
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from biomodel_agent import evidence, pde_system, recovery, reporting  # noqa: E402
from biomodel_agent import research_projects as projects  # noqa: E402
from biomodel_agent import research_supervisor as supervisor  # noqa: E402
from biomodel_agent import typed_models, typed_references, validation  # noqa: E402
from biomodel_agent.domain.contracts import SimulationResult  # noqa: E402
from biomodel_agent.domain.serialization import from_dict  # noqa: E402
from biomodel_agent.llm import ChatProvider  # noqa: E402
from biomodel_agent.storage import ArtifactStore, _atomic_write  # noqa: E402

RUNNER_PATH = Path(__file__).resolve()
TASK_ID = 'dsh-modeling-pipeline-pilot-v3-20261112'
PILOT_ID = 'modeling_pipeline_pilot_v3'
BASE_URL = 'https://api.llm.ustc.edu.cn/v1'
KEY_ENV = 'BIOMODEL_API_KEY'
ALLOWED_MODELS = ('deepseek-flash-2', 'glm-5.3-flash')
DEFAULT_MODEL = 'glm-5.3-flash'
REASONING_EFFORT = 'max'
CASE_IDS = ('production_loss', 'missing_rate', 'pde_fourier_decay', 'pde_logistic_2d')
UNITS = {'time': 'synthetic second (declared, uncalibrated)',
         'space': 'synthetic length unit (declared, uncalibrated)',
         'state': 'dimensionless synthetic concentration'}
DEFAULT_BUDGETS = dict(planner_max_calls=32, max_iterations=2, max_native_calls=12,
                       native_calls_per_iteration=6, max_literature_requests=0,
                       context_max_chars=120000)
DEFAULT_PROVIDER_TIMEOUT = 180.0
DEFAULT_MAX_TOKENS = 16000
BUDGET_BOUNDS = {'planner_max_calls': (1, 50), 'max_iterations': (1, 8),
                 'max_native_calls': (1, 200), 'native_calls_per_iteration': (1, 20),
                 'max_literature_requests': (0, 20), 'context_max_chars': (16000, 120000)}
PURPOSE = ('Engineering pipeline trial of the real model service path over four fixed '
           'synthetic controls. Pre-acceptance work for the 100-paper MM/Expt benchmark; '
           'it does not count toward paper A/B completion and the scientific score stays null.')
BUDGET_POLICY = ('Budgets are frozen into this manifest before any provider call; every value '
                 'comes from an explicit CLI flag with the documented default. The runner never '
                 'shrinks a budget silently, and live mode refuses flag values that differ from '
                 'the frozen manifest.')
SCORING_NOTE = ('Scores are recomputed by this runner from confirmed actual run records and the '
                'frozen references only. Agent self-assessments and concluded states are recorded '
                'but never trusted. PDE cases check the actual model/profile/coefficients/shape/'
                'settings and the bound frozen reference, then report per-field scaled RMSE and '
                'maximum absolute error; a single missing or failing required field fails the '
                'case and is never averaged away. ODE cases check the exact frozen time grid, '
                'the declared time unit, the exact output columns and the fixed scales. Times, '
                'coordinates and values are validated as strict finite numbers (booleans are '
                'never coerced into 0/1), and every reported metric stays finite JSON. The '
                'runner also keeps the project SDK gate statuses (confirmed execution, native '
                'numeric checks, reference comparison and, for the complete ODE, source '
                'binding); a case passes only when its required gates and the independent '
                'recomputation both pass, and unknown or unreadable records keep an explicit '
                'reason. Numeric coverage (frozen coordinates/units/fields bound correctly and '
                'all finite errors actually computed) is reported separately from numeric '
                'attainment (errors within every frozen threshold); a scorable failure still '
                'counts as coverage. A complete case without a confirmed computation is '
                'not_run/failed; the missing-rate control passes only with exactly one '
                'precise pending question that asks for the missing rate value/units and zero '
                'scientific execution while every iteration fact is known.')
CREDENTIAL_NOTE = ('The API key is read only from the ' + KEY_ENV + ' environment variable at '
                   'request time by the existing provider adapter. No credential file is read, '
                   'no key is written anywhere, and no key material enters this manifest.')
OPERATION_EVENT_SEMANTICS = (
    'operation-events.jsonl is a derived export of the SDK-persisted '
    'research_project_operation reservations and research_project_return receipts read through '
    'projects.history after the case finished; the runner never writes it before or during the '
    'calls it describes. sequence is the original project operation sequence and attempt_ref the '
    'stored reservation ref. index_registered_at is the UTC timestamp the ArtifactStore SQLite '
    'index recorded when that artifact was first registered; it is index insertion metadata, not '
    'the operation start time, and it is None when no index row exists. elapsed_s is the '
    'SDK-measured perform/callback duration; state and error_stage come from the bound return '
    'receipt, and prompt_ref/response_ref point at the stored prompt and planner response.')


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def check_model(model):
    if model not in ALLOWED_MODELS:
        raise ValueError('model must be one of ' + ', '.join(ALLOWED_MODELS) + '; got ' + repr(model))
    return model


def _check_provider_limits(provider):
    """Frozen transport limits shared by budget checks and the provider factory."""
    timeout = provider.get('timeout_s')
    if isinstance(timeout, bool) or type(timeout) not in (int, float) or not math.isfinite(timeout) \
            or not 30 <= timeout <= 600:
        raise ValueError('provider timeout_s must be a number in [30, 600]')
    max_tokens = provider.get('max_tokens')
    if type(max_tokens) is not int or not 1000 <= max_tokens <= 64000:
        raise ValueError('provider max_tokens must be an integer in [1000, 64000]')


def check_budgets(budgets, provider):
    for key, (low, high) in BUDGET_BOUNDS.items():
        value = budgets.get(key)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'{key} must be an integer in [{low}, {high}]; got {value!r}')
    if budgets['native_calls_per_iteration'] > budgets['max_native_calls']:
        raise ValueError('native_calls_per_iteration must not exceed max_native_calls')
    _check_provider_limits(provider)


_FROZEN_FLAGS = {'model': ('provider', 'model'), 'provider_timeout_s': ('provider', 'timeout_s'),
                 'max_tokens': ('provider', 'max_tokens'),
                 'planner_max_calls': ('budgets', 'planner_max_calls'),
                 'max_iterations': ('budgets', 'max_iterations'),
                 'max_native_calls': ('budgets', 'max_native_calls'),
                 'native_calls_per_iteration': ('budgets', 'native_calls_per_iteration'),
                 'max_literature_requests': ('budgets', 'max_literature_requests'),
                 'context_max_chars': ('budgets', 'context_max_chars')}


def check_frozen_args(args, manifest):
    """Live mode runs exactly the frozen manifest values; flag drift is refused."""
    mismatch = []
    for flag, (section, key) in _FROZEN_FLAGS.items():
        if getattr(args, flag, None) != manifest[section][key]:
            mismatch.append(flag.replace('_', '-'))
    if mismatch:
        raise ValueError('frozen manifest differs for CLI value(s): ' + ', '.join(mismatch)
                         + '; prepare a new output directory instead of overriding a frozen run')


# --------------------------------------------------------------------------
# Explicit mathematical case definitions (generated offline, before any call)
# --------------------------------------------------------------------------

def _cell_axis(size, length):
    """Exact cell-centre axis, computed with the same operations as pde_system."""
    delta = length / size
    return delta, (np.arange(size, dtype=float) + 0.5) * delta


def production_loss_definition():
    times = [i * 0.5 for i in range(21)]
    reference = dict(times=times, values={'p': [2.0 - math.exp(-0.3 * t) for t in times]},
                     time_unit='second', backend='independent_analytic_control_from_explicit_source',
                     status='succeeded')
    scenario = ('Synthetic engineering-trial control (explicitly not a paper and not '
                'experimental evidence). Reproduce the complete one-state ODE specification in '
                'the supplied source exactly: it declares state p (a normalized variable), '
                'initial p = 1, parameters b and k with explicit values, the equation '
                'dp/dt = b - k*p, and a second-based clock. Freeze the experiment to the 21 '
                'sample times 0, 0.5, 1.0, ..., 10.0 seconds with time_unit second and no '
                'overrides. Use a strict_reproduction frame and the deterministic source route '
                '(bind_source then open_source_iteration) or an explicit reproduction '
                'decision_plan; name the state exactly p and add no observations beyond the '
                'declared state. Use solver rtol 1e-9 and atol 1e-12 or tighter. Pass the '
                'supplied frozen series reference_ref; its scales and threshold are already '
                'fixed and must not be changed or re-derived. Report numbers only from executed '
                'runs. This synthetic control does not certify scientific capability, and '
                'independent review has not been performed.')
    return dict(id='production_loss', kind='ode', mode='reproduction', material='synthetic_control',
                identity='synthetic_control:production_loss; complete explicit synthetic ODE text; '
                         'not a paper and not experimental evidence',
                expected_behavior='numeric_and_reference', numerically_evaluable=True,
                source_files=[dict(name='production-loss.txt',
                                   origin='benchmarks/inputs/native-plan-pilot-20261029/production-loss.txt')],
                model_files=[], model=None, settings=None,
                reference=dict(type='time_series',
                               scale_meaning={'p': 'fixed initial amplitude p(0)=1'},
                               register=dict(reference=reference, scales={'p': 1.0}, threshold=0.001,
                                             note='Independent continuous analytic solution of '
                                                  'dp/dt = 0.6 - 0.3*p with p(0)=1, frozen before any '
                                                  'provider call; operator-prepared synthetic control, '
                                                  'not independent domain review.')),
                experiment_grid=dict(times=times, time_unit='second', samples=21),
                threshold_budget=dict(
                    spatial='none; a lumped one-state ODE has no spatial discretization',
                    time='bounded solver profile (scenario instructs rtol<=1e-9, atol<=1e-12); the '
                         'smooth exponential over T=10 s gives an expected scaled error <= 1e-7',
                    threshold=0.001,
                    margin='>=1e3x the expected time-integration bound; fixed before any provider call',
                    fixed_before_any_call=True),
                scenario=scenario,
                notes='Complete case: 21 samples over 0..10 seconds. The product injects the frozen '
                      'reference, scales and threshold from the registered reference_ref.')


def missing_rate_definition():
    scenario = ('Synthetic engineering-trial control (explicitly not a paper and not '
                'experimental evidence). The supplied source declares state z (a normalized '
                'variable), initial z = 3, the equation dz/dt = -k*z, a second-based clock, and '
                'states explicitly that parameter k is not reported and no numerical value for k '
                'is available. Do not substitute, estimate, assume, bound or compute any value '
                'for k, and do not run any simulation or numerical study. Keep a '
                'strict_reproduction frame. When the missing rate blocks the reproduction, ask '
                'the researcher exactly one precise alignment question that names the missing '
                'quantity k and what is needed, then stop and leave the question pending. '
                'Preserve the problem exactly as written: executing anything numeric or '
                'inventing any value is a failure of this control.')
    return dict(id='missing_rate', kind='ode', mode='reproduction', material='synthetic_control',
                identity='synthetic_control:missing_rate; explicit synthetic ODE text with an '
                         'unresolved rate; not a paper and not experimental evidence',
                expected_behavior='clarification_without_computation', numerically_evaluable=False,
                source_files=[dict(name='missing-rate.txt',
                                   origin='benchmarks/inputs/native-plan-pilot-20261029/missing-rate.txt')],
                model_files=[], model=None, settings=None, reference=None,
                experiment_grid=None, threshold_budget=None,
                scenario=scenario,
                notes='No analytic reference, model or computed facts are prepared for this case; '
                      'the source keeps the missing rate explicitly unresolved. Behavior passes '
                      'only with a precise pending question and zero scientific execution.')


def _fourier_threshold_budget(size, diffusion, decay, duration, threshold=0.002):
    """A-priori spatial error budget computed from the frozen coefficients.

    The discrete periodic cell-centred eigenvalue of the sin(x) mode at N cells
    over L=2*pi is lambda_d=(N*sin(pi/N)/pi)^2 < 1. For a field with diffusion D
    and first-order decay k, the scaled deviation between the discrete and the
    continuous decay is bounded (mean-value theorem on exp(-D*lambda*t)) by

        D*(1-lambda_d) * max_{t in [0,T]} t*exp(-min(D*lambda_d+k, D+k)*t),

    and t*exp(-rate*t) attains its maximum on [0, T] at t=min(T, 1/rate). The
    numbers below are therefore a planned error budget fixed before any
    provider call, not a measured error.
    """
    number = size
    lambda_d = (number * math.sin(math.pi / number) / math.pi) ** 2
    relative_error = 1.0 - lambda_d
    bounds = {}
    rates = {}
    for field_id in sorted(decay):
        d, k = diffusion[field_id], decay[field_id]
        rate = min(d * lambda_d + k, d + k)
        t_star = min(duration, 1.0 / rate)
        rates[field_id] = rate
        bounds[field_id] = d * relative_error * t_star * math.exp(-rate * t_star)
    worst = max(bounds.values())
    spatial = ('Second-order cell-centred finite-volume truncation: the discrete periodic '
               f'eigenvalue of sin(x) at N={number}, L=2*pi is '
               f'lambda_d=(N*sin(pi/N)/pi)^2={lambda_d:.6f} versus the continuous k^2=1, a '
               f'relative eigenvalue error {relative_error:.3e}. Pre-planned analytic per-field '
               'bound D*(1-lambda_d)*max_(t in [0,T]) t*exp(-min(D*lambda_d+k, D+k)*t), attained '
               f'at t=min(T, 1/rate): u {bounds["u"]:.3e}, w {bounds["w"]:.3e} scaled error. '
               'This is an a-priori planned error budget computed from the frozen coefficients '
               'before any provider call, not a measured error.')
    return dict(
        spatial=spatial,
        spatial_bounds=dict(lambda_d=lambda_d, relative_eigenvalue_error=relative_error,
                            per_field_scaled_bound=dict(bounds),
                            per_field_decay_rate_used=dict(rates),
                            formula='D*(1-lambda_d)*max_{t in [0,T]} t*exp(-min(D*lambda_d+k, D+k)*t), '
                                    'maximized at t=min(T, 1/rate); a-priori planned budget'),
        time='BDF with frozen rtol 1e-10 and atol 1e-12 over T=2 s on a smooth '
             'single-mode exponential decay; expected scaled contribution <= 1e-8.',
        threshold=threshold,
        margin=f'>={math.floor(threshold / worst)}x the a-priori per-field spatial bound and '
               '>=1e5x the time bound; fixed before any provider call',
        fixed_before_any_call=True)


def fourier_definition():
    size, length = 96, 2 * math.pi
    _, axis = _cell_axis(size, length)
    xs = [float(x) for x in axis]
    amplitude = {'u': 1.6, 'w': 0.8}
    diffusion = {'u': 0.35, 'w': 0.12}
    decay = {'u': 0.4, 'w': 0.05}
    model = dict(schema_version=1, model_type='pde', profile=pde_system.PROFILE,
                 id='pde_fourier_decay_control', units=dict(UNITS),
                 grid=dict(shape=[size], lengths=[length]), boundary='periodic',
                 parameters={'k_u': decay['u'], 'k_w': decay['w']},
                 fields=[dict(id='u', diffusion=diffusion['u'],
                              initial=[amplitude['u'] * math.sin(x) for x in xs],
                              reaction='-k_u*u', nonnegative=False),
                         dict(id='w', diffusion=diffusion['w'],
                              initial=[amplitude['w'] * math.sin(x) for x in xs],
                              reaction='-k_w*w', nonnegative=False)],
                 note='Synthetic engineering control: single Fourier eigenmode sin(x) on a '
                      'periodic cell-centred grid; not a paper and not experimental evidence.')
    settings = dict(duration=2.0, samples=21, method='BDF', rtol=1e-10, atol=1e-12,
                    timeout_s=120.0, max_rhs_evaluations=50000)
    times = np.linspace(0.0, settings['duration'], settings['samples']).tolist()
    # Continuous analytic reference: eigenmode sin(x) of the continuous Laplacian
    # (k^2 = 1) with per-field total decay rate D + k_decay. The discrete
    # finite-volume eigenvalue differs at second order in dx; that gap is the
    # documented spatial term of the threshold budget.
    rates = {'u': diffusion['u'] + decay['u'], 'w': diffusion['w'] + decay['w']}
    values = [[[amplitude[f] * math.exp(-rates[f] * t) * math.sin(x) for x in xs]
               for f in ('u', 'w')] for t in times]
    spec = dict(schema_version=1, model_type='pde', profile=typed_references.PDE_SYSTEM_PROFILE,
                units=dict(UNITS), times=times, axes={'x': xs}, field_ids=['u', 'w'],
                values=values, scales=[amplitude['u'], amplitude['w']], threshold=0.002)
    scenario = ('Synthetic engineering-trial control (explicitly not a paper and not '
                'experimental evidence). Run the prepared two-field reaction-diffusion model '
                'supplied for this project as an explicit hypothetical application on its frozen '
                'one-dimensional periodic grid. Use direct deterministic execution of the '
                'prepared model: open one iteration with config.model_type "pde", config.model_ref '
                'set to the supplied prepared model, execution.settings exactly {duration: 2.0, '
                'samples: 21, method: "BDF", rtol: 1e-10, atol: 1e-12, timeout_s: 120.0, '
                'max_rhs_evaluations: 50000}, and the supplied frozen typed_observations '
                'reference_ref; do not set native_agent and do not create a new draft. Do not '
                'modify the model, grid, boundary, coefficients, units or settings. The frozen '
                'reference compares every field separately against its fixed scale on the exact '
                'frozen grid. Report per-field outcomes from the actual saved run. Numerical '
                'agreement is not scientific certification; independent review has not been '
                'performed.')
    return dict(id='pde_fourier_decay', kind='pde', mode='application', material='synthetic_control',
                identity='synthetic_control:pde_fourier_decay; generated prepared PDE model; '
                         'not a paper and not experimental evidence',
                expected_behavior='prepared_model_numeric_per_field', numerically_evaluable=True,
                source_files=[], model_files=[dict(name='pde_fourier_decay_model.json')],
                model=model, settings=settings,
                reference=dict(type='typed_observations', profile=typed_references.PDE_SYSTEM_PROFILE,
                               scale_meaning={'u': 'initial amplitude 1.6', 'w': 'initial amplitude 0.8'},
                               register=dict(reference=spec, model_type='pde',
                                             note='Independent continuous analytic reference: exact '
                                                  'Fourier eigenmode decay evaluated on the frozen '
                                                  'cell-centred grid; frozen before any provider call; '
                                                  'operator-prepared synthetic control, not independent '
                                                  'domain review.')),
                experiment_grid=dict(times=times, samples=21, duration=2.0),
                threshold_budget=_fourier_threshold_budget(size, diffusion, decay, settings['duration']),
                scenario=scenario,
                notes='Fields u and w share the eigenmode sin(x) but differ in diffusion and decay; '
                      'the continuous reference intentionally keeps k^2=1 so the run must absorb '
                      'the second-order spatial truncation error inside the frozen threshold.')


def logistic_definition():
    nx, ny, lx, ly = 14, 9, 2.8, 1.8
    _, axis_x = _cell_axis(nx, lx)
    _, axis_y = _cell_axis(ny, ly)
    xs = [float(x) for x in axis_x]
    ys = [float(y) for y in axis_y]
    u0 = [[0.4 + 0.15 * math.sin(1.3 * x) * math.cos(0.9 * y) for y in ys] for x in xs]
    w0 = [[0.25 + 0.10 * math.cos(0.7 * x + 0.5 * y) for y in ys] for x in xs]
    model = dict(schema_version=1, model_type='pde', profile=pde_system.PROFILE,
                 id='pde_logistic_2d_control', units=dict(UNITS),
                 grid=dict(shape=[nx, ny], lengths=[lx, ly]), boundary='no_flux',
                 parameters={'r_u': 1.1, 'c_u': 2.4, 'r_w': 0.7, 'c_w': 1.6},
                 fields=[dict(id='u', diffusion=0.0, initial=u0,
                              reaction='r_u*u*(1-u/c_u)', nonnegative=True),
                         dict(id='w', diffusion=0.0, initial=w0,
                              reaction='r_w*w*(1-w/c_w)', nonnegative=True)],
                 note='Synthetic engineering control: zero-diffusion nonlinear logistic growth on '
                      'a non-square 2D no_flux grid; not a paper and not experimental evidence.')
    settings = dict(duration=3.0, samples=21, method='BDF', rtol=1e-10, atol=1e-12,
                    timeout_s=120.0, max_rhs_evaluations=50000)
    times = np.linspace(0.0, settings['duration'], settings['samples']).tolist()

    def logistic(u_init, capacity, rate, t):
        return capacity * u_init / (u_init + (capacity - u_init) * math.exp(-rate * t))

    values = []
    for t in times:
        if t == 0.0:
            values.append([deepcopy(u0), deepcopy(w0)])
        else:
            values.append([[[logistic(u0[i][j], 2.4, 1.1, t) for j in range(ny)] for i in range(nx)],
                           [[logistic(w0[i][j], 1.6, 0.7, t) for j in range(ny)] for i in range(nx)]])
    spec = dict(schema_version=1, model_type='pde', profile=typed_references.PDE_SYSTEM_PROFILE,
                units=dict(UNITS), times=times, axes={'x': xs, 'y': ys}, field_ids=['u', 'w'],
                values=values, scales=[2.4, 1.6], threshold=1e-4)
    scenario = ('Synthetic engineering-trial control (explicitly not a paper and not '
                'experimental evidence). Run the prepared two-field zero-diffusion nonlinear '
                'logistic model supplied for this project as an explicit hypothetical application '
                'on its frozen two-dimensional non-square no_flux grid. Use direct deterministic '
                'execution of the prepared model: open one iteration with config.model_type "pde", '
                'config.model_ref set to the supplied prepared model, execution.settings exactly '
                '{duration: 3.0, samples: 21, method: "BDF", rtol: 1e-10, atol: 1e-12, timeout_s: '
                '120.0, max_rhs_evaluations: 50000}, and the supplied frozen typed_observations '
                'reference_ref; do not set native_agent and do not create a new draft. Do not '
                'modify the model, grid, boundary, coefficients, units or settings. The frozen '
                'reference compares every field separately against its fixed capacity scale on '
                'the exact frozen grid. Report per-field outcomes from the actual saved run. '
                'Numerical agreement is not scientific certification; independent review has not '
                'been performed.')
    return dict(id='pde_logistic_2d', kind='pde', mode='application', material='synthetic_control',
                identity='synthetic_control:pde_logistic_2d; generated prepared PDE model; '
                         'not a paper and not experimental evidence',
                expected_behavior='prepared_model_numeric_per_field', numerically_evaluable=True,
                source_files=[], model_files=[dict(name='pde_logistic_2d_model.json')],
                model=model, settings=settings,
                reference=dict(type='typed_observations', profile=typed_references.PDE_SYSTEM_PROFILE,
                               scale_meaning={'u': 'logistic capacity c_u=2.4', 'w': 'logistic capacity c_w=1.6'},
                               register=dict(reference=spec, model_type='pde',
                                             note='Independent continuous analytic reference: exact '
                                                  'per-cell logistic solution evaluated on the frozen '
                                                  '2D grid; frozen before any provider call; '
                                                  'operator-prepared synthetic control, not independent '
                                                  'domain review.')),
                experiment_grid=dict(times=times, samples=21, duration=3.0),
                threshold_budget=dict(
                    spatial='Both diffusion coefficients are exactly 0.0, so the finite-volume '
                            'spatial operator is the zero operator with no truncation term; the '
                            'second-order spatial error contribution is exactly zero. The no_flux '
                            'boundary is declared and validated but dynamically inactive for zero '
                            'diffusion.',
                    time='BDF with frozen rtol 1e-10 and atol 1e-12 over T=3 s on a smooth bounded '
                         'logistic approach to capacity; expected scaled contribution <= 1e-8.',
                    threshold=1e-4,
                    margin='>=1e4x the expected time-integration bound; fixed before any provider call',
                    fixed_before_any_call=True),
                scenario=scenario,
                notes='Non-square grid (14x9 cells over 2.8x1.8) with no_flux boundary and zero '
                      'diffusion in both fields; the analytic per-cell logistic solution is exact.')


def definitions():
    return [production_loss_definition(), missing_rate_definition(),
            fourier_definition(), logistic_definition()]


# --------------------------------------------------------------------------
# Offline preparation and verification
# --------------------------------------------------------------------------

def _write_materials(output, case_definitions):
    materials = output / 'materials'
    materials.mkdir()
    entries = {}
    for case in case_definitions:
        for entry in case['source_files']:
            data = (ROOT / entry['origin']).read_bytes()
            (materials / entry['name']).write_bytes(data)
            entries[entry['name']] = dict(path='materials/' + entry['name'],
                                          sha256=hashlib.sha256(data).hexdigest(),
                                          origin=entry['origin'], kind='raw_source_copy')
        for entry in case['model_files']:
            text = json.dumps(case['model'], ensure_ascii=False, indent=2, sort_keys=True) + '\n'
            (materials / entry['name']).write_text(text, encoding='utf-8')
            entries[entry['name']] = dict(path='materials/' + entry['name'],
                                          sha256=sha256_file(materials / entry['name']),
                                          origin='generated_from_explicit_math_definition',
                                          kind='generated_prepared_model')
    return entries


def prepare(output, *, model=DEFAULT_MODEL, budget_overrides=None, provider_overrides=None):
    """Offline freeze: materials, manifest and manifest hash; never touches the network."""
    budgets = dict(DEFAULT_BUDGETS)
    budgets.update(budget_overrides or {})
    provider = dict(base_url=BASE_URL, api_key_env=KEY_ENV, model=model,
                    reasoning_effort=REASONING_EFFORT, timeout_s=DEFAULT_PROVIDER_TIMEOUT,
                    max_tokens=DEFAULT_MAX_TOKENS, json_mode=True)
    provider.update(provider_overrides or {})
    check_model(provider['model'])
    check_budgets(budgets, provider)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    case_definitions = definitions()
    manifest = dict(version=1, pilot=PILOT_ID, task_id=TASK_ID, purpose=PURPOSE,
                    created_at=_utc_now(), provider=provider, budgets=budgets,
                    budget_policy=BUDGET_POLICY,
                    implementation_sha256=recovery.implementation_fingerprint(),
                    runner_sha256=sha256_file(RUNNER_PATH),
                    definitions=case_definitions,
                    materials=_write_materials(output, case_definitions),
                    scoring=SCORING_NOTE, credentials=CREDENTIAL_NOTE,
                    independent_evalset_accessed=False, scientific_support='unknown',
                    scientific_mm_a_runs=0, expt_b_runs=0, scientific_score=None)
    _write_json(output / 'manifest.json', manifest)
    (output / 'manifest.sha256').write_text(sha256_file(output / 'manifest.json') + '\n', encoding='utf-8')
    (output / 'runner.py').write_bytes(RUNNER_PATH.read_bytes())
    with (output / 'events.jsonl').open('x', encoding='utf-8') as log:
        _emit(log, dict(event='frozen', cases=[c['id'] for c in case_definitions], live=False,
                        model=provider['model'], reasoning_effort=provider['reasoning_effort'],
                        note='offline preparation only; no provider call was made'))
    return manifest


def verify_prepared(output):
    """Re-verify the frozen manifest, hashes and product-contract validity of every case."""
    output = Path(output)
    manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
    if sha256_file(output / 'manifest.json') != (output / 'manifest.sha256').read_text().strip():
        raise ValueError('manifest changed after preparation; refusing to run against it')
    if manifest.get('implementation_sha256') != recovery.implementation_fingerprint():
        raise ValueError('product implementation changed after preparation; freeze a new manifest')
    if manifest.get('runner_sha256') != sha256_file(RUNNER_PATH):
        raise ValueError('runner changed after preparation; freeze a new manifest')
    snapshot = output / 'runner.py'
    if not snapshot.exists() or sha256_file(snapshot) != manifest['runner_sha256']:
        raise ValueError('frozen runner snapshot missing or changed')
    check_model(manifest['provider']['model'])
    if manifest['provider']['reasoning_effort'] != REASONING_EFFORT:
        raise ValueError('reasoning_effort must stay ' + REASONING_EFFORT)
    if manifest['provider']['base_url'] != BASE_URL:
        raise ValueError('provider base_url changed after preparation')
    if manifest['provider'].get('api_key_env') != KEY_ENV:
        raise ValueError('provider api_key_env changed after preparation')
    if manifest['provider'].get('json_mode') is not True:
        raise ValueError('live provider requires the frozen json_mode=True response contract')
    check_budgets(manifest['budgets'], manifest['provider'])
    if [case['id'] for case in manifest['definitions']] != list(CASE_IDS):
        raise ValueError('case scope changed after preparation')
    seen = set()
    for case in manifest['definitions']:
        if case['id'] in seen:
            raise ValueError('duplicate case id: ' + case['id'])
        seen.add(case['id'])
        for entry in case['source_files'] + case['model_files']:
            path = output / 'materials' / entry['name']
            record = manifest['materials'][entry['name']]
            if not path.exists() or sha256_file(path) != record['sha256']:
                raise ValueError('frozen material changed: ' + entry['name'])
        if case['kind'] == 'pde':
            register = case['reference']['register']
            if register.get('model_type') != 'pde':
                raise ValueError('PDE reference registration must declare model_type pde: ' + case['id'])
            pde_system.validate(case['model'])
            if pde_system.config(case['settings']) != case['settings']:
                raise ValueError('frozen settings are not in merged canonical form')
            spec = case['reference']['register']['reference']
            typed_references.validate(spec)
            typed_references.compatible(spec, case['model'], case['settings'])
            axes, _ = pde_system.grid(case['model'])
            for name, values in spec['axes'].items():
                if axes[name].tolist() != values:
                    raise ValueError('reference axes drift from the model grid in ' + case['id'])
        elif case['kind'] == 'ode' and case.get('reference'):
            register = case['reference']['register']
            ref = from_dict(SimulationResult, register['reference'])
            validation.validate_reference(ref)
            validation.validate_scales(register['scales'], ref.values)
        if case['numerically_evaluable'] and not case.get('threshold_budget'):
            raise ValueError('numeric case lacks its frozen threshold budget: ' + case['id'])
    return manifest


def make_provider(config):
    """Validate the frozen provider contract, then build the real ChatProvider.

    Live calls only ever use this production adapter, and only with the frozen
    json_mode=True response contract; scripted or fabricated responses are
    never constructed here.
    """
    if not isinstance(config, dict):
        raise ValueError('provider config must be the frozen manifest provider mapping')
    check_model(config.get('model'))
    if config.get('base_url') != BASE_URL:
        raise ValueError('provider base_url must stay the frozen USTC endpoint: ' + BASE_URL)
    if config.get('api_key_env') != KEY_ENV:
        raise ValueError('provider api_key_env must stay ' + KEY_ENV)
    if config.get('reasoning_effort') != REASONING_EFFORT:
        raise ValueError('reasoning_effort must stay ' + REASONING_EFFORT)
    if config.get('json_mode') is not True:
        raise ValueError('live provider requires the frozen json_mode=True response contract')
    _check_provider_limits(config)
    return ChatProvider(config['base_url'], config['model'], api_key_env=config['api_key_env'],
                        timeout_s=config['timeout_s'], max_tokens=config['max_tokens'],
                        reasoning_effort=config['reasoning_effort'], json_mode=True)


def _emit(log, payload):
    line = json.dumps(dict(ts=_utc_now(), **payload), ensure_ascii=False, allow_nan=False)
    log.write(line + '\n')
    log.flush()
    print(line, flush=True)


def _write_json(path, payload):
    """Atomically publish a finite JSON snapshot."""
    data = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    _atomic_write(Path(path), data.encode('utf-8'))


def _environment_secrets():
    """Run-environment key value for in-memory redaction only; never written."""
    value = os.environ.get(KEY_ENV)
    return [value.strip()] if isinstance(value, str) and value.strip() else []


def _safe_error_message(exc):
    """error_type plus a redacted, truncated message; key material never lands on disk."""
    message = type(exc).__name__ + ': ' + str(exc)
    for secret in _environment_secrets():
        if secret:
            message = message.replace(secret, '[redacted]')
    message = re.sub(r'(?i)bearer\s+\S+', 'bearer [redacted]', message)
    return message[:500]


def _strict_number(value):
    """Strict finite float; bools and non-numbers are rejected, never coerced."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _strict_series(value, length):
    """Strict finite-float list of exactly `length` entries, else None."""
    if not isinstance(value, list) or len(value) != length:
        return None
    out = []
    for item in value:
        number = _strict_number(item)
        if number is None:
            return None
        out.append(number)
    return out


def _strict_matches(series, frozen):
    """Exact float equality against the frozen series (same validated length)."""
    return series is not None and all(a == b for a, b in zip(series, frozen))


def _scaled_errors(actual, expected, scale):
    """Per-point scaled errors, or None when a difference leaves the float range."""
    errors = []
    for a, e in zip(actual, expected):
        try:
            difference = a - e
        except OverflowError:
            return None
        if not math.isfinite(difference):
            return None
        scaled = difference / scale
        if not math.isfinite(scaled):
            return None
        errors.append(scaled)
    return errors


def _stable_rmse(errors):
    """RMSE that stays finite for large but finite scaled errors."""
    peak = max(abs(e) for e in errors)
    if peak == 0.0:
        return 0.0
    return peak * math.sqrt(sum((e / peak) ** 2 for e in errors) / len(errors))


# --------------------------------------------------------------------------
# Independent scoring from confirmed run records and frozen references only
# --------------------------------------------------------------------------

def _flat_numeric(value, shape):
    """Flat finite-float list for a nested list matching shape, else None."""
    if len(shape) == 0:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return None
        return [float(value)]
    if not isinstance(value, list) or len(value) != shape[0]:
        return None
    out = []
    for item in value:
        nested = _flat_numeric(item, shape[1:])
        if nested is None:
            return None
        out.extend(nested)
    return out


def score_ode_case(case, simulation, declared_solver=None):
    """Recheck a confirmed ODE run against the frozen series reference (independent).

    Times, the declared time unit and every output column are validated as
    strict finite numbers before any comparison; ``numeric_evaluable`` is True
    only when the frozen grid/unit/columns all matched and every finite error
    was actually computed (a scorable above-threshold failure stays evaluable).
    """
    frozen = case['reference']['register']
    reference, scales, threshold = frozen['reference'], frozen['scales'], frozen['threshold']
    base = dict(case_id=case['id'], expected_behavior=case['expected_behavior'],
                threshold=threshold, numeric_evaluable=False,
                rule='every frozen column must meet the threshold separately; '
                     'no averaging across columns')
    if simulation is None:
        return dict(**base, status='not_run', reason='No confirmed application run was recorded.',
                    fields={}, declared_solver=declared_solver or 'unknown')
    if not isinstance(simulation, dict) or simulation.get('status') != 'succeeded':
        return dict(**base, status='failed',
                    reason='Confirmed run did not succeed: status '
                           + repr(simulation.get('status') if isinstance(simulation, dict) else None) + '.',
                    fields={}, declared_solver=declared_solver or 'unknown')
    times = _strict_series(simulation.get('times'), len(reference['times']))
    if not _strict_matches(times, reference['times']):
        return dict(**base, status='fail',
                    reason='time grid differs from the frozen experiment grid; strict finite '
                           'numbers are required and booleans are never accepted as 0/1',
                    expected_samples=len(reference['times']),
                    actual_samples=len(simulation.get('times')) if isinstance(simulation.get('times'), list) else None,
                    fields={}, declared_solver=declared_solver or 'unknown')
    declared_unit = simulation.get('time_unit')
    if declared_unit != reference.get('time_unit'):
        return dict(**base, status='fail',
                    reason='declared time_unit differs from the frozen reference clock; no unit '
                           'conversion is ever applied',
                    expected_time_unit=reference.get('time_unit'),
                    actual_time_unit=declared_unit if isinstance(declared_unit, str) else None,
                    fields={}, declared_solver=declared_solver or 'unknown')
    values = simulation.get('values')
    if not isinstance(values, dict) or set(values) != set(reference['values']):
        return dict(**base, status='fail', reason='output columns (states/observations) differ from '
                                                  'the frozen reference columns',
                    expected=sorted(reference['values']),
                    actual=sorted(values) if isinstance(values, dict) else None, fields={},
                    declared_solver=declared_solver or 'unknown')
    fields = {}
    evaluable = True
    for name, ref_column in reference['values'].items():
        column = _strict_series(values.get(name), len(times))
        frozen_column = _strict_series(ref_column, len(times))
        if column is None:
            evaluable = False
            fields[name] = dict(status='fail', scale=scales[name],
                                reason='column is missing, has the wrong length, or contains '
                                       'non-numeric or non-finite entries; no averaging is applied')
            continue
        if frozen_column is None:
            evaluable = False
            fields[name] = dict(status='fail', scale=scales[name],
                                reason='frozen reference column is not a finite numeric series; '
                                       'the case cannot be evaluated')
            continue
        errors = _scaled_errors(column, frozen_column, scales[name])
        if errors is None:
            evaluable = False
            fields[name] = dict(status='fail', scale=scales[name],
                                reason='scaled error leaves the finite float range; no finite '
                                       'metric can be reported and the field fails')
            continue
        peak = max(abs(e) for e in errors)
        fields[name] = dict(status='pass' if peak <= threshold else 'fail', scale=scales[name],
                            max_scaled_error=peak, rmse_scaled=_stable_rmse(errors),
                            max_absolute_error=max(abs(a - e) for a, e in zip(column, frozen_column)))
    status = 'pass' if fields and all(v['status'] == 'pass' for v in fields.values()) else 'fail'
    base['numeric_evaluable'] = evaluable
    return dict(**base, status=status, fields=fields,
                declared_solver=declared_solver or 'unknown', time_unit=reference.get('time_unit'))


def score_pde_case(case, model, settings, reference_matches_frozen, result):
    """Recheck a confirmed typed run per field against the frozen system reference.

    Times and every named axis are validated as strict finite numbers and the
    grid shape is taken explicitly in x,y name order (never from axes dict
    insertion order). ``numeric_evaluable`` is True only when the binding, the
    frozen grid and every required field were actually evaluated to finite
    errors; a scorable above-threshold failure stays evaluable.
    """
    spec = case['reference']['register']['reference']
    threshold, scales, field_ids = spec['threshold'], spec['scales'], list(spec['field_ids'])
    axis_names = [name for name in ('x', 'y') if name in spec['axes']]
    grid_shape = tuple(len(spec['axes'][name]) for name in axis_names)
    binding = dict(model_matches_frozen=model == case['model'],
                   profile_matches_frozen=isinstance(model, dict) and model.get('profile') == pde_system.PROFILE,
                   settings_match_frozen=settings == case['settings'],
                   reference_matches_frozen=reference_matches_frozen is True)
    base = dict(case_id=case['id'], expected_behavior=case['expected_behavior'],
                threshold=threshold, binding=binding, numeric_evaluable=False,
                rule='every declared field must meet the frozen threshold separately on the exact '
                     'frozen grid; a single failing or missing field fails the case and is never '
                     'averaged away')
    if not all(binding.values()):
        return dict(**base, status='fail',
                    reason='Executed model, profile, settings or reference binding differs from the '
                           'frozen manifest.', fields={})
    if result is None:
        return dict(**base, status='not_run', reason='No confirmed typed run was recorded.', fields={})
    if not isinstance(result, dict) or result.get('status') != 'succeeded':
        return dict(**base, status='failed',
                    reason='Confirmed typed run did not succeed: status '
                           + repr(result.get('status') if isinstance(result, dict) else None) + '.',
                    fields={})
    data = result.get('data')
    if not isinstance(data, dict):
        return dict(**base, status='failed', reason='Confirmed run carries no data payload.', fields={})
    problems = []
    if data.get('field_ids') != field_ids:
        problems.append('field_ids differ from the frozen reference')
    times = _strict_series(data.get('times'), len(spec['times']))
    if not _strict_matches(times, spec['times']):
        problems.append('times differ from the frozen reference grid; strict finite numbers are '
                        'required and booleans are never accepted as 0/1')
    axes = data.get('axes')
    if not isinstance(axes, dict) or set(axes) != set(spec['axes']):
        problems.append('grid axes differ from the frozen reference axes')
    else:
        for name in axis_names:
            axis = _strict_series(axes.get(name), len(spec['axes'][name]))
            if not _strict_matches(axis, spec['axes'][name]):
                problems.append('grid axis ' + name + ' differs from the frozen reference axis; '
                                'strict finite numbers are required')
    if problems:
        return dict(**base, status='fail', reason='; '.join(problems), fields={})
    samples = len(spec['times'])
    fields = {}
    evaluable = True
    for index, field_id in enumerate(field_ids):
        actual = expected = None
        bad = None
        for series, source in (('actual', data.get('values')), ('expected', spec['values'])):
            if not isinstance(source, list) or len(source) != samples:
                bad = series + ' trajectory rows are missing or incomplete for field ' + field_id
                break
            flat = []
            for row in source:
                if not isinstance(row, list) or len(row) != len(field_ids):
                    bad = series + ' values are missing, ragged or non-finite for field ' + field_id
                    break
                part = _flat_numeric(row[index], grid_shape)
                if part is None:
                    bad = series + ' values are missing, ragged or non-finite for field ' + field_id
                    break
                flat.extend(part)
            if bad:
                break
            if series == 'actual':
                actual = flat
            else:
                expected = flat
        if bad:
            evaluable = False
            fields[field_id] = dict(status='fail', scale=scales[index], reason=bad + '; no averaging is applied')
            continue
        errors = _scaled_errors(actual, expected, scales[index])
        if errors is None:
            evaluable = False
            fields[field_id] = dict(status='fail', scale=scales[index],
                                    reason='scaled error leaves the finite float range; no finite '
                                           'metric can be reported and the field fails')
            continue
        peak = max(abs(e) for e in errors)
        fields[field_id] = dict(status='pass' if _stable_rmse(errors) <= threshold else 'fail',
                                scale=scales[index], rmse_scaled=_stable_rmse(errors),
                                max_scaled_error=peak,
                                max_absolute_error=max(abs(a - e) for a, e in zip(actual, expected)))
    status = 'pass' if fields and all(v['status'] == 'pass' for v in fields.values()) else 'fail'
    base['numeric_evaluable'] = evaluable
    return dict(**base, status=status, fields=fields)


def confirmed_runs(store, view):
    """Application runs confirmed by the store's own receipts, one per iteration.

    The SDK-computed execution/native-check/source/reference statuses from
    projects.facts are kept on every record; unreadable records are marked
    explicitly instead of being silently dropped.
    """
    out = []
    for ref in view.get('iteration_refs') or []:
        try:
            facts = projects.facts(store, ref)
            app = facts.get('application_run_ref')
            kind = store.get(app)['kind'] if app else None
        except Exception as exc:
            out.append(dict(iteration_ref=ref, run_ref=None, kind=None, executed=False,
                            facts_status='unreadable', error_type=type(exc).__name__))
            continue
        out.append(dict(iteration_ref=ref, run_ref=app, kind=kind,
                        executed=bool(facts.get('executed')), facts_status='read', facts=facts))
    return out


def case_metrics(case, store, view):
    """Independent recomputation combined with the SDK's own project gates.

    The runner never overrides a real native check failure with its own
    trajectory recomputation: both must pass, and every gate value is output.
    """
    runs = confirmed_runs(store, view)
    summary = dict(case_id=case['id'],
                   confirmed_application_runs=[r['run_ref'] for r in runs if r.get('run_ref')])
    if not case['numerically_evaluable']:
        return dict(**summary, status='not_applicable_numeric', numeric_evaluable=False,
                    reason='Clarification control: a numeric comparison is intentionally undefined; '
                           'behavior passes only with a precise pending question and zero scientific '
                           'execution.')
    unreadable = [r for r in runs if r.get('facts_status') != 'read']
    if unreadable:
        return dict(**summary, status='unknown', numeric_evaluable=False,
                    reason='confirmed iteration facts unreadable: '
                           + ', '.join(sorted({r['error_type'] for r in unreadable}))
                           + '; unknown records are never treated as zero')
    kind = 'typed_run' if case['kind'] == 'pde' else 'run'
    candidates = [r for r in runs if r.get('kind') == kind]
    if not candidates:
        return dict(**summary, status='not_run', numeric_evaluable=False,
                    reason='No confirmed ' + kind + ' application run; a missing or failed execution '
                           'is never treated as numeric coverage.')
    latest = candidates[-1]
    facts = latest['facts']
    gates = dict(confirmed_execution='pass' if facts.get('executed') and latest.get('run_ref') else 'fail',
                 native_numeric_checks=facts.get('numeric_status') or 'unknown',
                 reference_comparison=facts.get('comparison_status') or 'unknown',
                 source_binding=facts.get('source_binding_status') or 'unknown')
    if case['kind'] == 'pde':
        try:
            inp, _, result = typed_models.result(store, latest['run_ref'])
        except Exception as exc:
            return dict(**summary, status='unknown', numeric_evaluable=False, gates=gates,
                        reason='confirmed typed run unreadable: ' + type(exc).__name__)
        model_payload = store.get(inp['model_ref'])['payload']
        ref_payload = None
        if inp.get('reference_ref'):
            try:
                ref_payload = store.get(inp['reference_ref'])['payload'].get('reference')
            except Exception:
                ref_payload = None
        metrics = score_pde_case(case, model_payload.get('model'), inp.get('settings'),
                                 ref_payload == case['reference']['register']['reference'], result)
    else:
        payload = store.get(latest['run_ref'])['payload']
        solver = 'unknown'
        input_ref = payload.get('input_ref')
        if isinstance(input_ref, str):
            try:
                solver = store.get(input_ref)['payload'].get('solver', 'unknown')
            except Exception:
                solver = 'unknown'
        metrics = score_ode_case(case, payload.get('simulation'), solver)
    metrics['evaluated_run_ref'] = latest['run_ref']
    metrics['latest_iteration_ref'] = latest['iteration_ref']
    metrics['confirmed_application_runs'] = summary['confirmed_application_runs']
    metrics['gates'] = gates
    if metrics['status'] in ('not_run', 'failed'):
        return metrics
    required = ['confirmed_execution', 'native_numeric_checks', 'reference_comparison']
    if case['kind'] == 'ode':
        required.append('source_binding')
    failed_gates = [name for name in required if gates.get(name) != 'pass']
    if failed_gates:
        metrics['status'] = 'fail'
        metrics['reason'] = ('required project gates did not pass: '
                             + ', '.join(name + '=' + repr(gates.get(name)) for name in failed_gates)
                             + '; the runner never overrides a failed native check with its own '
                               'recomputation')
    return metrics


def call_records(store, project_ref):
    """Per planner call: attempt/prompt/response refs, duration, usage and error stage.

    When a response never returned, the reported usage, elapsed time and raw
    model-content blob ref are read from the bound provider-failure receipt;
    anything the receipt does not actually report stays explicitly 'unknown'.
    """
    attempts, returns, _ = projects.history(store, project_ref)
    records = []
    for ref, attempt in attempts:
        if attempt['operation'] != 'respond':
            continue
        args = attempt.get('args') if isinstance(attempt.get('args'), dict) else {}
        record = dict(attempt_ref=ref, stage='planner_respond', prompt_ref=args.get('request_ref'),
                      response_ref=attempt.get('response_ref'))
        result = returns.get(ref)
        if result is None:
            record.update(state='unknown', usage='unknown', elapsed_s=None,
                          error_stage='unknown_effect_no_return')
            records.append(record)
            continue
        return_ref, payload = result
        diagnostic = payload.get('diagnostic')
        record.update(return_ref=return_ref, state=payload.get('state'),
                      elapsed_s=payload.get('elapsed_s'),
                      error_stage=diagnostic.get('stage') if isinstance(diagnostic, dict) else None,
                      error_type=payload.get('error_type'),
                      provider_failure_ref=payload.get('provider_failure_ref'))
        if payload.get('state') == 'returned':
            usage = payload.get('result', {}).get('usage')
            record['usage'] = usage if isinstance(usage, dict) and usage else 'unknown'
            record['response_ref'] = payload.get('result', {}).get('response_ref')
            records.append(record)
            continue
        failure_ref = payload.get('provider_failure_ref')
        receipt = None
        if isinstance(failure_ref, str):
            try:
                receipt = projects.own(store, failure_ref, projects.PROVIDER_FAILURE, project_ref)
            except Exception:
                receipt = None
        if not isinstance(receipt, dict) or receipt.get('attempt_ref') != ref:
            record['usage'] = 'unknown'
            record['failure_receipt'] = 'unreadable_or_unbound'
            records.append(record)
            continue
        usage = receipt.get('usage')
        record['usage'] = usage if isinstance(usage, dict) and usage else 'unknown'
        record['model_content_blob_ref'] = receipt.get('model_content_json_blob_ref')
        receipt_elapsed = receipt.get('elapsed_s')
        if not isinstance(receipt_elapsed, bool) and isinstance(receipt_elapsed, (int, float)) \
                and math.isfinite(receipt_elapsed) and receipt_elapsed >= 0:
            record['elapsed_s'] = receipt_elapsed
        records.append(record)
    return records


def operation_events(store, project_ref):
    """Derived export of the SDK's persisted operation reservations and returns.

    Read through projects.history after the run; the runner never writes this
    export before or during the calls it describes. ``index_registered_at`` is
    the store's own SQLite index registration timestamp (UTC) read with
    store._index_row; it is None when the index has no row for the ref.
    """
    attempts, returns, _ = projects.history(store, project_ref)
    events = []
    for ref, attempt in attempts:
        row = store._index_row(ref)
        registered = row.get('created_at') if isinstance(row, dict) else None
        args = attempt.get('args') if isinstance(attempt.get('args'), dict) else {}
        event = dict(sequence=attempt.get('sequence'), operation=attempt.get('operation'),
                     attempt_ref=ref, index_registered_at=registered,
                     actor=attempt.get('actor'),
                     native_allocation=attempt.get('native_allocation'),
                     prompt_ref=args.get('request_ref') if attempt.get('operation') == 'respond' else None,
                     response_ref=attempt.get('response_ref'))
        result = returns.get(ref)
        if result is None:
            event.update(state='unknown', return_ref=None, return_index_registered_at=None,
                         elapsed_s=None, error_stage=None,
                         error_type=None, error='unknown_effect_no_return')
        else:
            return_ref, payload = result
            return_row = store._index_row(return_ref)
            diagnostic = payload.get('diagnostic')
            event.update(state=payload.get('state'), return_ref=return_ref,
                         return_index_registered_at=(return_row.get('created_at')
                                                     if isinstance(return_row, dict) else None),
                         elapsed_s=payload.get('elapsed_s'),
                         error_stage=diagnostic.get('stage') if isinstance(diagnostic, dict) else None,
                         error_type=payload.get('error_type'),
                         error=diagnostic.get('code') if isinstance(diagnostic, dict) else None,
                         provider_failure_ref=payload.get('provider_failure_ref'))
            if attempt.get('operation') == 'respond' and payload.get('state') == 'returned':
                event['response_ref'] = payload.get('result', {}).get('response_ref')
        events.append(event)
    return events


def _questions(store, project_ref, view):
    questions = []
    for ref in view.get('pending_questions') or []:
        try:
            payload = projects.own(store, ref, projects.QUESTION, project_ref)
            questions.append(dict(question_ref=ref, category=payload.get('category'),
                                  question=payload.get('question'),
                                  affected_fields=payload.get('affected_fields')))
        except Exception as exc:
            questions.append(dict(question_ref=ref, question='unknown', error_type=type(exc).__name__))
    return questions


def _claimed_outcome(store, project_ref, view):
    """Agent conclusion is recorded for audit only; scoring never trusts it."""
    ref = view.get('conclusion_ref')
    if not ref:
        return None
    try:
        payload = projects.own(store, ref, projects.CONCLUSION, project_ref)
        return dict(outcome=payload.get('outcome'), summary=payload.get('summary'),
                    trusted_for_scoring=False)
    except Exception as exc:
        return dict(outcome='unknown', error_type=type(exc).__name__, trusted_for_scoring=False)


_MISSING_RATE_SYMBOL = re.compile(r'\bk\b')
_MISSING_RATE_REQUEST = re.compile(r'\b(value|unit|units|numeric|numerical|rate|constant|parameter)\b', re.I)


def _clarification_checks(questions):
    """Keyword sub-checks over the actual pending question text of the project."""
    checks = []
    for question in questions:
        text = question.get('question')
        readable = (isinstance(text, str) and bool(text.strip())
                    and 'error_type' not in question)
        checks.append(dict(question_ref=question.get('question_ref'), category=question.get('category'),
                           readable=readable,
                           mentions_missing_symbol=bool(readable and _MISSING_RATE_SYMBOL.search(text)),
                           requests_missing_value_or_unit=bool(readable and _MISSING_RATE_REQUEST.search(text))))
    return checks


def behavior_status(case, metrics, questions, iteration_facts):
    """Behavior verdict from the project's own records; never a self-signature.

    The clarification control passes only when every iteration fact is
    readable, nothing was executed or reserved, and exactly one readable
    pending question explicitly asks for the missing rate value/units.
    Unreadable facts or questions degrade to 'unknown', never to a pass.
    """
    if not case['numerically_evaluable']:
        entries = list(iteration_facts.values()) if isinstance(iteration_facts, dict) else None
        if entries is None or any(not isinstance(v, dict) or v.get('status') == 'unknown'
                                  or 'executed' not in v for v in entries):
            return 'unknown'
        if any(v.get('executed') or v.get('scientific_attempt_reserved') for v in entries):
            return 'fail'
        checks = _clarification_checks(questions)
        if any(not c['readable'] for c in checks):
            return 'unknown'
        precise = [c for c in checks if c['mentions_missing_symbol'] and c['requests_missing_value_or_unit']]
        if len(checks) == 1 and len(precise) == 1:
            return 'pass'
        return 'fail'
    return metrics.get('status', 'unknown') if isinstance(metrics, dict) else 'unknown'


def run_case(case, manifest, output):
    """One fresh store + one fresh real provider object; never retried on failure."""
    directory = output / case['id']
    directory.mkdir()
    store = ArtifactStore(directory / 'store')
    materials = output / 'materials'
    sources = [evidence.ingest_source(store, materials / entry['name'])
               for entry in case['source_files']]
    models = [typed_models.import_model(store, materials / entry['name'])
              for entry in case['model_files']]
    refs = []
    if case.get('reference'):
        refs.append(projects.register_reference(store, deepcopy(case['reference']['register']),
                                                'pipeline_pilot_operator'))
    project = projects.create(store, dict(scenario=case['scenario'], mode=case['mode'],
                                          source_refs=sources, model_refs=models,
                                          reference_refs=refs, policy=deepcopy(manifest['budgets'])))
    provider = make_provider(manifest['provider'])
    started = time.monotonic()
    error = None
    snapshot_ref = None
    try:
        snapshot_ref = supervisor.run(store, provider, project)
    except Exception as exc:
        error = dict(error_type=type(exc).__name__, message=_safe_error_message(exc))
        try:
            snapshot_ref = projects.snapshot(store, project)
        except Exception as inner:
            error['snapshot_error_type'] = type(inner).__name__
    try:
        view = projects.inspect(store, project)
    except Exception as exc:
        view = dict(project_ref=project, state='inspect_failed', inspect_error_type=type(exc).__name__)
    iteration_facts = {}
    for ref in view.get('iteration_refs') or []:
        try:
            iteration_facts[ref] = projects.facts(store, ref)
        except Exception as exc:
            # Unknown facts are recorded per iteration and never counted as zero.
            iteration_facts[ref] = dict(status='unknown', error_type=type(exc).__name__)
    try:
        calls = call_records(store, project)
    except Exception as exc:
        calls = dict(status='unknown', error_type=type(exc).__name__)
    try:
        events = operation_events(store, project)
    except Exception as exc:
        events = dict(status='unknown', error_type=type(exc).__name__)
    try:
        usage = supervisor.usage(store, project)
    except Exception as exc:
        usage = dict(status='unknown', error_type=type(exc).__name__)
    questions = _questions(store, project, view)
    result = dict(case_id=case['id'], identity=case['identity'], material=case['material'],
                  mode=case['mode'], expected_behavior=case['expected_behavior'],
                  project_ref=project, snapshot_ref=snapshot_ref, state=view.get('state'),
                  budget=view.get('budget'), iteration_facts=iteration_facts,
                  pending_questions=questions, agent_claimed_outcome=_claimed_outcome(store, project, view),
                  call_records=calls, usage=usage, error=error,
                  elapsed_s=time.monotonic() - started,
                  scientific_support='unknown', scientific_mm_a_runs=0, expt_b_runs=0,
                  synthetic_control=True)
    if isinstance(events, list):
        # Derived export of the SDK's own persisted reservations/returns; written
        # only here, after the calls it describes have actually happened.
        (directory / 'operation-events.jsonl').write_text(
            ''.join(json.dumps(event, ensure_ascii=False, allow_nan=False) + '\n' for event in events),
            encoding='utf-8')
        result['operation_events_file'] = 'operation-events.jsonl'
        result['operation_events_sha256'] = sha256_file(directory / 'operation-events.jsonl')
        result['operation_event_count'] = len(events)
    else:
        result['operation_events_export'] = events
    result['operation_event_semantics'] = OPERATION_EVENT_SEMANTICS
    try:
        metrics = case_metrics(case, store, view)
    except Exception as exc:
        metrics = dict(case_id=case['id'], status='unknown', numeric_evaluable=False,
                       reason='metrics computation failed: ' + type(exc).__name__)
    result['metrics'] = metrics
    result['clarification_question_checks'] = _clarification_checks(questions)
    result['behavior_status'] = behavior_status(case, metrics, questions, iteration_facts)
    if case['id'] == 'missing_rate':
        result['clarification_question_mentions_missing_symbol'] = any(
            c['mentions_missing_symbol'] for c in result['clarification_question_checks'])
    _write_json(directory / 'result.json', result)
    _write_json(directory / 'metrics.json', metrics)
    if snapshot_ref:
        try:
            report = reporting.render_report(store, snapshot_ref)
        except Exception as exc:
            report = 'report rendering failed: ' + type(exc).__name__ + '\n'
        (directory / 'report.md').write_text(report, encoding='utf-8')
    return result


def score_summary(manifest, results):
    by_id = {result['case_id']: result for result in results
             if isinstance(result, dict) and 'case_id' in result}

    def bucket(table, statuses):
        out = {}
        for status in statuses:
            members = sorted(k for k, v in table.items() if v == status)
            if members:
                out[status] = members
        return out

    behavior = {case['id']: (by_id.get(case['id']) or {}).get('behavior_status', 'not_run')
                for case in manifest['definitions']}
    applicable = [case['id'] for case in manifest['definitions'] if case['numerically_evaluable']]
    coverage = {}
    target = {}
    for case in manifest['definitions']:
        if not case['numerically_evaluable']:
            continue
        metrics = (by_id.get(case['id']) or {}).get('metrics')
        if not isinstance(metrics, dict) or not metrics:
            coverage[case['id']] = 'not_run'
            target[case['id']] = 'not_run'
            continue
        coverage[case['id']] = 'evaluated' if metrics.get('numeric_evaluable') is True else 'not_evaluated'
        target[case['id']] = metrics.get('status', 'not_run')
    passes = sorted(k for k, v in behavior.items() if v == 'pass')
    covered = sorted(k for k, v in coverage.items() if v == 'evaluated')
    numeric_passes = sorted(k for k, v in target.items() if v == 'pass')
    behavior_statuses = ('pass', 'fail', 'not_run', 'failed', 'unknown', 'not_applicable',
                         'not_applicable_numeric')
    return dict(version=2,
                behavior_pass=dict(numerator=len(passes), denominator=len(manifest['definitions']),
                                   passed=passes, by_case=behavior,
                                   breakdown=bucket(behavior, behavior_statuses)),
                numeric_coverage=dict(numerator=len(covered), denominator=len(applicable),
                                      evaluated=covered, by_case=coverage,
                                      breakdown=bucket(coverage, ('evaluated', 'not_evaluated', 'not_run')),
                                      note='Coverage counts the numerically applicable cases whose '
                                           'frozen coordinates/units/fields and bindings were correct '
                                           'and whose finite errors were actually computed; a scorable '
                                           'failure above the frozen threshold still counts as covered.'),
                numeric_target=dict(numerator=len(numeric_passes), denominator=len(applicable),
                                    passed=numeric_passes, by_case=target,
                                    breakdown=bucket(target, ('pass', 'fail', 'not_run', 'failed',
                                                              'unknown')),
                                    note='Attainment counts only cases whose independent recomputed '
                                         'errors meet every frozen threshold and whose required '
                                         'project gates pass; it is never used as coverage.'),
                numeric_applicability=dict(numerator=len(applicable),
                                           denominator=len(manifest['definitions']),
                                           by_case={case['id']: bool(case['numerically_evaluable'])
                                                    for case in manifest['definitions']},
                                           note='Cases whose frozen expected behavior includes a '
                                                'numeric reference comparison; the missing-rate '
                                                'clarification control is excluded by design and is '
                                                'never counted as numeric coverage.'),
                scientific_mm_a_runs=0, expt_b_runs=0, scientific_score=None,
                scientific_support='unknown', synthetic_controls_only=True,
                claim='Engineering pipeline trial on four fixed synthetic controls; no scientific '
                      'qualification is certified here and no paper A/B case is counted.')


def run_live(args, output=None):
    """Explicit authorized execution; refuses drift, missing key, and any rerun."""
    output = Path(output or args.output)
    manifest = verify_prepared(output)
    check_frozen_args(args, manifest)
    if not os.environ.get(KEY_ENV):
        raise ValueError(KEY_ENV + ' is not set; live mode reads the key only from the environment '
                                   'and never from a file')
    with (output / 'live-started.json').open('x', encoding='utf-8') as handle:
        json.dump(dict(started_at=_utc_now(), model=manifest['provider']['model'],
                       reasoning_effort=manifest['provider']['reasoning_effort'],
                       rerun_policy='a started live run is never overwritten or auto-resumed'), handle)
    results = []
    summary = None
    with (output / 'events.jsonl').open('a', encoding='utf-8') as log:
        _emit(log, dict(event='live_started', cases=[c['id'] for c in manifest['definitions']],
                        model=manifest['provider']['model'],
                        reasoning_effort=manifest['provider']['reasoning_effort'],
                        base_url=manifest['provider']['base_url'],
                        budgets=manifest['budgets']))
        try:
            for case in manifest['definitions']:
                _emit(log, dict(event='case_started', case=case['id'], mode=case['mode'],
                                expected_behavior=case['expected_behavior']))
                started = time.monotonic()
                try:
                    result = run_case(case, manifest, output)
                except Exception as exc:
                    result = dict(case_id=case['id'], behavior_status='failed',
                                  error_type=type(exc).__name__, error=_safe_error_message(exc),
                                  metrics=dict(status='unknown', numeric_evaluable=False,
                                               reason='case runner failed: ' + type(exc).__name__),
                                  scientific_support='unknown')
                result['elapsed_s'] = result.get('elapsed_s', time.monotonic() - started)
                results.append(result)
                _write_json(output / 'results.json', results)
                # The aggregate summary is refreshed and written after every case,
                # so an interrupted run still leaves an up-to-date aggregate on disk.
                summary = score_summary(manifest, results)
                _write_json(output / 'summary.json', summary)
                _emit(log, dict(event='case_finished', case=case['id'],
                                behavior_status=result.get('behavior_status'),
                                metrics_status=(result.get('metrics') or {}).get('status'),
                                numeric_evaluable=(result.get('metrics') or {}).get('numeric_evaluable'),
                                state=result.get('state')))
            _emit(log, dict(event='finished',
                            behavior_pass=dict(numerator=summary['behavior_pass']['numerator'],
                                               denominator=summary['behavior_pass']['denominator']),
                            numeric_coverage=dict(numerator=summary['numeric_coverage']['numerator'],
                                                  denominator=summary['numeric_coverage']['denominator']),
                            numeric_target=dict(numerator=summary['numeric_target']['numerator'],
                                                denominator=summary['numeric_target']['denominator']),
                            scientific_mm_a_runs=0, expt_b_runs=0, scientific_score=None))
        finally:
            if summary is None:
                summary = score_summary(manifest, results)
            _write_json(output / 'results.json', results)
            _write_json(output / 'summary.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path,
                        help='new output directory; an existing directory is never overwritten')
    parser.add_argument('--run-live', action='store_true',
                        help='authorize real provider calls; default is offline preparation only')
    parser.add_argument('--model', choices=list(ALLOWED_MODELS), default=DEFAULT_MODEL,
                        help='planner model; reasoning_effort is fixed to ' + REASONING_EFFORT)
    parser.add_argument('--planner-max-calls', type=int, default=32)
    parser.add_argument('--max-iterations', type=int, default=2)
    parser.add_argument('--max-native-calls', type=int, default=12)
    parser.add_argument('--native-calls-per-iteration', type=int, default=6)
    parser.add_argument('--max-literature-requests', type=int, default=0)
    parser.add_argument('--context-max-chars', type=int, default=120000)
    parser.add_argument('--provider-timeout-s', type=float, default=180.0)
    parser.add_argument('--max-tokens', type=int, default=16000)
    args = parser.parse_args()
    try:
        check_model(args.model)
        check_budgets({key: getattr(args, key) for key in DEFAULT_BUDGETS},
                      dict(timeout_s=args.provider_timeout_s, max_tokens=args.max_tokens))
    except ValueError as exc:
        parser.error(str(exc))
    if args.run_live:
        if not os.environ.get(KEY_ENV):
            parser.error('Set ' + KEY_ENV + ' in the environment for live mode; it is never read '
                                           'from a file.')
        try:
            summary = run_live(args)
        except ValueError as exc:
            parser.error(str(exc))
        print(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        prepare(args.output, model=args.model,
                budget_overrides={key: getattr(args, key) for key in DEFAULT_BUDGETS},
                provider_overrides=dict(timeout_s=args.provider_timeout_s, max_tokens=args.max_tokens))


if __name__ == '__main__':
    main()
