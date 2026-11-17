"""Multi-field nonlinear reaction-diffusion on uniform 1D/2D Cartesian grids.

The spatial unknowns are cell-centred field values.  Diffusion is
discretised with conservative finite volumes (zero-flux or periodic wrap) or
with zero Dirichlet values imposed on the domain *faces*.  The resulting
method-of-lines system is advanced with :func:`scipy.integrate.solve_ivp`.
Reactions are restricted expressions evaluated at the same grid cell; the
validated :mod:`biomodel_agent.expressions` interpreter is reused rather than
re-implemented.
"""
from __future__ import annotations

import ast
import math

import numpy as np
from scipy import sparse
from scipy.integrate import solve_ivp

from . import expressions
from . import model_contracts as c

MODEL_TYPE = 'pde'
PROFILE = 'reaction_diffusion_cartesian_v1'
LIMITATIONS = [
    'Uniform Cartesian intervals in one or two dimensions only; cell-centred finite volumes with constant spacing per axis.',
    'One isotropic scalar diffusion coefficient per field; no anisotropy, cross-diffusion, advection, nonlocal terms or moving grids.',
    'Reactions are local (same cell) and may use fields, parameters, time and, in 2D, x/y; a common boundary condition applies to every field and axis.',
    'Zero Dirichlet is imposed on boundary faces; zero-flux and periodic boundaries are conservative. Spatial and temporal errors require separate convergence assessment.',
    'Declared nonnegative fields are checked after integration, not enforced or clipped. Mass is the spatial integral of state, not biological mass.',
    'Units are declared but not dimensionally analysed. timeout_s is validated for a host worker; this module starts no subprocess and has no internal wall-clock timeout.',
]


_DEFAULT_SETTINGS = {
    'timeout_s': 60.0,
    'duration': 1.0,
    'samples': 21,
    'method': 'BDF',
    'rtol': 1e-6,
    'atol': 1e-9,
    'max_rhs_evaluations': 10000,
}
_RESERVED_SYMBOLS = frozenset({'time', 'x', 'y', *expressions.ALLOWED_FUNCTIONS})
_MAX_INPUT_STATE_VALUES = 32768
_MAX_OUTPUT_SCALARS = 1000000
_FINITE_MAX = float(np.finfo(float).max)


def _number(value, low, high, label):
    """Use the shared strict number contract, including for very large ints."""
    try:
        return c.number(value, low, high, label)
    except OverflowError as exc:
        raise ValueError(f'{label} must be finite in [{low}, {high}]') from exc


def _check_reserved(name, what):
    if name in _RESERVED_SYMBOLS:
        raise ValueError(f'{what} {name!r} is reserved')


def _grid_parts(shape_value, lengths_value):
    if type(shape_value) is not list or len(shape_value) not in (1, 2):
        raise ValueError('grid shape must contain one or two axes')
    shape = []
    for index, value in enumerate(shape_value):
        shape.append(c.integer(value, 3, 128, f'grid shape[{index}]'))
    if math.prod(shape) > 4096:
        raise ValueError('grid has more than 4096 cells')
    if type(lengths_value) is not list or len(lengths_value) != len(shape):
        raise ValueError('grid lengths must match the number of axes')
    lengths = []
    for index, value in enumerate(lengths_value):
        length = _number(value, 0.0, _FINITE_MAX, f'grid length[{index}]')
        if length <= 0.0:
            raise ValueError('grid lengths must be positive and finite')
        lengths.append(length)
    spacing = []
    axes = {}
    names = ('x', 'y')
    for name, size, length in zip(names, shape, lengths):
        delta = length / size
        if not math.isfinite(delta) or delta <= 0.0:
            raise ValueError('grid spacing must be positive and finite')
        spacing.append(float(delta))
        axis = (np.arange(size, dtype=float) + 0.5) * delta
        if (not np.isfinite(axis).all() or axis[0] <= 0.0
                or not np.all(np.diff(axis) > 0.0)):
            raise ValueError('grid coordinates are not numerically representable')
        axes[name] = axis
    volume = math.prod(spacing)
    if not math.isfinite(volume) or volume <= 0.0:
        raise ValueError('grid cell volume is not numerically representable')
    return tuple(shape), tuple(lengths), axes, spacing


