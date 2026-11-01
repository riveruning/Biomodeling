"""Bounded advisory intake: evidence, explicit selection and call accounting."""
from copy import deepcopy
import json

import pytest

from biomodel_agent import bundles, cli, evidence, reporting, research_tasks as tasks, task_proposals as proposals, typed_models
from biomodel_agent.storage import ArtifactStore
from test_fba import Scripted,act
from test_research_tasks import setup as native_setup

TEXT='Initial A = False.\nInitial B = False.\nRule A = not B.\nRule B = A.\nUpdate = synchronous.\nTime unit = sweep.\nClamps = none.\n'


def setup(tmp_path,text=TEXT,quota=1):
    store=ArtifactStore(tmp_path/'store');path=tmp_path/'network.txt';path.write_text(text)
    source=evidence.ingest_source(store,path)
    config=dict(schema_version=1,goal='Follow the explicit Boolean network in the supplied source.',
        source_refs=[source],proposal_max_calls=quota,max_calls=3,execution={'settings':{'steps':3}})
    request=tasks.prepare(store,config)
    anchors=[dict(id=f'E{i}',kind='source',source_ref=source,page=1,quote=line) for i,line in enumerate(TEXT.splitlines())]
    spec=dict(schema_version=1,summary='A source-declared Boolean network is a candidate.',evidence=anchors,
        candidates=[dict(id='direct',model_type='boolean',rationale='The source explicitly supplies initial states and rules.',
            evidence_ids=[a['id'] for a in anchors],unknowns=[],boolean_draft=dict(schema_version=1,model_type='boolean',
                id='literal_network',units={'time':'sweep'},nodes=[dict(id='A',initial=False,rule='not B'),
                dict(id='B',initial=False,rule='A')],update='synchronous',clamps={}),
            evidence_map=dict(zip(['A/initial','B/initial','A/rule','B/rule','update','time_unit','clamps'],[[a['id']] for a in anchors])))],
        recommendation='direct',questions=[])
    return store,request,spec


def select(store,ref,**kwargs):
    return proposals.accept(store,ref,'direct',reviewer='Local operator',reason='Select the literal source declarations.',**kwargs)


def test_literal_boolean_source_to_model_requires_selection_then_runs_once(tmp_path):
    store,request,spec=setup(tmp_path);provider=Scripted([act('propose_task',proposal=spec)])
    ref=proposals.propose(store,provider,request)
    assert len(provider.requests)==1 and not store.list('typed_input') and not store.list('typed_run')
    before=store.list();view=proposals.inspect(store,ref)
    assert view['candidates'][0]['draft_adoptable'] and store.list()==before
    assert tasks.status(store,request)['state']=='awaiting_type'
    selected=select(store,ref)
    assert select(store,ref)==selected
    assert tasks.status(store,selected)['state']=='ready_to_run'
    assert not store.list('typed_run')
    modelref=tasks.load(store,selected)['spec']['model_ref']
    assert typed_models.load(store,modelref)['nodes']==spec['candidates'][0]['boolean_draft']['nodes']
    assert store.get(modelref)['payload']['origin']=='accepted_source_bound_boolean_proposal'
    run=tasks.execute(store,Scripted([act('run_typed'),act('finish')]),selected)
    result=tasks.result(store,run)
    assert (result['call_count'],result['proposal_call_count'],result['total_call_count'],result['run_count'])==(2,1,3,1)
    assert tasks.status(store,selected)['total_call_count']==3
    assert tasks.execute(store,None,selected)==run
    with pytest.raises(ValueError):proposals.propose(store,Scripted([]),request)
    with pytest.raises(ValueError):select(store,ref)
    bundle=tmp_path/'bundle';bundles.export_bundle(store,run,bundle)
    detached=ArtifactStore(bundle)
    assert reporting.render_report(detached,run)==reporting.render_report(store,run)
    assert proposals.load(detached,ref)==proposals.load(store,ref)
    assert len(tasks._records(detached,proposals.ATTEMPT))==1


