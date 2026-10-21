import json
from pathlib import Path
from copy import deepcopy
from biomodel_agent import agent, agent_tools, application
from biomodel_agent.domain.contracts import ExperimentSpec, ModelDraft, Parameter, RateRule, State, Reaction
from biomodel_agent.domain.serialization import to_dict
from biomodel_agent.storage import ArtifactStore

ROOT = Path(__file__).parent

class Scripted:
    last_usage = {}
    def __init__(self, actions):
        self.actions = iter(actions)
        self.calls = 0
    def respond(self, messages):
        self.calls += 1
        action = next(self.actions)
        if isinstance(action, BaseException):
            raise action
        return deepcopy(action)

def setup(name, *, text=None, k=.4, expression='-k', nonnegative=False):
    store = ArtifactStore(ROOT / name)
    text = text or 'Initial x = 1.\nParameter k = 0.4.\nEquation dx/dt = -k.'
    ref = store.put('source', {'pages': [{'number': 1, 'text': text}]})
    draft = ModelDraft('audit', (State('x', 1., nonnegative=nonnegative),),
                       (Parameter('k', k),), rate_rules=(RateRule('x', expression),))
    args = {'draft': to_dict(draft), 'evidence': [
        {'target': target, 'source_ref': ref, 'page': 1, 'quote': quote}
        for target, quote in zip(['x/initial', 'k/value', 'rule:x/expression'], text.splitlines())]}
    actions = [{'action': 'submit_draft', 'args': args}, {'action': 'run', 'args': {}},
               {'action': 'finish', 'args': {}}]
    return store, ref, args, actions

def main():
    results = {}
    store, ref, args, actions = setup('numeric-v2')
    draft = ModelDraft('negative_species', (State('x', 1., quantity_kind='amount', nonnegative=True),),
        (Parameter('k', .4),), reactions=(Reaction('loss', {'x':1.}, {}, 'k'),))
    args['draft'] = to_dict(draft)
    args['evidence'][2]['target'] = 'reaction:loss/rate'
    agent_ref = agent.run_agent(store, Scripted(actions), [ref], ExperimentSpec((0., 2., 4.)))
    ar = store.get(agent_ref)['payload']
    run = store.get(ar['run_ref'])['payload']
    results['negative_state_strict_mode'] = {
        'agent_state': ar['state'], 'scientific_status': run['scientific_status'],
        'solver_status': run['status'], 'x': run['simulation']['values']['x'],
        'numeric_validation': run['numeric_validation'], 'agent_ref': agent_ref}

    store, ref, args, _ = setup('binding', text='Initial x = 1.\nParameter k = 0.4; assay pH = 7.4.\nEquation dx/dt = -k*x.',
                               k=7.4, expression='-k*x')
    out = agent_tools.submit_draft(store, args, [ref])
    results['wrong_number_binding'] = {'state': out['state'], 'problems': out['problems'],
                                       'source_review': out['source_review'], 'proposed_k': 7.4}
    store, ref, args, _ = setup('formula', expression='k*x')
    out = agent_tools.submit_draft(store, args, [ref])
    results['wrong_formula_binding'] = {'state': out['state'], 'problems': out['problems'],
                                        'source_expression': '-k', 'proposed_expression': 'k*x'}

    store, ref, _, _ = setup('provider-crash')
    provider = Scripted([SystemExit('injected interruption during response')])
    try:
        agent.run_agent(store, provider, [ref], ExperimentSpec((0., 1.)))
    except SystemExit:
        pass
    results['provider_crash'] = {'attempted_calls': provider.calls,
        'persisted_tasks': len(store.list('agent_task')),
        'persisted_checkpoints': len(store.list('agent_checkpoint')),
        'persisted_agent_runs': len(store.list('agent_run'))}

    store, ref, _, actions = setup('post-run-crash', expression='-k*x',
        text='Initial x = 1.\nParameter k = 0.4.\nEquation dx/dt = -k*x.')
    original = application.run_reproduction
    def fail_after_save(*args, **kwargs):
        original(*args, **kwargs)
        raise SystemExit('injected interruption after run persisted')
    application.run_reproduction = fail_after_save
    provider = Scripted(actions)
    try:
        agent.run_agent(store, provider, [ref], ExperimentSpec((0., 1.)))
    except SystemExit:
        pass
    finally:
        application.run_reproduction = original
    results['post_run_crash'] = {'attempted_calls': provider.calls,
        'persisted_scientific_runs': len(store.list('run')),
        'persisted_checkpoints': len(store.list('agent_checkpoint')),
        'persisted_agent_runs': len(store.list('agent_run'))}
    (ROOT / 'probe-results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(json.dumps(results, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
