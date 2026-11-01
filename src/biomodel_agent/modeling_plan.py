"""Evidence -> qualitative mechanisms -> explicit, bounded ODE decisions.

This layer never infers a rate law from a qualitative relation. Plans are
immutable proposals, and compilation delegates to the existing draft gate.
"""
from __future__ import annotations

from copy import deepcopy

from . import agent_tools, bindings, evidence, modeling
from .domain.contracts import ModelArtifact, ModelDraft
from .domain.serialization import from_dict, to_dict
from .model_contracts import fields, integer, label
from .storage import canonical_bytes

PLAN = 'modeling_plan'
COMPILATION = 'modeling_plan_compilation'
PROFILE = 'explicit_ode_decisions_v1'
RELATIONS = {'promotes', 'inhibits', 'production', 'degradation', 'conversion', 'association', 'unspecified'}
ORIGINS = {'reported', 'assumed', 'unresolved'}


def _text(value, limit=1000):
    return label(value, limit)


def _identifier(value):
    from .domain.contracts import require_id
    return require_id(value, 'plan identifier')


def _items(value, maximum, name, minimum=0):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError(f'{name} must be a list with {minimum}..{maximum} entries')
    return value


def _context(value):
    if not isinstance(value, dict) or len(value) > 16:
        raise ValueError('context must be a mapping of at most 16 explicit conditions')
    for k, v in value.items():
        _text(k, 100); _text(v, 300)
    return deepcopy(value)


def _ids(values, available, name, minimum=0):
    _items(values, 64, name, minimum)
    if any(not isinstance(v, str) or v not in available for v in values) or len(set(values)) != len(values):
        raise ValueError(f'{name} must name distinct existing IDs')
    return list(values)