@pytest.mark.parametrize('kind',tasks.TYPES)
def test_all_six_types_have_scaffolds_without_inventing_native_parameters(tmp_path,kind):
    store,config,actions=native_setup(tmp_path,kind)
    request=tasks.prepare(store,config)
    spec=dict(schema_version=1,summary='Select an explicitly supplied profile.',
        evidence=[dict(id='G',kind='goal',quote=config['goal'])],
        candidates=[dict(id='direct',model_type=kind,rationale='The operator explicitly supplied this native workflow.',evidence_ids=['G'],unknowns=[])],
        recommendation='direct',questions=[])
    ref=proposals.prepare(store,request,spec);view=proposals.inspect(store,ref)
    assert view['origin']=='operator_supplied' and view['external_preparation_usage']=='unknown'
    assert view['proposal_call_count']==0 and view['candidates'][0]['required_inputs']
    assert view['candidates'][0]['input_draft']['execution']==config['execution']
    selected=select(store,ref)
    assert tasks.status(store,selected)['state']=='ready_to_run'
    assert not store.list('agent_task')


@pytest.mark.parametrize('change',[
    lambda s:s['candidates'][0]['boolean_draft']['nodes'][0].update(initial=True),
    lambda s:s['candidates'][0]['boolean_draft']['nodes'][0].update(rule='B'),
    lambda s:s['candidates'][0]['boolean_draft'].update(update='random_sequential'),
    lambda s:s['candidates'][0]['boolean_draft'].update(clamps={'A':True}),
    lambda s:s['candidates'][0]['evidence_map'].pop('clamps'),
    lambda s:s['candidates'][0]['evidence_map'].update(update=['missing']),
    lambda s:s['candidates'][0]['boolean_draft']['nodes'].append(dict(id='C',initial=False,rule='A')),
    lambda s:s['candidates'][0]['boolean_draft']['nodes'][0].update(rule='__import__("os")'),
])
def test_incomplete_invented_or_unsupported_drafts_are_inspectable_but_cannot_be_selected(tmp_path,change):
    store,request,spec=setup(tmp_path);change(spec)
    ref=proposals.prepare(store,request,spec)
    view=proposals.inspect(store,ref)
    assert not view['candidates'][0]['draft_adoptable'] and view['candidates'][0]['draft_check']['issues']
    with pytest.raises(ValueError):select(store,ref)
    assert not store.list('typed_model') and not store.list('typed_run')


@pytest.mark.parametrize('extra',[
    'Initial A = True.', 'Rule A = B.', 'Update = random_sequential.',
    'Initial C = False.','Rule C = True.', 'Under a different condition: Rule A = B.',
    'Rule A = not B only during stress.', 'Clamps = A.',
])
def test_uncited_conflicts_or_qualifiers_in_authorized_scope_block_adoption(tmp_path,extra):
    store,request,spec=setup(tmp_path,TEXT+extra+'\n')
    ref=proposals.prepare(store,request,spec)
    assert not proposals.inspect(store,ref)['candidates'][0]['draft_adoptable']
    with pytest.raises(ValueError):select(store,ref)


def test_truncated_quote_cannot_strip_a_source_qualifier(tmp_path):
    text=TEXT.replace('Rule A = not B.','Rule A = not B. only under stress')
    store,request,spec=setup(tmp_path,text)
    ref=proposals.prepare(store,request,spec)
    assert not proposals.inspect(store,ref)['candidates'][0]['draft_adoptable']
    with pytest.raises(ValueError):select(store,ref)


def test_source_scope_limit_fails_closed_even_if_cited_prefix_is_complete(tmp_path):
    store,request,spec=setup(tmp_path,TEXT+'x'*64000)
    ref=proposals.prepare(store,request,spec)
    assert not proposals.inspect(store,ref)['candidates'][0]['draft_adoptable']
    with pytest.raises(ValueError):select(store,ref)


def test_ast_whitespace_equivalence_is_allowed_without_rewriting_rules(tmp_path):
    store,request,spec=setup(tmp_path)
    spec['candidates'][0]['boolean_draft']['nodes'][0]['rule']='not   (B)'
    ref=proposals.prepare(store,request,spec)
    assert proposals.inspect(store,ref)['candidates'][0]['draft_adoptable']
    selected=select(store,ref)
    assert typed_models.load(store,tasks.load(store,selected)['spec']['model_ref'])['nodes'][0]['rule']=='not   (B)'


