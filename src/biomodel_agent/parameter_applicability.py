"""Bounded checks of explicitly declared parameter metadata.

This is a structural compatibility check, not independent scientific review.
Requirements are frozen by the task caller. Candidate values and metadata are
read only from complete, target-labelled lines in an authorized source.
"""
from __future__ import annotations

import math
import re

from .domain.contracts import strict_float
from .retrieval import search_sources
from .source_review import review_sources
from .storage import canonical_bytes

TARGET = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,127}/(?:value|initial)')
FIELDS = {'organism', 'tissue', 'temperature_c', 'pH', 'model_version', 'reaction',
          'compartment', 'measurement_method', 'substrate', 'quantity_kind',
          'experiment_type', 'enzyme_name', 'enzyme_variant', 'enzyme_status',
          'buffer', 'strain', 'quantity_species'}
NUMERIC_FIELDS = {'temperature_c', 'pH'}
# Exact aliases only. Unsupported dimensional expressions remain unknown.
UNITS = {
    'dimensionless': ('dimensionless', 1.),
    'second': ('time', 1.), 's': ('time', 1.),
    'minute': ('time', 60.), 'min': ('time', 60.), 'hour': ('time', 3600.),
    'second^-1': ('inverse_time', 1.), 's^-1': ('inverse_time', 1.), '1/s': ('inverse_time', 1.),
    'minute^-1': ('inverse_time', 1./60.), 'min^-1': ('inverse_time', 1./60.),
    'hour^-1': ('inverse_time', 1./3600.),
    'M': ('concentration', 1.), 'mM': ('concentration', .001),
    'mole_per_litre': ('concentration', 1.),
    'uM': ('concentration', .000001), 'nM': ('concentration', .000000001),
    'mole': ('amount', 1.), 'mol': ('amount', 1.),
    'millimole': ('amount', .001),
}
NUMBER = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'


def requirements(value):
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 32:
        raise ValueError('parameter_requirements must map at most 32 targets to unit/conditions')
    result = {}
    for target, spec in value.items():
        if not isinstance(target, str) or not TARGET.fullmatch(target):
            raise ValueError('invalid parameter requirement target')
        if not isinstance(spec, dict) or set(spec) != {'unit', 'conditions'}:
            raise ValueError('each parameter requirement needs exactly unit and conditions')
        if not isinstance(spec['unit'], str) or spec['unit'] not in UNITS:
            raise ValueError('unsupported parameter requirement unit')
        conditions = spec['conditions']
        if not isinstance(conditions, dict) or set(conditions) - FIELDS:
            raise ValueError('unsupported parameter requirement condition')
        normalized = {}
        for key, expected in conditions.items():
            if key in NUMERIC_FIELDS:
                normalized[key] = strict_float(expected, key)
                if key == 'pH' and not 0 <= normalized[key] <= 14:
                    raise ValueError('required pH must be in 0..14')
            else:
                if not isinstance(expected, str) or not expected.strip() or len(expected) > 256:
                    raise ValueError('condition strings must contain 1..256 characters')
                normalized[key] = expected
        result[target] = {'unit': spec['unit'], 'conditions': normalized}
    return result


def _unit_status(left, right):
    if not isinstance(left, str) or left not in UNITS:
        return 'unknown'
    ldim, lscale = UNITS[left]
    rdim, rscale = UNITS[right]
    if ldim != rdim:
        return 'mismatch'
    return 'match' if lscale == rscale else 'conversion_required'


def _same(left, right):
    if isinstance(right, (float, int)) and not isinstance(right, bool):
        return isinstance(left, (float, int)) and not isinstance(left, bool) and math.isclose(left, right, rel_tol=1e-12, abs_tol=0.)
    # Categorical identifiers are deliberately exact: no synonym inference.
    return type(left) is type(right) and left == right


