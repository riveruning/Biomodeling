"""Local synthetic browser fixture. No model service or credentials are enabled."""
import hashlib
import json
from pathlib import Path
import sys
import threading

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))

from biomodel_agent import material_review,research_projects as p,research_supervisor as supervisor
from biomodel_agent.storage import canonical_bytes
from biomodel_agent.workbench import Workbench
from biomodel_agent.workbench_server import Handler,Server


def main():
    app=Workbench(sys.argv[1]);store=app.store
    paid=p.create(store,dict(scenario='Browser saved decision fixture.',policy=dict(planner_max_calls=1)))
    messages=supervisor._messages(store,paid);scope=hashlib.sha256(canonical_bytes(messages)).hexdigest()
    identity={'class':'offline.fixture','endpoint_sha256':None}
    prompt=store.put('research_project_prompt',dict(project_ref=paid,messages=messages,scope_sha256=scope,provider=identity),refs=(paid,))
    action=dict(action='scenario_source',args={},reason='Already paid fixture response; no service call.')
    def saved(attempt):
        ref=store.put(p.RESPONSE,dict(version=1,project_ref=paid,attempt_ref=attempt,request_ref=prompt,scope_sha256=scope,
            provider=identity,response=action,usage={'total_tokens':7},elapsed_s=.001),refs=(paid,attempt,prompt))
        return dict(response_ref=ref,response=action,usage={'total_tokens':7})
    p._operate_owned(store,paid,'respond',dict(request_ref=prompt,scope_sha256=scope,provider=identity),callback=saved)
    plotted=p.create(store,dict(scenario='Browser chart fixture.',policy=dict(planner_max_calls=1)))
    frame=dict(objective='Synthetic Boolean browser control.',mode='application_modeling',granularity='Two nodes.',modeling_targets=['A and B'],
        rationale='Test stored state visualization.',evidence_needs=[],assumptions=[dict(id='H1',statement='Synthetic rules only.',rationale='A UI control.',evidence_ids=[],decision='adopt')],
        criteria=[dict(id='C1',kind='native_checks',description='Native checks.',required=True)],evidence=[],unresolved=['Biological support unknown.'])
    p.operate(store,plotted,'revise_frame',dict(frame=frame,reason='Freeze browser control.'),actor='operator')
    model=json.loads((ROOT/'examples/model-types/boolean.json').read_text())
    it=p.operate(store,plotted,'open_iteration',dict(reason='Plot stored output.',native_draft=model,hypothesis_ids=['H1'],
        config=dict(model_type='boolean',execution=dict(settings=dict(steps=4)))),actor='operator')['iteration_ref']
    p.operate(store,plotted,'run_iteration',dict(iteration_ref=it),actor='operator')
    region=None
    try:
        import pymupdf
        from biomodel_agent import evidence
        document=pymupdf.open();page=document.new_page(width=300,height=200);page.insert_text((30,60),'Initial x = 1.')
        path=Path(sys.argv[1])/'browser-fixture.pdf';path.write_bytes(document.tobytes());document.close()
        source=evidence.ingest_source(store,path);region=material_review.region(store,source,1,dpi=100)
    except ImportError:pass
    config=dict(paid=paid,plotted=plotted,region=region)
    script=(ROOT/'scripts/tests/workbench-browser-harness.js').read_text().replace('__FIXTURE__',json.dumps(config))
    class FixtureHandler(Handler):
        def do_GET(self):
            if self.path=='/browser-smoke.js':
                if self._guard(False):self._send(200,script.encode(),'text/javascript; charset=utf-8')
            else:super().do_GET()
        def _send(self,code,data,content_type='application/json; charset=utf-8',filename=None):
            if code==200 and content_type.startswith('text/html') and isinstance(data,bytes):
                data=data.replace(b'</head>',b'<script src="/browser-smoke.js" defer></script></head>')
            super()._send(code,data,content_type,filename)
    server=Server(app);server.RequestHandlerClass=FixtureHandler
    print(json.dumps(dict(origin=server.origin,**config)),flush=True)
    try:server.serve_forever(poll_interval=.05)
    finally:server.server_close();app.close()


if __name__=='__main__':main()