def validate(store, spec, source_refs):
    """Validate structure and exact source slices without writing any objects."""
    if len(canonical_bytes(spec)) > 128 * 1024:
        raise ValueError('modeling plan exceeds 128 KiB')
    fields(spec, {'schema_version', 'id', 'model_type', 'mode', 'context', 'entities',
                  'evidence', 'mechanisms', 'candidates', 'selected_candidate'})
    if type(spec['schema_version']) is not int or spec['schema_version'] != 1 or spec['model_type'] != 'ode':
        raise ValueError('only schema_version=1 and explicit model_type=ode are supported')
    _identifier(spec['id'])
    if spec['mode'] not in ('reproduction', 'exploration'):
        raise ValueError('mode must be reproduction or exploration')
    context = _context(spec['context'])
    _items(source_refs, 16, 'source_refs', 1)
    if any(not isinstance(r, str) for r in source_refs) or len(set(source_refs)) != len(source_refs):
        raise ValueError('source_refs must be distinct references')
    for ref in source_refs:
        if store.get(ref)['kind'] != 'source':
            raise ValueError('plan sources must be explicit source objects')
    entities = {}
    for item in _items(spec['entities'], 32, 'entities', 1):
        fields(item, {'id', 'label'})
        eid = _identifier(item['id']); _text(item['label'], 300)
        if eid in entities:
            raise ValueError('duplicate entity ID')
        entities[eid] = item
    anchors = {}
    for item in _items(spec['evidence'], 64, 'evidence'):
        fields(item, {'id', 'source_ref', 'page', 'start', 'end', 'quote', 'interpretation', 'conditions'})
        eid = _identifier(item['id'])
        if eid in anchors:
            raise ValueError('duplicate evidence ID')
        if item['source_ref'] not in source_refs:
            raise ValueError('evidence source outside frozen plan scope')
        integer(item['page'], 1, 100000, 'page')
        text = evidence._source_page_text(store, item['source_ref'], item['page'])
        integer(item['start'], 0, len(text), 'start'); integer(item['end'], 1, len(text), 'end')
        if item['start'] >= item['end'] or text[item['start']:item['end']] != item['quote']:
            raise ValueError('evidence quote differs from exact frozen source slice')
        if (not isinstance(item['quote'], str) or not item['quote'].strip() or len(item['quote']) > 8000
                or any(ord(c) < 32 and c not in '\n\r\t' for c in item['quote'])):
            raise ValueError('quote must be bounded source text')
        _text(item['interpretation']); _context(item['conditions'])
        anchors[eid] = item
    mechanisms = {}
    for item in _items(spec['mechanisms'], 32, 'mechanisms'):
        fields(item, {'id', 'subject', 'relation', 'object', 'evidence_ids', 'conditions', 'rationale'})
        mid = _identifier(item['id'])
        if mid in mechanisms:
            raise ValueError('duplicate mechanism ID')
        if item['subject'] not in entities or item['object'] not in entities or item['relation'] not in RELATIONS:
            raise ValueError('mechanism requires known entities and a supported qualitative relation')
        _ids(item['evidence_ids'], anchors, 'mechanism evidence_ids', 1)
        _context(item['conditions']); _text(item['rationale'])
        mechanisms[mid] = item
    candidates = []
    for item in _items(spec['candidates'], 8, 'candidates', 1):
        fields(item, {'id', 'label', 'variable_map', 'draft', 'decisions'})
        cid = _identifier(item['id']); _text(item['label'], 300)
        if any(c['id'] == cid for c in candidates):
            raise ValueError('duplicate candidate ID')
        draft = from_dict(ModelDraft, item['draft'])
        if draft.element_map or draft.hypothesis_refs or draft.parent_ref:
            raise ValueError('candidate cannot assign evidence, hypotheses or lineage references')
        if not 1 <= len(draft.states) <= 16 or len(draft.parameters) > 32 or len(draft.rate_rules) > 16:
            raise ValueError('candidate exceeds bounded ODE profile')
        if draft.reactions or draft.derived:
            raise ValueError('first decision profile supports explicit rate-rule ODEs only')
        if to_dict(draft)['compartment'] != dict(id='cell', size=1.0, unit='dimensionless'):
            raise ValueError('first decision profile requires the default unit compartment')
        mapping = item['variable_map']
        if (not isinstance(mapping, dict) or not set(mapping) <= set(entities)
                or any(not isinstance(v, str) for v in mapping.values())
                or set(mapping.values()) != {s.id for s in draft.states}
                or len(set(mapping.values())) != len(mapping)):
            raise ValueError('variable_map must map known entities one-to-one onto all draft states')
        targets = bindings.draft_targets(draft)
        seen = set()
        for decision in _items(item['decisions'], 64, 'decisions', 1):
            fields(decision, {'target', 'origin', 'evidence_ids', 'mechanism_ids', 'rationale', 'alternatives'})
            target = decision['target']
            if not isinstance(target, str) or target not in targets or target in seen or decision['origin'] not in ORIGINS:
                raise ValueError('decisions require distinct draft targets and a supported origin')
            seen.add(target)
            _ids(decision['evidence_ids'], anchors, 'decision evidence_ids')
            _ids(decision['mechanism_ids'], mechanisms, 'decision mechanism_ids')
            _text(decision['rationale'])
            for alternative in _items(decision['alternatives'], 8, 'alternatives'):
                _text(alternative)
            if decision['origin'] == 'reported' and not decision['evidence_ids']:
                raise ValueError('reported decisions require exact source evidence')
        if seen != set(targets):
            raise ValueError('every parameter, initial value and rate expression needs an explicit decision')
        candidates.append({**deepcopy(item), 'draft': to_dict(draft)})
    selected = spec['selected_candidate']
    if selected is not None and (not isinstance(selected, str) or selected not in {c['id'] for c in candidates}):
        raise ValueError('selected_candidate must be an explicit existing candidate ID or null')
    return {**deepcopy(spec), 'context': context, 'candidates': candidates}


def prepare(store, spec, source_refs, *, parent_ref=None, reason=None):
    """Freeze a new proposal; revision explicitly replaces, never mutates, scope."""
    normalized = validate(store, spec, source_refs)
    revision = 0
    if parent_ref is not None:
        parent = load(store, parent_ref)
        _text(reason)
        if parent['spec']['id'] != spec['id'] or parent['spec']['mode'] != spec['mode']:
            raise ValueError('revision must retain plan identity and execution mode')
        revision = parent['revision'] + 1
        if revision > 16:
            raise ValueError('plan revision budget of 16 exhausted')
    elif reason is not None:
        raise ValueError('revision reason requires a parent plan')
    return store.put(PLAN, dict(version=1, profile=PROFILE, spec=normalized, source_refs=list(source_refs),
        parent_ref=parent_ref, revision=revision, revision_reason=reason, scientific_support='unknown'),
        refs=tuple(source_refs) + ((parent_ref,) if parent_ref else ()))


