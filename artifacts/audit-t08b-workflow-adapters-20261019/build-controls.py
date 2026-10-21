"""Literal known development decisions, not an LLM or an independent benchmark."""
import importlib.util
from pathlib import Path

from biomodel_agent.domain.contracts import ModelDraft, Parameter, RateRule, State
from biomodel_agent.domain.serialization import to_dict

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[1]
spec=importlib.util.spec_from_file_location('t08b_control_driver',ROOT/'benchmarks/development/run_agent_comparison.py')
driver=importlib.util.module_from_spec(spec); spec.loader.exec_module(driver)


def proposal(case):
    if case=='decay':
        draft=ModelDraft('decay',(State('A',2.),),(Parameter('k',.4),),
            rate_rules=(RateRule('A','-k*A'),),time_unit='minute')
        declarations=[('A/initial','$main','Initial A = 2.0.'),('k/value','$main','Parameter k = 0.4.'),
                      ('rule:A/expression','$main','Equation dA/dt = -k*A.')]
    elif case=='translation':
        draft=ModelDraft('translation',(State('M',1.),State('P',.2)),
            tuple(Parameter(k,v) for k,v in [('s',.8),('dm',.4),('kp',.6),('dp',.2)]),
            rate_rules=(RateRule('M','s-dm*M'),RateRule('P','kp*M-dp*P')),time_unit='minute')
        declarations=[('M/initial','$main','Initial M = 1.0.'),('P/initial','$reply','Initial P = 0.2.'),
            ('s/value','$main','Parameter s = 0.8.'),('dm/value','$main','Parameter dm = 0.4.'),
            ('kp/value','$main','Parameter kp = 0.6.'),('dp/value','$main','Parameter dp = 0.2.'),
            ('rule:M/expression','$main','Equation dM/dt = s-dm*M.'),('rule:P/expression','$main','Equation dP/dt = kp*M-dp*P.')]
    else:
        draft=ModelDraft('logistic',(State('X',.2),),(Parameter('r',.8),Parameter('K',2.)),
            rate_rules=(RateRule('X','r*X*(1-X/K)'),),time_unit='minute')
        declarations=[('X/initial','$main','Initial X = 0.2.'),('r/value','$main','Parameter r = 0.8.'),
            ('K/value','$main','Parameter K = 2.0.'),('rule:X/expression','$reply',
             'The author confirms the intended equation is dX/dt = r*X*(1-X/K).')]
    return {'action':'submit_draft','args':{'draft':to_dict(draft),'evidence':[
        {'target':t,'source_ref':s,'page':1,'quote':q} for t,s,q in declarations]}}


def main():
    manifest=driver.protocol.development_manifest(ROOT)
    scripts={}
    for trial in driver.protocol.schedule(manifest):
        initial_gap=trial['phase']=='initial' and trial['case_id']!='decay'
        if initial_gap:
            question='What is the initial P value?' if trial['case_id']=='translation' else 'Which reported dX/dt alternative was actually used?'
            actions=[{'action':'ask_researcher','args':{'question':question}}]
        else:
            actions=[proposal(trial['case_id'])]
            if trial['workflow']!='fixed_workflow':
                actions.extend([{'action':'run','args':{}},{'action':'finish','args':{}}])
        if trial['workflow']!='fixed_workflow' and trial['phase']=='initial':
            actions.insert(0,{'action':'inspect_source','args':{
                'source_ref':'$main','page':1,'start':0,'end':'$main_length'}})
        scripts[trial['id']]=actions
    directory=OUT/'control-inputs'; directory.mkdir(exist_ok=False)
    driver.adapters._write(directory/'manifest.json',manifest)
    driver.adapters._write(directory/'scripts.json',{'evidence_kind':'scripted_control','trials':scripts})
    print('Prepared 30 literal scripted phase controls; no model or scientific call made during preparation.')


if __name__=='__main__': main()
