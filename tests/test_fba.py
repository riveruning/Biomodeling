"""FBA product acceptance: analytic LPs, frozen public development model and failure controls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

import libsbml
import pytest

from biomodel_agent import agent, bundles, cli, fba, fba_agent, recovery, reporting
from biomodel_agent.storage import ArtifactStore

PILOT=Path(__file__).resolve().parent/'fixtures/fba'


def model():
    return dict(schema_version=1,kind='fba',id='analytic_chain',formulation='steady_state',flux_unit='mmol_per_gDW_per_hour',
        metabolites=[dict(id='A',boundary_condition=False,compartment='c'),dict(id='B_ext',boundary_condition=True,compartment='e')],
        reactions=[dict(id='uptake',stoichiometry={'A':1.,'B_ext':-1.},lower_bound=0.,upper_bound=10.),
                   dict(id='biomass',stoichiometry={'A':-1.},lower_bound=0.,upper_bound=None)],
        objective=dict(sense='maximize',coefficients={'biomass':1.}))


def imported(tmp_path,m=None):
    store=ArtifactStore(tmp_path/'store');path=tmp_path/'model.json';path.write_text(json.dumps(m or model()))
    return store,fba.import_model(store,path)


def act(action,**args):return dict(action=action,args=args)


class Scripted:
    last_usage={}
    def __init__(self,actions):self.actions=iter(actions);self.requests=[]
    def respond(self,messages):
        self.requests.append(deepcopy(messages));value=next(self.actions)
        if isinstance(value,BaseException):raise value
        return value


def sleeping_worker(model,settings,connection):
    time.sleep(5.)


def crashing_worker(model,settings,connection):
    connection.close()


@pytest.mark.parametrize('sense,expected',[('maximize',10.),('minimize',0.)])
def test_actual_analytic_solution_and_independent_reference(tmp_path,sense,expected):
    m=model();m['objective']['sense']=sense;store,ref=imported(tmp_path,m)
    run=fba.run(store,ref,dict(reference=dict(objective=expected,source='analytic steady-state chain')))
    out=fba.summary(store,run)
    assert out['status']=='optimal' and out['completion_eligible'],out
    assert out['objective']==pytest.approx(expected,abs=1e-8)
    assert out['comparison']['status']=='pass' and out['scientific_support']=='unknown'
    assert all(c['status']=='pass' for c in out['checks'])
    assert 'FBA steady-state LP' in reporting.render_report(store,run)
    assert store.get(store.get(run)['payload']['input_ref'])['payload']['model_ref']==ref


@pytest.mark.parametrize('method',['highs-ds','highs-ipm'])
def test_public_textbook_matches_official_objective(tmp_path,method):
    data=(PILOT/'textbook.xml').read_bytes();receipt=json.loads((PILOT/'source-receipt.json').read_text())
    assert hashlib.sha256(data).hexdigest()==receipt['sbml_sha256']
    store=ArtifactStore(tmp_path/'store');ref=fba.import_model(store,PILOT/'textbook.xml',format='fbc')
    m=fba.load(store,ref)
    assert len(m['metabolites'])==72 and len(m['reactions'])==95
    assert m['flux_unit']=='mmol_per_gDW_per_hr'
    run=fba.run(store,ref,dict(method=method,reference=dict(objective=receipt['reference_objective'],source=receipt['reference_url'])))
    out=fba.summary(store,run)
    assert out['completion_eligible'] and out['objective']==pytest.approx(.8739215069684307,abs=1e-8),out
    assert out['checks'][0]['max_abs']<1e-8
    assert store.get(ref)['payload']['source_blob_ref'] in store.get(ref)['refs']


@pytest.mark.parametrize('case,expected',[('infeasible','infeasible'),('unbounded','unbounded')])
def test_infeasible_and_unbounded_are_saved_failures(tmp_path,case,expected):
    m=model()
    if case=='infeasible':m['reactions'][1]['lower_bound']=11.
    else:m['reactions'][0]['upper_bound']=None
    store,ref=imported(tmp_path,m);run=fba.run(store,ref);out=fba.summary(store,run)
    assert out['status']==expected and not out['completion_eligible']
    assert out['objective'] is None and out['fluxes'] is None
    assert store.get(run)['payload']['receipt_ref'] in store.get(run)['refs']


def test_wrong_reference_does_not_change_the_model_or_threshold(tmp_path):
    store,ref=imported(tmp_path)
    frozen=deepcopy(store.get(ref))
    run=fba.run(store,ref,dict(reference=dict(objective=99.,source='deliberately wrong analytic control')))
    out=fba.summary(store,run)
    assert out['status']=='optimal' and out['comparison']['status']=='fail' and not out['completion_eligible']
    assert store.get(ref)==frozen and out['objective']==10.


def test_reversible_negative_flux_bounds_are_preserved(tmp_path):
    m=model();m['reactions'][0].update(lower_bound=-10.,upper_bound=0.,stoichiometry={'A':-1.,'B_ext':1.})
    store,ref=imported(tmp_path,m);out=fba.summary(store,fba.run(store,ref))
    assert out['completion_eligible'] and out['fluxes']['uptake']==-10. and out['fluxes']['biomass']==10.


def test_boundary_species_are_explicitly_excluded_not_balanced(tmp_path):
    m=model();m['metabolites'][0]['boundary_condition']=True;m['reactions'][1]['upper_bound']=100.
    store,ref=imported(tmp_path,m);out=fba.summary(store,fba.run(store,ref))
    assert out['completion_eligible'] and out['objective']==100.


@pytest.mark.parametrize('mutation',['bool_bound','nan','infinity','unknown_species','unknown_objective','duplicate_metabolite',
    'duplicate_reaction','missing_bound','inverted_bounds','unsupported_field','zero_objective','zero_stoich','bad_formulation','bool_version','huge_integer','missing_unit'])
def test_bad_native_inputs_are_rejected(mutation):
    m=model()
    if mutation=='bool_bound':m['reactions'][0]['lower_bound']=False
    if mutation=='nan':m['objective']['coefficients']['biomass']=float('nan')
    if mutation=='infinity':m['reactions'][0]['upper_bound']=float('inf')
    if mutation=='unknown_species':m['reactions'][0]['stoichiometry']['unknown']=1.
    if mutation=='unknown_objective':m['objective']['coefficients']={'unknown':1.}
    if mutation=='duplicate_metabolite':m['metabolites'].append(deepcopy(m['metabolites'][0]))
    if mutation=='duplicate_reaction':m['reactions'].append(deepcopy(m['reactions'][0]))
    if mutation=='missing_bound':m['reactions'][0].pop('lower_bound')
    if mutation=='inverted_bounds':m['reactions'][0]['lower_bound']=11.
    if mutation=='unsupported_field':m['gene_edits']={}
    if mutation=='zero_objective':m['objective']['coefficients']['biomass']=0.
    if mutation=='zero_stoich':m['reactions'][0]['stoichiometry']['A']=0.
    if mutation=='bad_formulation':m['formulation']='dynamic'
    if mutation=='bool_version':m['schema_version']=True
    if mutation=='huge_integer':m['reactions'][0]['lower_bound']=10**1000
    if mutation=='missing_unit':m['flux_unit']=''
    with pytest.raises(ValueError):fba.validate(m)


@pytest.mark.parametrize('settings',[{'method':'simplex'},{'timeout_s':True},{'timeout_s':0.},{'timeout_s':61.},
    {'threshold':1.},{'reference':{'objective':1.,'source':''}},{'reference':{'objective':True,'source':'analytic'}},
    {'reference':{'objective':1.,'source':'analytic','tolerance':1.}}])
def test_solver_and_reference_settings_cannot_be_relaxed(settings):
    with pytest.raises(ValueError):fba.config(settings)


def test_duplicate_json_keys_and_input_size_are_rejected(tmp_path,monkeypatch):
    store=ArtifactStore(tmp_path/'store');p=tmp_path/'bad.json';p.write_text('{"kind":"fba","kind":"ode"}')
    with pytest.raises(ValueError,match='duplicate'):fba.import_model(store,p)
    monkeypatch.setattr(fba,'MAX_BYTES',3)
    with pytest.raises(ValueError,match='size'):fba.import_model(store,p)


@pytest.mark.parametrize('mutation',['strict_false','missing_bound','variable_bound','kinetic_law','rule','variable_stoich','missing_stoich','mixed_units','missing_objective','unknown_reaction'])
def test_unsupported_fbc_semantics_are_not_silently_dropped(tmp_path,mutation):
    doc=libsbml.readSBMLFromFile(str(PILOT/'textbook.xml'));m=doc.getModel();r=m.getReaction(0);plugin=r.getPlugin('fbc')
    if mutation=='strict_false':m.getPlugin('fbc').setStrict(False)
    if mutation=='missing_bound':plugin.unsetUpperFluxBound()
    if mutation=='variable_bound':m.getParameter(plugin.getUpperFluxBound()).setConstant(False)
    if mutation=='kinetic_law':r.createKineticLaw().setMath(libsbml.parseL3Formula('1'))
    if mutation=='rule':
        rule=m.createAssignmentRule();rule.setVariable(plugin.getUpperFluxBound());rule.setMath(libsbml.parseL3Formula('1'))
    if mutation=='variable_stoich':r.getReactant(0).setConstant(False)
    if mutation=='missing_stoich':r.getReactant(0).unsetStoichiometry()
    if mutation=='mixed_units':m.getParameter(plugin.getUpperFluxBound()).unsetUnits()
    if mutation=='missing_objective':m.getPlugin('fbc').unsetActiveObjectiveId()
    if mutation=='unknown_reaction':m.getPlugin('fbc').getObjective(0).getFluxObjective(0).setReaction('does_not_exist')
    path=tmp_path/'bad.xml';libsbml.writeSBMLToFile(doc,str(path))
    with pytest.raises(ValueError):fba.import_model(ArtifactStore(tmp_path/'store'),path,format='fbc')


@pytest.mark.parametrize('injection',[b'<!DOCTYPE sbml [<!ENTITY x "bad">]>',b'<!ENTITY x "bad">'])
def test_xml_declarations_are_rejected(tmp_path,injection):
    p=tmp_path/'bad.xml';p.write_bytes(injection+(PILOT/'textbook.xml').read_bytes())
    with pytest.raises(ValueError):fba.import_model(ArtifactStore(tmp_path/'store'),p,format='fbc')


def test_unknown_sbml_package_is_not_ignored(tmp_path):
    text=(PILOT/'textbook.xml').read_text().replace('<sbml ','<sbml xmlns:unsupported="http://www.sbml.org/sbml/level3/version1/unsupported/version1" ',1)
    p=tmp_path/'unknown.xml';p.write_text(text)
    with pytest.raises(ValueError,match='package'):fba.import_model(ArtifactStore(tmp_path/'store'),p,format='fbc')


def test_changed_derived_model_is_rejected_against_original_blob(tmp_path):
    store,ref=imported(tmp_path);obj=store.get(ref);changed=deepcopy(obj['payload'])
    changed['model']['reactions'][0]['upper_bound']=999.
    forged=store.put('fba_model',changed,refs=obj['refs'])
    with pytest.raises(ValueError,match='recomputed'):fba.load(store,forged)


@pytest.mark.parametrize('x,reported',[([10.,9.],9.),([11.,11.],11.),([10.,10.],999.),([float('inf'),1.],1.),([1e308,1e308],1e308)])
def test_solver_success_does_not_override_bad_actual_fluxes(x,reported):
    out=fba.checks(fba.validate(model()),fba.config(),dict(status='optimal',fluxes=x,solver_objective=reported))
    assert not out['completion_eligible'] and out['status']=='numerical_failure'
    json.dumps(out,allow_nan=False)


def test_parent_deadline_and_worker_crash_do_not_produce_optima():
    start=time.monotonic()
    out=fba.solve(model(),dict(timeout_s=.2),_target=sleeping_worker)
    assert out['status']=='time_limit' and time.monotonic()-start<2.
    out=fba.solve(model(),dict(timeout_s=5.),_target=crashing_worker)
    assert out['status']=='solver_error'


def test_report_and_summary_ignore_forged_completion_string(tmp_path):
    store,ref=imported(tmp_path);run=fba.run(store,ref,dict(reference=dict(objective=99.,source='wrong reference')));obj=store.get(run);bad=deepcopy(obj['payload'])
    bad['verification']['completion_eligible']=True
    forged=store.put('fba_run',bad,refs=obj['refs'])
    assert not fba.summary(store,forged)['completion_eligible']
    assert 'Completion eligible: False' in reporting.render_report(store,forged)


def test_native_cli_replay_and_portable_bundle(tmp_path,capsys):
    store,model_ref=imported(tmp_path)
    assert cli.main(['fba-run',model_ref,'--store',str(store.root)])==0
    data=json.loads(capsys.readouterr().out);run=data['ref']
    assert cli.main(['fba-replay',run,'--store',str(store.root)])==0
    replay=json.loads(capsys.readouterr().out)['ref']
    assert store.get(replay)['payload']['replay_of']==run
    manifest=bundles.export_bundle(store,run,tmp_path/'bundle')
    assert len(manifest['objects_and_blobs'])>=6
    assert fba.summary(ArtifactStore(tmp_path/'bundle'),run)==fba.summary(store,run)


def test_cli_import_and_failure_exit_code(tmp_path,capsys):
    p=tmp_path/'m.json';m=model();m['reactions'][1]['lower_bound']=11.;p.write_text(json.dumps(m))
    root=str(tmp_path/'store')
    assert cli.main(['fba-import',str(p),'--store',root])==0
    ref=json.loads(capsys.readouterr().out)['ref']
    assert cli.main(['fba-run',ref,'--store',root])==2
    assert json.loads(capsys.readouterr().out)['status']=='infeasible'


def test_fba_agent_routes_to_one_real_frozen_lp(tmp_path):
    store,ref=imported(tmp_path)
    provider=Scripted([act('inspect_fba'),dict(action='run_fba',args={},reason='Execute frozen LP'),act('finish')])
    root=agent.run_model_agent(store,provider,model_type='fba',model_ref=ref)
    p=store.get(root)['payload']
    assert p['state']=='completed' and p['call_count']==3 and p['run_count']==1
    assert fba.summary(store,p['run_ref'])['objective']==10.
    assert p['model_type']=='fba' and 'Provider calls: 3' in reporting.render_report(store,root)
    assert any(store.get(r)['payload'].get('name')=='run_fba' and store.get(r)['payload'].get('phase')=='reserved' for r in p['invocation_refs'])
    report=recovery.diagnose_execution(store,p['execution_ref'])
    assert report['status']=='blocked' and any('fba_recovery_not_supported' in r for r in report['reasons'])


@pytest.mark.parametrize('bad_action',[act('submit_draft'),act('run'),act('run_fba',upper_bound=999.),act('run_study')])
def test_fba_agent_cannot_switch_to_ode_or_edit_the_lp(tmp_path,bad_action):
    store,ref=imported(tmp_path);before=deepcopy(store.get(ref))
    root=agent.run_model_agent(store,Scripted([bad_action]),model_type='fba',model_ref=ref)
    p=store.get(root)['payload']
    assert p['state']=='incomplete' and p['run_count']==0 and not store.list('fba_run')
    assert store.get(ref)==before


def test_fba_agent_repeated_run_is_blocked_before_second_scientific_call(tmp_path):
    store,ref=imported(tmp_path)
    root=fba_agent.run_agent(store,Scripted([act('run_fba'),act('run_fba')]),ref)
    p=store.get(root)['payload']
    assert p['state']=='incomplete' and p['run_count']==1 and len(store.list('fba_run'))==1


def test_fba_agent_numerical_reference_failure_stops_without_another_model_call(tmp_path):
    store,ref=imported(tmp_path)
    root=fba_agent.run_agent(store,Scripted([act('run_fba')]),ref,settings=dict(reference=dict(objective=99.,source='wrong fixture')))
    p=store.get(root)['payload']
    assert p['state']=='incomplete' and p['call_count']==1 and p['run_count']==1


def test_unknown_scientific_effect_is_not_retried(tmp_path,monkeypatch):
    store,ref=imported(tmp_path)
    def fail(*args,**kwargs):raise RuntimeError('synthetic unknown result')
    monkeypatch.setattr(fba,'run',fail)
    root=fba_agent.run_agent(store,Scripted([act('run_fba')]),ref)
    p=store.get(root)['payload']
    assert p['state']=='incomplete' and p['run_count']==1 and p['run_ref'] is None
    assert any(store.get(r)['payload'].get('phase')=='error' for r in p['invocation_refs'])


def test_unsupported_model_type_is_explicit(tmp_path):
    store,ref=imported(tmp_path)
    with pytest.raises(ValueError,match='model_type'):agent.run_model_agent(store,Scripted([]),model_type='pde',model_ref=ref)
    wrong=store.put('model',dict(format='sbml'))
    with pytest.raises(ValueError,match='fba_model'):fba.load(store,wrong)



def test_feasible_but_nonoptimal_flux_cannot_reuse_a_success_flag(tmp_path):
    store,ref=imported(tmp_path);run=fba.run(store,ref)
    original=store.get(run);changed=deepcopy(original['payload'])
    changed['result'].update(fluxes=[0.,0.],solver_objective=0.)
    out=fba.checks(fba.load(store,ref),fba.config(),changed['result'])
    assert not out['completion_eligible'] and out['status']=='numerical_failure'
    assert next(c for c in out['checks'] if c['code']=='lp_optimality_certificate')['status']=='fail'


def test_missing_dual_certificate_does_not_count_as_a_verified_optimum(tmp_path):
    store,ref=imported(tmp_path);run=fba.run(store,ref);original=store.get(run)
    changed=deepcopy(original['payload']);changed['result'].pop('certificate')
    assert not fba.checks(fba.load(store,ref),fba.config(),changed['result'])['completion_eligible']


def test_provider_failure_is_saved_without_retry_or_raw_message(tmp_path):
    store,ref=imported(tmp_path)
    root=fba_agent.run_agent(store,Scripted([RuntimeError('private diagnostic should not be copied')]),ref)
    p=store.get(root)['payload']
    assert p['call_count']==1 and p['state']=='incomplete' and p['run_count']==0
    assert 'private diagnostic' not in json.dumps(p)


def test_scientific_budget_is_reserved_before_callback(tmp_path,monkeypatch):
    store,ref=imported(tmp_path);put=store.put
    def crash(kind,payload,refs=None):
        out=put(kind,payload,refs)
        if kind=='agent_invocation' and payload.get('name')=='run_fba' and payload.get('phase')=='reserved':
            raise SystemExit('reservation persisted')
        return out
    monkeypatch.setattr(store,'put',crash)
    monkeypatch.setattr(fba,'run',lambda *a,**k:pytest.fail('scientific callback ran before durable reservation'))
    with pytest.raises(SystemExit):fba_agent.run_agent(store,Scripted([act('run_fba')]),ref)
    records=[store.get(r)['payload'] for r in store.list('agent_invocation')]
    reservation=next(r for r in records if r.get('name')=='run_fba')
    assert reservation['run_count']==1 and reservation['phase']=='reserved'


def test_ode_router_preserves_existing_arguments(tmp_path,monkeypatch):
    store,ref=imported(tmp_path);received=[]
    def ode(*args,**kwargs):received.append(kwargs);return 'sentinel'
    monkeypatch.setattr(agent,'run_agent',ode)
    assert agent.run_model_agent(store,Scripted([]),model_type='ode',source_refs=['original'],experiment='original')=='sentinel'
    assert received==[dict(source_refs=['original'],experiment='original')]



def test_result_cannot_be_detached_from_its_actual_return_receipt(tmp_path):
    store,ref=imported(tmp_path);run=fba.run(store,ref);obj=store.get(run)
    bad=deepcopy(obj['payload']);bad['result']['fluxes']=[0.,0.]
    forged=store.put('fba_run',bad,refs=obj['refs'])
    with pytest.raises(ValueError,match='receipt binding'):fba.summary(store,forged)



def test_fbc_infinite_upper_bound_is_preserved_as_explicit_none(tmp_path):
    doc=libsbml.readSBMLFromFile(str(PILOT/'textbook.xml'));m=doc.getModel()
    rid=m.getReaction(0).getId();key=m.getReaction(0).getPlugin('fbc').getUpperFluxBound()
    m.getParameter(key).setValue(float('inf'))
    p=tmp_path/'unlimited.xml';libsbml.writeSBMLToFile(doc,str(p))
    store=ArtifactStore(tmp_path/'store');ref=fba.import_model(store,p,format='fbc')
    assert next(r for r in fba.load(store,ref)['reactions'] if r['id']==rid)['upper_bound'] is None


def test_fbc_dangling_compartment_is_rejected_by_semantic_validation(tmp_path):
    doc=libsbml.readSBMLFromFile(str(PILOT/'textbook.xml'));doc.getModel().getSpecies(0).setCompartment('not_declared')
    p=tmp_path/'invalid.xml';libsbml.writeSBMLToFile(doc,str(p))
    with pytest.raises(ValueError):fba.import_model(ArtifactStore(tmp_path/'store'),p,format='fbc')