def assess(store, source_refs, target, spec, source_ref, page, start, end, *, persist=True):
    requirement = requirements({target: spec})[target]
    if source_ref not in source_refs:
        raise ValueError('parameter assessment source outside task')
    if any(type(v) is not int for v in (page, start, end)) or page < 1 or not 0 <= start < end or end-start > 8000:
        raise ValueError('invalid parameter assessment span')
    # Reuse the full authorized-corpus scan budget before source review.
    search_sources(store, source_refs, target.split('/')[0])
    obj = store.get(source_ref)
    from .parameter_sources import validate as validate_source, context_exclusion
    derivation = validate_source(store, source_ref)
    if derivation and derivation['target'] != target:
        raise ValueError('native parameter mapping differs from assessment target')
    text = next((p['text'] for p in obj['payload']['pages'] if p['number'] == page), None)
    if obj['kind'] != 'source' or text is None or end > len(text):
        raise ValueError('parameter assessment page or range unavailable')
    if (start and text[start-1] not in '\r\n') or (end < len(text) and text[end-1] not in '\r\n' and text[end] not in '\r\n'):
        raise ValueError('parameter assessment must include complete source lines')
    symbol, field = target.split('/')
    prefix = ('Parameter ' if field == 'value' else 'Initial ') + re.escape(symbol)
    declaration = re.compile(prefix + r'\s*=\s*(' + NUMBER + r')\.?\s*')
    metadata = re.compile(prefix + r'\s+(unit|context\.([A-Za-z_][A-Za-z0-9_]*))\s*:\s*(.+?)\s*')
    values, units, conditions, locations = [], [], {}, {}
    conflicts = []
    offset = start
    for raw in text[start:end].splitlines(keepends=True):
        line = raw.rstrip('\r\n')
        loc = dict(source_ref=source_ref, page=page, start=offset, end=offset+len(line), quote=line)
        offset += len(raw)
        match = declaration.fullmatch(line)
        if match:
            try:
                number = strict_float(float(match[1]), 'candidate value')
            except (ValueError, OverflowError):
                continue
            values.append(number)
            locations.setdefault('value', []).append(loc)
            continue
        match = metadata.fullmatch(line)
        if not match:
            continue
        key = 'unit' if match[1] == 'unit' else match[2]
        if key not in FIELDS | {'unit'}:
            continue
        value = match[3]
        if key in NUMERIC_FIELDS:
            if not re.fullmatch(NUMBER, value):
                value = None
            else:
                try:
                    value = strict_float(float(value), key)
                except (ValueError, OverflowError):
                    value = None
        locations.setdefault(key, []).append(loc)
        if key == 'unit':
            units.append(value)
        elif key in conditions and not _same(conditions[key], value):
            conflicts.append(key)
        else:
            conditions[key] = value
    if values and any(not _same(v, values[0]) for v in values[1:]):
        conflicts.append('value')
    if len(set(units)) > 1:
        conflicts.append('unit')
    # The selected excerpt cannot hide a contradictory labelled condition
    # elsewhere in the authorized corpus. This grammar has no condition-block
    # scoping, so repeated conflicting declarations require clarification.
    selected_fields = {**conditions, **({'unit': units[0]} if units else {})}
    metadata_conflicts = []
    exclusions = []
    for ref in source_refs:
        difference = context_exclusion(store, ref, target, requirement)
        if ref != source_ref and difference:
            exclusions.append(dict(source_ref=ref, reason='different_explicit_context', fields=difference))
            continue
        for entry in store.get(ref)['payload']['pages']:
            position = 0
            for raw in entry['text'].splitlines(keepends=True):
                line = raw.rstrip('\r\n')
                match = metadata.fullmatch(line)
                if match:
                    key = 'unit' if match[1] == 'unit' else match[2]
                    if key in selected_fields:
                        candidate = match[3]
                        if key in NUMERIC_FIELDS:
                            try:
                                candidate = strict_float(float(candidate), key) if re.fullmatch(NUMBER, candidate) else None
                            except (ValueError, OverflowError):
                                candidate = None
                        if not _same(candidate, selected_fields[key]):
                            conflicts.append(key)
                            metadata_conflicts.append(dict(field=key, source_ref=ref, page=entry['number'],
                                                           start=position, end=position+len(line), quote=line))
                position += len(raw)
    candidate_unit = units[0] if units else None
    unit_check = _unit_status(candidate_unit, requirement['unit'])
    checks = [{'field': 'unit', 'status': unit_check, 'expected': requirement['unit'], 'actual': candidate_unit}]
    for key, expected in requirement['conditions'].items():
        actual = conditions.get(key)
        status = 'unknown' if actual is None else 'match' if _same(actual, expected) else 'mismatch'
        checks.append(dict(field=key, status=status, expected=expected, actual=actual))
    if not values:
        checks.append(dict(field='value', status='unknown', expected='explicit finite declaration', actual=None))
    if derivation:
        # Adopting a native value requires an explicit acknowledgement of its
        # recorded context, including enzyme variants; omissions stay unknown.
        for key in derivation['required_context_fields']:
            if key not in requirement['conditions']:
                checks.append(dict(field=key, status='unknown', expected='explicit frozen context requirement', actual=conditions.get(key)))
    review = review_sources(store, source_refs, [target], parameter_requirements={target: requirement})
    statuses = {c['status'] for c in checks}
    status = ('source_conflict' if conflicts or review['issues'] else
              'mismatch' if 'mismatch' in statuses else
              'unknown' if 'unknown' in statuses else
              'conversion_required' if 'conversion_required' in statuses else 'declared_fields_match')
    payload = dict(version=1, target=target, requirements=requirement,
                   source_refs=list(source_refs), source=dict(source_ref=source_ref, page=page, start=start, end=end),
                   candidate=dict(value=values[0] if values else None, unit=candidate_unit, conditions=conditions),
                   checks=checks, conflicting_fields=sorted(set(conflicts)), metadata_conflicts=metadata_conflicts, source_review=review,
                   locations=locations, status=status, semantic_support='unknown',
                   note='Only explicitly declared fields were compared. No scientific applicability certification, parameter estimation or unit conversion was performed.')
    if derivation:
        payload.update(derivation=derivation, context_exclusions=exclusions,
                       note='Native fields and explicit conversion were recomputed; target mapping and required declared context were checked. Scientific applicability remains unknown.')
    if not persist:
        return payload
    ref = store.put('parameter_assessment', payload, refs=tuple(source_refs))
    return {'assessment_ref': ref, **payload}


