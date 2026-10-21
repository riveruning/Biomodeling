"""Conservative checks of explicit source declarations, not general semantic review.

Detects named missing values, inconsistent repeated numeric/rate declarations,
explicit alternative ODEs, and conflicting named time-unit labels. Only an
explicit, target-specific author or attributed researcher clarification can
supersede a declaration for analysis; this never verifies paper fidelity.
Unsupported wording remains unknown; absence of flags never proves fidelity.
"""
from __future__ import annotations

import math
import re

_NUM = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
_UNIT = r'(?:per\s+(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|s|h)\b|(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|s|h)\s*(?:\^|\*\*)?\s*[-−]1\b)'
_MISSING = re.compile(r'not\s+(?:reported|specified|provided|given|available)|unknown|missing|unspecified|未(?:报告|提供|给出|指定)|缺失', re.I)
_UNRESOLVED = re.compile(r'not\s+(?:specify|specified|state|clear)|unclear|unresolved|ambiguous|which|未(?:说明|确定)|歧义', re.I)
_CONTRAST = re.compile(r'\bbut\b|conflict|contradict|inconsisten|冲突|不一致', re.I)
_AUTHOR = re.compile(r'^\s*(?:the\s+)?author\s+(?:confirms?|corrects?|clarifies?)\b', re.I)
_HEADER = re.compile(r'^\s*(?:author|researcher)\s+(?:clarification|correction)\b', re.I)


def _direct_assignment(line, match):
    """Do not mistake either side of an arithmetic expression for a literal.

    This check only recognizes literal assignments; it does not solve sums,
    products or ratios for a parameter. Leading Markdown list markers remain
    valid declaration contexts.
    """
    before = line[:match.start()].rstrip()
    if before and before[-1] in '+-−*/^=<>≤≥≠×÷' and before not in ('-', '*'):
        return False
    if 'value' in match.re.groupindex:
        unit = match.group('unit')
        # A numeric regex can otherwise stop at 1 in "1/2", or at 1 in
        # malformed "1e-". Never manufacture a numeric declaration from that
        # prefix. Recognized time units include their own negative exponent.
        value_end = match.end('value')
        if not unit and re.match(r'[\w]|\.\d', line[value_end:]):
            return False
        end = match.end('unit') if unit else value_end
        remainder = line[end:].lstrip()
        if re.match(r'[+\-−*/^×÷%<>≤≥≠]', remainder):
            return False
        # Preserve the existing explicit unit-conversion notation, such as
        # k = 0.1 per second = 6 per minute; it is not a bare numeric RHS.
        if not unit and remainder.startswith('='):
            return False
    return True


def _unit_scale(text):
    token = re.sub(r'\s+', '', text.lower())
    if re.search(r'min', token):return 60.
    if re.search(r'hour|hr|^h(?:\^|\*\*|-)|perh$', token):return 3600.
    return 1.


def _location(source_ref, page, start, quote):
    return {'source_ref':source_ref,'page':page,'start':start,'end':start+len(quote),'quote':quote}


def _researcher_assertion(store, source):
    ref = source['payload'].get('clarification_ref')
    if not ref or ref not in source['refs']:
        return None
    assertion = store.get(ref)
    p = assertion['payload']
    if (assertion['kind'] == 'researcher_clarification'
            and p.get('authority') == 'researcher_assertion'
            and p.get('blob_ref') == source['payload'].get('blob_ref')
            and p.get('reply') == ''.join(page['text'] for page in source['payload']['pages'])):
        return p
    return None


def _ordered_sources(store, source_refs):
    # Element-map iteration order is not chronology. The immutable reply lineage
    # determines which declarations preceded each researcher clarification.
    ordered, seen = [], set()
    def visit(ref):
        if ref in seen:
            return
        seen.add(ref)
        source = store.get(ref)
        if source['kind'] != 'source':
            raise ValueError('source review requires source artifacts')
        assertion = _researcher_assertion(store, source)
        if assertion:
            for prior in assertion.get('prior_source_refs', []):
                visit(prior)
        ordered.append(ref)
    for ref in source_refs:
        visit(ref)
    return ordered


