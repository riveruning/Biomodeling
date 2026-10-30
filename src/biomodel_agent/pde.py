"""One-dimensional scalar linear reaction-diffusion, uniform spatial grids.

u_t = D u_xx - decay*u. Zero Dirichlet uses nodal unknowns; zero-flux
and periodic boundaries use conservative cell-centered finite volumes.
The semidiscrete linear system is propagated with SciPy expm_multiply.
"""
import numpy as np
from scipy import sparse
from scipy.sparse.linalg import expm_multiply
from . import model_contracts as c

MODEL_TYPE = 'pde'
PROFILE = 'linear_reaction_diffusion_1d_v1'
LIMITATIONS = ['One nonnegative field on a uniform 1D interval; constant diffusion and linear decay only.',
               'Zero Dirichlet, zero flux or periodic boundaries; no nonlinear, advective or coupled PDEs.',
               'Matrix exponential advances the spatial discretization, not an exact continuum solution; spatial error needs separate assessment.']


def validate(model):
    c.base(model, MODEL_TYPE, {'length', 'diffusion', 'decay', 'boundary', 'initial'}, {'time', 'space', 'state'})
    c.number(model['length'], 1e-9, 1e6, 'length')
    c.number(model['diffusion'], 0, 1e6, 'diffusion'); c.number(model['decay'], 0, 1e6, 'decay')
    if model['boundary'] not in ('dirichlet_zero', 'no_flux', 'periodic'):
        raise ValueError('unsupported PDE boundary')
    if not isinstance(model['initial'], list) or not 3 <= len(model['initial']) <= 256:
        raise ValueError('PDE requires 3..256 grid values')
    for v in model['initial']:
        c.number(v, 0, 1e9, 'initial field')
    if model['boundary'] == 'dirichlet_zero' and (model['initial'][0] != 0 or model['initial'][-1] != 0):
        raise ValueError('zero Dirichlet initial boundary values must be zero')
    return model


def config(value=None):
    s = c.settings(value, dict(timeout_s=15., duration=1., samples=21))
    c.number(s['duration'], 1e-12, 1e6, 'duration'); c.integer(s['samples'], 2, 1001, 'samples')
    return s


def grid(model):
    n = len(model['initial']); L = model['length']
    dx = L / (n-1 if model['boundary'] == 'dirichlet_zero' else n)
    x = np.linspace(0, L, n) if model['boundary'] == 'dirichlet_zero' else (np.arange(n)+.5)*dx
    return x, dx


def compatible(model, s):
    _, dx = grid(model)
    if model['diffusion'] * s['duration'] / dx**2 > 1e6 or model['decay'] * s['duration'] > 1e6:
        raise ValueError('PDE stiffness/work envelope exceeded')
    if len(model['initial']) * s['samples'] > 250000:
        raise ValueError('PDE output budget exceeded')


def operator(model):
    _, dx = grid(model); n = len(model['initial'])
    interior = model['boundary'] == 'dirichlet_zero'
    size = n-2 if interior else n
    A = sparse.diags([np.ones(size-1), -2*np.ones(size), np.ones(size-1)], [-1, 0, 1], format='lil')
    if model['boundary'] == 'no_flux':
        A[0, 0] = -1.; A[-1, -1] = -1.
    elif model['boundary'] == 'periodic':
        A[0, -1] = 1.; A[-1, 0] = 1.
    return A.tocsr() * (model['diffusion']/dx**2) - sparse.eye(size, format='csr') * model['decay']


def execute(model, s):
    x, dx = grid(model); u = np.asarray(model['initial'], dtype=float)
    interior = model['boundary'] == 'dirichlet_zero'
    values = expm_multiply(operator(model), u[1:-1] if interior else u, start=0., stop=s['duration'], num=s['samples'])
    if interior:
        values = np.pad(values, ((0, 0), (1, 1)))
    weights = np.ones(len(x))*dx
    if interior:
        weights[[0, -1]] *= .5
    return dict(times=np.linspace(0, s['duration'], s['samples']).tolist(), x=x.tolist(), values=values.tolist(),
                mass=(values @ weights).tolist(), grid_kind='nodal' if interior else 'cell_centered', dx=dx,
                time_integration='matrix_exponential_of_spatial_discretization')


def checks(model, s, r):
    a = np.asarray(r['values']); x, dx = grid(model)
    valid = a.shape == (s['samples'], len(x)) and np.isfinite(a).all()
    tol = 1e-9 * max(1., max(model['initial']))
    weights = np.ones(len(x))*dx
    if model['boundary'] == 'dirichlet_zero':
        weights[[0, -1]] *= .5
    mass = a @ weights if valid else np.array([])
    mass_ok = valid and np.allclose(r['mass'], mass, rtol=1e-9, atol=tol*model['length'])
    if model['boundary'] == 'dirichlet_zero':
        boundary_ok = valid and np.all(a[:, [0, -1]] == 0)
        balance_ok = valid and (np.diff(mass) <= tol*model['length']).all()
    else:
        boundary_ok = valid  # zero flux/periodicity are encoded in the spatial operator, not boundary equality of cell centers
        expected = mass[0]*np.exp(-model['decay']*np.asarray(r['times'])) if valid else []
        balance_ok = valid and np.allclose(mass, expected, rtol=1e-8, atol=tol*model['length'])
    return [c.check('shape_finite', valid, 'Requested finite field trajectory.'),
            c.check('nonnegative', valid and (a >= -tol).all(), 'Nonnegative field within declared floating tolerance; values are not clipped.'),
            c.check('initial_state', valid and np.allclose(a[0], model['initial'], rtol=0, atol=tol), 'Frozen initial field.'),
            c.check('grid_time', np.array_equal(r['x'], x) and r['dx'] == dx and np.array_equal(r['times'], np.linspace(0, s['duration'], s['samples'])) and r['grid_kind'] == ('nodal' if model['boundary']=='dirichlet_zero' else 'cell_centered'), 'Frozen spatial convention and sample times.'),
            c.check('boundary', boundary_ok, 'Zero Dirichlet samples or conservative periodic/no-flux operator.'),
            c.check('mass_balance', mass_ok and balance_ok, 'Mass decreases with absorbing boundaries, or follows exact linear decay with closed boundaries.')]
