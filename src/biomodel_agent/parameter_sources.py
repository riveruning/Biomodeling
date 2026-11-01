"""Recomputable SABIO-RK parameter transcriptions with explicit unit witnesses.

This adapter does not establish biological applicability or infer mechanisms.
Operators prepare sources before starting a new, frozen task. No network access
or expansion of an active task's source scope takes place here.
"""
from fractions import Fraction
import math

from .domain.contracts import strict_float
from .storage import canonical_bytes

VERSION = 'sabiork_parameter_v1'
# Canonical units have exact rational scales relative to seconds, molar, mol.
SCALES = {
    'dimensionless': ('dimensionless', Fraction(1)),
    'second': ('time', Fraction(1)), 'minute': ('time', Fraction(60)),
    'hour': ('time', Fraction(3600)),
    'second^-1': ('inverse_time', Fraction(1)),
    'minute^-1': ('inverse_time', Fraction(1, 60)),
    'hour^-1': ('inverse_time', Fraction(1, 3600)),
    'M': ('concentration', Fraction(1)), 'mole_per_litre': ('concentration', Fraction(1)),
    'mM': ('concentration', Fraction(1, 1000)),
    'uM': ('concentration', Fraction(1, 1000000)),
    'nM': ('concentration', Fraction(1, 1000000000)),
    'mole': ('amount', Fraction(1)), 'millimole': ('amount', Fraction(1, 1000)),
}
ALIASES = {'min^(-1)': 'minute^-1', 's^(-1)': 'second^-1', 'h^(-1)': 'hour^-1',
           's^-1': 'second^-1', '1/s': 'second^-1', 'min^-1': 'minute^-1',
           's': 'second', 'min': 'minute', 'mol': 'mole', 'µM': 'uM'}


def conversion(value, source_unit, target_unit):
    """No offsets, inferred volumes, normalized-value shortcuts or coercion."""
    value = strict_float(value, 'native parameter value')
    source = ALIASES.get(source_unit, source_unit) if isinstance(source_unit, str) else None
    target = ALIASES.get(target_unit, target_unit) if isinstance(target_unit, str) else None
    if source not in SCALES or target not in SCALES:
        raise ValueError('unsupported parameter unit; explicit supported units are required')
    dimension, scale = SCALES[source]
    if dimension != SCALES[target][0]:
        raise ValueError('incompatible dimensions; no inferred volume or quantity conversion')
    factor = scale / SCALES[target][1]
    try:
        converted = float(Fraction(str(value)) * factor)
    except OverflowError as exc:
        raise ValueError('unit conversion overflow') from exc
    if not math.isfinite(converted) or (value != 0 and converted == 0):
        raise ValueError('nonfinite or underflowed unit conversion')
    return dict(original_value=value, original_unit=source_unit, unit=target,
                factor_numerator=factor.numerator, factor_denominator=factor.denominator,
                value=converted, rule='value * factor_numerator / factor_denominator',
                dimension=dimension)


def _text(value, field):
    if not isinstance(value, str) or not value.strip() or len(value) > 256 or any(ord(c) < 32 for c in value):
        raise ValueError('invalid native '+field)
    return value


def _native(store, source_ref):
    from .knowledge import _json
    env = store.get(source_ref)
    p = env['payload']
    if env['kind'] != 'source' or p.get('database') != 'sabiork' or p.get('parser') != 'public_knowledge_v1':
        raise ValueError('requires an imported SABIO-RK native source')
    req = store.get(p['request_ref'])
    if req['kind'] != 'knowledge_request' or req['payload']['blob_ref'] != p['blob_ref']:
        raise ValueError('native source and request blob differ')
    if p['request_ref'] not in env['refs'] or p['blob_ref'] not in env['refs'] or req['payload'].get('http_status') != 200:
        raise ValueError('native source request linkage is invalid')
    body = _json(store.get_blob(p['blob_ref']))
    response = body.get('response', {})
    docs = response.get('docs', [])
    if response.get('numFound') != 1 or len(docs) != 1 or str(docs[0].get('EntryID')) != p['record_id']:
        raise ValueError('native record identity is ambiguous')
    doc = docs[0]
    if len(p['pages']) != 1 or _json(p['pages'][0]['text']) != doc:
        raise ValueError('native source text differs from raw response')
    nested = doc.get('Json')
    if isinstance(nested, list):
        if len(nested) != 1:
            raise ValueError('ambiguous native Json records')
        nested = nested[0]
    details = _json(nested) if isinstance(nested, str) else nested
    if not isinstance(details, dict) or str(details.get('id', doc['EntryID'])) != p['record_id']:
        raise ValueError('native details identity differs')
    return p, doc, details