def review_sources(store, source_refs, targets):
    targets = sorted(set(targets))
    missing = {}; numeric = {}; alternatives = {}; unit_labels = {}; resolved = {}
    for source_ref in _ordered_sources(store, source_refs):
        source = store.get(source_ref)
        if source['kind'] != 'source':raise ValueError('source review requires source artifacts')
        attributed = _researcher_assertion(store, source) is not None
        clarification = False
        for page in source['payload']['pages']:
            offset = 0
            for raw in page['text'].splitlines(keepends=True):
                line = raw.rstrip('\r\n')
                location = _location(source_ref,page['number'],offset,line)
                offset += len(raw)
                if not line.strip():clarification = False
                if _HEADER.search(line):clarification = True
                explicit_author = bool(_AUTHOR.search(line))
                for target in targets:
                    name,field = target.rsplit('/',1)
                    if name.startswith('rule:'):
                        state = name.removeprefix('rule:')
                        ode_pattern = r'\bd\s*(?-i:'+re.escape(state)+r')\s*/\s*dt\b'
                        odes = list(re.finditer(ode_pattern,line,re.I))
                        if not odes:continue
                        if ((explicit_author or attributed) and len(odes)==1 and re.search(ode_pattern+r'\s*=\s*\S',line,re.I)
                                and not re.search(r'\bOR\b',line,re.I) and not _UNRESOLVED.search(line)
                                and not re.search(r'\b(?:maybe|possibly|unsure)\b|\?',line,re.I)):
                            resolved[target] = location
                        elif _UNRESOLVED.search(line) and re.search(r'\bOR\b|alternatives?',line,re.I):
                            resolved.pop(target,None)
                            alternatives.setdefault(target,[]).append(location)
                        continue
                    if field not in ('initial','value'):continue
                    token = '(?-i:'+re.escape(name)+')'
                    if field == 'initial':
                        prefix = r'(?:\bInitial\s+'+token+r'\b|\b'+token+r'\s+initial\b|\b'+token+r'\s*\(\s*0\s*\))'
                    else:
                        prefix = r'(?<![\w])'+token+r'(?![\w])(?:\s+value\b)?'
                    declarations = [m for m in re.finditer(prefix+r'\s*(?:value\s*)?(?:=|:|is\b)?\s*(?P<value>'+_NUM+r')\s*(?P<unit>'+_UNIT+r')?',line,re.I)
                                    if _direct_assignment(line, m)]
                    absent = next((m for m in re.finditer(prefix+r'\s*(?:value\s*)?(?:is\b|was\b|=|:)?\s*(?:explicitly\s+)?(?:'+_MISSING.pattern+r')',line,re.I)
                                   if _direct_assignment(line, m)), None)
                    if absent:
                        missing.setdefault(target,[]).append(location)
                        resolved.pop(target,None)
                    for declaration in declarations:
                        value = float(declaration.group('value'))
                        if not math.isfinite(value):continue
                        unit = declaration.group('unit')
                        fact = {**location,'value':value,'unit':unit}
                        if ((explicit_author or clarification or attributed) and not absent and len(declarations)==1
                                and not re.search(r'\b(?:or|maybe|possibly|unclear|unsure)\b|\?',line,re.I)):
                            # A resolution is local to the named target and actual declared value.
                            resolved[target] = fact
                            numeric[target] = [fact]
                            if unit:
                                resolved[name+'/unit'] = fact
                                unit_labels.pop(name+'/unit',None)
                        else:numeric.setdefault(target,[]).append(fact)
                    if field == 'value' and _CONTRAST.search(line) and not explicit_author:
                        labels = [m for m in re.finditer(r'(?<![\w])'+token+r'(?![\w])\s+(?:as\s+|in\s+)?(?P<unit>'+_UNIT+r')',line,re.I)
                                  if _direct_assignment(line, m)]
                        label_scales = {_unit_scale(m.group('unit')) for m in labels}
                        declared_scales = {_unit_scale(m.group('unit')) for m in declarations if m.group('unit')}
                        scales = label_scales | declared_scales
                        # Fully specified equivalent conversions are checked numerically below.
                        if len(scales)>1 and (len([m for m in declarations if m.group('unit')])<2 or not label_scales.issubset(declared_scales)):
                            resolved.pop(name+'/unit',None)
                            unit_labels.setdefault(name+'/unit',[]).append(location)
    issues=[]
    def issue(target,kind,locations):
        issues.append({'target':target,'kind':kind,'question':f'{target}: please clarify {kind.replace("_"," ")}',
                       'evidence':locations})
    for target,locations in missing.items():
        if target not in resolved:issue(target,'missing_source_value',locations)
    for target,locations in alternatives.items():
        if target not in resolved:issue(target,'unresolved_equation_alternatives',locations)
    for target,locations in unit_labels.items():
        if target not in resolved:issue(target,'conflicting_time_units',locations)
    for target,facts in numeric.items():
        if len(facts)<2:continue
        with_units = [f for f in facts if f['unit']]
        values = ([f['value']/_unit_scale(f['unit']) for f in with_units]
                  if len(with_units)>=2 else [f['value'] for f in facts])
        conflict = any(not math.isclose(values[0],v,rel_tol=1e-8,abs_tol=1e-12) for v in values[1:])
        if len(with_units)>=2:
            conflict = conflict or any(not f['unit'] and not any(
                math.isclose(f['value'],u['value'],rel_tol=1e-8,abs_tol=1e-12) for u in with_units) for f in facts)
        if conflict:issue(target,'conflicting_declared_values',facts)
    return {'status':'requires_clarification' if issues else 'no_explicit_conflict_detected',
            'issues':issues,'resolutions':[{'target':k,'evidence':v} for k,v in sorted(resolved.items())],
            'semantic_support':'unknown',
            'limitations':['bounded explicit English declaration checks, not arbitrary-language or paper understanding',
                           'author corrections and researcher clarifications are assertions for analysis, not independent verification',
                           'no detected conflict does not establish semantic support']}
