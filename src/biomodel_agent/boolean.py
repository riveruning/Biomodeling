"""Boolean regulatory networks with an interpreted, closed expression grammar."""
import ast
import numpy as np
from . import model_contracts as c

MODEL_TYPE = 'boolean'
PROFILE = 'boolean_network_v1'
LIMITATIONS = ['Explicit Boolean rules and static clamps only; no rule inference or continuous kinetics.',
               'Synchronous steps or random sequential sweeps (each free node once per sweep).',
               'A repeated asynchronous state does not establish a cycle or an attractor.']


def parse_rule(text, names):
    c.label(text, 500)
    try:
        tree = ast.parse(text, mode='eval')
    except (SyntaxError, RecursionError) as exc:
        raise ValueError('invalid Boolean rule') from exc
    nodes = list(ast.walk(tree))
    if len(nodes) > 100:
        raise ValueError('Boolean rule AST budget exceeded')
    for node in nodes:
        if not isinstance(node, (ast.Expression, ast.Name, ast.Load, ast.Constant, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not)):
            raise ValueError('Boolean grammar permits names, True, False, and, or, not only')
        if isinstance(node, ast.Name) and node.id not in names:
            raise ValueError('unknown Boolean variable')
        if isinstance(node, ast.Constant) and type(node.value) is not bool:
            raise ValueError('Boolean constants must be True or False')
    return tree.body


def evaluate(node, state):
    if isinstance(node, ast.Name):
        return state[node.id]
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp):
        return not evaluate(node.operand, state)
    values = (evaluate(n, state) for n in node.values)
    return all(values) if isinstance(node.op, ast.And) else any(values)


def validate(model):
    c.base(model, MODEL_TYPE, {'nodes', 'update', 'clamps'}, {'time'})
    if model['units']['time'] != 'sweep' or model['update'] not in ('synchronous', 'random_sequential'):
        raise ValueError('Boolean time is sweep; choose synchronous or random_sequential')
    if not isinstance(model['nodes'], list) or not 1 <= len(model['nodes']) <= 64:
        raise ValueError('Boolean network requires 1..64 nodes')
    ids = []
    for n in model['nodes']:
        c.fields(n, {'id', 'initial', 'rule'}); ids.append(c.identifier(n['id']))
        if type(n['initial']) is not bool:
            raise ValueError('Boolean initial states must be true/false')
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate Boolean node')
    for n in model['nodes']:
        parse_rule(n['rule'], ids)
    if not isinstance(model['clamps'], dict) or set(model['clamps']) - set(ids):
        raise ValueError('unknown clamped node')
    for n in model['nodes']:
        if n['id'] in model['clamps'] and (type(model['clamps'][n['id']]) is not bool or n['initial'] != model['clamps'][n['id']]):
            raise ValueError('clamps must be Boolean and agree with initial state')
    return model


def config(value=None):
    s = c.settings(value, dict(timeout_s=15., seed=0, steps=20))
    c.integer(s['steps'], 1, 10000, 'steps'); return s


def compatible(model, s):
    if len(model['nodes']) * (s['steps']+1) > 250000:
        raise ValueError('Boolean output budget exceeded')


def transition(model, rules, state, order):
    new = state.copy()
    for name in order:
        new[name] = evaluate(rules[name], state if model['update'] == 'synchronous' else new)
    new.update(model['clamps'])
    return new


def diagnostics(model, rules, states):
    ids = [n['id'] for n in model['nodes']]
    final = dict(zip(ids, states[-1]))
    fixed = all(k in model['clamps'] or evaluate(rules[k], final) == final[k] for k in ids)
    cycle = None
    if model['update'] == 'synchronous':
        seen = {}
        for i, state in enumerate(states):
            key = tuple(state)
            if key in seen:
                cycle = dict(first_step=seen[key], period=i-seen[key]); break
            seen[key] = i
    return dict(terminal_fixed_point=fixed, synchronous_cycle=cycle,
                asynchronous_attractor='not_assessed' if model['update']=='random_sequential' else 'not_applicable')


def execute(model, s):
    ids = [n['id'] for n in model['nodes']]
    rules = {n['id']: parse_rule(n['rule'], ids) for n in model['nodes']}
    state = {n['id']: n['initial'] for n in model['nodes']}
    free = [k for k in ids if k not in model['clamps']]
    rng = np.random.default_rng(s['seed'])
    states = [[state[k] for k in ids]]; orders = []
    for _ in range(s['steps']):
        order = free.copy()
        if model['update'] == 'random_sequential':
            rng.shuffle(order)
        state = transition(model, rules, state, order)
        states.append([state[k] for k in ids]); orders.append(order)
    return dict(nodes=ids, steps=list(range(s['steps']+1)), states=states, update_orders=orders,
                **diagnostics(model, rules, states))


def checks(model, s, r):
    ids = [n['id'] for n in model['nodes']]
    states = r['states']; free = [k for k in ids if k not in model['clamps']]
    shape = (isinstance(states, list) and len(states)==s['steps']+1 and
             all(isinstance(row, list) and len(row)==len(ids) and all(type(v) is bool for v in row) for row in states))
    rules = {n['id']: parse_rule(n['rule'], ids) for n in model['nodes']}
    transitions = shape and states[0]==[n['initial'] for n in model['nodes']] and len(r['update_orders'])==s['steps']
    orders_ok = transitions; rng = np.random.default_rng(s['seed'])
    if transitions:
        for i, order in enumerate(r['update_orders']):
            expected = free.copy()
            if model['update'] == 'random_sequential':
                rng.shuffle(expected)
            orders_ok = orders_ok and order==expected
            prior = dict(zip(ids, states[i])); nxt = transition(model, rules, prior, order)
            transitions = transitions and [nxt[k] for k in ids]==states[i+1]
    diag = diagnostics(model, rules, states) if shape else {}
    return [c.check('shape_boolean', shape and r['nodes']==ids and r['steps']==list(range(s['steps']+1)), 'Ordered Boolean states at every sweep.'),
            c.check('update_schedule', orders_ok, 'Saved activation orders match the frozen policy and seed.'),
            c.check('rule_transitions', transitions, 'Each saved transition satisfies rules, initial state and static clamps.'),
            c.check('diagnostics', bool(diag) and all(r[k]==v for k, v in diag.items()), 'Fixed point and synchronous period recomputed; no asynchronous cycle claim.')]
