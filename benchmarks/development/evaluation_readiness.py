"""Read-only R11a inventory and review preparation for fixed, seen materials.

Composes the existing T07 review packet and T08 v2 scorer. It never runs an
agent, admits unseen materials, signs a review, or adds a scientific verdict.
"""
from __future__ import annotations

import argparse
import csv
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = 'evaluation_readiness_development_v1'
CHECKS = ('source_scope', 'equations_parameters_initials', 'units_and_conditions',
          'reference_independence', 'expected_action_and_clarification',
          'metric_applicability', 'family_and_exposure', 'permissions_and_delivery')
ANALYTIC_SOURCES = {
    'decay': ('initial', 'ffc2e454726221a03c7cd00fc621579a4b825a0c6c9857e63a61c78698e8f375'),
    'translation': ('clarified', '82ef5f29120b52c01f94f8d7ef3cf04212ab5dd086e09c5859acd008e557989f'),
    'logistic': ('clarified', '7d01cf55cb1e23f355a026303a12e8350767dcb7c61a88500bd50289e7497ed8'),
}


def analytic_reference(case, root=ROOT):
    """Hand-coded closed forms for exactly three source-bound synthetic controls."""
    from biomodel_agent.domain.contracts import ExperimentSpec
    from biomodel_agent.domain.serialization import from_dict
    if case not in ANALYTIC_SOURCES:
        raise ValueError('no analyst reference for this case')
    phase, expected_hash = ANALYTIC_SOURCES[case]
    prefix = f'benchmarks/inputs/reliability/{case}/{phase}/'
    raw = read_file(root, prefix + 'source.txt')
    if sha256(raw).hexdigest() != expected_hash:
        raise ValueError('analyst closed-form reference requires rechecking changed source')
    source = raw.decode()
    exp_raw = read_file(root, prefix + 'experiment.json')
    exp = from_dict(ExperimentSpec, v1._read(Path(root) / (prefix + 'experiment.json')))
    if exp.times[0] != 0 or exp.initial_overrides or exp.parameter_overrides or exp.segments or exp.time_unit not in ('unknown', 'minute'):
        raise ValueError('closed-form controls require t0=0, the source minute clock, and no overrides/segments')
    t = exp.times
    shared = ['Time t is in minutes. States are dimensionless abstract activities.']
    if case == 'decay':
        quotes = ['Initial A = 2.0.', 'Parameter k = 0.4.', 'Equation dA/dt = -k*A.']
        values = {'A': [2 * math.exp(-.4*x) for x in t]}
        expressions = {'A': '2*exp(-0.4*t)'}; scales = {'A': 2.}
    elif case == 'translation':
        quotes = ['Initial M = 1.0.', 'Initial P is not reported.', 'Parameter s = 0.8.',
                  'Parameter dm = 0.4.', 'Parameter kp = 0.6.', 'Parameter dp = 0.2.',
                  'Equation dM/dt = s-dm*M.', 'Equation dP/dt = kp*M-dp*P.',
                  'Author clarification (supersedes the unresolved statement above):\nInitial P = 0.2.']
        values = {'M': [2-math.exp(-.4*x) for x in t],
                  'P': [6+3*math.exp(-.4*x)-8.8*math.exp(-.2*x) for x in t]}
        expressions = {'M': '2-exp(-0.4*t)', 'P': '6+3*exp(-0.4*t)-8.8*exp(-0.2*t)'}
        scales = {'M': 2., 'P': 6.}
    else:
        quotes = ['Initial X = 0.2.', 'Parameter r = 0.8.', 'Parameter K = 2.0.',
                  'The source contains two equally authoritative alternatives: dX/dt = r*X*(1-X/K) OR dX/dt = r*X*(1-X)/K. It does not specify which was used.',
                  'Author clarification (supersedes the unresolved statement above):\nThe author confirms the intended equation is dX/dt = r*X*(1-X/K). The alternative is rejected.']
        values = {'X': [2/(1+9*math.exp(-.8*x)) for x in t]}
        expressions = {'X': '2/(1+9*exp(-0.8*t))'}; scales = {'X': 2.}
    spans = []
    for quote in shared + quotes:
        start = source.index(quote)
        spans.append(dict(path=prefix+'source.txt', source_sha256=expected_hash,
                          start=start, end=start+len(quote), quote=quote))
    return dict(kind='analyst_closed_form_development_reference', case=case, phase=phase,
                source_sha256=expected_hash, experiment_sha256=sha256(exp_raw).hexdigest(), source_spans=spans,
                expressions=expressions, series=dict(times=list(t), values=values, time_unit='minute', status='succeeded'),
                scales=scales, threshold=.001, independent_review='pending', scientific_support='unknown',
                note='Analyst-coded mathematical reference, not independent domain review or an LLM score. Initial gap stages have no trajectory reference.')


