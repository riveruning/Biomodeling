"""Offline R10 diagnostic. Image stages use the optional figures runtime.

Run stages in order: prepare (figures), simulate (ODE), compare (figures).
The original model, windows, extraction and protocol are never fitted to image data.
"""
import argparse
import csv
import json
from pathlib import Path
import numpy as np
from biomodel_agent.storage import ArtifactStore
from biomodel_agent import paper_reference as paper
from biomodel_agent.domain.serialization import from_dict
from biomodel_agent.domain.contracts import ModelArtifact, ExperimentSpec, SolverConfig

ROOT=Path(__file__).resolve().parents[2]
FIXTURE=ROOT/'examples/goodwin/figure2-reference'
MODEL_REF='9917ecd7d5b792bdef3a55b5a5d5a560e294add7a268b3434d373e129aaf5a11'


def save(path,value):
    path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')


def main(stage,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    store=ArtifactStore(output/'store')
    if stage=='prepare':
        from biomodel_agent import bundles
        if (output/'protocol-receipt.json').exists():raise ValueError('use a fresh output directory')
        source=ArtifactStore(FIXTURE/'source-model-bundle')
        # Copy the existing source-bound executable and its dependency closure.
        bundles.export_bundle(source,MODEL_REF,output/'model-bundle')
        source=ArtifactStore(output/'model-bundle')
        def copy(ref):
            if (source.objects_dir/f'{ref}.json').exists():
                obj=source.get(ref)
                for child in obj['refs']:copy(child)
                assert store.put(obj['kind'],obj['payload'],refs=obj['refs'])==ref
            else:assert store.put_blob(source.get_blob(ref))==ref
        copy(MODEL_REF)
        assert store.get(MODEL_REF)['payload']['sbml']==(ROOT/'examples/goodwin/model.xml').read_text()
        reference=paper.prepare_reference(store,FIXTURE/'publisher-figure-2.png',json.loads((FIXTURE/'calibration.json').read_text()))
        protocol=paper.protocol(store,reference,MODEL_REF,json.loads((FIXTURE/'protocol.json').read_text()))
        save(output/'protocol-receipt.json',dict(reference_ref=reference,protocol_ref=protocol,model_ref=MODEL_REF,stage='frozen_before_new_execution; retrospective_development'))
        p=store.get(reference)['payload']
        for name,rows in p['curves'].items():
            with (output/f'digitized-{name}.csv').open('w',newline='') as fh:
                writer=csv.DictWriter(fh,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        save(output/'image-features.json',p['features'])
        print(json.dumps({'reference_ref':reference,'protocol_ref':protocol,'features':p['features']},indent=2))
    elif stage=='simulate':
        from biomodel_agent import application,reporting
        from scipy.integrate import solve_ivp
        receipt=json.loads((output/'protocol-receipt.json').read_text());p=store.get(receipt['protocol_ref'])['payload'];spec=p['spec']
        artifact=from_dict(ModelArtifact,store.get(p['model_ref'])['payload'])
        exp=from_dict(ExperimentSpec,spec['experiment']);solver=from_dict(SolverConfig,spec['solver'])
        run=application.run_reproduction(store,artifact,exp,solver=solver,strict=True,use_cache=False,nonnegative=tuple(spec['nonnegative']))
        r=store.get(run)['payload'];save(output/'run-receipt.json',dict(run_ref=run))
        (output/'run-report.md').write_text(reporting.render_report(store,run))
        if r['status']!='succeeded':raise RuntimeError('ODE simulation failed; inspect run-report.md')
        def rhs(t,v):
            x,y,z=v
            return [1/(1+z**10)-.1*x,x-.1*y,y-.1*z]
        cross=solve_ivp(rhs,(0,840),[.1,.2,.3],t_eval=exp.times,method='DOP853',rtol=1e-11,atol=1e-13)
        if not cross.success:raise RuntimeError('SciPy crosscheck failed')
        err=max(float(np.max(np.abs(np.asarray(r['simulation']['values'][n])-cross.y[i])/10)) for i,n in enumerate('XYZ'))
        save(output/'numeric-crosscheck.json',dict(method='independently coded SciPy DOP853 versus RoadRunner; same analyst initials',max_scaled_error=err,scales=10,threshold=.001,status='pass' if err<=.001 else 'fail',reference_type='numerical_solver_crosscheck; not publisher data',scientific_support='unknown'))
        print(json.dumps({'run_ref':run,'crosscheck_error':err}))
    else:
        from biomodel_agent import bundles,reporting
        receipt=json.loads((output/'protocol-receipt.json').read_text());run=json.loads((output/'run-receipt.json').read_text())['run_ref']
        ref=paper.compare(store,receipt['protocol_ref'],run);p=store.get(ref)['payload']
        save(output/'comparison.json',dict(comparison_ref=ref,**p))
        (output/'comparison-report.md').write_text(reporting.render_report(store,ref))
        bundles.export_bundle(store,ref,output/'comparison-bundle')
        render(output,store,ref)
        print(json.dumps({'comparison_ref':ref,'status':p['status'],'comparisons':p['comparisons']},indent=2))


def render(output,store,ref):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from PIL import Image
    p=store.get(ref)['payload'];reference=store.get(p['reference_ref'])['payload']
    fig,axes=plt.subplots(2,2,figsize=(9.2,6.4),layout='constrained')
    for ax,name,marker in zip(axes[0],('Y','Z'),('o','s')):
        rows=reference['curves'][name];times=[r['time'] for r in rows]
        vals=np.array([np.nan if r['value'] is None else r['value'] for r in rows])
        low=[np.nan if r['low'] is None else r['low'] for r in rows];high=[np.nan if r['high'] is None else r['high'] for r in rows]
        ax.plot(times,vals,color='#0072B2',marker=marker,markevery=30,ms=3,lw=1,label='Digitized publisher curve')
        ax.fill_between(times,low,high,color='#0072B2',alpha=.25,label='Stroke + pixel interval')
        ax.set(xlabel='Publisher time (a.u.)',ylabel=f'{name} (a.u.)',title=f'{name}: original image samples; gaps retained')
    for ax,name in zip(axes[1],('Y','Z')):
        selected=[c for c in p['comparisons'] if c['target'].startswith(name+'/') and not c['target'].endswith('period')]
        for i,c in enumerate(selected):
            r=c['reference']
            ax.errorbar(i-.06,r['value'],yerr=[[r['value']-r['low']],[r['high']-r['value']]],fmt='s',capsize=5,color='#0072B2',label='Image feature interval' if i==0 else None)
            ax.plot(i+.06,c['model'],'D',color='#D55E00',label='Model: 600–800 a.u.' if i==0 else None)
        ax.set(xticks=[0,1],xticklabels=['Minimum','Maximum'],ylabel=f'{name} (a.u.)',title=f'{name}: phase-free feature comparison')
        ax.legend(fontsize=8)
    fig.suptitle('Goodwin Figure 2B: retrospective development diagnostic\nUnknown original initials; no phase fit; independent review pending',fontsize=11)
    fig.savefig(output/'figure2-diagnostic.png',dpi=300,metadata={'Description':'Internal provisional figure. Stroke/calibration intervals are not confidence intervals.'})
    fig.savefig(output/'figure2-diagnostic.svg')
    plt.close(fig)
    with Image.open(output/'figure2-diagnostic.png') as im:size=list(im.size)
    save(output/'figure-provenance.json',dict(size_inches=[9.2,6.4],size_pixels=size,dpi=300,source_ref=p['reference_ref'],comparison_ref=ref,uncertainty='deterministic stroke extent plus 1.5 px calibration/sampling allowance; not CI',transform='palette to RGB; channel threshold; masked legend; no smoothing/interpolation; median extrema and within-segment trough spacings',destination='internal provisional review; no journal compliance claim',software_citation='https://doi.org/10.48550/arXiv.2609.00065'))
    (output/'figure-alt-text.txt').write_text('Two upper panels show raw digitized Y and Z publisher curves with stroke/calibration intervals; the upper-right Z curve has gaps where the legend covers it. Lower panels compare median minima/maxima: blue squares and intervals for the image; orange diamonds for the settled model. Original initials are unknown; no phase was fitted. Period values and all disagreements appear in comparison-report.md. This is a retrospective diagnostic, not scientific validation.\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('stage',choices=['prepare','simulate','compare']);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();main(args.stage,args.output)
