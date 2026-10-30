"""Re-run the Goodwin model and compare its settled cycle with publisher Fig. 2B.

This reproduces the model's asymptotic cycle, not the unknown original initial state.
"""
from __future__ import annotations

import argparse
import csv
from hashlib import sha256
import json
from pathlib import Path
import shutil

import numpy as np
from scipy.integrate import solve_ivp
from scipy.signal import find_peaks

from biomodel_agent import application, bundles, reporting
from biomodel_agent.domain.contracts import ExperimentSpec, ModelArtifact, SimulationResult
from biomodel_agent.domain.serialization import from_dict
from biomodel_agent.storage import ArtifactStore

ROOT = Path(__file__).resolve().parents[2]
PAPER_IMAGE_URL = 'https://journals.plos.org/plosone/article/figure/image?download&size=large&id=10.1371/journal.pone.0069573.g002'


def write(path, obj):
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False)+'\n')


def main(output, publisher_figure):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    publisher_figure = Path(publisher_figure)
    if not publisher_figure.is_file():
        raise ValueError('An original publisher Figure 2 PNG is required')
    shutil.copyfile(publisher_figure, output/'publisher-figure-2.png')
    prior = ROOT/'artifacts/literature-workflow-demo-20261021-normalized'
    prior_summary = json.loads((prior/'summary.json').read_text())
    bundles.export_bundle(ArtifactStore(prior/'store'), prior_summary['task_ref'], output/'store')
    store = ArtifactStore(output/'store')
    model_ref = json.loads((prior/'04-submission-result.json').read_text())['model_ref']
    artifact = from_dict(ModelArtifact, store.get(model_ref)['payload'])
    (output/'model.xml').write_text(artifact.sbml)
    times = np.linspace(0., 840., 1681)
    def rhs(t, y):
        x, protein, inhibitor = y
        return [1/(1+inhibitor**10)-.1*x, x-.1*protein, protein-.1*inhibitor]
    scipy = solve_ivp(rhs, (0., 840.), [.1,.2,.3], method='DOP853',
                      t_eval=times, rtol=1e-11, atol=1e-13)
    if not scipy.success or not np.isfinite(scipy.y).all():
        raise AssertionError('Independent SciPy integration failed')
    reference = SimulationResult('succeeded', tuple(times),
        {name: scipy.y[i].tolist() for i,name in enumerate('XYZ')})
    run_ref = application.run_reproduction(store, artifact, ExperimentSpec(tuple(times),time_unit='unknown'),
        reference=reference, scales={name:10. for name in 'XYZ'}, threshold=1e-3,
        strict=True, use_cache=False, nonnegative=tuple('XYZ'))
    run = store.get(run_ref)['payload']
    if run['status']!='succeeded' or run['comparison']['status']!='pass':
        raise AssertionError('New simulation or independent numerical check failed')
    (output/'run-report.md').write_text(reporting.render_report(store, run_ref))
    write(output/'run-summary.json', {'run_ref':run_ref, 'execution':run['status'],
          'numerical_comparison':run['comparison'], 'scientific_status':run['scientific_status'],
          'source_semantic_support':run['source_validation']['semantic_support']})
    late = (times>=600.) & (times<=800.)
    shifted = times[late]-600.
    with (output/'settled-cycle.csv').open('w',newline='') as fh:
        w=csv.writer(fh); w.writerow(['display_time_au','original_simulation_time_au','X','Y','Z'])
        for j,t in enumerate(times[late]):
            idx=np.flatnonzero(late)[j]
            w.writerow([shifted[j],t,*[run['simulation']['values'][name][idx] for name in 'XYZ']])
    vals=np.asarray([run['simulation']['values'][name] for name in 'XYZ'])
    peak_indices,_=find_peaks(vals[2], distance=50)
    peak_times=times[peak_indices]
    stable_peaks=peak_times[(peak_times>=600)&(peak_times<=840)]
    metrics={
        'paper_doi':'10.1371/journal.pone.0069573',
        'publisher_figure_url':PAPER_IMAGE_URL,
        'publisher_figure_sha256':sha256((output/'publisher-figure-2.png').read_bytes()).hexdigest(),
        'input_model_ref':model_ref, 'new_run_ref':run_ref,
        'all_times_au':[0,840], 'display_window_au':[600,800],
        'display_time_shift_au':600,
        'input_initials':{'X':.1,'Y':.2,'Z':.3},
        'input_initials_origin':'analyst exploratory protocol, paper Figure 2B initials unavailable',
        'parameters_unchanged':True, 'phase_alignment_to_paper':'none',
        'model_period_au':float(np.mean(np.diff(stable_peaks))),
        'paper_caption_period_au_approx':40.,
        'model_min':{name:float(vals[i,late].min()) for i,name in enumerate('XYZ')},
        'model_max':{name:float(vals[i,late].max()) for i,name in enumerate('XYZ')},
        'numeric_crosscheck':run['comparison'],
        'source_semantic_support':run['source_validation']['semantic_support'],
        'original_figure_pointwise_comparison':'not_run: image digitization and original initials unavailable',
    }
    write(output/'metrics.json', metrics)
    print(json.dumps(metrics,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--publisher-figure',required=True,type=Path)
    a=p.parse_args(); main(a.output,a.publisher_figure)
