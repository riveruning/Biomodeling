"""New material/control/result flows through the actual workbench adapters."""
import base64
from copy import deepcopy
import json
import threading
from uuid import uuid4

import pytest

from biomodel_agent import evidence,research_projects as projects,research_results
from biomodel_agent.workbench import OPERATION
from test_research_projects import opened,step
from test_workbench import app,web,request,submit,wait


def test_pdf_import_preserves_bytes_and_region_can_be_reviewed_in_workbench(app):
    engine=pytest.importorskip('pymupdf');document=engine.open();page=document.new_page(width=300,height=200);page.insert_text((30,60),'Initial x = 1.')
    raw=document.tobytes();document.close()
    job=submit(app,'import_pdf',dict(filename='equation.pdf',content=base64.b64encode(raw).decode('ascii')))
    assert job['state']=='returned';source=job['result']['source_ref']
    assert app.store.get_blob(app.store.get(source)['payload']['blob_ref'])==raw
    region=submit(app,'prepare_region',dict(source_ref=source,page=1,dpi=100))['result']['region_ref']
    assert app.region_png(region).startswith(b'\x89PNG')
    done=submit(app,'transcribe_region',dict(region_ref=region,content='Initial x = 1.',actor='tester',note='Read original image.'))
    assert done['state']=='returned' and done['result']['source_ref']!=source


def test_authenticated_http_stop_bypasses_blocked_worker(web,monkeypatch):
    wb=web.workbench;root=projects.create(wb.store,dict(scenario='Stop channel control.'))
    entered=threading.Event();release=threading.Event();original=wb._perform
    def block(operation,params):
        if operation=='snapshot_project':entered.set();assert release.wait(10)
        return original(operation,params)
    monkeypatch.setattr(wb,'_perform',block)
    job=wb.submit(uuid4().hex,'snapshot_project',dict(project_ref=root))
    try:
        assert entered.wait(5)
        code,_,_=request(web,'/api/project-stop',body=dict(project_ref=root,actor='tester',reason='Stop while worker is busy.'))
        assert code==200
        assert projects.inspect(wb.readonly,root)['control']['stop_requested']
        assert len(wb.store.list(OPERATION))==1
        code,_,_=request(web,'/api/project-stop',body=dict(project_ref=root,actor='tester',reason='Unauthorized.'),headers={'X-Workbench-Token':'wrong'})
        assert code==403
    finally:release.set()
    assert wait(wb,job)['state']=='returned'


def test_operator_source_admission_changes_scope_without_modifying_old_frame(app):
    root=projects.create(app.store,dict(scenario='Admit a reviewed supplement.'))
    from test_research_projects import frame
    before=step(app.store,root,'revise_frame',frame=frame(),reason='Freeze old scope.')['frame_ref']
    source=submit(app,'import_source',dict(filename='supplement.csv',content='time [second],x [uM]\n0,1\n1,2\n'))['result']['source_ref']
    with pytest.raises(ValueError,match='operator'):
        projects.operate(app.store,root,'admit_source',dict(source_ref=source,reason='Attempted agent admission.'))
    result=submit(app,'admit_project_source',dict(project_ref=root,source_ref=source,reason='Explicitly authorize the reviewed supplement.'))
    assert result['state']=='returned' and projects.inspect(app.readonly,root)['source_refs']==[source]
    assert projects.inspect(app.readonly,root)['frame_ref']==before


def test_result_plots_have_native_units_and_diff_the_explicit_parent(tmp_path):
    store,root,first=opened(tmp_path,native_agent=False)
    step(store,root,'run_iteration',iteration_ref=first)
    it=projects.own(store,first,projects.ITERATION,root)
    raw=deepcopy(store.get(it['config']['model_ref'])['payload']['model']);raw['nodes'][0]['rule']='B'
    child=step(store,root,'open_iteration',parent_iteration_ref=first,reason='Explicit alternative rule.',hypothesis_ids=['H1'],
        native_draft=raw,config=dict(model_type='boolean',execution=dict(settings=dict(steps=3))))['iteration_ref']
    step(store,root,'run_iteration',iteration_ref=child)
    before=set(store.list());result=research_results.collect(store,root,projects.inspect(store,root))
    assert set(store.list())==before
    first_plot=result['iterations'][0]['plots'][0]
    assert first_plot['x_unit']=='sweep' and first_plot['y_unit']=='Boolean (0/1)' and first_plot['step']
    assert first_plot['series'][0]['values'][0] in (0.,1.)
    assert result['iterations'][1]['parent_iteration_ref']==first
    assert any(change['path']=='/nodes/0/rule' for change in result['iterations'][1]['model_diff']['changes'])
