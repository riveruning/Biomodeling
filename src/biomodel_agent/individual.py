"""Discrete lattice cells with exclusion, random activation and lineage.

Each sweep shuffles the cells present at its start. A cell dies first, then
may move, then may divide into an empty von Neumann neighbor. Newborns wait
until the next sweep. Probabilities are per activation, not continuous rates.
"""
import numpy as np
from . import model_contracts as c

MODEL_TYPE = 'individual'
PROFILE = 'lattice_cells_v1'
LIMITATIONS = ['2D square lattice, one cell per site, death/migration/division and contact exclusion only.',
               'No cell mechanics, continuous positions, signaling fields or tissue-specific parameter validation.',
               'Random sequential sweeps; per-activation probabilities are not continuous-time rates.']


def validate(model):
    c.base(model, MODEL_TYPE, {'width', 'height', 'boundary', 'initial', 'probabilities'}, {'time', 'space'})
    if model['units'] != dict(time='sweep', space='lattice_site'):
        raise ValueError('individual model units must be sweep and lattice_site')
    w = c.integer(model['width'], 2, 32, 'width'); h = c.integer(model['height'], 2, 32, 'height')
    if model['boundary'] not in ('closed', 'periodic'):
        raise ValueError('unsupported lattice boundary')
    if not isinstance(model['initial'], list) or len(model['initial']) > w*h:
        raise ValueError('invalid initial cell list')
    points = []
    for pos in model['initial']:
        if not isinstance(pos, list) or len(pos)!=2:
            raise ValueError('each cell position must be [x,y]')
        points.append((c.integer(pos[0], 0, w-1, 'x'), c.integer(pos[1], 0, h-1, 'y')))
    if len(set(points))!=len(points):
        raise ValueError('multiple cells on an initial site')
    c.fields(model['probabilities'], {'death', 'move', 'divide'})
    for p in model['probabilities'].values():
        c.number(p, 0., 1., 'probability')
    return model


def config(value=None):
    s = c.settings(value, dict(timeout_s=15., seed=0, steps=20, max_activations=100000))
    c.integer(s['steps'], 1, 256, 'steps'); c.integer(s['max_activations'], 1, 200000, 'max_activations')
    return s


def compatible(model, s):
    if model['width']*model['height']*(s['steps']+1) > 100000:
        raise ValueError('individual trajectory output budget exceeded')


def neighbors(model, pos):
    x, y = pos; w, h = model['width'], model['height']; out = []
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        a, b = x+dx, y+dy
        if model['boundary']=='periodic':
            a %= w; b %= h
        if 0<=a<w and 0<=b<h and (a,b) not in out:
            out.append((a,b))
    return out


def frame(cells):
    return [[i, *cells[i]] for i in sorted(cells)]


def execute(model, s):
    rng = np.random.default_rng(s['seed']); p = model['probabilities']
    cells = {i: tuple(pos) for i, pos in enumerate(model['initial'])}
    occupied = set(cells.values()); next_id = len(cells); activations = 0
    frames = [frame(cells)]; events = []
    for _ in range(s['steps']):
        active = list(cells); rng.shuffle(active)
        event = dict(activation_order=active.copy(), deaths=[], moves=[], births=[])
        for i in active:
            if activations >= s['max_activations']:
                raise c.WorkLimit('individual total activation budget exhausted')
            activations += 1
            if rng.random() < p['death']:
                occupied.remove(cells.pop(i)); event['deaths'].append(i); continue
            if rng.random() < p['move']:
                empty = [v for v in neighbors(model, cells[i]) if v not in occupied]
                if empty:
                    pos = empty[int(rng.integers(len(empty)))]; old = cells[i]
                    occupied.remove(old); occupied.add(pos); cells[i] = pos
                    event['moves'].append(dict(id=i, old=list(old), new=list(pos)))
            if rng.random() < p['divide']:
                empty = [v for v in neighbors(model, cells[i]) if v not in occupied]
                if empty:
                    pos = empty[int(rng.integers(len(empty)))]; cells[next_id] = pos; occupied.add(pos)
                    event['births'].append(dict(id=next_id, parent=i, position=list(pos))); next_id += 1
        events.append(event); frames.append(frame(cells))
    return dict(steps=list(range(s['steps']+1)), frames=frames, population=[len(f) for f in frames],
                events=events, activation_count=activations,
                activation_policy='shuffle_start_of_sweep_death_then_move_then_divide_newborns_wait')


