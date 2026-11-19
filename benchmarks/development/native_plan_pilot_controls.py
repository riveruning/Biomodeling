"""Offline solvability controls for the pilot; scripted, never a live score."""
import argparse
import json
from pathlib import Path

import native_plan_pilot as pilot
from biomodel_agent import agent, reporting
from biomodel_agent.domain.contracts import ExperimentSpec, ModelDraft, State, Parameter, RateRule
from biomodel_agent.domain.serialization import to_dict
from biomodel_agent.evidence import ingest_source
from biomodel_agent.storage import ArtifactStore


class Scripted:
    last_usage = {}

    def __init__(self, actions):
        self.actions = iter(actions)

    def respond(self, messages):
        return next(self.actions)


def proposal(case, source):
    if case == 'production-loss':
        draft = ModelDraft('production_loss', (State('p',1.),), (Parameter('b',.6),Parameter('k',.3)),
                           rate_rules=(RateRule('p','b-k*p'),))
        pairs = [('p/initial','Initial p = 1.'),('b/value','Parameter b = 0.6.'),
                 ('k/value','Parameter k = 0.3.'),('rule:p/expression','Equation dp/dt = b-k*p.')]
    elif case == 'transfer':
        draft = ModelDraft('transfer', (State('a',2.),State('b',1.)), (Parameter('k',.2),),
                           rate_rules=(RateRule('a','-k*a'),RateRule('b','k*a')))
        pairs = [('a/initial','Initial a = 2.'),('b/initial','Initial b = 1.'),('k/value','Parameter k = 0.2.'),
                 ('rule:a/expression','Equation da/dt = -k*a.'),('rule:b/expression','Equation db/dt = k*a.')]
    else:
        draft = ModelDraft('missing_rate', (State('z',3.),), (Parameter('k',None),),
                           rate_rules=(RateRule('z','-k*z'),))
        pairs = [('z/initial','Initial z = 3.'),('k/value',None),('rule:z/expression','Equation dz/dt = -k*z.')]
    anchors = [dict(id=f'E{i}',source_ref=source,page=1,quote=q,interpretation='Scripted reading of an explicit declaration.',conditions={})
               for i,(_,q) in enumerate(pairs) if q]
    decisions = [dict(target=t,origin='reported' if q else 'unresolved',evidence_ids=[f'E{i}'] if q else [],
                      mechanism_ids=[],rationale='Use the supplied declaration.' if q else 'What is the missing k?',alternatives=[])
                 for i,(t,q) in enumerate(pairs)]
    return dict(schema_version=1,id=draft.id,model_type='ode',mode='reproduction',context={},
        entities=[dict(id='entity_'+s.id,label=s.id) for s in draft.states],evidence=anchors,mechanisms=[],
        candidates=[dict(id='explicit',label='Supplied equations',variable_map={'entity_'+s.id:s.id for s in draft.states},
                         draft=to_dict(draft),decisions=decisions)],selected_candidate='explicit')


def run(prepared, output):
    manifest=pilot.checked(prepared)
    output=Path(output).absolute();output.mkdir(exist_ok=False)
    results=[]
    for case in manifest['cases']:
        folder=output/case['id'];folder.mkdir()
        store=ArtifactStore(folder/'store')
        source=ingest_source(store,Path(prepared)/case['source'])
        actions=[dict(action='submit_plan',args={'plan':proposal(case['id'],source)})]
        if case['id']!='missing-rate':actions += [dict(action='run',args={}),dict(action='finish',args={})]
        ref=agent.run_agent(store,Scripted(actions),[source],ExperimentSpec(tuple(manifest['times']),time_unit='second'),
                            modeling_decisions=True,max_calls=6,max_repairs=2,max_retries=0)
        result=pilot.outcome(store,ref,case['id'],manifest['analytic_absolute_tolerance'])
        result['case']=case['id'];results.append(result)
        (folder/'report.md').write_text(reporting.render_report(store,ref))
    summary=dict(scope='scripted_solvability_controls_not_live_model_results',results=results,
                 live_requests=0,scientific_application_calls=2,scripted_responses=7)
    pilot.save(output/'summary.json',summary)
    if not all(r['control_pass'] for r in results):raise RuntimeError('offline control failed; inspect saved results')
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    print(json.dumps(run(args.prepared,args.output),ensure_ascii=False,indent=2))
