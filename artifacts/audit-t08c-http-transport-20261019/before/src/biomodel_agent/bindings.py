"""Bounded, deterministic text-to-target checks, not scientific endorsement.

Only direct declarations and restricted expressions are recognized. Everything
else remains unknown. No execution, numeric probing, or language model is used.
"""
from __future__ import annotations

import ast
from fractions import Fraction
import math
import re

from . import expressions

VERSION = 1
IDENT = r'[A-Za-z_][A-Za-z0-9_]*'
NUMBER = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
MAX_TERMS = 128
MAX_BITS = 4096
MAX_POWER = 8
MAX_DEGREE = 64


def draft_targets(draft):
    result = {p.id+'/value': p.value for p in draft.parameters}
    result.update({s.id+'/initial': s.initial for s in draft.states})
    result.update({'rule:'+r.state+'/expression': r.expression for r in draft.rate_rules})
    result.update({'reaction:'+r.id+'/rate': r.rate for r in draft.reactions})
    result.update({'derived:'+d.id+'/expression': d.expression for d in draft.derived})
    return result


def draft_symbols(draft):
    return {v.id for group in (draft.states, draft.parameters, draft.derived) for v in group} | {draft.compartment.id, 'time'}


def sbml_targets(sbml):
    """Read expected values from executable SBML, never from claimed bindings."""
    import libsbml
    document = libsbml.readSBMLFromString(sbml)
    model = document.getModel()
    if model is None:
        return {}, set()
    assignments = {r.getVariable() for r in model.getListOfRules() if r.isAssignment()}
    rates = {r.getVariable() for r in model.getListOfRules() if r.isRate()}
    result = {}
    names = {'time'}
    for sp in model.getListOfSpecies():
        names.add(sp.getId())
        result[sp.getId()+'/initial'] = (sp.getInitialAmount() if sp.isSetInitialAmount()
                                       else sp.getInitialConcentration() if sp.isSetInitialConcentration() else None)
    for p in model.getListOfParameters():
        names.add(p.getId())
        if p.getId() not in assignments:
            kind = '/initial' if not p.getConstant() or p.getId() in rates else '/value'
            result[p.getId()+kind] = p.getValue() if p.isSetValue() else None
    for c in model.getListOfCompartments():
        names.add(c.getId())
    for rule in model.getListOfRules():
        if not rule.isAlgebraic():
            prefix = 'derived:' if rule.isAssignment() else 'rule:'
            result[prefix+rule.getVariable()+'/expression'] = libsbml.formulaToL3String(rule.getMath())
    for reaction in model.getListOfReactions():
        kinetic = reaction.getKineticLaw()
        result['reaction:'+reaction.getId()+'/rate'] = (libsbml.formulaToL3String(kinetic.getMath()) if kinetic else None)
    return result, names


def _bounded(poly):
    poly = {key: value for key, value in poly.items() if value}
    if len(poly) > MAX_TERMS or any(max(v.numerator.bit_length(), v.denominator.bit_length()) > MAX_BITS for v in poly.values()):
        raise ValueError('polynomial normalization budget exceeded')
    return poly


def _sum(a, b, sign=1):
    out = dict(a)
    for key, value in b.items():
        out[key] = out.get(key, Fraction(0)) + sign*value
    return _bounded(out)


def _mul(a, b):
    if len(a)*len(b) > MAX_TERMS*MAX_TERMS:
        raise ValueError('polynomial multiplication budget exceeded')
    out = {}
    for left, av in a.items():
        for right, bv in b.items():
            if len(left)+len(right) > MAX_DEGREE:
                raise ValueError('polynomial degree budget exceeded')
            key = tuple(sorted(left+right))
            out[key] = out.get(key, Fraction(0)) + av*bv
            if len(out) > MAX_TERMS:
                raise ValueError('polynomial normalization budget exceeded')
    return _bounded(out)