def validate_proposal(store, draft, source_refs, frozen_requirements, assessment_refs):
    """Recompute assessments and match actual draft values, never trust status."""
    frozen = requirements(frozen_requirements)
    if not isinstance(assessment_refs, dict) or set(assessment_refs) - set(frozen):
        raise ValueError('parameter_assessments must map only frozen requirement targets to refs')
    targets = {p.id+'/value': (p.value, p.unit) for p in draft.parameters}
    targets.update({s.id+'/initial': (s.initial, s.unit) for s in draft.states})
    diagnostics = []
    for target, requirement in frozen.items():
        diagnostic = dict(target=target, status='invalid', reason='assessment missing', assessment_ref=assessment_refs.get(target))
        if target not in targets:
            diagnostic['reason'] = 'required target missing from draft'
        elif target in assessment_refs:
            try:
                obj = store.get(assessment_refs[target])
                p = obj['payload']
                if obj['kind'] != 'parameter_assessment' or p.get('target') != target:
                    raise ValueError('incorrect assessment kind or target')
                if p.get('requirements') != requirement or p.get('source_refs') != list(source_refs):
                    raise ValueError('assessment requirements or source scope changed; reassess')
                fresh = assess(store, source_refs, target, requirement, **p['source'], persist=False)
                if canonical_bytes(fresh) != canonical_bytes(p):
                    raise ValueError('assessment differs from recomputed source facts')
                diagnostic.update(status=fresh['status'], reason=fresh['status'])
                value, unit = targets[target]
                from .modeling import canonical_unit
                if fresh['status'] == 'declared_fields_match' and (not _same(value, fresh['candidate']['value']) or _unit_status(unit, requirement['unit']) != 'match' or canonical_unit(unit) is None):
                    diagnostic.update(status='invalid', reason='draft value or unit differs from assessed source declaration')
                if diagnostic['status'] == 'declared_fields_match' and UNITS[unit][0] == 'inverse_time':
                    from .time_units import normalize_time_unit
                    clock = normalize_time_unit(draft.time_unit)
                    expected_scale = {'second': 1., 'minute': 1./60., 'hour': 1./3600.}.get(clock)
                    if expected_scale != UNITS[unit][1]:
                        diagnostic.update(status='invalid', reason='inverse-time parameter and model clock differ or clock is unknown; explicit conversion is required')
            except (ValueError, KeyError, TypeError) as exc:
                diagnostic.update(status='invalid', reason=str(exc))
        diagnostics.append(diagnostic)
    return diagnostics
