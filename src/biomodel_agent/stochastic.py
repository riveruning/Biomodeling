"""Well-mixed integer mass-action networks, Gillespie direct SSA.

Rates are stochastic constants: a_j(n)=c_j product(comb(n_i,nu_ij)).
They are NOT deterministic concentration rate constants; no implicit conversion.
"""
import math
import numpy as np
from . import model_contracts as c

MODEL_TYPE = 'stochastic'
PROFILE = 'mass_action_ssa_v1'
LIMITATIONS = ['Well-mixed time-homogeneous elementary reactions of order 0, 1 or 2 only.',
               'Stochastic constants and molecule counts only; concentration/volume conversion is not inferred.',
               'Finite seeded ensembles are not distributional validation or confidence intervals.']


def validate(model):
    c.base(model, MODEL_TYPE, {'species', 'reactions', 'rate_convention'}, {'time', 'state'})
    if model['rate_convention'] != 'stochastic_combinations' or model['units']['state'] != 'molecule_count':
        raise ValueError('SSA requires stochastic_combinations rates and molecule_count state')
    if not isinstance(model['species'], list) or not 1 <= len(model['species']) <= 32:
        raise ValueError('SSA requires 1..32 species')
    ids = []
    for s in model['species']:
        c.fields(s, {'id', 'initial'}); ids.append(c.identifier(s['id']))
        c.integer(s['initial'], 0, 10**9, 'molecule count')
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate species')
    if not isinstance(model['reactions'], list) or not 1 <= len(model['reactions']) <= 64:
        raise ValueError('SSA requires 1..64 reactions')
    reaction_ids = []
    for r in model['reactions']:
        c.fields(r, {'id', 'reactants', 'products', 'rate'}); reaction_ids.append(c.identifier(r['id']))
        c.number(r['rate'], 0, 1e9, 'stochastic rate')
        for side in ('reactants', 'products'):
            if not isinstance(r[side], dict) or set(r[side]) - set(ids):
                raise ValueError('unknown reaction species')
            for v in r[side].values():
                c.integer(v, 1, 2, 'stoichiometry')
        if sum(r['reactants'].values()) > 2 or sum(r['products'].values()) > 2 or r['reactants'] == r['products']:
            raise ValueError('only non-null elementary reactions with at most two reactants/products')
    if len(reaction_ids) != len(set(reaction_ids)):
        raise ValueError('duplicate reaction id')
    return model


def config(value=None):
    s = c.settings(value, dict(timeout_s=15., seed=0, duration=10., samples=51, trajectories=16, max_events=100000))
    c.number(s['duration'], 1e-12, 1e6, 'duration')
    c.integer(s['samples'], 2, 1001, 'samples'); c.integer(s['trajectories'], 1, 512, 'trajectories')
    c.integer(s['max_events'], 1, 1000000, 'max_events')
    return s


def compatible(model, s):
    if len(model['species']) * s['samples'] * s['trajectories'] > 500000:
        raise ValueError('SSA output budget exceeded')


def propensities(model, counts):
    return np.asarray([r['rate'] * math.prod(math.comb(int(counts[k]), n) if counts[k] >= n else 0
                       for k, n in r['reactants'].items()) for r in model['reactions']], dtype=float)


def execute(model, s):
    rng = np.random.default_rng(s['seed'])
    ids = [v['id'] for v in model['species']]
    times = np.linspace(0, s['duration'], s['samples'])
    data = []; events = 0; reaction_counts = []
    for _ in range(s['trajectories']):
        counts = {v['id']: v['initial'] for v in model['species']}
        t = 0.; row = [[counts[k] for k in ids]]; sample = 1
        counts_by_reaction = [0]*len(model['reactions'])
        while sample < len(times):
            a = propensities(model, counts); cumulative = np.cumsum(a); total = float(cumulative[-1])
            if not math.isfinite(total):
                raise ArithmeticError('non-finite propensity')
            if total == 0:
                row.extend([[counts[k] for k in ids] for _ in range(sample, len(times))]); break
            next_t = t + float(rng.exponential(1. / total))
            if next_t <= t:
                raise ArithmeticError('SSA clock no longer advances')
            # State at a sample is the most recent post-event state (right continuous).
            while sample < len(times) and times[sample] < next_t:
                row.append([counts[k] for k in ids]); sample += 1
            if sample == len(times):
                break
            if events >= s['max_events']:
                raise c.WorkLimit('SSA total event budget exhausted')
            index = min(int(np.searchsorted(cumulative, rng.random() * total, side='right')), len(a)-1)
            r = model['reactions'][index]
            for k in ids:
                counts[k] += r['products'].get(k, 0) - r['reactants'].get(k, 0)
                if not 0 <= counts[k] <= 10**9:
                    raise ArithmeticError('molecule count outside bounded state space')
            t = next_t; events += 1; counts_by_reaction[index] += 1
        data.append(row); reaction_counts.append(counts_by_reaction)
    arr = np.asarray(data, dtype=float)
    return dict(times=times.tolist(), species=ids, trajectories=data, event_count=events,
                mean=arr.mean(axis=0).tolist(), variance=arr.var(axis=0, ddof=0).tolist(), reaction_counts=reaction_counts,
                variance_convention='population descriptive variance, ddof=0')


def checks(model, s, r):
    a = np.asarray(r['trajectories'])
    shape = (s['trajectories'], s['samples'], len(model['species']))
    finite = a.shape == shape and np.isfinite(a).all()
    rc = np.asarray(r['reaction_counts'])
    ids = [v['id'] for v in model['species']]
    S = np.asarray([[reaction['products'].get(k,0)-reaction['reactants'].get(k,0) for k in ids] for reaction in model['reactions']])
    balances = (rc.shape==(s['trajectories'],len(model['reactions'])) and np.isfinite(rc).all()
                and (rc>=0).all() and (rc==np.floor(rc)).all() and (rc<=s['max_events']).all())
    balances = balances and finite and np.array_equal(a[:,-1], np.array([v['initial'] for v in model['species']])+rc @ S)
    return [c.check('shape_finite', finite, 'All requested trajectories and samples are finite.'),
            c.check('integer_counts', finite and (a >= 0).all() and (a <= 10**9).all() and (a == np.floor(a)).all(), 'Nonnegative bounded integer molecule counts.'),
            c.check('initial_state', finite and np.array_equal(a[:, 0], np.tile([v['initial'] for v in model['species']], (s['trajectories'], 1))), 'Every trajectory starts at the frozen initial counts.'),
            c.check('sampling', r['species'] == [v['id'] for v in model['species']] and np.array_equal(r['times'], np.linspace(0, s['duration'], s['samples'])), 'Ordered species and uniform frozen observation times.'),
            c.check('moments', finite and np.allclose(r['mean'], a.mean(axis=0)) and np.allclose(r['variance'], a.var(axis=0)), 'Reported moments recomputed from saved paths; no distributional claim.'),
            c.check('reaction_balance', balances, 'Final counts equal initial counts plus stoichiometric changes from saved reaction totals.'),
            c.check('event_budget', balances and type(r['event_count']) is int and 0 <= r['event_count'] <= s['max_events'] and rc.sum()==r['event_count'], 'Total events bounded across all trajectories.')]
