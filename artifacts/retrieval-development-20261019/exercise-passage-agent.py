"""Scripted integration exercise, not autonomous model performance."""
import json
import math
from pathlib import Path
from biomodel_agent import agent, evidence
from biomodel_agent.domain.contracts import ExperimentSpec, ModelDraft, State, Parameter, RateRule
from biomodel_agent.domain.serialization import to_dict
from biomodel_agent.storage import ArtifactStore

def main():
    root = Path(__file__).resolve().parent / 'offline-integration-v2'
    root.mkdir(exist_ok=False)
    text = 'Overview of initial values, parameter k and model equations.\n\n' + 'Background apparatus. '*100 + '\n\nInitial x = 1.\nParameter k = 0.4.\nEquation dx/dt = -k*x.'
    path = root / 'source.txt'
    path.write_text(text)
    store = ArtifactStore(root / 'store')
    ref = evidence.ingest_source(store, path)

    class Scripted:
        last_usage = {}
        count = 0
        def respond(self, messages):
            self.count += 1
            if self.count == 1:
                return {'action': 'search_sources', 'args': {'query': 'Initial x Parameter k Equation'}}
            if self.count == 2:
                feedback = json.loads(messages[-1]['content'])
                assert feedback['method'] == 'passage_v2'
                quotes = [('x/initial', 'Initial x = 1.'), ('k/value', 'Parameter k = 0.4.'),
                          ('rule:x/expression', 'Equation dx/dt = -k*x.')]
                links = []
                for target, quote in quotes:
                    hit = next(h for h in feedback['results'] if quote in h['text'])
                    links.append({'target': target, 'source_ref': hit['source_ref'], 'page': hit['page'], 'quote': quote})
                draft = ModelDraft('decay', (State('x', 1.),), (Parameter('k', .4),), rate_rules=(RateRule('x', '-k*x'),))
                return {'action': 'submit_draft', 'args': {'draft': to_dict(draft), 'evidence': links}}
            return {'action': 'run' if self.count == 3 else 'finish', 'args': {}}

    provider = Scripted()
    run_ref = agent.run_agent(store, provider, [ref], ExperimentSpec((0., 1.)), retrieval_method='passage_v2', max_calls=4)
    run = store.get(run_ref)['payload']
    actual = store.get(run['run_ref'])['payload']
    endpoint = actual['simulation']['values']['x'][-1]
    result = {'kind': 'scripted_offline_integration_not_model_performance', 'agent_run_ref': run_ref,
              'state': run['state'], 'calls': run['call_count'], 'retrieval_method': 'passage_v2',
              'actual_endpoint': endpoint, 'analytic_endpoint': math.exp(-.4),
              'absolute_error': abs(endpoint - math.exp(-.4)), 'scientific_status': actual['scientific_status']}
    assert result['state'] == 'completed' and result['calls'] == 4 and result['absolute_error'] < 1e-5
    (root / 'results.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