def load(store, ref):
    obj = store.get(ref); p = obj['payload']
    fields(p, {'version', 'profile', 'spec', 'source_refs', 'parent_ref', 'revision', 'revision_reason', 'scientific_support'})
    if (obj['kind'] != PLAN or type(p['version']) is not int or p['version'] != 1
            or p['profile'] != PROFILE or p['scientific_support'] != 'unknown'):
        raise ValueError('unsupported modeling plan')
    normalized = validate(store, p['spec'], p['source_refs'])
    if canonical_bytes(normalized) != canonical_bytes(p['spec']):
        raise ValueError('plan is not in canonical contract form')
    integer(p['revision'], 0, 16, 'revision')
    expected_refs = set(p['source_refs'])
    if p['parent_ref'] is None:
        if p['revision'] != 0 or p['revision_reason'] is not None:
            raise ValueError('invalid root revision')
    else:
        # Bound recursion using the declared revision, and verify the actual chain.
        _text(p['revision_reason']); parent = store.get(p['parent_ref'])
        previous = parent['payload']
        if (parent['kind'] != PLAN or previous.get('revision') != p['revision'] - 1
                or previous.get('spec', {}).get('id') != p['spec']['id']
                or previous.get('spec', {}).get('mode') != p['spec']['mode']):
            raise ValueError('revision parent mismatch')
        load(store, p['parent_ref']); expected_refs.add(p['parent_ref'])
    if set(obj['refs']) != expected_refs:
        raise ValueError('plan dependency closure mismatch')
    return p