def _initial_array(value, shape):
    if type(value) is not list:
        raise ValueError('initial field must be a nested list')
    if len(shape) == 1:
        if len(value) != shape[0]:
            raise ValueError('initial field shape does not match grid')
        flat = [_number(item, -_FINITE_MAX, _FINITE_MAX, 'initial field value')
                for item in value]
    else:
        if len(value) != shape[0]:
            raise ValueError('initial field shape does not match grid')
        flat = []
        for row in value:
            if type(row) is not list or len(row) != shape[1]:
                raise ValueError('initial field shape does not match grid')
            flat.extend(_number(item, -_FINITE_MAX, _FINITE_MAX, 'initial field value')
                        for item in row)
    return np.asarray(flat, dtype=float).reshape(shape)


def _is_constant_zero(node):
    """True only for a finite expression with no variable references and value 0."""
    for item in ast.walk(node):
        if isinstance(item, ast.Name) and item.id not in expressions.ALLOWED_FUNCTIONS:
            return False
    try:
        return expressions._eval(node, {}) == 0.0
    except ValueError:
        return False


def _validated(model):
    """Validate the complete model and compile the reusable expression trees."""
    c.base(model, MODEL_TYPE,
           {'profile', 'grid', 'boundary', 'parameters', 'fields'},
           {'time', 'space', 'state'})
    if model['profile'] != PROFILE:
        raise ValueError(f'unsupported PDE profile; expected {PROFILE!r}')
    c.fields(model['grid'], {'shape', 'lengths'})
    shape, lengths, axes, spacing = _grid_parts(model['grid']['shape'],
                                                 model['grid']['lengths'])
    dimension = len(shape)

    boundary = model['boundary']
    if not isinstance(boundary, str) or boundary not in (
            'no_flux', 'periodic', 'dirichlet_zero'):
        raise ValueError('unsupported PDE boundary')

    raw_parameters = model['parameters']
    if type(raw_parameters) is not dict:
        raise ValueError('parameters must be an object')
    parameters = {}
    for name, value in raw_parameters.items():
        c.identifier(name)
        _check_reserved(name, 'parameter')
        parameters[name] = _number(value, -_FINITE_MAX, _FINITE_MAX,
                                   f'parameter {name!r}')

    raw_fields = model['fields']
    if type(raw_fields) is not list or not 1 <= len(raw_fields) <= 8:
        raise ValueError('PDE requires 1..8 fields')
    fields = []
    field_ids = set()
    for index, item in enumerate(raw_fields):
        c.fields(item, {'id', 'diffusion', 'initial', 'reaction', 'nonnegative'})
        field_id = c.identifier(item['id'])
        if field_id in field_ids:
            raise ValueError(f'duplicate field id {field_id!r}')
        _check_reserved(field_id, 'field id')
        if field_id in parameters:
            raise ValueError(f'field id {field_id!r} conflicts with a parameter')
        diffusion = _number(item['diffusion'], 0.0, _FINITE_MAX,
                            f'diffusion for field {field_id!r}')
        if type(item['nonnegative']) is not bool:
            raise ValueError(f'nonnegative for field {field_id!r} must be boolean')
        initial = _initial_array(item['initial'], shape)
        if item['nonnegative'] and np.any(initial < 0.0):
            raise ValueError(f'initial field {field_id!r} is declared nonnegative but has a negative value')
        if not isinstance(item['reaction'], str):
            raise ValueError(f'reaction for field {field_id!r} must be text')
        field_ids.add(field_id)
        fields.append({
            'id': field_id,
            'diffusion': diffusion,
            'initial': initial,
            'reaction': item['reaction'],
            'nonnegative': item['nonnegative'],
            'expression': None,
            'reaction_is_zero': False,
        })

    allowed = set(parameters) | field_ids | {'time', 'x'}
    if dimension == 2:
        allowed.add('y')
    for item in fields:
        try:
            tree = expressions.parse_expression(item['reaction'], allowed_symbols=allowed)
        except ValueError as exc:
            raise ValueError(f"reaction for field {item['id']!r} is invalid: {exc}") from exc
        item['expression'] = tree.body
        item['reaction_is_zero'] = _is_constant_zero(tree.body)

    return {
        'shape': shape,
        'lengths': lengths,
        'axes': axes,
        'spacing': tuple(spacing),
        'dimension': dimension,
        'boundary': boundary,
        'parameters': parameters,
        'fields': fields,
    }


def validate(model):
    _validated(model)
    return model


