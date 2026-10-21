"""Operator-written audit from supplied materials; never imports dataset builders/product."""
from pathlib import Path
import hashlib
import json
import numpy as np
from scipy.integrate import solve_ivp

ROOT = Path(__file__).resolve().parents[2]
DATA = Path('/home/riveruning/biomodel_agent_evalset')
OUT = ROOT/'artifacts/holdout-evaluation-20261017'


def definitions():
    # Hand-transcribed from the case materials and frozen clarification assertions.
    return {
        'c01': (['A','B'], [4,0], lambda t,y: [-.85*y[0], .85*y[0]-.25*y[1]]),
        'c02': (['A','B','C','P'], [6,0,0,0], lambda t,y: [-.7*y[0], .7*y[0]-.3*y[1], .3*y[1]-.12*y[2], .12*y[2]]),
        'c03': (['S'], [8], lambda t,y: [-2.4*y[0]/(1.5+y[0])]),
        'c04': (['D'], [10], lambda t,y: [-.6*y[0]]),
        'c05': (['E','S','Es'], [5,2,.5], lambda t,y: [-.75*y[0]*y[1]+.2*y[2], -.75*y[0]*y[1]+.2*y[2], .75*y[0]*y[1]-.2*y[2]]),
        'c06': (['X','Y'], [5,0], lambda t,y: [-3*y[0]/(2+y[0]), 3*y[0]/(2+y[0])]),
        'c07': (['P','Q'], [4,0], lambda t,y: [-(2*y[0]/1.2-1.1*y[1]/1.6)/(1+y[0]/1.2+y[1]/1.6), (2*y[0]/1.2-1.1*y[1]/1.6)/(1+y[0]/1.2+y[1]/1.6)]),
        'c08': (['A','B','As','C'], [3,.4,0,0], lambda t,y: [-1.1*y[0]*y[1]+.35*y[2], -1.1*y[0]*y[1]+.95*y[2], 1.1*y[0]*y[1]-.95*y[2], .6*y[2]]),
        'c09': (['S'], [1], lambda t,y: [-.9*y[0]/(1.5+y[0]+y[0]**2/.8)]),
        'c10': (['A','B','C'], [6,0,0], lambda t,y: [-.5*y[0]+.25*y[1], .5*y[0]-.25*y[1]-1.8*y[1]/(1+y[1]), 1.8*y[1]/(1+y[1])]),
        'c11': (['P','A'], [0,2], lambda t,y: [.8*y[1]-.55*y[0], -.8*y[1]]),
        'c12': (['C','T'], [4,0], lambda t,y: [-.6*y[0]+.3*y[1], .6*y[0]-.3*y[1]]),
        'c13': (['S','I'], [3,2], lambda t,y: [-1.2*y[0]/(.9*(1+y[1]/1.4)+y[0]), 0]),
        'c14': (['R','P'], [.3,.2], lambda t,y: [1.2/(1+(y[0]/.9)**2)-.45*y[0], .8*y[0]-.25*y[1]]),
        'c15': (['X'], [.25], lambda t,y: [1.6*y[0]*(1-y[0]/4)-.35*y[0]]),
        'c16': (['Y'], [7], lambda t,y: [-.4*y[0]]),
        'c17': (['R'], [2], lambda t,y: [-.05*y[0]]),
        'c18': (['M'], [5], lambda t,y: [-.3*y[0]]),
        'c19': (['Z'], [9], lambda t,y: [-3*y[0]]),
        'c20': (['C','T'], [4,0], lambda t,y: [-.03*y[0]+.02*y[1], .03*y[0]-.02*y[1]]),
    }


def main():
    manifest = json.loads((DATA/'protocol/case_manifest.json').read_text())
    rows=[]
    for cid,c in manifest['cases'].items():
        ref=json.loads((DATA/c['reference']).read_text())
        experiment=json.loads((DATA/c['phases']['incomplete']['experiment']).read_text())
        times=np.array(experiment['times']); names,y0,rhs=definitions()[cid[:3]]
        assert np.array_equal(times,np.array(ref['times']))
        solution=solve_ivp(rhs,(times[0],times[-1]),y0,method='Radau',t_eval=times,rtol=1e-11,atol=1e-13)
        assert solution.success and np.isfinite(solution.y).all()
        errors={name:float(np.max(np.abs(solution.y[i]-np.array(ref['values'][name])))/max(1.,np.max(np.abs(ref['values'][name])))) for i,name in enumerate(names)}
        row={'case':cid,'method':'separately hand-coded RHS from materials; Radau', 'max_scaled_errors':errors,
             'tolerance':1e-6,'pass':max(errors.values())<=1e-6,'species_match':set(names)==set(ref['values'])}
        prefix=cid[:3]
        if prefix in ['c04','c16','c17','c18','c19']:
            rates={'c04':.6,'c16':.4,'c17':.05,'c18':.3,'c19':3.}
            closed=y0[0]*np.exp(-rates[prefix]*times)
            row['analytic_max_absolute_error']=float(np.max(np.abs(closed-solution.y[0])))
        rows.append(row)
    assert all(row['pass'] and row['species_match'] for row in rows), rows
    report={'status':'operator_numerical_audit_passed; human_domain_review_pending','human_reviewed':False,
        'dataset_builders_imported':False,'product_code_used':False,'cases':rows,
        'boundary_cases':{
            'c06_reversible_saturation_missing_initial':'Preparation contains no Y; Y(0)=0 is supported by supplied prose, not an unsupported guess.',
            'c07_mm_reversible_missing_initial':'Purified P with no Q introduced supports Q(0)=0.',
            'c08_sequential_activation_missing_initial':'No complex forms before illumination; As(0)=0 is inferable.',
            'c15_autocatalytic_ambiguous':'Alternative appendix expression is dimensionally inconsistent with declared K and k units; a justified dimensional rejection must be distinguished from arbitrary choice.',
            'c16_decay_ambiguous':'Rate 0.4 and time constant 2.5 are reciprocal consistent entries; ambiguity rubric may penalize justified interpretation.',
            'c17_mathematical_decay_unit_conflict':'0.05 per second equals 3 per minute; legend and second timetable agree. No conflicting physical rate is specified.'},
        'other_findings':[
            'allowed.json discloses expected execution and blocking target; omit it from all model-visible workspaces.',
            'c03 statement about finite-time exhaustion is mathematically false for Michaelis-Menten with Km>0. Positive S decays asymptotically; the numeric reference remains consistent.',
            'c01 prose S1/S2 and equation symbols A/B differ; equations plus reporting identify the intended mapping.',
            'Original scale text in experiment mentions max absolute reference; scoring_rules uses max(1,max absolute reference). Freeze the latter explicitly.',
            'Units clarification c20 explicitly provides both per-minute rates; its numeric effect is real, unlike the same-rate conversions in c17.'],
        'pilot_selection_rule':'first unambiguous case in each category, ordered by case ID; no system outputs observed before selection',
        'pilot_cases':['c01_multistep_decay','c05_seq_activation_missing_initial','c09_two_step_saturation_missing_parameter','c13_mm_with_inhibitor_ambiguous','c18_two_pathway_unit_conflict'],
        'pilot_selection_note':'c17 is a boundary case; c18 is the next unit-conflict case. c13 has genuine unresolved inhibitor interpretation despite missing full alternative equations.',
        'formal_superiority_claim_allowed':False,
        'auditor_code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (OUT/'operator-audit.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'numerical_cases_passed':len(rows),'boundary_cases':len(report['boundary_cases']),
                      'pilot_cases':report['pilot_cases'],'human_reviewed':False}))


if __name__=='__main__': main()
