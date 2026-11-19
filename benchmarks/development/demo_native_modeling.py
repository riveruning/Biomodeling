"""Scripted native-Agent decisions with real gates, ODE runs and bundle reads.

Uses only packaged synthetic development controls. No live model or evaluation
dataset is involved; scripted response counts are not model token measurements.
"""
from copy import deepcopy
import argparse
import json
import math
from pathlib import Path

from biomodel_agent import agent, bundles, reporting, timeline, workspace_tools
from biomodel_agent.domain.contracts import ExperimentSpec, SimulationResult
from biomodel_agent.storage import ArtifactStore


class Scripted:
    last_usage = {}

    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def respond(self, messages):
        self.calls += 1
        response = next(self.responses)
        return response(messages) if callable(response) else deepcopy(response)


def action(name, **args):
    return dict(action=name, args=args)


def run(output):
    output = Path(output).absolute()
    output.mkdir(exist_ok=False)
    initialized = workspace_tools.initialize(output / 'workspace')
    store = ArtifactStore(output / 'workspace' / 'store')
    sources = initialized['example_source_refs']
    experiment = ExperimentSpec((0., .5, 1., 2.), time_unit='second')
    results = {}
    response_count = 0

    def proposal(case):
        spec = json.loads((output / 'workspace' / 'examples' / f'{case}-plan.json').read_text())
        for e in spec['evidence']:
            e.pop('start', None)
            e.pop('end', None)
        return spec

    def save(case, ref):
        payload = store.get(ref)['payload']
        report = reporting.render_report(store, ref)
        (output / f'{case}-report.md').write_text(report)
        (output / f'{case}-timeline.md').write_text(timeline.render_markdown(timeline.from_run(store, ref)))
        bundles.export_bundle(store, ref, output / f'{case}-bundle')
        detached = ArtifactStore(output / f'{case}-bundle')
        if reporting.render_report(detached, ref) != report:
            raise RuntimeError('detached report differs')
        result = dict(agent_run_ref=ref, task_ref=payload['task_ref'], state=payload['state'],
                      call_count=payload['call_count'], run_count=payload['run_count'],
                      modeling_decisions=payload['progress']['modeling_decisions'],
                      detached_report_matches=True)
        if payload.get('run_ref'):
            scientific = store.get(payload['run_ref'])['payload']
            result.update(application_run_ref=payload['run_ref'], comparison=scientific['comparison']['status'],
                          final_x=scientific['simulation']['values']['x'][-1])
        results[case] = result
        return payload

    for case in ('complete', 'ambiguous', 'missing'):
        actions = [action('submit_plan', plan=proposal(case))]
        if case == 'complete':
            actions += [action('inspect_progress'), action('run'), action('finish')]
        provider = Scripted(actions)
        ref = agent.run_agent(store, provider, [sources[case]], experiment,
                              modeling_decisions=True, max_calls=8)
        response_count += provider.calls
        payload = save(case, ref)
        expected = 'completed' if case == 'complete' else 'awaiting_input'
        if payload['state'] != expected:
            raise RuntimeError(f'{case}: expected {expected}, got {payload["state"]}')
        if case == 'missing':
            def clarified(messages):
                reply = next(json.loads(m['content']) for m in reversed(messages)
                             if m['role'] == 'user' and json.loads(m['content']).get('tool') == 'researcher_reply')
                spec = proposal('missing')
                candidate = spec['candidates'][0]
                candidate['draft']['parameters'][0]['value'] = .4
                spec['evidence'].append(dict(id='E_reply', source_ref=reply['source_ref'], page=1,
                    quote='Parameter k = 0.4.', interpretation='Attributed researcher declaration.', conditions={}))
                decision = next(d for d in candidate['decisions'] if d['target'] == 'k/value')
                decision.update(origin='reported', evidence_ids=['E_reply'], rationale='Use the explicit attributed reply.')
                return action('submit_plan', plan=spec, reason='Resolve k using the attributed reply.')
            provider = Scripted([clarified, action('run'), action('finish')])
            final = agent.resume_agent(store, provider, ref, 'Parameter k = 0.4.', respondent='Synthetic operator')
            response_count += provider.calls
            resumed = save('clarified', final)
            if resumed['state'] != 'completed' or resumed['task_ref'] != payload['task_ref'] or resumed['call_count'] != 4:
                raise RuntimeError('clarification did not retain the original task and cumulative budget')

    for case, shift, expected in [('analytic_reference', 0., 'completed'), ('wrong_reference', 1., 'incomplete')]:
        reference = SimulationResult('succeeded', experiment.times,
            {'x': tuple(math.exp(-.4*t)+shift for t in experiment.times)}, time_unit='second')
        provider = Scripted([action('submit_plan', plan=proposal('complete')), action('run'), action('finish')])
        ref = agent.run_agent(store, provider, [sources['complete']], experiment, modeling_decisions=True,
                              reference=reference, scales={'x': 1.})
        response_count += provider.calls
        if save(case, ref)['state'] != expected:
            raise RuntimeError('reference control outcome differs')
    summary = dict(version=1, scope='seen_synthetic_scripted_native_agent_controls', results=results,
        scripted_provider_responses=response_count, live_provider_calls=0, token_usage_measured=False,
        scientific_application_calls=len(store.list('run')), scientific_support='unknown',
        independent_domain_reviews=0, external_evaluation_material_accessed=False)
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output), ensure_ascii=False, indent=2))
