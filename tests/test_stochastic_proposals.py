"""Source-bound SSA: literal math, units, assumptions, selection and real paths."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from biomodel_agent import bundles,evidence,reporting,research_tasks as tasks,stochastic,task_proposals as proposals,typed_models
from biomodel_agent.storage import ArtifactStore
from test_fba import Scripted,act

ROOT=Path(__file__).parents[1]
EXAMPLES=ROOT/'examples/source-stochastic'
TEXT=(EXAMPLES/'birth-death.txt').read_text()
TEMPLATE=json.loads((EXAMPLES/'proposal.template.json').read_text())


def setup(tmp_path,text=TEXT):
    store=ArtifactStore(tmp_path/'store');path=tmp_path/'source.txt';path.write_text(text)
    source=evidence.ingest_source(store,path)
    config=json.loads((EXAMPLES/'request.template.json').read_text().replace('SOURCE_REF',source))
    request=tasks.prepare(store,config);spec=deepcopy(TEMPLATE);spec['evidence']=[]
    candidate=spec['candidates'][0];candidate['evidence_ids']=['G'];candidate['evidence_map']={}
    spec['evidence'].append(dict(id='G',kind='goal',quote=config['goal']))
    for target,ids in TEMPLATE['candidates'][0]['evidence_map'].items():
        original=next(a for a in TEMPLATE['evidence'] if a['id']==ids[0]);quote=original['quote']
        if quote in text:
            anchor=dict(original,source_ref=source,start=text.index(quote),end=text.index(quote)+len(quote))
            spec['evidence'].append(anchor);candidate['evidence_ids'].append(anchor['id']);candidate['evidence_map'][target]=[anchor['id']]
        else:candidate['evidence_map'][target]=[]
    return store,request,spec


def select(store,ref,unknowns=None):
    return proposals.accept(store,ref,'direct',reviewer='Synthetic operator',reason='Select only explicitly sourced SSA declarations.',
        acknowledged_unknowns=TEMPLATE['candidates'][0]['unknowns'] if unknowns is None else unknowns)


def proposal(store,request,spec):return proposals.prepare(store,request,spec)


def draft(spec):return spec['candidates'][0]['stochastic_draft']


def checked(store,ref):return proposals.inspect(store,ref)['candidates'][0]


def test_source_declared_ssa_selects_without_compute_then_runs_once_and_exports(tmp_path):
    store,r,spec=setup(tmp_path);provider=Scripted([act('propose_task',proposal=spec)])
    pref=proposals.propose(store,provider,r)
    before=store.list();view=checked(store,pref)
    assert view['draft_adoptable'] and view['model_draft']==draft(spec)
    assert store.list()==before and not store.list('typed_model') and not store.list('typed_run')
    selected=select(store,pref);assert select(store,pref)==selected
    config=tasks.load(store,selected)['spec'];modelref=config['model_ref'];saved=store.get(modelref)['payload']
    assert saved['origin']=='accepted_source_bound_stochastic_proposal'
    assert saved['source_binding']['rate_units']=={'birth':'1/second','death':'1/second'}
    assert saved['source_binding']['mixing']=='well_mixed' and saved['source_binding']['kinetics']=='time_homogeneous'
    assert not store.list('typed_run') and tasks.status(store,selected)['state']=='ready_to_run'
    actions=Scripted([act('run_typed'),act('finish')]);run=tasks.execute(store,actions,selected)
    result=tasks.result(store,run)
    assert (result['state'],result['run_count'],result['call_count'],result['proposal_call_count'],result['total_call_count'])==('completed',1,2,1,3)
    assert tasks.execute(store,None,selected)==run and len(actions.requests)==2 and len(provider.requests)==1
    native=store.get(result['native_run_ref'])['payload'];inputs,model,data=typed_models.result(store,native['run_ref'])
    assert stochastic.checks(model,inputs['settings'],data['data'])
    assert all(c['status']=='pass' for c in stochastic.checks(model,inputs['settings'],data['data']))
    assert sum(map(sum,data['data']['reaction_counts']))==data['data']['event_count']
    assert 'Stochastic constant units' in reporting.render_report(store,pref)
    bundles.export_bundle(store,run,tmp_path/'bundle')
    detached=ArtifactStore(tmp_path/'bundle')
    assert reporting.render_report(detached,run)==reporting.render_report(store,run)
    assert checked(detached,pref)['draft_adoptable']


@pytest.mark.parametrize('target',list(TEMPLATE['candidates'][0]['evidence_map']))
def test_every_initial_reaction_rate_unit_and_assumption_needs_a_citation(tmp_path,target):
    store,r,spec=setup(tmp_path);spec['candidates'][0]['evidence_map'][target]=[]
    pref=proposal(store,r,spec)
    assert not checked(store,pref)['draft_adoptable']
    with pytest.raises(ValueError):select(store,pref)
    assert not store.list('typed_model') and not store.list('typed_run')


@pytest.mark.parametrize('target',list(TEMPLATE['candidates'][0]['evidence_map']))
def test_missing_source_declaration_is_not_filled_from_model_defaults(tmp_path,target):
    ids=TEMPLATE['candidates'][0]['evidence_map'][target]
    line=next(a['quote'] for a in TEMPLATE['evidence'] if a['id']==ids[0])
    store,r,spec=setup(tmp_path,TEXT.replace(line+'\n',''))
    pref=proposal(store,r,spec)
    assert not checked(store,pref)['draft_adoptable']
    with pytest.raises(ValueError):select(store,pref)
    assert not store.list('typed_model')


@pytest.mark.parametrize('change',[
    lambda s:draft(s)['species'][0].update(initial=5),
    lambda s:draft(s)['species'][0].update(initial=4.0),
    lambda s:draft(s)['species'][0].update(initial=-1),
    lambda s:draft(s)['reactions'][0].update(rate=0.0),
    lambda s:draft(s)['reactions'][1].update(rate=.25),
    lambda s:draft(s)['reactions'][1].update(rate=True),
    lambda s:draft(s)['reactions'][0].update(products={'X':2}),
    lambda s:draft(s)['reactions'][1].update(reactants={'X':2}),
    lambda s:draft(s).update(rate_convention='deterministic_mass_action'),
    lambda s:draft(s)['units'].update(state='molar'),
    lambda s:draft(s)['units'].update(time='minute'),
    lambda s:s['candidates'][0]['stochastic_rate_units'].update(death='1/minute'),
    lambda s:s['candidates'][0].update(stochastic_rate_units={'birth':'1/second'}),
])
def test_invented_or_mismatched_model_values_and_units_block_selection(tmp_path,change):
    store,r,spec=setup(tmp_path);change(spec);pref=proposal(store,r,spec)
    assert not checked(store,pref)['draft_adoptable']
    with pytest.raises(ValueError):select(store,pref)


@pytest.mark.parametrize('extra',[
    'Initial X = 5.','Initial Y = 0.','Reaction extra: X -> 0.',
    'Reaction birth: 0 -> 2 X.','Stochastic rate death = 0.25.',
    'Stochastic rate unit death = 1/minute.','Time unit = minute.',
    'State unit = molar.','Rate convention = falling_factorial.',
    'Mixing = spatial.','Kinetics = time_dependent.',
    'If stimulated: Stochastic rate birth = 7.',
    'Stochastic rate birth = 5 only during stress.',
    'Reaction birth: 0 -> X while activated.',
])
def test_uncited_conflicting_omitted_or_qualified_declarations_block_entire_scope(tmp_path,extra):
    store,r,spec=setup(tmp_path,TEXT+extra+'\n');pref=proposal(store,r,spec)
    assert not checked(store,pref)['draft_adoptable']
    with pytest.raises(ValueError):select(store,pref)


def test_citation_cannot_strip_rate_qualifier(tmp_path):
    store,r,spec=setup(tmp_path,TEXT.replace('Stochastic rate birth = 5.','Stochastic rate birth = 5. under stimulation only'))
    pref=proposal(store,r,spec)
    assert not checked(store,pref)['draft_adoptable']


def test_same_species_second_order_uses_combinations_without_factor_two_conversion(tmp_path):
    text=TEXT.replace('Reaction death: X -> 0.','Reaction death: 2 X -> 0.')
    store,r,spec=setup(tmp_path,text);draft(spec)['reactions'][1]['reactants']={'X':2}
    anchor=next(a for a in TEMPLATE['evidence'] if a['quote']=='Reaction death: X -> 0.')
    updated=dict(anchor,source_ref=tasks.load(store,r)['spec']['source_refs'][0],quote='Reaction death: 2 X -> 0.')
    spec['evidence'].append(updated);spec['candidates'][0]['evidence_map']['death/reaction']=[anchor['id']]
    spec['candidates'][0]['evidence_ids'].append(anchor['id'])
    pref=proposal(store,r,spec);assert checked(store,pref)['draft_adoptable']
    selected=select(store,pref);model=typed_models.load(store,tasks.load(store,selected)['spec']['model_ref'])
    assert stochastic.propensities(model,{'X':4}).tolist()==[5.,3.]
    # 0.5*comb(4,2)=3, not 0.5*4*3=6; no deterministic-rate conversion.
    assert model['reactions'][1]['rate']==.5


def replace_anchor(store,r,spec,target,quote):
    candidate=spec['candidates'][0]
    old_ids=TEMPLATE['candidates'][0]['evidence_map'][target]
    original=next(a for a in TEMPLATE['evidence'] if a['id']==old_ids[0]);eid=original['id']
    spec['evidence']=[a for a in spec['evidence'] if a['id']!=eid]
    source=tasks.load(store,r)['spec']['source_refs'][0]
    spec['evidence'].append(dict(id=eid,kind='source',source_ref=source,page=1,quote=quote))
    candidate['evidence_map'][target]=[eid]
    if eid not in candidate['evidence_ids']:candidate['evidence_ids'].append(eid)


@pytest.mark.parametrize('value',['0.10000000000000001','0.50000000000000000001'])
def test_decimal_precision_is_not_silently_rounded_into_a_false_match(tmp_path,value):
    quote='Stochastic rate death = '+value+'.'
    store,r,spec=setup(tmp_path,TEXT.replace('Stochastic rate death = 0.5.',quote))
    draft(spec)['reactions'][1]['rate']=float(value)
    replace_anchor(store,r,spec,'death/rate',quote)
    pref=proposal(store,r,spec);assert not checked(store,pref)['draft_adoptable']
    assert any('differs' in i['message'] or 'conflicting' in i['message'] for i in checked(store,pref)['draft_check']['issues'])


def test_exact_decimal_exponent_notation_preserves_value_without_scaling(tmp_path):
    quote='Stochastic rate death = 5e-1.'
    store,r,spec=setup(tmp_path,TEXT.replace('Stochastic rate death = 0.5.',quote))
    replace_anchor(store,r,spec,'death/rate',quote)
    assert checked(store,proposal(store,r,spec))['draft_adoptable']


@pytest.mark.parametrize('time_unit',['minute','hour'])
def test_matching_model_time_and_inverse_rate_labels_are_preserved(tmp_path,time_unit):
    text=TEXT.replace('second',time_unit);store,r,spec=setup(tmp_path,text)
    draft(spec)['units']['time']=time_unit
    spec['candidates'][0]['stochastic_rate_units']={'birth':'1/'+time_unit,'death':'1/'+time_unit}
    for target in ('time_unit','birth/rate_unit','death/rate_unit'):
        quote=next(a['quote'] for a in TEMPLATE['evidence'] if a['id']==TEMPLATE['candidates'][0]['evidence_map'][target][0]).replace('second',time_unit)
        replace_anchor(store,r,spec,target,quote)
    pref=proposal(store,r,spec);assert checked(store,pref)['draft_adoptable']
    selected=select(store,pref);model=typed_models.load(store,tasks.load(store,selected)['spec']['model_ref'])
    assert model['units']['time']==time_unit and model['reactions'][1]['rate']==.5


def test_goal_or_supplied_model_evidence_cannot_certify_a_rate(tmp_path):
    store,r,spec=setup(tmp_path);spec['candidates'][0]['evidence_map']['birth/rate']=['G']
    assert not checked(store,proposal(store,r,spec))['draft_adoptable']


def test_units_and_assumptions_are_bound_to_the_selected_model_not_an_unchecked_note(tmp_path):
    store,r,spec=setup(tmp_path);pref=proposal(store,r,spec);selected=select(store,pref)
    selected_obj=store.get(selected);modelref=selected_obj['payload']['spec']['model_ref'];model=store.get(modelref)
    bad=deepcopy(model['payload']);bad['source_binding']['rate_units']['birth']='1/minute'
    fake_model=store.put('typed_model',bad,refs=model['refs'])
    altered=deepcopy(selected_obj['payload']['spec']);altered['model_ref']=fake_model
    with pytest.raises(ValueError):proposals.check_selection(store,selected_obj['payload']['selection_ref'],altered)


@pytest.mark.parametrize('change',[
    lambda s:draft(s)['species'].extend(dict(id='S'+str(i),initial=0) for i in range(8)),
    lambda s:draft(s)['reactions'].extend(dict(id='R'+str(i),reactants={},products={'X':1},rate=1.) for i in range(15)),
])
def test_source_profile_limits_are_narrower_than_the_native_backend(tmp_path,change):
    store,r,spec=setup(tmp_path);change(spec);stochastic.validate(draft(spec))
    pref=proposal(store,r,spec);assert not checked(store,pref)['draft_adoptable']


def test_source_scope_limit_and_non_text_parser_do_not_certify_absence_of_conflicts(tmp_path):
    store,r,spec=setup(tmp_path,TEXT+'x'*64000);pref=proposal(store,r,spec)
    assert not checked(store,pref)['draft_adoptable']
    source=store.get(tasks.load(store,r)['spec']['source_refs'][0]);payload=deepcopy(source['payload']);payload['parser']='pypdf'
    fake=store.put('source',payload,refs=source['refs'])
    config=deepcopy(tasks.load(store,r)['spec']);config['source_refs']=[fake];r2=tasks.prepare(store,config)
    for a in spec['evidence']:
        if a['kind']=='source':a['source_ref']=fake
    assert not checked(store,proposal(store,r2,spec))['draft_adoptable']


def test_rate_and_unit_fields_cannot_be_smuggled_into_other_model_types(tmp_path):
    store,r,spec=setup(tmp_path);spec['candidates'][0]['model_type']='pde'
    with pytest.raises(ValueError):proposal(store,r,spec)


def explicit_case(tmp_path,body,model,rate_units,mappings):
    text='Synthetic independent reaction-network control.\n'+body
    store=ArtifactStore(tmp_path/'store');path=tmp_path/'network.txt';path.write_text(text)
    source=evidence.ingest_source(store,path)
    request=tasks.prepare(store,dict(schema_version=1,goal='Check explicit reaction channels, without inference.',
        source_refs=[source],execution={'settings':{'duration':1.,'samples':5,'trajectories':8,'seed':123,'max_events':10000}}))
    spec=dict(schema_version=1,summary='Explicit independent SSA control.',evidence=[],candidates=[],recommendation='direct',questions=[])
    common={'mixing':'Mixing = well_mixed.','kinetics':'Kinetics = time_homogeneous.','time_unit':'Time unit = second.',
        'state_unit':'State unit = molecule_count.','rate_convention':'Rate convention = stochastic_combinations.'}
    evidence_map={}
    for i,(target,quote) in enumerate({**common,**mappings}.items()):
        spec['evidence'].append(dict(id=f'E{i}',kind='source',source_ref=source,page=1,quote=quote))
        evidence_map[target]=[f'E{i}']
    spec['candidates']=[dict(id='direct',model_type='stochastic',rationale='Only the stated integer network is tested.',
        evidence_ids=[a['id'] for a in spec['evidence']],unknowns=[],stochastic_draft=model,
        stochastic_rate_units=rate_units,evidence_map=evidence_map)]
    return store,request,spec


COMMON='Mixing = well_mixed.\nKinetics = time_homogeneous.\nTime unit = second.\nState unit = molecule_count.\nRate convention = stochastic_combinations.\n'


@pytest.mark.parametrize('case',['reversible','catalyst'])
def test_independent_bimolecular_or_reversible_network_preserves_stoichiometric_invariants(tmp_path,case):
    import numpy as np
    if case=='reversible':
        body=COMMON+'Initial A = 6.\nInitial B = 0.\nReaction forward: A -> B.\nStochastic rate forward = 0.4.\nStochastic rate unit forward = 1/second.\nReaction reverse: B -> A.\nStochastic rate reverse = 0.3.\nStochastic rate unit reverse = 1/second.\n'
        model=dict(schema_version=1,model_type='stochastic',id='reversible',units={'time':'second','state':'molecule_count'},
            rate_convention='stochastic_combinations',species=[dict(id='A',initial=6),dict(id='B',initial=0)],
            reactions=[dict(id='forward',reactants={'A':1},products={'B':1},rate=.4),dict(id='reverse',reactants={'B':1},products={'A':1},rate=.3)])
        mappings={'A/initial':'Initial A = 6.','B/initial':'Initial B = 0.','forward/reaction':'Reaction forward: A -> B.',
            'forward/rate':'Stochastic rate forward = 0.4.','forward/rate_unit':'Stochastic rate unit forward = 1/second.',
            'reverse/reaction':'Reaction reverse: B -> A.','reverse/rate':'Stochastic rate reverse = 0.3.','reverse/rate_unit':'Stochastic rate unit reverse = 1/second.'}
        units={'forward':'1/second','reverse':'1/second'}
    else:
        body=COMMON+'Initial X = 4.\nInitial E = 2.\nInitial Y = 0.\nReaction cat: X + E -> Y + E.\nStochastic rate cat = 0.25.\nStochastic rate unit cat = 1/second.\n'
        model=dict(schema_version=1,model_type='stochastic',id='catalyst',units={'time':'second','state':'molecule_count'},
            rate_convention='stochastic_combinations',species=[dict(id='X',initial=4),dict(id='E',initial=2),dict(id='Y',initial=0)],
            reactions=[dict(id='cat',reactants={'X':1,'E':1},products={'Y':1,'E':1},rate=.25)])
        mappings={'X/initial':'Initial X = 4.','E/initial':'Initial E = 2.','Y/initial':'Initial Y = 0.',
            'cat/reaction':'Reaction cat: X + E -> Y + E.','cat/rate':'Stochastic rate cat = 0.25.','cat/rate_unit':'Stochastic rate unit cat = 1/second.'}
        units={'cat':'1/second'}
    store,r,spec=explicit_case(tmp_path,body,model,units,mappings);pref=proposal(store,r,spec)
    assert checked(store,pref)['draft_adoptable']
    selected=select(store,pref,unknowns=[]);modelref=tasks.load(store,selected)['spec']['model_ref']
    frozen=typed_models.load(store,modelref);assert frozen==model
    run=tasks.execute(store,Scripted([act('run_typed'),act('finish')]),selected)
    native=store.get(tasks.result(store,run)['native_run_ref'])['payload']
    inputs,_,data=typed_models.result(store,native['run_ref']);paths=np.asarray(data['data']['trajectories'])
    if case=='reversible':
        assert stochastic.propensities(frozen,{'A':4,'B':2}).tolist()==[1.6,.6]
        assert np.all(paths.sum(axis=2)==6)
    else:
        assert stochastic.propensities(frozen,{'X':4,'E':2,'Y':0}).tolist()==[2.]
        assert np.all(paths[:,:,1]==2) and np.all(paths[:,:,0]+paths[:,:,2]==4)
    assert all(c['status']=='pass' for c in stochastic.checks(frozen,inputs['settings'],data['data']))


def test_explicit_zero_rates_are_allowed_but_never_inferred(tmp_path):
    store,r,spec=setup(tmp_path,TEXT.replace('Stochastic rate birth = 5.','Stochastic rate birth = 0.').replace('Stochastic rate death = 0.5.','Stochastic rate death = 0.'))
    for target,quote in [('birth/rate','Stochastic rate birth = 0.'),('death/rate','Stochastic rate death = 0.')]:
        replace_anchor(store,r,spec,target,quote)
    for reaction in draft(spec)['reactions']:reaction['rate']=0.
    pref=proposal(store,r,spec);assert checked(store,pref)['draft_adoptable']
    selected=select(store,pref);result=tasks.execute(store,Scripted([act('run_typed'),act('finish')]),selected)
    native=store.get(tasks.result(store,result)['native_run_ref'])['payload']
    _,_,data=typed_models.result(store,native['run_ref'])
    assert data['data']['event_count']==0 and all(row==[4] for path in data['data']['trajectories'] for row in path)


def test_repeated_reactant_notation_does_not_cancel_or_duplicate_rate(tmp_path):
    quote='Reaction death: X + X -> 0.'
    store,r,spec=setup(tmp_path,TEXT.replace('Reaction death: X -> 0.',quote))
    draft(spec)['reactions'][1]['reactants']={'X':2}
    replace_anchor(store,r,spec,'death/reaction',quote)
    assert checked(store,proposal(store,r,spec))['draft_adoptable']


def test_missing_rate_can_remain_a_question_without_creating_a_model(tmp_path):
    store,r,spec=setup(tmp_path,TEXT.replace('Stochastic rate birth = 5.\n',''))
    candidate=spec['candidates'][0]
    for key in ('stochastic_draft','stochastic_rate_units','evidence_map'):candidate.pop(key)
    candidate['unknowns']=['The stochastic birth constant is missing.']
    spec['questions']=['Which stochastic birth constant and inverse-time unit are reported?'];spec['recommendation']=None
    pref=proposal(store,r,spec)
    assert proposals.inspect(store,pref)['state']=='awaiting_input'
    selected=select(store,pref,unknowns=candidate['unknowns'])
    assert tasks.status(store,selected)['state']=='awaiting_input' and not store.list('typed_model') and not store.list('typed_run')


@pytest.mark.parametrize('authorized',[True,False])
def test_only_the_authorized_scope_is_scanned_for_cross_source_conflicts(tmp_path,authorized):
    store,r,spec=setup(tmp_path);path=tmp_path/'other.txt'
    path.write_text('Stochastic rate death = 0.25.\n')
    other=evidence.ingest_source(store,path)
    config=deepcopy(tasks.load(store,r)['spec'])
    if authorized:config['source_refs'].append(other)
    new_request=tasks.prepare(store,config)
    pref=proposal(store,new_request,spec)
    assert checked(store,pref)['draft_adoptable'] is not authorized
    if authorized:
        with pytest.raises(ValueError):select(store,pref)


def test_final_report_contains_selected_rate_units_assumptions_and_original_slices(tmp_path):
    store,r,spec=setup(tmp_path);pref=proposal(store,r,spec);selected=select(store,pref)
    run=tasks.execute(store,Scripted([act('run_typed'),act('finish')]),selected)
    report=reporting.render_report(store,run)
    assert 'Explicit input selection' in report and '1/second' in report
    assert 'Mixing = well\\_mixed.' in report or 'Mixing = well_mixed.' in report
    assert 'Kinetics = time\\_homogeneous.' in report or 'Kinetics = time_homogeneous.' in report
    assert 'Stochastic rate death = 0.5.' in report