def inspect(store, ref):
    """Derive readiness anew; neither mechanism prose nor saved flags grant it."""
    p = load(store, ref); spec = p['spec']; selected = spec['selected_candidate']
    result = dict(plan_ref=ref, profile=PROFILE, revision=p['revision'], mode=spec['mode'],
        selected_candidate=selected, candidates=[dict(id=c['id'], label=c['label']) for c in spec['candidates']],
        state='awaiting_selection', issues=[], binding_diagnostics=[], checks=[], assumptions=[],
        scientific_support='unknown', mechanism_support='unverified_interpretation',
        next_action='Choose a mathematical candidate explicitly and prepare a new plan revision.')
    if selected is None:
        result['issues'].append(dict(code='candidate_not_selected', target=None,
            question='Which mathematical candidate should be considered? Qualitative mechanisms do not choose a rate law.'))
        return result
    candidate = next(c for c in spec['candidates'] if c['id'] == selected)
    draft = from_dict(ModelDraft, candidate['draft']); required = bindings.draft_targets(draft)
    anchors = {e['id']: e for e in spec['evidence']}; mechanisms = {m['id']: m for m in spec['mechanisms']}
    def issue(code, target, question):
        result['issues'].append(dict(code=code, target=target, question=question))
    for decision in candidate['decisions']:
        target = decision['target']; origin = decision['origin']
        if required[target] is None:
            issue('missing_value', target, f'{target}: provide a value and its source or an explicit exploratory assumption.')
        if origin == 'unresolved':
            issue('unresolved_decision', target, f'{target}: {decision["rationale"]}')
        elif origin == 'assumed':
            result['assumptions'].append(deepcopy(decision))
            if spec['mode'] == 'reproduction':
                issue('assumption_in_reproduction', target, f'{target}: a modeling assumption cannot fill a strict reproduction gap.')
        relevant = [anchors[eid] for eid in decision['evidence_ids']]
        for mid in decision['mechanism_ids']:
            mechanism = mechanisms[mid]
            if mechanism['subject'] not in candidate['variable_map'] or mechanism['object'] not in candidate['variable_map']:
                issue('unmapped_mechanism_entity', target,
                    f'{target}: map both entities of mechanism {mid} into this candidate or revise the mechanism interpretation.')
            relevant.extend(anchors[eid] for eid in mechanism['evidence_ids'])
            relevant.append(mechanism)
        for item in relevant:
            if any(spec['context'].get(k) != v for k, v in item['conditions'].items()):
                issue('condition_mismatch', target, f'{target}: evidence/mechanism {item["id"]} conditions differ or are unspecified in this task.')
        if origin == 'reported' and required[target] is not None:
            for eid in decision['evidence_ids']:
                e = anchors[eid]
                diagnostic = bindings.check(evidence._source_page_text(store, e['source_ref'], e['page']),
                    e['source_ref'], e['page'], e['start'], e['end'], target, required[target], bindings.draft_symbols(draft))
                result['binding_diagnostics'].append(diagnostic)
                if diagnostic['status'] != 'matched':
                    issue('source_binding_' + diagnostic['status'], target, f'{target}: {diagnostic["reason"]}')
    from .source_review import review_sources
    review = review_sources(store, p['source_refs'], required)
    reported = {d['target'] for d in candidate['decisions'] if d['origin'] == 'reported'}
    for problem in review['issues']:
        if spec['mode'] == 'reproduction' or problem.get('target') in reported:
            issue('source_review', problem.get('target'), problem['question'])
    static = modeling.validate_draft(draft)
    result['checks'] = [to_dict(c) for c in static.checks]
    for check in static.checks:
        if check.status == 'fail' and check.severity == 'blocking':
            # Missing values already have targeted questions. The aggregate
            # draft_valid flag adds no independent structural information.
            if check.code not in ('initial_missing', 'parameter_missing', 'draft_valid'):
                issue('invalid_model', check.code, check.message)
    result['state'] = ('invalid' if any(i['code'] in ('invalid_model', 'source_binding_mismatch') for i in result['issues'])
                       else 'awaiting_input' if result['issues'] else 'ready_to_compile')
    result['next_action'] = (result['issues'][0]['question'] if result['issues'] else
        'Compile through the existing draft/evidence gate; numerical execution is a separate explicit action.')
    return result


def compile_plan(store, ref):
    readiness = inspect(store, ref); p = load(store, ref); spec = p['spec']
    result = dict(version=1, profile=PROFILE, plan_ref=ref, state=readiness['state'],
        draft_ref=None, model_ref=None, hypothesis_ref=None, source_review_ref=None,
        issues=readiness['issues'], assumptions=readiness['assumptions'], checks=readiness['checks'],
        scientific_support='unknown')
    if readiness['state'] == 'ready_to_compile':
        selected = next(c for c in spec['candidates'] if c['id'] == spec['selected_candidate'])
        anchors = {e['id']: e for e in spec['evidence']}
        citations = []
        for d in selected['decisions']:
            if d['origin'] != 'reported':
                continue
            for eid in d['evidence_ids']:
                e = anchors[eid]
                citations.append(dict(target=d['target'], **{k: e[k] for k in ('source_ref', 'page', 'start', 'end')}))
        exploration = spec['mode'] == 'exploration'
        args = dict(draft=selected['draft'], evidence=citations)
        if exploration:
            args['mechanism'] = selected['id']
        compiled = agent_tools.submit_draft(store, args, p['source_refs'],
            mode='hypothesis' if exploration else 'reproduction', allowed_mechanisms=(selected['id'],) if exploration else ())
        result.update(state=compiled['state'], checks=compiled['checks'])
        result['issues'] = [dict(code='draft_gate', target=None, question=q) for q in compiled['problems']]
        for key in ('draft_ref', 'model_ref', 'hypothesis_ref', 'source_review_ref'):
            result[key] = compiled.get(key)
    from .recovery import implementation_fingerprint
    result['implementation_sha256'] = implementation_fingerprint()
    refs = [ref, *[result[k] for k in ('draft_ref', 'model_ref', 'hypothesis_ref', 'source_review_ref') if result[k]]]
    return store.put(COMPILATION, result, refs=tuple(refs))


