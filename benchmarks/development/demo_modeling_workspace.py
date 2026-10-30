"""Reproduce explicit workspace controls through public CLI subprocesses.

No provider calls. These seen synthetic controls validate plumbing and numerical
results; they are not a natural-language modeling or paper-reproduction score.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import subprocess
import sys


def run(output):
    output = Path(output).absolute()
    if output.exists():
        raise ValueError('output must be a new directory')
    output.mkdir()
    counter = 0; commands = []
    def invoke(args, expected=0, parse=True):
        nonlocal counter
        counter += 1
        command = [sys.executable, '-m', 'biomodel_agent', *map(str, args)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=90)
        prefix = output / f'{counter:02d}-{args[0]}'
        prefix.with_suffix('.stdout').write_text(result.stdout)
        prefix.with_suffix('.stderr').write_text(result.stderr)
        commands.append(dict(command=command, exit_code=result.returncode, expected=expected,
                             stdout=prefix.with_suffix('.stdout').name, stderr=prefix.with_suffix('.stderr').name))
        (output/'commands.json').write_text(json.dumps(commands, indent=2))
        if result.returncode != expected:
            raise RuntimeError(f'{args[0]} returned {result.returncode}, expected {expected}; inspect saved output')
        return json.loads(result.stdout) if parse else result.stdout
    workspace = output/'workspace'
    initialized = invoke(['workspace-init', '--output', workspace])
    store = workspace/'store'; base = ['--store', store]
    results = {}
    for case, expected, state in [('complete', 0, 'ready_to_compile'), ('ambiguous', 2, 'awaiting_selection'),
                                   ('missing', 2, 'awaiting_input')]:
        prepared = invoke(['model-plan-prepare', '--config', workspace/f'examples/{case}-plan.json',
            '--source-ref', initialized['example_source_refs'][case], *base], expected)
        if prepared['state'] != state:
            raise RuntimeError('unexpected readiness state')
        compiled = invoke(['model-plan-compile', prepared['plan_ref'], *base], expected)
        results[case] = dict(plan_ref=prepared['plan_ref'], compilation_ref=compiled['compilation_ref'],
                             readiness=state, issues=prepared['issues'], executed=False)
        if case == 'complete':
            compilation_ref = compiled['compilation_ref']
            task = invoke(['model-plan-execution-prepare', compilation_ref, '--config', workspace/'examples/execution.json', *base])
            outcome = invoke(['model-plan-execute', task['task_ref'], *base])
            again = invoke(['model-plan-execute', task['task_ref'], *base])
            if outcome['run_ref'] != again['run_ref']:
                raise RuntimeError('same task did not return its original result')
            run_ref = outcome['run_ref']
            results[case].update(executed=True, task_ref=task['task_ref'], run_ref=run_ref, outcome=outcome)
            invoke(['agent-timeline', run_ref, '--output', output/'timeline.md', *base], parse=False)
            invoke(['report', run_ref, '--output', output/'report.md', *base], parse=False)
            bundle = output/'bundle'
            invoke(['export', run_ref, '--output', bundle, *base], parse=False)
            invoke(['report', run_ref, '--output', output/'detached-report.md', '--store', bundle], parse=False)
            if (output/'report.md').read_bytes() != (output/'detached-report.md').read_bytes():
                raise RuntimeError('detached report differs')
            results['explicit_replay'] = invoke(['model-plan-replay', run_ref, '--store', bundle])
    # Predeclared analytic decay reference plus an intentional wrong-trajectory control.
    config = json.loads((workspace/'examples/execution.json').read_text())
    times = config['experiment']['times']
    analytic = dict(status='succeeded', times=times, values={'x': [math.exp(-.4*t) for t in times]}, time_unit='second')
    config.update(reference=analytic, scales={'x': 1.}, threshold=.001)
    for case, shift, expected in [('analytic_reference', 0., 0), ('wrong_reference', 1., 1)]:
        config['reference']['values']['x'] = [math.exp(-.4*t)+shift for t in times]
        path = output/f'{case}-execution.json'; path.write_text(json.dumps(config, indent=2))
        task = invoke(['model-plan-execution-prepare', compilation_ref, '--config', path, *base])
        results[case] = invoke(['model-plan-execute', task['task_ref'], *base], expected)
    summary = dict(version=1, scope='seen_synthetic_cli_and_numerical_controls', results=results,
        scientific_application_calls=4, explicit_replays=1, product_provider_calls=0,
        independent_domain_reviews=0, live_external_agent_decisions=0, scientific_support='unknown',
        detached_report_bytes_match=True, source='three packaged development controls; no evaluation material accessed')
    (output/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output), ensure_ascii=False, indent=2))
