import json

import pytest

from biomodel_agent import agent, diagnostics, evidence, research_projects as projects
from biomodel_agent.domain.contracts import ExperimentSpec
from biomodel_agent.storage import ArtifactStore
from test_context_budget import Scripted, action


def source(tmp_path):
    store=ArtifactStore(tmp_path/'store');path=tmp_path/'source.txt'
    path.write_text('Exact original source text.')
    return store,evidence.ingest_source(store,path),path.read_text()


@pytest.mark.parametrize('start,end',[(0,8000),(-1,4),(0,True),(4,4),(100,101)])
def test_span_error_is_actionable_without_executing_or_clipping_read(tmp_path,start,end):
    store,ref,text=source(tmp_path)
    project=projects.create(store,dict(scenario='Read the authorized source.',source_refs=[ref]))
    with pytest.raises(diagnostics.SourceReadError):
        projects.operate(store,project,'inspect_source',dict(source_ref=ref,page=1,start=start,end=end))
    _,returns,pending=projects.history(store,project)
    assert pending==[] and len(returns)==1
    returned=next(iter(returns.values()))[1]
    assert returned['state']=='rejected' and returned['result'] is None
    diagnostic=returned['diagnostic']['tool_diagnostic']
    assert diagnostic['page_characters']==len(text)
    assert diagnostic['error_type']=='invalid_source_span'
    assert diagnostic['what_engine_supports'] and diagnostic['suggested_fix']
    result=projects.operate(store,project,'inspect_source',diagnostic['example'])
    assert result['text']==text and result['end']==len(text)
    assert projects.inspect(store,project)['budget']['planner_calls']==0


def test_native_agent_receives_same_factual_repair_and_keeps_counted_history(tmp_path):
    store,ref,text=source(tmp_path)
    provider=Scripted([action('inspect_source',source_ref=ref,page=1,start=0,end=8000),
        action('inspect_source',source_ref=ref,page=1,start=0,end=len(text)),
        action('ask_researcher',question='Which scientific target should be reproduced?')])
    run=store.get(agent.run_agent(store,provider,[ref],ExperimentSpec((0.,1.)),max_calls=3))['payload']
    errors=[e for e in run['events'] if e.get('error')=='invalid source span']
    assert errors[0]['diagnostic']['example']==dict(source_ref=ref,page=1,start=0,end=len(text))
    assert run['call_count']==3 and run['state']=='awaiting_input'
    assert len([e for e in run['events'] if e.get('tool')=='inspect_source' and e.get('ok')])==1


def test_empty_page_does_not_offer_a_fabricated_read():
    with pytest.raises(diagnostics.SourceReadError) as failure:
        diagnostics.validate_source_span('source',1,0,1,0)
    assert failure.value.tool_diagnostic['example'] is None


def test_oversize_read_keeps_limit_instead_of_silently_truncating():
    with pytest.raises(diagnostics.SourceReadError) as failure:
        diagnostics.validate_source_span('source',1,0,9000,10000)
    assert failure.value.tool_diagnostic['example']['end']==8000