def _polynomial(node):
    if isinstance(node, ast.Constant):
        return _bounded({(): node._binding_fraction})
    if isinstance(node, ast.Name):
        return {(node.id,): Fraction(1)}
    if isinstance(node, ast.UnaryOp):
        p = _polynomial(node.operand)
        return p if isinstance(node.op, ast.UAdd) else {k: -v for k, v in p.items()}
    if isinstance(node, ast.BinOp):
        left, right = _polynomial(node.left), _polynomial(node.right)
        if isinstance(node.op, ast.Add):
            return _sum(left, right)
        if isinstance(node.op, ast.Sub):
            return _sum(left, right, -1)
        if isinstance(node.op, ast.Mult):
            return _mul(left, right)
        if isinstance(node.op, ast.Div) and set(right) == {()} and right[()]:
            return _bounded({k: v/right[()] for k, v in left.items()})
        if isinstance(node.op, ast.Pow) and (not right or set(right) == {()}):
            power = right.get((), Fraction(0))
            if power.denominator == 1 and 0 <= power <= MAX_POWER:
                out = {(): Fraction(1)}
                for _ in range(int(power)):
                    out = _mul(out, left)
                return out
    raise ValueError('outside bounded polynomial grammar')


def _signature(text, names, symbol_map=None):
    tree = expressions.parse_expression(text, None)
    candidate = text.replace('^', '**')
    # Python AST numbers are floats. Preserve the literal's exact decimal value
    # so rounding/underflow cannot manufacture an equivalence proof.
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            literal = ast.get_source_segment(candidate, node)
            if not literal or not re.fullmatch(NUMBER, literal) or len(literal) > 256:
                raise ValueError('unsupported numeric literal')
            exponent = re.search(r'[eE]([+-]?\d+)$', literal)
            if exponent and (len(exponent[1]) > 5 or abs(int(exponent[1])) > 1000):
                raise ValueError('numeric literal budget exceeded')
            node._binding_fraction = Fraction(literal)
            node.value = str(node._binding_fraction)
    mapping = symbol_map or {}
    source_names = expressions.symbols(text)
    if len({mapping.get(n, n) for n in source_names}) != len(source_names):
        raise ValueError('symbol mapping collapses distinct source symbols')
    class Rename(ast.NodeTransformer):
        def visit_Call(self, node):
            node.func.id = 'ln' if node.func.id in ('log', 'ln') else node.func.id
            node.args = [self.visit(a) for a in node.args]
            if node.func.id == 'pow' and len(node.args) == 2:
                return ast.BinOp(left=node.args[0], op=ast.Pow(), right=node.args[1])
            return node

        def visit_Name(self, node):
            name = mapping.get(node.id, node.id)
            if name not in names:
                raise ValueError('undeclared or unmapped source symbol: '+node.id)
            return ast.copy_location(ast.Name(id=name, ctx=ast.Load()), node)
    tree = Rename().visit(tree)
    try:
        poly = _polynomial(tree.body)
        return {'kind': 'polynomial', 'terms': [[list(k), str(v)] for k, v in sorted(poly.items())]}
    except ValueError:
        return {'kind': 'syntax', 'tree': ast.dump(tree, include_attributes=False)}


def _clauses(text, start, end):
    """Bounded outside context prevents a selected substring hiding operators."""
    left, right = max(0, start-256), min(len(text), end+256)
    chunk = text[left:right]
    cursor = 0
    for sep in list(re.finditer(r'[;\n]|\.(?=\s|$)', chunk))+[None]:
        stop = sep.start() if sep else len(chunk)
        raw = chunk[cursor:stop]
        a = left+cursor+len(raw)-len(raw.lstrip())
        b = left+stop-(len(raw)-len(raw.rstrip()))
        if a < end and b > start:
            complete = not ((cursor == 0 and left > 0 and text[left-1] not in ';\n') or
                            (sep is None and right < len(text)))
            yield a, b, text[a:b], complete
        cursor = sep.end() if sep else stop


