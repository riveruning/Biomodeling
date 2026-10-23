"""Auditable Goodwin tool workflow, orchestrated by Codex; no product LLM call.

The proposal below is a frozen transcription of supplied development material,
not a general extractor. Independent SciPy code checks the SBML execution.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import subprocess

import numpy as np
from scipy.integrate import solve_ivp
from scipy.signal import find_peaks

from biomodel_agent import agent_tools, application, bundles, evidence, reporting
from biomodel_agent.domain.contracts import (
    ExperimentSpec, ModelDraft, Parameter, RateRule, SimulationResult, State,
)
from biomodel_agent.domain.serialization import to_dict
from biomodel_agent.storage import ArtifactStore

ROOT = Path(__file__).resolve().parents[2]
PARAMETERS = dict(k1=1.0, k2=0.1, k3=1.0, k4=0.1, k5=1.0, k6=0.1, Ki=1.0, n=10.0)
EQUATIONS = dict(X='k1*Ki^n/(Ki^n + Z^n) - k2*X', Y='k3*X - k4*Y', Z='k5*Y - k6*Z')
INITIALS = dict(X=0.1, Y=0.2, Z=0.3)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def proposal(source_ref, complete):
    draft = ModelDraft('goodwin2013_exploratory',
        tuple(State(k, INITIALS[k] if complete else None) for k in INITIALS),
        tuple(Parameter(k, v, origin='assistant_transcription_pending_review')
              for k, v in PARAMETERS.items()),
        rate_rules=tuple(RateRule(k, v) for k, v in EQUATIONS.items()), time_unit='unknown')
    citations = []
    for k in PARAMETERS:
        quote = ('k1 = 1.0; k3 = 1.0; k5 = 1.0.' if k in ('k1', 'k3', 'k5') else
                 'k2 = 0.1; k4 = 0.1; k6 = 0.1.' if k in ('k2', 'k4', 'k6') else
                 'Ki = 1.0.' if k == 'Ki' else 'n = 10.')
        citations.append(dict(target=k+'/value', quote=quote, claim_type='paper_assumption'))
    for k, expression in EQUATIONS.items():
        citations.append(dict(target=f'rule:{k}/expression', quote=f'd{k}/dt = {expression}.',
                              claim_type='mathematical_definition'))
    if complete:
        for k, value in INITIALS.items():
            citations.append(dict(target=k+'/initial', quote=f'Initial {k} = {value}.',
                                  claim_type='system_conjecture'))
    for citation in citations:
        citation.update(source_ref=source_ref, page=1)
    return dict(draft=to_dict(draft), evidence=citations)


def main(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    inputs = output / 'inputs'
    inputs.mkdir()
    sources = {
        'assisted': ROOT/'benchmarks/inputs/literature/gonze2013/assisted-transcription.txt',
        'complete': ROOT/'benchmarks/inputs/comparison-pilot/complete/source.txt',
        'publisher_xml': ROOT/'benchmarks/inputs/literature/gonze2013/article.xml',
    }
    copied = {}
    for name, path in sources.items():
        destination = inputs / (name + path.suffix)
        shutil.copyfile(path, destination)
        copied[name] = dict(path=str(destination.relative_to(output)),
                            original=str(path.relative_to(ROOT)), sha256=sha256(path.read_bytes()).hexdigest())
    # Freeze the protocol and thresholds before either numerical solver runs.
    times = np.linspace(0., 240., 481)
    experiment = ExperimentSpec(tuple(times), time_unit='unknown')
    task = dict(schema_version='1', case='gonze2013-goodwin-tool-workflow',
        created_at=datetime.now(timezone.utc).isoformat(),
        paper=dict(title='The Goodwin Model: Behind the Hill Function', doi='10.1371/journal.pone.0069573'),
        orchestration='Codex-prepared proposal executed through real product tools; no product LLM invocation',
        extraction='frozen assisted transcription; not automatic PDF extraction or blind evaluation',
        sources=copied, initials=INITIALS, initial_origin='existing analyst exploration protocol, not paper initial values',
        initial_claim_type='system_conjecture; not a new human assertion',
        experiment=to_dict(experiment), scales={k:10. for k in INITIALS}, threshold=1e-3,
        semantic_review='pending_human_review', original_figure_reproduction='not_verified',
        git_head=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
        runner_sha256=sha256(Path(__file__).read_bytes()).hexdigest())
    write_json(output/'task.json', task)
    write_json(output/'experiment.json', to_dict(experiment))
    store = ArtifactStore(output/'store')
    assisted_ref = evidence.ingest_source(store, inputs/'assisted.txt')
    missing_proposal = proposal(assisted_ref, False)
    write_json(output/'01-missing-proposal.json', missing_proposal)
    missing = agent_tools.submit_draft(store, missing_proposal, [assisted_ref])
    write_json(output/'02-missing-result.json', missing)
    if missing['state'] != 'awaiting_input' or missing['model_ref'] is not None:
        raise AssertionError('Missing initial values must prevent executable model creation')
    original_complete_ref = evidence.ingest_source(store, inputs/'complete.txt')
    # Preserve the original and make an explicit layout-only derived source.
    # The bounded binder treats image labels on declaration lines as unknown.
    original_text = (inputs/'complete.txt').read_text()
    normalized, changes = re.subn(r'(?m)^(e02[1-4]:) ', r'\1\n', original_text)
    if changes != 4:
        raise AssertionError('Unexpected source layout; review before normalization')
    normalized_path = inputs/'complete-normalized.txt'
    normalized_path.write_text(normalized)
    normalization = dict(operation='replace one space after e021/e022/e023/e024 labels with newline',
        substitutions=changes, original_source_ref=original_complete_ref,
        original_sha256=sha256(original_text.encode()).hexdigest(),
        derived_sha256=sha256(normalized.encode()).hexdigest(),
        note='Layout-only assistance for bounded binder; no numeric or equation edits; not OCR')
    write_json(output/'normalization.json', normalization)
    ingested_ref = evidence.ingest_source(store, normalized_path)
    ingested = store.get(ingested_ref)
    complete_ref = store.put('source', {**ingested['payload'], 'normalization':normalization},
                             refs=(*ingested['refs'], original_complete_ref))
    args = proposal(complete_ref, True)
    write_json(output/'03-complete-proposal.json', args)
    accepted = agent_tools.submit_draft(store, args, [complete_ref])
    write_json(output/'04-submission-result.json', {k:v for k,v in accepted.items() if k != 'artifact'})
    if accepted['state'] != 'ready':
        raise AssertionError(accepted['problems'])
    artifact = accepted['artifact']
    (output/'model.xml').write_text(artifact.sbml)
    # Deliberately independent, handwritten RHS; no draft/expression parser used.
    def rhs(t, y):
        x, protein, inhibitor = y
        return [1/(1+inhibitor**10)-0.1*x, x-0.1*protein, protein-0.1*inhibitor]
    ref = solve_ivp(rhs, (times[0], times[-1]), [0.1, 0.2, 0.3],
                    t_eval=times, method='DOP853', rtol=1e-11, atol=1e-13)
    if not ref.success or not np.isfinite(ref.y).all():
        raise AssertionError('Independent integration failed')
    reference = SimulationResult('succeeded', tuple(times),
                                {k:ref.y[i].tolist() for i,k in enumerate(INITIALS)})
    write_json(output/'reference.json', to_dict(reference))
    run_ref = application.run_reproduction(store, artifact, experiment, reference=reference,
        scales=task['scales'], threshold=task['threshold'], strict=True,
        use_cache=False, nonnegative=tuple(INITIALS))
    run = store.get(run_ref)['payload']
    write_json(output/'05-run.json', run)
    (output/'run-report.md').write_text(reporting.render_report(store, run_ref))
    if run['status'] != 'succeeded' or run['comparison']['status'] != 'pass':
        raise AssertionError('Simulation or independent reference comparison failed')
    if run['scientific_status'] != 'not_verified':
        raise AssertionError('Pending semantics must not become verified paper reproduction')
    with (output/'trajectories.csv').open('w', newline='') as fh:
        writer = csv.writer(fh)
        writer.writerow(['time_au'] + [f'{k}_{method}' for k in INITIALS for method in ('roadrunner','scipy','absolute_error')])
        for i,t in enumerate(times):
            row = [t]
            for j,k in enumerate(INITIALS):
                actual = run['simulation']['values'][k][i]
                row.extend([actual, ref.y[j,i], abs(actual-ref.y[j,i])])
            writer.writerow(row)
    task_ref = store.put('literature_workflow_demo', task,
        refs=(assisted_ref, complete_ref, missing['draft_ref'], accepted['model_ref'], run_ref))
    manifest = bundles.export_bundle(store, task_ref, output/'bundle')
    bundle_store = ArtifactStore(output/'bundle')
    replay_ref = application.replay_run(bundle_store, run_ref)
    replay = bundle_store.get(replay_ref)['payload']
    write_json(output/'06-replay.json', replay)
    replay_error = max(abs(a-b) for k in INITIALS
        for a,b in zip(run['simulation']['values'][k], replay['simulation']['values'][k]))
    if replay['status'] != 'succeeded' or replay['comparison']['status'] != 'pass' or replay_error > 1e-10:
        raise AssertionError('Exported bundle replay failed')
    peaks, _ = find_peaks(ref.y[0])
    late = times[peaks][times[peaks] >= 120]
    summary = dict(task_ref=task_ref, run_ref=run_ref, replay_ref=replay_ref,
        missing_initial_state=missing['state'], submission_state=accepted['state'],
        evidence_targets=len(args['evidence']), matched_bindings=sum(d['status']=='matched' for d in accepted['binding_diagnostics']),
        execution=run['status'], comparison=run['comparison'], scientific_status=run['scientific_status'],
        semantic_support=run['source_validation']['semantic_support'],
        replay_status=replay['status'], replay_comparison=replay['comparison']['status'],
        replay_max_absolute_difference=replay_error, replay_environment_matches=replay['environment_matches_recorded'],
        exported_objects_and_blobs=len(manifest['objects_and_blobs']),
        late_peak_period_au=float(np.mean(np.diff(late))) if len(late)>1 else None,
        llm_api_calls=0, product_agent_loop='not_run',
        workflow='real product tool execution; prepared proposal, not LLM performance evaluation')
    write_json(output/'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    main(parser.parse_args().output)