def config(value=None):
    raw = {} if value is None else value
    c.fields(raw, (), _DEFAULT_SETTINGS)
    merged = {**_DEFAULT_SETTINGS, **raw}
    out = {}
    out['timeout_s'] = _number(merged['timeout_s'], 0.1, 600.0, 'timeout_s')
    duration = _number(merged['duration'], 0.0, 1e6, 'duration')
    if duration <= 0.0:
        raise ValueError('duration must be finite in (0, 1e6]')
    out['duration'] = duration
    out['samples'] = c.integer(merged['samples'], 2, 1001, 'samples')
    method = merged['method']
    if not isinstance(method, str) or method not in ('BDF', 'RK45'):
        raise ValueError("method must be 'BDF' or 'RK45'")
    out['method'] = method
    out['rtol'] = _number(merged['rtol'], 1e-10, 1e-2, 'rtol')
    out['atol'] = _number(merged['atol'], 1e-12, 1e-3, 'atol')
    out['max_rhs_evaluations'] = c.integer(merged['max_rhs_evaluations'],
                                           1, 100000, 'max_rhs_evaluations')
    return out


def grid(model):
    info = _validated(model)
    axes = {name: values.copy() for name, values in info['axes'].items()}
    return axes, list(info['spacing'])


def _compatible_info(info, s):
    state_values = len(info['fields']) * math.prod(info['shape'])
    if state_values > _MAX_INPUT_STATE_VALUES:
        raise ValueError('PDE input/state budget exceeded')
    output_scalars = (s['samples'] * state_values
                      + s['samples'] * len(info['fields'])
                      + s['samples']
                      + sum(info['shape']))
    if output_scalars > _MAX_OUTPUT_SCALARS:
        raise ValueError(
            f'PDE output scalar budget exceeded: {output_scalars} > {_MAX_OUTPUT_SCALARS}')


def compatible(model, settings):
    info = _validated(model)
    s = config(settings)
    _compatible_info(info, s)


def _axis_second_difference(size, boundary):
    """Cell-centred second difference, including the boundary convention."""
    matrix = sparse.lil_matrix((size, size), dtype=float)
    for index in range(size):
        matrix[index, index] = -2.0
    for index in range(size - 1):
        matrix[index, index + 1] = 1.0
        matrix[index + 1, index] = 1.0
    if boundary == 'no_flux':
        # Zero flux through a face is the same as a mirrored ghost cell.
        matrix[0, 0] = -1.0
        matrix[-1, -1] = -1.0
    elif boundary == 'dirichlet_zero':
        # The face value is zero.  The ghost cell is -u[0], so the first
        # cell centre uses (-3*u[0] + u[1]) / dx^2 (and symmetrically).
        matrix[0, 0] = -3.0
        matrix[-1, -1] = -3.0
    else:  # periodic
        matrix[0, size - 1] = 1.0
        matrix[size - 1, 0] = 1.0
    return matrix.tocsr()


def _spatial_operator(info):
    """Sparse sum D * (Lx/dx^2 + Ly/dy^2) for one field, without D."""
    shape = info['shape']
    spacing = info['spacing']
    if info['dimension'] == 1:
        matrix = _axis_second_difference(shape[0], info['boundary'])
        return (matrix / (spacing[0] ** 2)).tocsr()
    ax = _axis_second_difference(shape[0], info['boundary'])
    ay = _axis_second_difference(shape[1], info['boundary'])
    ix = sparse.identity(shape[0], format='csr')
    iy = sparse.identity(shape[1], format='csr')
    matrix = (sparse.kron(ax, iy, format='csr') / (spacing[0] ** 2)
              + sparse.kron(ix, ay, format='csr') / (spacing[1] ** 2))
    return matrix.tocsr()


def _jacobian_sparsity(info, spatial, size):
    """Conservative local sparsity: same-cell fields plus diffusive neighbours."""
    nfields = len(info['fields'])
    identity = sparse.identity(size, format='csr')
    blocks = [[identity.copy() for _ in range(nfields)] for _ in range(nfields)]
    for index, item in enumerate(info['fields']):
        if item['diffusion'] > 0.0 and spatial is not None:
            spatial_pattern = spatial.copy()
            spatial_pattern.data[:] = 1.0
            blocks[index][index] = identity.maximum(spatial_pattern)
    pattern = sparse.bmat(blocks, format='csr')
    pattern.data[:] = 1.0
    pattern.eliminate_zeros()
    return pattern