@pytest.mark.parametrize('quota',[True,-1,4,1.0,'1'])
def test_proposal_budget_is_strict_and_opt_in(tmp_path,quota):
    with pytest.raises(ValueError):setup(tmp_path,quota=quota)


def test_default_disabled_budget_reserves_nothing(tmp_path):
    store,request,spec=setup(tmp_path,quota=0);provider=Scripted([]);before=store.list()
    with pytest.raises(ValueError):proposals.propose(store,provider,request)
    assert store.list()==before and not provider.requests


def test_budget_survives_revisions_and_index_deletion_and_cannot_be_increased(tmp_path):
    store,request,spec=setup(tmp_path)
    proposals.propose(store,Scripted([act('propose_task',proposal=spec)]),request)
    config=deepcopy(tasks.load(store,request)['spec'])
    for field in ('max_calls','proposal_max_calls'):
        changed=deepcopy(config);changed[field]+=1
        with pytest.raises(ValueError):tasks.prepare(store,changed,parent_ref=request,reason='Try to increase allowance.')
    config['goal']='Explicitly revised goal.'
    revision=tasks.prepare(store,config,parent_ref=request,reason='Clarify goal.')
    # SQLite is only an index; immutable attempt files define quota use.
    store.index_path.unlink()
    assert not store.index_path.exists()
    provider=Scripted([])
    with pytest.raises(ValueError):proposals.propose(store,provider,revision)
    assert not provider.requests and tasks.status(store,revision)['proposal_call_count']==1


def test_interrupted_unreturned_attempt_blocks_another_call_and_native_execution(tmp_path):
    store,config,_=native_setup(tmp_path,'boolean');config['proposal_max_calls']=2
    request=tasks.prepare(store,config)
    with pytest.raises(KeyboardInterrupt):proposals.propose(store,Scripted([KeyboardInterrupt()]),request)
    attempt=tasks._records(store,proposals.ATTEMPT)[0][0]
    view=tasks.status(store,request)
    assert view['proposal_call_count']==1 and view['proposal_unresolved_attempt_refs']==[attempt]
    for action in (lambda:proposals.propose(store,Scripted([]),request),lambda:tasks.execute(store,Scripted([]),request),
                   lambda:proposals.recover(store,attempt)):
        with pytest.raises(ValueError):action()
    assert not store.list('agent_task')


