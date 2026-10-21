"""Offline scripted agent actions + real fit and frozen holdout comparison.

Analytic synthetic data, not an autonomous model performance measurement.
Usage: PYTHONPATH=src .venv/bin/python benchmarks/development/demo_agent_study.py --output /tmp/new-study-demo
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
from pathlib import Path

from biomodel_agent import agent, evidence, reporting, studies
from biomodel_agent.domain.contracts import ExperimentSpec
from biomodel_agent.domain.serialization import from_dict
from biomodel_agent.storage import ArtifactStore


class ScriptedProvider:
    last_usage = {}
    def __init__(self):
        self.actions = iter([{'action': 'run_study', 'args': {}}, {'action': 'finish', 'args': {}}])
        self.calls = []
    def respond(self, messages):
        self.calls.append(deepcopy(messages))
        return next(self.actions)


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    out = Path(args.output)
    if out.exists():
        raise ValueError('output directory must not exist; preserve prior evidence')
    out.mkdir(parents=True)
    store = ArtifactStore(out / 'store')
    times = [i * .5 for i in range(61)]
    common = {'sigma': {'response': .01}, 'scales': {'response': 1.}}
    training = {**common, 'id': 'analytic-training', 'role': 'training',
                'experiment': {'times': times, 'time_unit': 'second'},
                'values': {'response': [2.5 * (math.exp(-.2*t) - math.exp(-t)) for t in times]}}
    holdout = {**common, 'id': 'private-intervention', 'role': 'holdout',
               'experiment': {'times': times, 'time_unit': 'second', 'parameter_overrides': {'b': 0.}},
               'values': {'response': [(5/3) * (1 - math.exp(-1.2*t)) for t in times]}}
    source_path = out / 'protocol.txt'
    source_path.write_text('Synthetic constrained adaptation study. Compare only the frozen candidate templates. '
                           'Fitting is not a biological truth claim. Do not alter the protocol after holdout scoring.')
    source = evidence.ingest_source(store, source_path)
    config = {'training': [training], 'holdout': [holdout], 'starts': 1, 'max_nfev': 100, 'timeout_s': 30.}
    dump(out / 'operator-config.json', config)
    plan_ref = studies.prepare_study(store, config)
    provider = ScriptedProvider()
    exp = from_dict(ExperimentSpec, studies.load_plan(store, plan_ref)['config']['training'][0]['experiment'])
    root = agent.run_agent(store, provider, [source], exp, mode='hypothesis', study_plan_ref=plan_ref)
    run = store.get(root)['payload']
    result = store.get(run['run_ref'])['payload'] if run['run_ref'] else {}
    dump(out / 'scripted-requests.json', provider.calls)
    (out / 'report.md').write_text(reporting.render_report(store, root))
    if run['run_ref']:
        (out / 'study-report.md').write_text(reporting.render_report(store, run['run_ref']))
    summary = {'type': 'scripted_actions_with_real_fitting', 'autonomous_model_score': False,
               'new_model_requests': 0, 'root_ref': root, 'plan_ref': plan_ref,
               'study_ref': run['run_ref'], 'state': run['state'], 'calls': run['call_count'],
               'comparison': result.get('comparison'), 'scientific_status': result.get('scientific_status'),
               'candidates': studies.result_summary(store, run['run_ref'])['candidates'] if run['run_ref'] else {}}
    dump(out / 'results.json', summary)
    assert run['state'] == 'completed' and result['comparison']['winner'] == 'cand_feedback', summary
    assert 'private-intervention' not in json.dumps(provider.calls)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