def _solver_count(solution, name):
    value = getattr(solution, name, 0)
    if value is None:
        return 0
    try:
        value = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f'solver returned an invalid {name} count') from exc
    if value < 0:
        raise RuntimeError(f'solver returned a negative {name} count')
    return value


def execute(model, settings):
    info = _validated(model)
    s = config(settings)
    _compatible_info(info, s)

    shape = info['shape']
    nfields = len(info['fields'])
    size = math.prod(shape)
    expected_times = np.linspace(0.0, s['duration'], s['samples'])
    initial = np.concatenate([item['initial'].ravel(order='C')
                              for item in info['fields']])

    spatial = None
    diffusion_operators = []
    if any(item['diffusion'] > 0.0 for item in info['fields']):
        spatial = _spatial_operator(info)
        for item in info['fields']:
            if item['diffusion'] > 0.0:
                diffusion_operators.append((item['diffusion'] * spatial).tocsr())
            else:
                diffusion_operators.append(None)
    else:
        diffusion_operators = [None] * nfields

    axes = info['axes']
    if info['dimension'] == 1:
        x_coords = axes['x']
        y_coords = None
    else:
        x_coords = np.repeat(axes['x'], shape[1])
        y_coords = np.tile(axes['y'], shape[0])

    parameters = info['parameters']
    field_ids = [item['id'] for item in info['fields']]
    expressions_ast = [item['expression'] for item in info['fields']]
    max_rhs = s['max_rhs_evaluations']
    rhs_evaluations = 0
    work_exhausted = False

    def rhs(time_value, state):
        nonlocal rhs_evaluations, work_exhausted
        rhs_evaluations += 1
        if rhs_evaluations > max_rhs:
            work_exhausted = True
            raise c.WorkLimit(
                f'PDE RHS work budget exceeded: {max_rhs} evaluations allowed')
        time_float = float(time_value)
        state = np.asarray(state, dtype=float)
        if state.shape != (nfields * size,) or not np.isfinite(state).all():
            raise ValueError('PDE RHS received a non-finite or malformed state')
        flat_values = state.reshape(nfields, size)
        derivatives = np.empty((nfields, size), dtype=float)
        environment = dict(parameters)
        environment['time'] = time_float
        for cell in range(size):
            environment['x'] = float(x_coords[cell])
            if info['dimension'] == 2:
                environment['y'] = float(y_coords[cell])
            for field_index, field_id in enumerate(field_ids):
                environment[field_id] = float(flat_values[field_index, cell])
            for field_index, node in enumerate(expressions_ast):
                try:
                    derivatives[field_index, cell] = expressions._eval(node, environment)
                except expressions.ExpressionError as exc:
                    raise ValueError(
                        f"reaction for field {field_ids[field_index]!r} failed at time {time_float}: {exc}") from exc
        if spatial is not None:
            with np.errstate(over='ignore', invalid='ignore'):
                for field_index, operator in enumerate(diffusion_operators):
                    if operator is not None:
                        derivatives[field_index] += operator @ flat_values[field_index]
        if not np.isfinite(derivatives).all():
            raise ValueError(f'PDE RHS produced a non-finite derivative at time {time_float}')
        return derivatives.ravel(order='C')

    sparsity = _jacobian_sparsity(info, spatial, size)
    options = dict(
        fun=rhs,
        t_span=(0.0, s['duration']),
        y0=initial,
        method=s['method'],
        t_eval=expected_times,
        rtol=s['rtol'],
        atol=s['atol'],
    )
    if s['method'] == 'BDF':
        options['jac_sparsity'] = sparsity

    try:
        solution = solve_ivp(**options)
    except Exception as exc:
        if work_exhausted:
            raise c.WorkLimit(
                f'PDE RHS work budget exceeded: {max_rhs} evaluations allowed') from exc
        if isinstance(exc, c.WorkLimit):
            raise
        if isinstance(exc, ValueError):
            raise
        raise RuntimeError(f'PDE integration failed: {exc}') from exc

    if work_exhausted:
        raise c.WorkLimit(
            f'PDE RHS work budget exceeded: {max_rhs} evaluations allowed')

    if (getattr(solution, 'success', False) is not True
            or getattr(solution, 'status', None) != 0):
        message = getattr(solution, 'message', 'solver reported failure')
        raise RuntimeError(f'PDE integration failed: {message}')
    try:
        times = np.asarray(solution.t, dtype=float)
        states = np.asarray(solution.y, dtype=float)
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError('PDE solver returned incomplete times/state') from exc
    if (times.shape != expected_times.shape
            or states.shape != (nfields * size, s['samples'])
            or not np.array_equal(times, expected_times)
            or not np.isfinite(times).all()
            or not np.isfinite(states).all()):
        raise RuntimeError('PDE solver output is incomplete or non-finite')

    values = states.T.reshape(s['samples'], nfields, *shape)
    volume = float(np.prod(info['spacing']))
    mass = values.reshape(s['samples'], nfields, size).sum(axis=2) * volume
    nfev = _solver_count(solution, 'nfev')
    njev = _solver_count(solution, 'njev')
    nlu = _solver_count(solution, 'nlu')
    return {
        'profile': PROFILE,
        'times': expected_times.tolist(),
        'axes': {name: axis.tolist() for name, axis in axes.items()},
        'spacing': list(info['spacing']),
        'field_ids': field_ids,
        'values': values.tolist(),
        'mass': mass.tolist(),
        'grid_kind': 'cell_centered',
        'time_integration': f"method_of_lines_{s['method']}",
        'solver': {
            'method': s['method'],
            'success': True,
            'nfev': nfev,
            'njev': njev,
            'nlu': nlu,
            'rhs_evaluations': int(rhs_evaluations),
        },
    }