def test_saved_return_is_adopted_after_crash_without_provider_call(tmp_path,monkeypatch):
    store,request,spec=setup(tmp_path);original=proposals._save
    with monkeypatch.context() as patch:
        patch.setattr(proposals,'_save',lambda *a,**k:(_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(KeyboardInterrupt):proposals.propose(store,Scripted([act('propose_task',proposal=spec)]),request)
    attempt=tasks._records(store,proposals.ATTEMPT)[0][0]
    ref=proposals.recover(store,attempt)
    assert proposals.recover(store,attempt)==ref
    assert proposals.inspect(store,ref)['candidates'][0]['draft_adoptable']
    assert len(tasks._records(store,proposals.ATTEMPT))==1


@pytest.mark.parametrize('response',[act('run_typed'),{'action':'propose_task','args':{}},{'action':'propose_task','args':{'proposal':{}}},
    ValueError('simulated unavailable provider')])
def test_bad_responses_and_errors_consume_one_call_without_auto_retry(tmp_path,response):
    store,request,_=setup(tmp_path);provider=Scripted([response]);ref=proposals.propose(store,provider,request)
    assert proposals.inspect(store,ref)['state']=='invalid' and len(provider.requests)==1
    assert tasks.status(store,request)['proposal_call_count']==1 and not store.list('typed_run')
    with pytest.raises(ValueError):proposals.propose(store,Scripted([]),request)
    with pytest.raises(ValueError):select(store,ref)


def test_explicit_second_attempt_within_cap_is_recorded_and_exported(tmp_path):
    store,request,spec=setup(tmp_path,quota=2)
    bad=proposals.propose(store,Scripted([act('run_typed')]),request)
    good=proposals.propose(store,Scripted([act('propose_task',proposal=spec)]),request)
    selected=select(store,good)
    assert tasks.status(store,selected)['total_call_count']==2
    bundles.export_bundle(store,selected,tmp_path/'bundle')
    detached=ArtifactStore(tmp_path/'bundle')
    assert proposals.load(detached,bad)['state']=='invalid'
    assert tasks.status(detached,selected)['proposal_call_count']==2


@pytest.mark.parametrize('mutation',[
    lambda s:s['evidence'][0].update(source_ref='sha256:'+'0'*64),
    lambda s:s['evidence'][0].update(quote='Not in the supplied source.'),
    lambda s:s['evidence'][0].update(start=0),
    lambda s:s['evidence'][0].update(start=True,end=5),
    lambda s:s['candidates'][0].update(model_type='hybrid'),
    lambda s:s.update(recommendation='not_present'),
    lambda s:s['candidates'][0].update(evidence_ids=['absent']),
])
def test_evidence_scope_shape_and_selection_references_are_validated(tmp_path,mutation):
    store,request,spec=setup(tmp_path);mutation(spec)
    with pytest.raises(ValueError):proposals.prepare(store,request,spec)


def test_unknowns_need_explicit_acknowledgment_and_ambiguous_type_does_not_auto_select(tmp_path):
    store,request,spec=setup(tmp_path)
    spec['recommendation']=None;spec['questions']=['Should we use synchronous updating?']
    spec['candidates'][0]['unknowns']=['Applicability to the biological condition remains unverified.']
    ref=proposals.prepare(store,request,spec)
    assert proposals.inspect(store,ref)['state']=='awaiting_input'
    with pytest.raises(ValueError):select(store,ref)
    selected=select(store,ref,acknowledged_unknowns=spec['candidates'][0]['unknowns'])
    assert tasks.load(store,selected)['selection_ref']
    assert 'scientific certification' in reporting.render_report(store,ref)


def test_provider_response_binding_rejects_replaced_candidate(tmp_path):
    store,request,spec=setup(tmp_path)
    ref=proposals.propose(store,Scripted([act('propose_task',proposal=spec)]),request)
    obj=store.get(ref);payload=deepcopy(obj['payload']);payload['proposal']['summary']='A replacement not returned by the provider.'
    fake=store.put(proposals.PROPOSAL,payload,refs=tuple(obj['refs']))
    with pytest.raises(ValueError):proposals.load(store,fake)


def test_cli_manual_create_show_select_are_readable_without_service_credentials(tmp_path,capsys):
    store,request,spec=setup(tmp_path);path=tmp_path/'proposal.json';path.write_text(json.dumps(spec))
    assert cli.main(['research-proposal-create','--store',str(store.root),request,'--config',str(path)])==0
    ref=json.loads(capsys.readouterr().out)['proposal_ref'];before=store.list()
    assert cli.main(['research-proposal-show','--store',str(store.root),ref])==0
    assert json.loads(capsys.readouterr().out)['candidates'][0]['draft_adoptable'] and store.list()==before
    assert cli.main(['research-proposal-select','--store',str(store.root),ref,'--candidate','direct','--reviewer','Operator',
        '--reason','Select the declared network.'])==0
    assert json.loads(capsys.readouterr().out)['state']=='ready_to_run' and not store.list('typed_run')


def test_complete_scope_must_be_a_raw_matching_utf8_transcription(tmp_path):
    store,original,spec=setup(tmp_path);config=deepcopy(tasks.load(store,original)['spec'])
    source=store.get(config['source_refs'][0]);payload=deepcopy(source['payload']);payload['parser']='pypdf'
    ref=store.put('source',payload,refs=tuple(source['refs']));config['source_refs']=[ref]
    for anchor in spec['evidence']:anchor['source_ref']=ref
    request=tasks.prepare(store,config);proposal=proposals.prepare(store,request,spec)
    assert not proposals.inspect(store,proposal)['candidates'][0]['draft_adoptable']
    with pytest.raises(ValueError):select(store,proposal)


def test_model_evidence_is_limited_to_the_supplied_model_and_cross_type_settings_are_not_reused(tmp_path):
    store,config,_=native_setup(tmp_path,'pde');request=tasks.prepare(store,config)
    spec=dict(schema_version=1,summary='An alternative needs a different native model.',
        evidence=[dict(id='M',kind='model',model_ref=config['model_ref'])],
        candidates=[dict(id='direct',model_type='boolean',rationale='Alternative for operator review only.',evidence_ids=['M'],unknowns=[])],
        recommendation=None,questions=['Would a Boolean approximation be scientifically justified?'])
    ref=proposals.prepare(store,request,spec);candidate=proposals.inspect(store,ref)['candidates'][0]
    assert candidate['input_draft']['model_ref'] is None and candidate['input_draft']['execution']=={}
    assert candidate['missing_inputs']==['model_ref'] and candidate['input_notes']
    selected=select(store,ref)
    assert tasks.status(store,selected)['state']=='awaiting_input' and not store.list('agent_task')
    spec['evidence'][0]['model_ref']='0'*64
    with pytest.raises(ValueError):proposals.prepare(store,request,spec)


def test_repeated_quotes_need_offsets_and_goal_evidence_cannot_certify_model_rules(tmp_path):
    store,request,spec=setup(tmp_path,TEXT+'Initial A = False.\n')
    with pytest.raises(ValueError):proposals.prepare(store,request,spec)
    spec['evidence'][0].update(start=0,end=len('Initial A = False.'))
    ref=proposals.prepare(store,request,spec)
    assert proposals.inspect(store,ref)['candidates'][0]['draft_adoptable']
    spec['evidence'][0]=dict(id='E0',kind='goal',quote=tasks.load(store,request)['spec']['goal'])
    ref=proposals.prepare(store,request,spec)
    assert not proposals.inspect(store,ref)['candidates'][0]['draft_adoptable']


def test_concurrent_owner_rejects_a_planner_before_reservation(tmp_path):
    store,request,_=setup(tmp_path);provider=Scripted([])
    with tasks._owner(store,tasks.load(store,request)['family_id']):
        with pytest.raises(ValueError):proposals.propose(store,provider,request)
    assert not provider.requests and not store.list(proposals.ATTEMPT)


def test_report_and_export_show_citation_offsets_and_do_not_execute_proposals(tmp_path):
    store,request,spec=setup(tmp_path);ref=proposals.prepare(store,request,spec)
    report=reporting.render_report(store,ref)
    assert 'Initial A = False.' in report and r'page 1 \[0:' in report
    manifest=bundles.export_bundle(store,ref,tmp_path/'proposal-bundle')
    assert 'research-proposal-show' in manifest['replay']
    assert reporting.render_report(ArtifactStore(tmp_path/'proposal-bundle'),ref)==report
    assert not store.list('typed_run')


def test_usage_is_separate_and_missing_usage_is_unknown_not_zero(tmp_path):
    store,request,spec=setup(tmp_path,quota=2)
    class Metered(Scripted):
        def respond(self,messages):
            result=super().respond(messages)
            self.last_usage=dict(prompt_tokens=80,completion_tokens=40,total_tokens=120)
            return result
    first=proposals.propose(store,Metered([act('propose_task',proposal=spec)]),request)
    assert proposals.inspect(store,first)['proposal_usage']['total_tokens']==120
    second=proposals.propose(store,Scripted([act('propose_task',proposal=spec)]),request)
    usage=proposals.inspect(store,second)['proposal_usage']
    assert usage==dict(call_count=2,known_total_tokens=120,missing_total_usage_calls=1,total_tokens=None)


def test_directly_forged_higher_budget_cannot_bypass_a_reserved_family(tmp_path):
    store,request,spec=setup(tmp_path,quota=1)
    proposals.propose(store,Scripted([act('propose_task',proposal=spec)]),request)
    original=store.get(request);payload=deepcopy(original['payload']);payload['spec']['proposal_max_calls']=3
    forged=store.put(tasks.REQUEST,payload,refs=tuple(original['refs']))
    provider=Scripted([])
    with pytest.raises(ValueError):proposals.propose(store,provider,forged)
    assert not provider.requests and tasks.status(store,request)['proposal_call_count']==1


@pytest.mark.parametrize('value',[True,0,2])
def test_proposal_schema_version_is_exact_integer_one(tmp_path,value):
    store,request,spec=setup(tmp_path);spec['schema_version']=value
    with pytest.raises(ValueError):proposals.prepare(store,request,spec)
