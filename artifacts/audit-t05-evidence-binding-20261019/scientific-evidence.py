"""T05 development evidence: scripted decisions, actual local scientific tools."""
from copy import deepcopy
from dataclasses import replace
import json
import math
from pathlib import Path
from unittest.mock import patch

from biomodel_agent import agent, agent_tools, application, bundles, evidence, modeling, recovery, reporting
from biomodel_agent.domain.contracts import ExperimentSpec, FrozenMap, ModelDraft, Parameter, RateRule, State
from biomodel_agent.domain.serialization import from_dict, to_dict
from biomodel_agent.storage import ArtifactStore

ROOT = Path(__file__).resolve().parent


class Scripted:
    last_usage = {'total_tokens': 3}
    def __init__(self, actions):
        self.actions = iter(actions)
        self.calls = []
    def respond(self, messages):
        self.calls.append(deepcopy(messages))
        action = next(self.actions)
        return action(messages) if callable(action) else deepcopy(action)


def act(name, **args):
    return {'action': name, 'args': args}


def case(name, formula='Equation dx/dt = -k*x.', parameter='Parameter k = 0.4.'):
    store = ArtifactStore(ROOT/name)
    lines = ['Initial x = 1.', parameter, formula]
    source = store.put('source', {'pages': [{'number': 1, 'text': '\n'.join(lines)}]})
    draft = ModelDraft('decay', (State('x', 1.),), (Parameter('k', .4),), rate_rules=(RateRule('x', '-k*x'),))
    proposal = {'draft': to_dict(draft), 'evidence': [
        {'target': target, 'source_ref': source, 'page': 1, 'quote': line}
        for target, line in zip(('x/initial', 'k/value', 'rule:x/expression'), lines)]}
    return store, source, proposal


def summarized(store, root):
    record = store.get(root)['payload']
    run = store.get(record['run_ref'])['payload'] if record.get('run_ref') else None
    result = {'root_ref': root, 'state': record['state'], 'call_count': record['call_count'],
              'repair_count': record['repair_count'], 'run_ref': record.get('run_ref'),
              'scientific_status': run['scientific_status'] if run else None,
              'binding_status': run['source_validation']['binding_status'] if run else None}
    if run:
        endpoint = run['simulation']['values']['x'][-1]
        assert abs(endpoint-math.exp(-.4)) < 1e-6
        result.update(endpoint=endpoint, expected_endpoint=math.exp(-.4),
                      absolute_error=abs(endpoint-math.exp(-.4)),
                      bindings=run['source_validation']['binding_diagnostics'])
        (store.root.parent/(store.root.name+'-report.md')).write_text(reporting.render_report(store, record['run_ref']))
    assert not run or result['binding_status'] == 'matched' and result['scientific_status'] == 'not_verified'
    return result


def main():
    results = {'evidence_kind': 'scripted inference; real local ODE solver; development controls only'}
    store, source, good = case('repair-store')
    bad = deepcopy(good)
    bad['draft']['rate_rules'][0]['expression'] = '-k*x*x'
    provider = Scripted([act('submit_draft', **bad), act('submit_draft', **good), act('run'), act('finish')])
    root = agent.run_agent(store, provider, [source], ExperimentSpec((0., 1.)), max_repairs=1)
    results['formula_repair'] = summarized(store, root)
    assert results['formula_repair']['state'] == 'completed' and results['formula_repair']['repair_count'] == 1
    results['formula_repair']['repair_feedback'] = json.loads(provider.calls[1][-1]['content'])
    manifest = bundles.export_bundle(store, root, ROOT/'repair-bundle')
    copied = ArtifactStore(ROOT/'repair-bundle')
    claims = store.list('claim')
    assert all(copied.get(ref) == store.get(ref) for ref in claims)
    results['bundle'] = {'root_ref': root, 'object_count': len(manifest['objects_and_blobs']), 'claim_refs': claims, 'claims_unchanged': True}

    store, source, proposal = case('clarification-store', formula='The response relaxes according to the displayed figure.')
    parent = agent.run_agent(store, Scripted([act('submit_draft', **proposal)]), [source], ExperimentSpec((0., 1.)))
    assert store.get(parent)['payload']['state'] == 'awaiting_input' and not store.list('claim') and not store.list('run')
    def answer(messages):
        info = json.loads(messages[-1]['content'])
        fixed = deepcopy(proposal)
        fixed['evidence'][2].update(source_ref=info['source_ref'], quote='Equation dx/dt = -k*x.')
        return act('submit_draft', **fixed)
    root = agent.resume_agent(store, Scripted([answer, act('run'), act('finish')]), parent,
                              'Equation dx/dt = -k*x.', respondent='Researcher A')
    results['clarification'] = dict(summarized(store, root), parent_ref=parent)
    assert results['clarification']['state'] == 'completed'

    store, source, proposal = case('manual-bypass-store', parameter='Buffer pH = 0.4.')
    text = evidence._source_page_text(store, source, 1)
    links = {}
    for item in proposal['evidence']:
        start = text.index(item['quote'])
        links[item['target']] = (evidence.add_claim(store, source, 1, start, start+len(item['quote']), item['target']),)
    artifact = modeling.build_model(replace(from_dict(ModelDraft, proposal['draft']), element_map=FrozenMap(links)))
    try:
        application.run_reproduction(store, artifact, ExperimentSpec((0., 1.)), strict=True)
    except ValueError as exc:
        error = str(exc)
    else:
        raise AssertionError('manual wrong-target claim bypassed binding gate')
    assert not store.list('run') and not store.list('simulation_input')
    results['manual_bypass'] = {'error': error, 'run_count': 0, 'simulation_input_count': 0}

    store, source, proposal = case('recovery-store')
    with patch.object(agent, '_run_facts', side_effect=SystemExit('injected after saved scientific result')):
        try:
            agent.run_agent(store, Scripted([act('submit_draft', **proposal), act('run')]), [source], ExperimentSpec((0., 1.)))
        except SystemExit:
            pass
        else:
            raise AssertionError('fault was not injected')
    execution = store.list('agent_execution')[-1]
    diagnostic = recovery.diagnose_execution(store, execution)
    assert diagnostic['status'] == 'ready'
    saved_runs, saved_claims = store.list('run'), store.list('claim')
    with patch.object(application, 'run_reproduction', side_effect=AssertionError('solver repeated')), \
         patch.object(agent_tools, 'submit_draft', side_effect=AssertionError('claims repeated')):
        root = agent.recover_agent(store, Scripted([act('finish')]), execution)
    assert store.list('run') == saved_runs and store.list('claim') == saved_claims
    results['recovery'] = dict(summarized(store, root), execution_ref=execution, no_solver_repeat=True, no_claim_repeat=True,
                               diagnostic=diagnostic)
    (ROOT/'scientific-results.json').write_text(json.dumps(results, indent=2, ensure_ascii=False)+'\n')
    print(json.dumps({name: value.get('state', value.get('error', 'verified')) for name, value in results.items() if isinstance(value, dict)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