def compiled_model(store, ref):
    """Revalidate the plan, compiler version and actual model before execution."""
    from .recovery import implementation_fingerprint
    obj = store.get(ref); c = obj['payload']
    if (obj['kind'] != COMPILATION or type(c.get('version')) is not int or c['version'] != 1
            or c.get('profile') != PROFILE or c.get('scientific_support') != 'unknown'
            or c.get('implementation_sha256') != implementation_fingerprint()):
        raise ValueError('unsupported/stale compilation; compile a new receipt without rewriting history')
    p = load(store, c['plan_ref']); summary = inspect(store, c['plan_ref'])
    expected_refs = {c['plan_ref'], *[c[k] for k in ('draft_ref', 'model_ref', 'hypothesis_ref', 'source_review_ref') if c.get(k)]}
    if (c.get('state') != 'ready' or summary['state'] != 'ready_to_compile' or set(obj['refs']) != expected_refs
            or c.get('assumptions') != summary['assumptions'] or c.get('issues') != []):
        raise ValueError('compilation is blocked or its dependency closure differs')
    model = store.get(c['model_ref']); draft_obj = store.get(c['draft_ref'])
    if model['kind'] != 'model' or draft_obj['kind'] != 'model_draft' or c['draft_ref'] not in model['refs']:
        raise ValueError('compiled model/draft binding mismatch')
    selected = next(v for v in p['spec']['candidates'] if v['id'] == p['spec']['selected_candidate'])
    draft = from_dict(ModelDraft, draft_obj['payload'])
    source_draft = deepcopy(selected['draft'])
    source_draft['element_map'] = to_dict(draft)['element_map']
    source_draft['hypothesis_refs'] = list(draft.hypothesis_refs)
    if canonical_bytes(source_draft) != canonical_bytes(to_dict(draft)):
        raise ValueError('compiled draft differs from selected mathematical candidate')
    artifact = from_dict(ModelArtifact, model['payload'])
    expected = modeling.build_model(draft)
    if to_dict(artifact) != to_dict(expected):
        raise ValueError('actual compiled SBML differs from the selected draft')
    anchors = {e['id']: e for e in p['spec']['evidence']}
    expected_claims = {}
    for decision in selected['decisions']:
        if decision['origin'] == 'reported':
            expected_claims[decision['target']] = {
                (anchors[eid]['source_ref'], anchors[eid]['page'], anchors[eid]['start'], anchors[eid]['end'])
                for eid in decision['evidence_ids']}
    if set(draft.element_map) != set(expected_claims):
        raise ValueError('draft citations differ from reported decisions')
    for target, claim_refs in draft.element_map.items():
        actual_claims = set()
        for claim_ref in claim_refs:
            claim = store.get(claim_ref); v = claim['payload']
            if claim['kind'] != 'claim' or v.get('statement') != target:
                raise ValueError('invalid decision claim')
            actual_claims.add(tuple(v[k] for k in ('source_ref', 'page', 'start', 'end')))
        if actual_claims != expected_claims[target]:
            raise ValueError('decision evidence anchors differ from compiled claims')
    if p['spec']['mode'] == 'exploration':
        hypothesis = store.get(c['hypothesis_ref'])
        if (hypothesis['kind'] != 'hypothesis' or tuple(draft.hypothesis_refs) != (c['hypothesis_ref'],)
                or hypothesis['payload'].get('mechanism') != selected['id']
                or hypothesis['payload'].get('source_refs') != p['source_refs']):
            raise ValueError('exploration hypothesis differs from selected plan')
    elif draft.hypothesis_refs or c.get('hypothesis_ref') is not None:
        raise ValueError('reproduction cannot acquire exploratory hypotheses')
    # Recheck links against the current frozen source versions, never prose flags.
    from .application import _source_validation
    validated = _source_validation(store, artifact)
    if p['spec']['mode'] == 'reproduction' and (validated['missing'] or validated['invalid']
            or validated['source_review']['issues'] or any(d['status'] != 'matched' for d in validated['binding_diagnostics'])):
        raise ValueError('compiled reproduction source validation no longer holds')
    return p, c, artifact