def _declaration(raw):
    # Two complete declarative forms, not a search for an embedded equation.
    # Keep the confirmation prefix inside the declaration span so a cropped
    # quote cannot discard it. Qualifiers remain outside this grammar or in
    # the RHS, where the restricted expression parser refuses them.
    intended = re.fullmatch(
        rf'(?i:(?:The\s+author\s+confirms\s+)?The\s+intended\s+equation\s+is)'
        rf'\s+d\s*({IDENT})\s*/\s*dt\s*=\s*(.+)', raw)
    if intended:
        return {'kind': 'rule', 'symbol': intended[1], 'rhs': intended[2].strip(), 'skip': 0}
    # These labels are context only; arbitrary prose/uncertainty is not stripped.
    label = re.match(r'(?i)(?:(?:Earlier|Confirmed) table:\s*|Author clarification:\s*|The author confirms\s+)', raw)
    skip = label.end() if label else 0
    body = raw[skip:]
    patterns = [
        ('initial', rf'(?i:Initial)\s+({IDENT})\s*(?:=|(?i:is))\s*(.+)'),
        ('initial', rf'({IDENT})\s+(?i:initial)\s*(?:=|(?i:is))?\s*(.+)'),
        ('initial', rf'({IDENT})\s*\(\s*0\s*\)\s*=\s*(.+)'),
        ('rule', rf'(?:(?i:Equation)\s+)?d\s*({IDENT})\s*/\s*dt\s*=\s*(.+)'),
        ('reaction', rf'(?i:Reaction)\s+({IDENT})\s+(?i:consumes)\s+{IDENT}\s+(?i:at constant rate)\s+(.+)'),
        ('reaction', rf'(?i:Reaction)\s+({IDENT})\s+(?i:rate)\s*=\s*(.+)'),
        ('derived', rf'(?i:Derived)\s+({IDENT})\s*=\s*(.+)'),
        ('value', rf'(?:(?i:Parameter|Buffer)\s+)?({IDENT})\s*(?:=|(?i:is)|(?i:value))\s*(.+)'),
    ]
    for kind, pattern in patterns:
        match = re.fullmatch(pattern, body)
        if match:
            return {'kind': kind, 'symbol': match[1], 'rhs': match[2].strip(), 'skip': skip}
    return None


