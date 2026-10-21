"""Adversarial text/identity checks for the bounded binding grammar."""
import pytest

from biomodel_agent import bindings


def checked(text, expected='-k*x', target='rule:x/expression', **kwargs):
    return bindings.check(text, 'a'*64, 1, 0, len(text), target, expected, {'x', 'k', 'q', 'time'}, **kwargs)


@pytest.mark.parametrize('rhs,expected', [
    ('-x*k', '-k*x'), ('-(k*x)', '-k*x'),
    ('k*(x+1)', 'k*x+k'), ('x^2', 'x*x'),
    ('x/2', '0.5*x'), ('1e-2*x', '0.01*x'), ('pow(x, 2)', 'x*x'),
    ('exp(-k*x)', 'exp(-k*x)'), ('log(x)', 'ln(x)'),
])
def test_provable_identities(rhs, expected):
    result = checked('Equation dx/dt = '+rhs+'.', expected)
    assert result['status'] == 'matched'
    assert result['semantic_support'] == 'unknown'
    for decl in result['declarations']:
        assert decl['start'] == 0 and decl['end'] == len('Equation dx/dt = '+rhs)


@pytest.mark.parametrize('rhs,expected,status', [
    ('k*x*x', 'k*x', 'mismatch'), ('-k*x', 'k*x', 'mismatch'),
    ('sin(x)^2+cos(x)^2', '1', 'unknown'),
    ('x/(x+1)', '1-1/(x+1)', 'unknown'),
    ('(x+1)^1000000', 'x', 'unknown'),
    ('1e-400*x', '0', 'mismatch'),
    ('0.100000000000000005*x', '0.1*x', 'mismatch'),
    ('exp(0.100000000000000005)', 'exp(0.1)', 'unknown'),
    ('__import__("os").system("echo forbidden")', '1', 'unknown'),
])
def test_unproved_expressions_never_match(rhs, expected, status):
    assert checked('Equation dx/dt = '+rhs+'.', expected)['status'] == status


@pytest.mark.parametrize('text,status', [
    ('Parameter k = 4e-1.', 'matched'), ('Parameter K = 0.4.', 'mismatch'),
    ('Initial k = 0.4.', 'mismatch'), ('Parameter k = 0.4/2.', 'unknown'),
    ('Parameter k = 0.4 or 7.', 'unknown'), ('Maybe Parameter k = 0.4.', 'unknown'),
    ('Parameter k = 0.4 per second.', 'unknown'), ('Parameter k = 1e999999999.', 'unknown'),
])
def test_numeric_scope_and_unknowns(text, status):
    assert checked(text, .4, 'k/value')['status'] == status


@pytest.mark.parametrize('mapping', [[], {'X': 'missing'}, {'X': 'x', 'Y': 'x'},
    {'time': 'x'}, {'X': 'time'}, {'sin': 'x'}, {'X': True}, {'bad-name': 'x'}])
def test_invalid_mapping_cannot_assert_support(mapping):
    result = checked('Equation dx/dt = -k*x.', symbol_map=mapping)
    assert result['status'] == 'unknown'


def test_large_clause_and_expression_inputs_are_bounded():
    text = ';'.join('Symbol X maps to x' for _ in range(65))
    assert 'count budget' in checked(text)['reason']
    assert checked('x'*8001)['status'] == 'unknown'
    assert checked('Equation dx/dt = '+('+'.join('x' for _ in range(600))))['status'] == 'unknown'
    nested = 'x'
    for _ in range(25):
        nested = '('+nested+')^8'
    assert checked('Equation dx/dt = '+nested, 'x')['status'] == 'unknown'


def test_outside_context_does_not_invent_a_boundary():
    text = 'a'*300+' Parameter k = 0.4.'
    start = text.index('Parameter')
    result = bindings.check(text, 'a'*64, 1, start, len(text), 'k/value', .4, {'k'})
    assert result['status'] == 'unknown'


@pytest.mark.parametrize('text', [
    'Symbol X maps to x; Symbol X maps to q; Equation dX/dt = -k*X.',
    'Symbol X maps to x; Equation dX/dt = X-x.',
])
def test_ambiguous_mapping_cannot_prove_equality(text):
    assert checked(text, '0', symbol_map={'X': 'x'})['status'] == 'unknown'