def checks(model, s, r):
    frames = r['frames']; events = r['events']; next_id = len(model['initial']); count = 0
    valid = len(frames)==s['steps']+1 and len(events)==s['steps'] and r['steps']==list(range(s['steps']+1))
    population_ok = r['population']==[len(f) for f in frames]
    transition_ok = valid and frames[0]==[[i, *p] for i,p in enumerate(model['initial'])]
    occupancy_ok = True
    for f in frames:
        occupancy_ok = occupancy_ok and all(isinstance(v,list) and len(v)==3 and all(type(n)is int for n in v) and v[0]>=0 and 0<=v[1]<model['width'] and 0<=v[2]<model['height'] for v in f)
        occupancy_ok = occupancy_ok and len({v[0] for v in f})==len(f) and len({tuple(v[1:]) for v in f})==len(f)
    schedule_ok = transition_ok; probability_ok = True
    if transition_ok and occupancy_ok:
        for step, e in enumerate(events):
            cells = {v[0]:tuple(v[1:]) for v in frames[step]}; start = set(cells)
            order = e['activation_order']; count += len(order)
            # Geometry/bookkeeping are checked independently of RNG; the seed is fixed in input and replay.
            schedule_ok = schedule_ok and len(order)==len(start) and set(order)==start
            deaths = e['deaths']; moved = {v['id']:v for v in e['moves']}; parents = {v['parent']:v for v in e['births']}
            p = model['probabilities']
            probability_ok = probability_ok and not (p['death']==0 and deaths or p['death']==1 and set(deaths)!=start
                or p['move']==0 and moved or p['divide']==0 and parents)
            if (len(set(deaths))!=len(deaths) or set(deaths)-start or len(moved)!=len(e['moves']) or
                len(parents)!=len(e['births']) or set(moved)-start or set(parents)-start or set(deaths)&(set(moved)|set(parents))):
                transition_ok=False; break
            for i in order:
                if i in deaths:
                    cells.pop(i); continue
                if i in moved:
                    move=moved[i]; new=tuple(move['new'])
                    if list(cells[i])!=move['old'] or new not in neighbors(model,cells[i]) or new in cells.values():
                        transition_ok=False; break
                    cells[i]=new
                if i in parents:
                    birth=parents[i]; pos=tuple(birth['position'])
                    if birth['id']!=next_id or pos not in neighbors(model,cells[i]) or pos in cells.values():
                        transition_ok=False; break
                    cells[next_id]=pos; next_id+=1
            if frame(cells)!=frames[step+1]:
                transition_ok=False
    return [c.check('occupancy', valid and occupancy_ok, 'Unique nonnegative identities and one cell per in-bounds lattice site.'),
            c.check('activation', schedule_ok and type(r['activation_count']) is int and count==r['activation_count'] and count<=s['max_activations'] and r['activation_policy']=='shuffle_start_of_sweep_death_then_move_then_divide_newborns_wait', 'Only start-of-sweep cells activate once; newborns wait; global activation budget.'),
            c.check('lineage_transitions', transition_ok, 'Deaths, neighboring moves, parent/child identities and exclusion reconstruct each saved frame.'),
            c.check('certain_probabilities', probability_ok, 'No event forbidden by a zero probability; certain death removes all active cells.'),
            c.check('population', population_ok, 'Population recomputed from saved individual states.')]