def numeric_diagnostic(case, phase, actual, root=ROOT):
    """Reuse the product numeric comparator, without modifying assessment records."""
    from biomodel_agent.validation import compare_series
    ref = analytic_reference(case, root)
    if phase != ref['phase']:
        raise ValueError('no numeric reference for an unresolved or different phase')
    comparison = compare_series(actual, ref['series'], ref['scales'], ref['threshold'])
    if comparison['time_unit_status'] != 'pass':
        raise ValueError('numeric diagnostic requires explicitly compatible time units')
    return dict(reference_sha256=digest(ref), candidate_sha256=digest(actual), comparison=comparison,
                source_support='unknown', independent_review='pending', formal_task_score='not_assigned')


def _module(name):
    spec = importlib.util.spec_from_file_location('readiness_' + name, Path(__file__).with_name(name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


reporting = _module('evaluation_reporting')
retrieval = _module('retrieval_review')
v1 = reporting.v1


def digest(value):
    return v1.digest(value)


def read_file(root, name):
    """Callers supply only fixed catalog paths; reject symlinks before reading."""
    rel = Path(name)
    if rel.is_absolute() or '..' in rel.parts:
        raise ValueError('material path must remain project-relative')
    base = Path(root).resolve()
    path = base
    for part in rel.parts:
        path /= part
        if path.is_symlink():
            raise ValueError('symlink material is outside this read-only audit contract')
    if not path.resolve().is_relative_to(base) or not path.is_file() or path.stat().st_size > 2_000_000:
        raise ValueError('material missing, outside scope, or over byte budget')
    return path.read_bytes()


def build(root=ROOT):
    root = Path(root)
    files = {}

    def bind(name):
        raw = read_file(root, name)
        files[name] = {'sha256': sha256(raw).hexdigest(), 'bytes': len(raw)}
        return raw

    def load(name):
        bind(name)
        return v1._read(root / name)

    # This explicit list does not traverse any other material pool or evalset.
    legacy = load('benchmarks/inputs/retrieval-development/cases.json')
    review_packet = retrieval.build_packet(legacy)
    for ident, phases in (('decay', ('initial',)), ('translation', ('initial', 'clarified')),
                          ('logistic', ('initial', 'clarified'))):
        for phase in phases:
            for filename in ('source.txt', 'experiment.json'):
                bind(f'benchmarks/inputs/reliability/{ident}/{phase}/{filename}')
    manifest = v1.development_manifest(root)
    for case in manifest['cases']:
        for phase in case['phases']:
            for item in phase['files']:
                bind(item['path'])
    model = bind('examples/goodwin/model.xml')
    for name in ('experiment.json', 'reference.json', 'figure2-reference/calibration.json',
                 'figure2-reference/protocol.json', 'figure2-reference/publisher-figure-2.png'):
        bind('examples/goodwin/' + name)
    cal = load('examples/goodwin/figure2-reference/calibration.json')
    if files['examples/goodwin/figure2-reference/publisher-figure-2.png']['sha256'] != cal['image_sha256']:
        raise ValueError('Goodwin image hash differs from frozen calibration')
    # Save hashes for the existing source closure, including assisted transcription.
    prefix = 'examples/goodwin/figure2-reference/source-model-bundle'
    bundle = load(prefix + '/bundle.json')
    refs = bundle['objects_and_blobs']
    if not isinstance(refs, list) or not 1 <= len(refs) <= 64 or len(set(refs)) != len(refs):
        raise ValueError('invalid bounded Goodwin source closure')
    if bundle['root_ref'] not in refs:
        raise ValueError('Goodwin source root missing from closure')
    for ref in refs:
        if not isinstance(ref, str) or len(ref) != 64 or any(c not in '0123456789abcdef' for c in ref):
            raise ValueError('invalid source closure identifier')
        name = prefix + '/objects/' + ref + '.json'
        if not (root / name).is_file():
            name = prefix + '/blobs/' + ref
        bind(name)
    saved_model = load(prefix + '/objects/' + bundle['root_ref'] + '.json')
    if saved_model['kind'] != 'model' or saved_model['payload']['sbml'].encode() != model:
        raise ValueError('Goodwin source-bound executable differs from example')
    fba_receipt = load('tests/fixtures/fba/source-receipt.json')
    bind('tests/fixtures/fba/textbook.xml')
    if files['tests/fixtures/fba/textbook.xml']['sha256'] != fba_receipt['sbml_sha256']:
        raise ValueError('public FBA fixture changed from source receipt')
    analytic = load('examples/fba-v1/analytic-reference.json')
    textbook = load('examples/fba-v1/textbook-reference.json')
    bind('examples/fba-v1/chain.json')
    if textbook['reference']['objective'] != fba_receipt['reference_objective']:
        raise ValueError('FBA scalar reference differs from source receipt')
    for name in ('agent_evaluation.py', 'evaluation_reporting.py', 'retrieval_review.py',
                 'system_comparison.py', 'evaluation_readiness.py'):
        bind('benchmarks/development/' + name)

    cards = []

    def card(ident, kind, family, paths, private, limits):
        entry = dict(id=ident, kind=kind, family=family, split='development', exposure='seen_development',
                     usage='development_only', files={p: files[p] for p in sorted(set(paths))},
                     private_rubric=private, limits=limits,
                     review={'status': 'pending_independent', 'reviewer': None, 'reviewed_at': None,
                             'evidence_refs': [], 'checks': {k: None for k in CHECKS}, 'notes': ''})
        cards.append(entry)

    for c in review_packet['cases']:
        card('retrieval--' + c['id'], 'retrieval_control', c['paper_family_id'],
             ['benchmarks/inputs/retrieval-development/cases.json'],
             dict(query=c['query'], source_spans=c['anchors'], expectation=c['expectation'],
                  allowed_clarification='none; retrieval-only control', scorer='retrieval_review_v1'),
             ['Synthetic retrieval only; not a full modeling task or independent paper.',
              'clarification_scope legacy conflict label has an unadopted AI revision candidate; independent review pending.']
             if c['id'] == 'clarification_scope' else ['Synthetic retrieval only; not a full modeling task or independent paper.'])
    for c in manifest['cases']:
        reference = analytic_reference(c['id'], root)
        card('agent--' + c['id'], 'staged_ode_task', c['family'],
             [f['path'] for p in c['phases'] for f in p['files']],
             dict(task=c['task'], phases=c['phases'], scorer=reporting.REPORTING_PROTOCOL,
                  allowed_clarification='Only the frozen clarified phase, after the initial phase; resumed and fresh are scored separately.',
                  numeric_reference=reference),
             ['Seen synthetic development task; repeats and phases are not independent tasks.',
              'Attached closed-form reference is analyst-coded; source/action and mathematical review remain independent pending tasks.'])
    goodwin_paths = [p for p in files if p.startswith('examples/goodwin/')]
    card('goodwin--figure2', 'publisher_figure_diagnostic', 'doi-10-1371-journal-pone-0069573', goodwin_paths,
         dict(reference_type='publisher image feature intervals; SciPy JSON is a separate numerical crosscheck',
              original_initials='unknown', analyst_initials={'X': .1, 'Y': .2, 'Z': .3},
              scorer='paper_comparison_protocol_v1; phase-free Y/Z extrema and period',
              allowed_clarification='none supplied; cannot invent original initials', pointwise='not_run'),
         ['Retrospective seen development example; source text was assisted transcription.',
          'X amplitude excluded; pixel bands are not confidence intervals; independent calibration/model review pending.'])
    card('fba--analytic-chain', 'lp_numeric_control', 'analytic-uptake-chain',
         ['examples/fba-v1/chain.json', 'examples/fba-v1/analytic-reference.json'],
         dict(reference=analytic['reference'], allowed_clarification='none; fixed network and bounds', scorer='FBA residual/primal-dual and scalar objective checks'),
         ['Analytic LP control; not a paper reconstruction task or LLM capability result.'])
    card('fba--textbook', 'public_lp_diagnostic', 'cobra-textbook-seen-model',
         ['tests/fixtures/fba/textbook.xml', 'tests/fixtures/fba/source-receipt.json', 'examples/fba-v1/textbook-reference.json'],
         dict(reference=textbook['reference'], allowed_clarification='none; unmodified public model', scorer='FBA residual/primal-dual and scalar objective checks'),
         ['Public already-seen model; two HiGHS methods are not independent engines.',
          'Scalar objective agreement does not certify a unique flux vector or independent scientific review.'])
    schedule = v1.schedule(manifest)
    empty_report = reporting.report(manifest, [], root)
    metrics = {w: {k: m['denominator'] for k, m in s['metrics'].items()} for w, s in empty_report['summary'].items()}
    packet = dict(protocol=PROTOCOL, version=1, scope='explicit_seen_development_subset_only',
                  out_of_scope={'independent_evalset': 'not_inspected', 'c13_c18': 'not_inspected',
                                'other_material_pools': 'not_inventoried; counts and readiness unknown'},
                  files=files, cards=cards, agent_manifest_sha256=digest(manifest),
                  workflow_plan=dict(protocol=v1.PROTOCOL, report_protocol=reporting.REPORTING_PROTOCOL,
                                     workflows=list(v1.WORKFLOWS), repeats=manifest['repeats'],
                                     base_cases=len(manifest['cases']), stage_variants=sum(len(c['phases']) for c in manifest['cases']),
                                     planned_stage_records=len(schedule), applicable_denominators_per_workflow=metrics,
                                     rubric_sha256=digest({c['id']: c['phases'] for c in manifest['cases']})),
                  evidence={'live_model_calls_this_preparation': 0, 'actual_independent_reviews_created': 0,
                            'scientific_support': 'unknown', 'formal_eligible_in_audited_subset': 0,
                            'reason': 'All audited cards are seen development; independent review pending. No approval or exposure promotion is supported.'},
                  limitations=['Card kinds have different units and must not be added as independent benchmark tasks.',
                               'Family labels are conservative metadata, not proven statistical independence or entity resolution.',
                               'Generated projections are not an OS sandbox; mount only one current stage at runtime.',
                               'No system identity, service, access permission or live-model efficacy is established.'])
    return packet, manifest, review_packet, empty_report


def check(packet, root=ROOT, *, require_formal=False):
    expected, _, _, _ = build(root)
    if digest(packet) != digest(expected):
        raise ValueError('stale or altered readiness packet: material, rubric, exposure, review, or implementation changed')
    if require_formal:
        raise ValueError('formal evaluation refused: audited materials are seen development and reviews remain pending')
    return {'status': 'development_preparation_valid', 'packet_sha256': digest(packet),
            'cards': len(packet['cards']), 'formal_eligible_in_audited_subset': 0,
            'independent_review': 'pending', 'scientific_support': 'unknown'}


def write(output, root=ROOT):
    packet, manifest, review_packet, empty_report = build(root)
    summary = check(packet, root)  # preflight before making an output directory
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)

    def save(name, obj):
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + '\n')

    save('private/readiness-packet.json', packet)
    save('private/agent-manifest.json', manifest)
    save('private/retrieval-packet.json', review_packet)
    save('private/empty-records-report-v2.json', empty_report)
    save('summary.json', summary)
    forms = [{'card_id': c['id'], 'content_sha256': digest(c), **deepcopy(c['review'])} for c in packet['cards']]
    save('private/review-forms.json', {'protocol': PROTOCOL, 'packet_sha256': digest(packet), 'forms': forms,
                                    'note': 'Unfilled independent-review worksheet; not accepted as a formal admission or score authority by this development-only tool.'})
    for card in packet['cards']:
        if card['kind'] == 'staged_ode_task':
            save('private/numeric-references/' + card['id'] + '.json', card['private_rubric']['numeric_reference'])
        detail = ['# ' + card['id'], '', 'Operator/reviewer only; never mount this file in an agent runtime.', '',
                  f"Kind: {card['kind']}; family: {card['family']}; usage: development only.",
                  'Independent review: pending. The worksheet is not a signed decision.', '', '## Frozen candidate rubric', '',
                  '```json', json.dumps(card['private_rubric'], ensure_ascii=False, indent=2), '```', '', '## Limits', '']
        detail.extend('- ' + note for note in card['limits'])
        detail.extend(['', '## Files', ''])
        detail.extend(f"- `{p}` SHA-256 `{h['sha256']}`" for p, h in card['files'].items())
        (output / 'private/review-cards').mkdir(exist_ok=True)
        (output / 'private/review-cards' / (card['id'] + '.md')).write_text('\n'.join(detail) + '\n')
    with (output / 'materials.csv').open('w', newline='') as fh:
        fields = ['id', 'kind', 'family', 'split', 'exposure', 'usage', 'review_status', 'formal_eligible']
        writer = csv.DictWriter(fh, fieldnames=fields); writer.writeheader()
        writer.writerows({**{k: c[k] for k in fields[:6]}, 'review_status': 'pending_independent',
                          'formal_eligible': False} for c in packet['cards'])
    for trial in v1.schedule(manifest):
        save('stage-projections/' + trial['id'] + '.json', v1.trial_input(manifest, trial, root))
    rows = ['# R11a development readiness', '',
            'Private/operator-only review packet. Mount only the current stage projection for an agent.', '',
            '| Card | Kind | Family | Independent review | Formal admission |', '|---|---|---|---|---|']
    rows.extend(f"| [{c['id']}](private/review-cards/{c['id']}.md) | {c['kind']} | {c['family']} | pending | development only |" for c in packet['cards'])
    rows.extend(['', 'No real-model or independent-review result is generated. See private/readiness-packet.json for limits and rubric.',
                 'Card counts are heterogeneous; phases and repetitions are not independent tasks.',
                 'Other material pools and the independent evalset are outside this audit; no global task-count claim.',
                 '', '## Next actions', '',
                 '1. Independent reviewers examine exact source spans, conditions, equations/initials, references, action rubric and family metadata; record disagreements without overwriting original annotations.',
                 '2. Resolve retrieval clarification_scope candidate and independently check attached analyst closed-form references for end-to-end ODE tasks.',
                 '3. Define separate real-system and same-model tracks, service/model versions, isolated runtime, budgets, clarification script and evidence collection before any live pilot.',
                 '4. Freeze a separately authorized prospective protocol for new materials. This development packet cannot be relabeled into an unseen test.'])
    (output / 'REVIEW.md').write_text('\n'.join(rows) + '\n')
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prepare = sub.add_parser('prepare'); prepare.add_argument('--output', type=Path, required=True)
    validate = sub.add_parser('check'); validate.add_argument('--packet', type=Path, required=True)
    validate.add_argument('--require-formal', action='store_true')
    numeric = sub.add_parser('numeric-check')
    numeric.add_argument('--case', choices=tuple(ANALYTIC_SOURCES), required=True)
    numeric.add_argument('--phase', choices=('initial', 'clarified'), required=True)
    numeric.add_argument('--trajectory', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            value = write(args.output)
        elif args.command == 'numeric-check':
            value = numeric_diagnostic(args.case, args.phase, v1._read(args.trajectory))
        else:
            value = check(v1._read(args.packet), require_formal=args.require_formal)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print('error: ' + str(exc)); return 2
    print(json.dumps(value, ensure_ascii=False))
    return 1 if args.command == 'numeric-check' and value['comparison']['status'] != 'pass' else 0


if __name__ == '__main__':
    raise SystemExit(main())