def _build(store, original_ref, target, parameter_index, unit):
    from .parameter_applicability import TARGET
    if not isinstance(target, str) or not TARGET.fullmatch(target) or not target.endswith('/value'):
        raise ValueError('native kinetic constants require a <symbol>/value target')
    source, doc, details = _native(store, original_ref)
    parameters = details.get('kineticlaw', {}).get('parameter', [])
    if not isinstance(parameters, list) or len(parameters) > 256:
        raise ValueError('invalid or oversized native parameter list')
    if type(parameter_index) is not int or not 0 <= parameter_index < len(parameters):
        raise ValueError('parameter_index must select an original kineticlaw.parameter index')
    param = parameters[parameter_index]
    if not isinstance(param, dict) or param.get('role') != 'Constant':
        raise ValueError('selected parameter is not a native Constant')
    value = strict_float(param.get('start_value'), 'native parameter value')
    if param.get('end_value') is not None and strict_float(param['end_value'], 'range end') != value:
        raise ValueError('native parameter is a range; cannot adopt one endpoint')
    original_unit = param.get('unit', {}).get('name')
    witness = conversion(value, original_unit, unit)
    name = _text(param.get('name'), 'parameter name')
    kind = param.get('parameter_type', {}).get('name')
    context, pointers = {}, {}
    def field(key, value, pointer):
        if value is not None and value != '' and value != '-':
            context[key] = _text(value, key)
            pointers[key] = pointer
    general = details.get('general', {})
    field('organism', general.get('organism', {}).get('name'), '/general/organism/name')
    field('reaction', details.get('reaction', {}).get('equation'), '/reaction/equation')
    field('quantity_kind', kind, '/kineticlaw/parameter/'+str(parameter_index)+'/parameter_type/name')
    field('experiment_type', general.get('experiment_type'), '/general/experiment_type')
    field('tissue', general.get('tissue', {}).get('name'), '/general/tissue/name')
    field('strain', general.get('strain', {}).get('name'), '/general/strain/name')
    enzyme = details.get('enzyme_description', {})
    field('enzyme_name', enzyme.get('enzyme_name'), '/enzyme_description/enzyme_name')
    field('enzyme_status', enzyme.get('wildtype'), '/enzyme_description/wildtype')
    field('enzyme_variant', enzyme.get('mutant_spec'), '/enzyme_description/mutant_spec')
    if enzyme.get('wildtype') == 'mutant' and 'enzyme_variant' not in context:
        raise ValueError('mutant enzyme has no explicit variant identity')
    field('quantity_species', param.get('species', {}).get('species_key'), '/kineticlaw/parameter/'+str(parameter_index)+'/species/species_key')
    conditions = details.get('experimental_conditions', {})
    if conditions.get('envvars_other'):
        raise ValueError('additional native experimental conditions are not supported')
    field('buffer', conditions.get('buffer'), '/experimental_conditions/buffer')
    for key, native_key in [('pH', 'envvar_ph'), ('temperature_c', 'envvar_temperature')]:
        item = conditions.get(native_key, {})
        if item.get('start_value') is None:
            continue
        number = strict_float(item['start_value'], key)
        if item.get('end_value') is not None and strict_float(item['end_value'], key+' range end') != number:
            raise ValueError('native condition is a range; not a single '+key)
        if key == 'temperature_c' and item.get('unit') not in ('°C', 'C', 'Celsius'):
            raise ValueError('temperature unit must explicitly identify Celsius')
        context[key] = number
        pointers[key] = '/experimental_conditions/'+native_key+'/start_value'
    # Search summaries are not used to fill missing nested conditions.
    summary_organism = doc.get('Organism')
    if summary_organism is not None and 'organism' in context:
        labels = [summary_organism] if isinstance(summary_organism, str) else summary_organism
        if labels != [context['organism']]:
            raise ValueError('native organism summary and detailed record conflict')
    normalized_check = 'not_checked'
    nunit = param.get('unit', {}).get('n_name')
    if param.get('n_start_value') is not None and ALIASES.get(nunit, nunit) in SCALES:
        expected = conversion(value, original_unit, nunit)['value']
        if not math.isclose(strict_float(param['n_start_value'], 'native normalized value'), expected, rel_tol=1e-10, abs_tol=0.):
            raise ValueError('native normalized value contradicts explicit conversion')
        normalized_check = 'consistent_with_recomputed_conversion'
    provenance = dict(version=VERSION, original_source_ref=original_ref,
                      request_ref=source['request_ref'], record_id=source['record_id'],
                      parameter_index=parameter_index, native_parameter_name=name,
                      target=target, parameter_pointer='/kineticlaw/parameter/'+str(parameter_index),
                      condition_pointers=pointers, conditions=context, conversion=witness,
                      required_context_fields=sorted(set(context) | {'organism', 'reaction', 'quantity_kind'}),
                      normalized_value_check=normalized_check,
                      origin='database_reported', scientific_support='unknown')
    symbol = target.split('/')[0]
    lines = [f'Parameter {symbol} = {witness["value"]!r}.', f'Parameter {symbol} unit: {witness["unit"]}']
    lines += [f'Parameter {symbol} context.{k}: {v}' for k, v in sorted(context.items())]
    text = '\n'.join(lines)+'\n'
    payload = dict(parser=VERSION, parser_version='1', filename=f'sabiork-{source["record_id"]}-parameter-{parameter_index}.txt',
                   url=source.get('url'), derivation=provenance, pages=[dict(number=1, text=text)],
                   warnings=['Derived transcription and explicit unit conversion, not a new measurement or scientific approval.'])
    return payload