def _numeric_array(value, shape):
    if not isinstance(value, (list, np.ndarray)):
        return None
    try:
        array = np.asarray(value)
    except (TypeError, ValueError):
        return None
    if array.dtype.kind not in ('i', 'u', 'f'):
        return None
    if array.shape != shape:
        return None
    if any(isinstance(item, (bool, np.bool_))
           for item in np.asarray(value, dtype=object).flat):
        return None
    return array.astype(float)


def checks(model, s, result):
    info = _validated(model)
    s = config(s)
    shape = info['shape']
    nfields = len(info['fields'])
    size = math.prod(shape)
    samples = s['samples']
    expected_times = np.linspace(0.0, s['duration'], samples)
    expected_ids = [item['id'] for item in info['fields']]
    required_result = {'profile', 'times', 'axes', 'spacing', 'field_ids',
                       'values', 'mass', 'grid_kind', 'time_integration', 'solver'}
    required_solver = {'method', 'success', 'nfev', 'njev', 'nlu',
                       'rhs_evaluations'}
    result_dict = result if isinstance(result, dict) else {}
    solver = result_dict.get('solver')
    solver_dict = solver if isinstance(solver, dict) else {}
    complete = (required_result <= set(result_dict)
                and required_solver <= set(solver_dict))

    values = _numeric_array(result_dict.get('values'),
                            (samples, nfields, *shape))
    mass = _numeric_array(result_dict.get('mass'), (samples, nfields))
    times = _numeric_array(result_dict.get('times'), (samples,))
    values_finite = values is not None and np.isfinite(values).all()
    mass_finite = mass is not None and np.isfinite(mass).all()
    times_finite = times is not None and np.isfinite(times).all()
    shape_finite = bool(complete and values_finite and mass_finite and times_finite)

    axes_result = result_dict.get('axes')
    axes_ok = isinstance(axes_result, dict) and set(axes_result) == set(info['axes'])
    if axes_ok:
        for name, expected_axis in info['axes'].items():
            actual_axis = _numeric_array(axes_result.get(name), expected_axis.shape)
            if actual_axis is None or not np.isfinite(actual_axis).all() \
                    or not np.array_equal(actual_axis, expected_axis):
                axes_ok = False
                break
    spacing_result = _numeric_array(result_dict.get('spacing'),
                                    (info['dimension'],))
    spacing_ok = (spacing_result is not None
                  and np.all(np.isfinite(spacing_result))
                  and np.array_equal(spacing_result, np.asarray(info['spacing'])))
    geometry_ok = bool(axes_ok and spacing_ok
                       and result_dict.get('grid_kind') == 'cell_centered'
                       and result_dict.get('profile') == PROFILE)

    field_ids_ok = (isinstance(result_dict.get('field_ids'), list)
                    and result_dict.get('field_ids') == expected_ids)
    time_samples_ok = bool(
        times_finite and np.array_equal(times, expected_times)
        and result_dict.get('time_integration') == f"method_of_lines_{s['method']}")

    initial_ok = False
    if values_finite:
        initial_ok = True
        for index, item in enumerate(info['fields']):
            scale = max(1.0, float(np.max(np.abs(item['initial']))))
            if not np.allclose(values[0, index], item['initial'],
                               rtol=0.0, atol=1e-12 * scale):
                initial_ok = False
                break

    volume = float(np.prod(info['spacing']))
    recomputed_mass = None
    mass_integral_ok = False
    if values_finite:
        recomputed_mass = (values.reshape(samples, nfields, size).sum(axis=2)
                           * volume)
        if mass is not None:
            mass_scale = max(1.0, float(np.max(np.abs(recomputed_mass))))
            mass_integral_ok = bool(
                np.allclose(mass, recomputed_mass, rtol=1e-9,
                            atol=1e-12 * mass_scale))

    count_names = ('nfev', 'njev', 'nlu', 'rhs_evaluations')
    counts_ok = all(type(solver_dict.get(name)) is int
                    and solver_dict.get(name) >= 0 for name in count_names)
    solver_ok = bool(counts_ok
                     and solver_dict.get('method') == s['method']
                     and solver_dict.get('success') is True
                     and solver_dict.get('nfev', 0) > 0
                     and solver_dict.get('rhs_evaluations', 0) > 0)
    work_ok = bool(solver_ok
                   and solver_dict['rhs_evaluations'] <= s['max_rhs_evaluations']
                   and solver_dict['nfev'] <= solver_dict['rhs_evaluations'])

    announced_tolerances = []
    nonnegative_ok = True
    declared_nonnegative = [item for item in info['fields'] if item['nonnegative']]
    if declared_nonnegative and not values_finite:
        nonnegative_ok = False
    elif values_finite:
        for index, item in enumerate(info['fields']):
            if not item['nonnegative']:
                continue
            scale = max(1.0, float(np.max(np.abs(values[:, index]))),
                        float(np.max(np.abs(item['initial']))))
            tolerance = max(1e-10, 10.0 * s['atol'], 10.0 * s['rtol'] * scale)
            announced_tolerances.append(tolerance)
            if np.any(values[:, index] < -tolerance):
                nonnegative_ok = False

    check_items = [
        c.check('shape_finite', shape_finite,
                'Complete finite values/mass/times with the declared field and grid shape.'),
        c.check('field_ids', field_ids_ok, 'Field order and identifiers match the model.'),
        c.check('grid_geometry', geometry_ok,
                'Cell-centred axes, spacing and profile match the declared Cartesian grid.'),
        c.check('time_samples', time_samples_ok,
                'Requested sample times and method-of-lines label are exact.'),
        c.check('initial_state', initial_ok, 'Saved initial state is the declared initial field.'),
        c.check('spatial_integral', mass_integral_ok,
                'Mass was recomputed as the spatial integral of state, not biological mass.'),
        c.check('solver_metadata', solver_ok,
                'Solver success, method and nonnegative integer work counts are present.'),
        c.check('work_budget', work_ok,
                'Actual RHS calls, including finite-difference Jacobian calls, stay within budget.'),
    ]
    if declared_nonnegative:
        message = ('Declared nonnegative fields stay above a floating tolerance without clipping.'
                   if nonnegative_ok else
                   'A declared nonnegative field is negative beyond the floating tolerance.')
        if announced_tolerances:
            message += f" Maximum tolerance: {max(announced_tolerances):.3g}."
        check_items.append(c.check('nonnegative', nonnegative_ok, message))
    else:
        check_items.append(c.check('nonnegative', True,
                                   'No field declared nonnegative; signed values are permitted.'))

    closed = info['boundary'] in ('no_flux', 'periodic')
    if closed and all(item['reaction_is_zero'] for item in info['fields']):
        conservation_ok = bool(mass_finite and values_finite)
        if conservation_ok:
            for index in range(nfields):
                reference = mass[0, index]
                state_scale = volume * float(np.max(
                    np.sum(np.abs(values[:, index].reshape(samples, size)), axis=1)))
                scale = max(1.0, state_scale,
                            float(np.max(np.abs(mass[:, index]))))
                if not np.allclose(mass[:, index], reference, rtol=1e-8,
                                   atol=1e-8 * scale):
                    conservation_ok = False
                    break
        check_items.append(c.check(
            'mass_conservation', conservation_ok,
            'Closed boundaries with a strictly constant-zero reaction conserve the state integral.'))
    return check_items
