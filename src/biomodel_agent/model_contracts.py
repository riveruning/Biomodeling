"""Strict shared contracts for bounded native model backends (no coercion)."""
import math
import re


class WorkLimit(RuntimeError):
    """A frozen work budget was exhausted; partial output is not success."""


def fields(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or not set(required) <= set(value):
        raise ValueError(f'expected fields {sorted(required)}; optional {sorted(optional)}')


def integer(value, low, high, label='integer'):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{label} must be an integer in [{low}, {high}]')
    return value


def number(value, low=0., high=1e12, label='number'):
    if type(value) not in (float, int) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{label} must be finite in [{low}, {high}]')
    return float(value)


def label(value, limit=120):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError('expected nonempty bounded single-line text')
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,47}', value) or value in ('True', 'False', 'and', 'or', 'not'):
        raise ValueError('invalid native identifier')
    return value


def base(model, model_type, extra, units):
    fields(model, {'schema_version', 'model_type', 'id', 'units', *extra}, {'note'})
    if type(model['schema_version']) is not int or model['schema_version'] != 1 or model['model_type'] != model_type:
        raise ValueError('unsupported native model_type/schema_version')
    identifier(model['id'])
    fields(model['units'], units)
    for v in model['units'].values():
        label(v)
    if 'note' in model:
        label(model['note'], 2000)


def settings(value, defaults):
    value = {} if value is None else value
    fields(value, (), defaults)
    out = {**defaults, **value}
    number(out['timeout_s'], .1, 60., 'timeout_s')
    if 'seed' in out:
        integer(out['seed'], 0, 2**32-1, 'seed')
    return out


def check(code, passed, message):
    return dict(code=code, status='pass' if bool(passed) else 'fail', message=message)