def prepare(store, original_ref, target, parameter_index, unit):
    try:
        payload = _build(store, original_ref, target, parameter_index, unit)
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError('malformed native parameter record') from exc
    refs = (original_ref, payload['derivation']['request_ref'])
    source_ref = store.put('source', payload, refs=refs)
    return dict(source_ref=source_ref, **payload,
                notice='Explicitly add this source to a NEW task and freeze its parameter requirements; active source scopes are unchanged.')


def validate(store, source_ref):
    env = store.get(source_ref)
    p = env['payload']
    if p.get('parser') != VERSION and 'derivation' not in p:
        return None
    d = p.get('derivation', {})
    if p.get('parser') != VERSION or d.get('version') != VERSION:
        raise ValueError('unsupported parameter derivation')
    try:
        fresh = _build(store, d['original_source_ref'], d['target'], d['parameter_index'], d['conversion']['unit'])
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError('malformed native parameter derivation') from exc
    if env['kind'] != 'source' or canonical_bytes(p) != canonical_bytes(fresh):
        raise ValueError('parameter source differs from recomputed native fields or conversion')
    if set(env['refs']) != {d['original_source_ref'], d['request_ref']}:
        raise ValueError('parameter source provenance links differ')
    return d


def context_exclusion(store, source_ref, target, spec):
    """Only explicit differences exclude a typed candidate; missing stays open."""
    d = validate(store, source_ref)
    if d is None or d['target'] != target or not spec:
        return None
    from .parameter_applicability import _same
    differences = {k: dict(expected=v, actual=d['conditions'][k])
                   for k, v in spec['conditions'].items()
                   if k in d['conditions'] and not _same(d['conditions'][k], v)}
    return differences or None