def check(text, source_ref, page, start, end, target, expected, names, symbol_map=None):
    """Build a reproducible binding record from original page offsets.

    A match concerns only the stated target/value/expression in this grammar.
    It leaves meaning, units, reaction stoichiometry and scientific truth unknown.
    """
    out = {'version': VERSION, 'target': target,
           'source': {'source_ref': source_ref, 'page': page, 'start': start, 'end': end},
           'status': 'unknown', 'reason': 'unsupported declaration; researcher review required',
           'symbol_map': {}, 'mapping_evidence': [], 'declarations': [],
           'proposed': None, 'semantic_support': 'unknown'}
    def done(status, reason):
        out.update(status=status, reason=reason)
        return out
    if (any(type(v) is not int for v in (page, start, end)) or page < 1 or
            not 0 <= start < end <= len(text) or end-start > 8000):
        return done('unknown', 'invalid or over-budget citation range')
    mapping = {} if symbol_map is None else symbol_map
    reserved = set(expressions.ALLOWED_FUNCTIONS) | {'time'}
    if (not isinstance(mapping, dict) or len(mapping) > 32 or
            any(not isinstance(a, str) or not re.fullmatch(IDENT, a) or a in reserved or
                not isinstance(b, str) or b not in names or b in reserved for a, b in mapping.items()) or
            len(set(mapping.values())) != len(mapping)):
        return done('unknown', 'invalid or over-budget symbol map')
    out['symbol_map'] = dict(sorted(mapping.items()))
    clauses = list(_clauses(text, start, end))
    if len(clauses) > 64:
        return done('unknown', 'declaration count budget exceeded')
    for a, b in mapping.items():
        witnesses = []
        for lo, hi, raw, complete in clauses:
            declared = re.fullmatch(rf'(?i:Symbol)\s+{re.escape(a)}\s+(?i:maps to)\s+({IDENT})', raw)
            if complete and start <= lo and hi <= end and declared and declared[1] != b:
                return done('unknown', 'conflicting cited symbol mappings for '+a)
            if complete and start <= lo and hi <= end and re.fullmatch(rf'(?i:Symbol)\s+{re.escape(a)}\s+(?i:maps to)\s+{re.escape(b)}', raw):
                witnesses.append({'start': lo, 'end': hi, 'source_symbol': a, 'model_symbol': b})
        if not witnesses:
            return done('unknown', 'symbol map requires an explicit cited declaration for '+a)
        out['mapping_evidence'].append(witnesses[0])
    if target.startswith(('rule:', 'reaction:', 'derived:')):
        kind, ident = target.split(':', 1)
        ident = ident.split('/')[0]
    elif target.endswith(('/initial', '/value')):
        ident, kind = target.rsplit('/', 1)
    else:
        return out
    numeric = kind in ('initial', 'value')
    try:
        if numeric:
            if isinstance(expected, bool) or not isinstance(expected, (float, int)) or not math.isfinite(expected):
                return done('unknown', 'target value is unknown')
            out['proposed'] = {'kind': kind, 'value': str(Fraction(str(expected)))}
        else:
            out['proposed'] = _signature(expected, names)
    except (ValueError, TypeError, RecursionError, OverflowError):
        return done('unknown', 'target expression is outside the supported grammar or budget')
    relevant, wrong, matched, uncertain = False, False, False, False
    for lo, hi, raw, complete in clauses:
        decl = _declaration(raw)
        if not decl:
            if not re.fullmatch(rf'(?i:Symbol)\s+{IDENT}\s+(?i:maps to)\s+{IDENT}', raw):
                uncertain = True
            continue
        lo += decl.pop('skip')
        decl.update(start=lo, end=hi)
        out['declarations'].append(decl)
        if complete and not start <= lo < hi <= end:
            return done('mismatch', 'citation truncates a declaration; cite the complete statement')
        if not complete:
            uncertain = True
            continue
        mapped = mapping.get(decl['symbol'], decl['symbol'])
        # A bare assignment is a supported derived definition only when explicitly
        # cited as that target; it cannot stand in for a differential equation.
        dk = 'derived' if kind == 'derived' and decl['kind'] == 'value' else decl['kind']
        if mapped != ident or dk != kind:
            wrong = True
            continue
        relevant = True
        rhs = decl['rhs']
        if numeric:
            match = re.fullmatch(NUMBER, rhs)
            if not match:
                uncertain = True
                continue
            try:
                exponent = re.search(r'[eE]([+-]?\d+)$', rhs)
                if len(rhs) > 256 or (exponent and (len(exponent[1]) > 5 or abs(int(exponent[1])) > 1000)):
                    raise ValueError('numeric declaration budget exceeded')
                equal = Fraction(rhs) == Fraction(str(expected))
            except (ValueError, OverflowError):
                uncertain = True
                continue
        else:
            try:
                actual = _signature(rhs, names, mapping)
            except (ValueError, TypeError, RecursionError, OverflowError):
                uncertain = True
                continue
            decl['normalized'] = actual
            equal = actual == out['proposed']
            if not equal and (actual['kind'] != 'polynomial' or out['proposed']['kind'] != 'polynomial'):
                uncertain = True
                continue
        if not equal:
            return done('mismatch', 'declared value or expression differs from this target')
        matched = True
    if uncertain:
        return done('unknown', 'unsupported or incomplete declaration context; cite a complete direct declaration')
    if matched:
        return done('matched', 'direct declaration matches target within the bounded grammar')
    if wrong and not relevant:
        return done('mismatch', 'citation declares another target or declaration type')
    return out
