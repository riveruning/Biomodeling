"""Existing public development model through pause, interruption and recovery."""
import json
from pathlib import Path

import pytest

from biomodel_agent import agent, bundles, fba, fba_agent_runtime as runtime, recovery, reporting
from biomodel_agent.storage import ArtifactStore
from test_fba import Scripted, act


def test_existing_textbook_pause_recover_and_report_without_second_lp(tmp_path, monkeypatch):
    fixtures=Path(__file__).parent/'fixtures/fba'
    reference=json.loads((fixtures/'source-receipt.json').read_text())
    store=ArtifactStore(tmp_path/'store')
    model=fba.import_model(store,fixtures/'textbook.xml',format='fbc')
    settings=dict(reference=dict(objective=reference['reference_objective'],source=reference['reference_url']))
    parent=agent.run_model_agent(store,Scripted([act('ask_researcher',question='Execute the frozen public development LP?')]),
        model_type='fba',model_ref=model,settings=settings,recoverable=True)
    with monkeypatch.context() as patch:
        patch.setattr(runtime,'bound_result',lambda *a,**k:(_ for _ in ()).throw(SystemExit('after confirmed LP return')))
        with pytest.raises(SystemExit):
            agent.resume_agent(store,Scripted([act('run_fba')]),parent,'Execute the fixed objective and bounds.',respondent='Synthetic workflow operator')
    execution=store.list('agent_execution')[-1]
    diagnosis=recovery.diagnose_execution(store,execution)
    assert diagnosis['status']=='ready' and diagnosis['run_count']==1,diagnosis
    saved=store.list('fba_run');assert len(saved)==1
    monkeypatch.setattr(fba,'run',lambda *a,**k:pytest.fail('second LP during recovery'))
    root=agent.recover_agent(store,Scripted([act('finish')]),execution)
    result=store.get(root)['payload'];actual=fba.summary(store,result['run_ref'])
    assert result['state']=='completed' and result['call_count']==3 and result['run_count']==1
    assert actual['completion_eligible'] and actual['objective']==pytest.approx(reference['reference_objective'],abs=1e-8)
    assert actual['comparison']['status']=='pass' and actual['scientific_support']=='unknown'
    assert store.list('fba_run')==saved and len(store.list('fba_execution'))==1
    report=reporting.render_report(store,root)
    (tmp_path/'fba-textbook-recovery-report.md').write_text(report)
    target=tmp_path/'bundle';bundles.export_bundle(store,root,target)
    assert reporting.render_report(ArtifactStore(target),root)==report
    assert recovery.diagnose_execution(store,execution)['status']=='finished'
