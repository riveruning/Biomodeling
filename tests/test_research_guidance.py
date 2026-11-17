"""On-demand guides retain native syntax and normal execution authorities."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from biomodel_agent import bundles, context, research_guidance as guidance
from biomodel_agent import research_projects as projects, research_schema, research_supervisor as supervisor
from test_research_background import indexed
from test_research_projects import frame
from test_research_provider_integration import adapter


def inspect_guide(store, project, **args):
    result = projects.operate(store, project, 'inspect_research_guidance', args)
    messages = supervisor._messages(store, project)
    assert context.size(messages) <= 60000
    observed = [json.loads(message['content']) for message in messages if message['role'] == 'user']
    assert any(row.get('tool') == 'inspect_research_guidance' and row.get('result') == result
               for row in observed)
    assert result['scientific_support'] == 'unknown'
    return result['guidance']


def test_complete_schemas_and_all_examples_remain_available_and_exported(tmp_path):
    store, project, _, _, _ = indexed(tmp_path)
    initial = supervisor._messages(store, project)
    assert initial[0]['content'] == supervisor.REQUIREMENTS_PROMPT
    state = json.loads(initial[-1]['content'])
    assert 'inspect_research_guidance' in state['action_schemas']
    assert state['action_schemas']['review_expt_background']['properties']['args']['properties']['reviews']['maxItems'] == 0
    full = inspect_guide(store, project, section='action_schema', action='review_expt_background')
    assert full == research_schema.schemas(projects.inspect(store, project))['review_expt_background']
    projects.operate(store, project, 'revise_frame', dict(frame=frame(), reason='Freeze explicit assumptions.'))
    assert supervisor._messages(store, project)[0]['content'] == supervisor.PROMPT
    full = inspect_guide(store, project, section='action_schema', action='open_iteration')
    assert full == research_schema.schemas(projects.inspect(store, project))['open_iteration']
    assets = Path(__file__).parents[1] / 'src/biomodel_agent/workspace_assets/templates'
    for example in guidance.EXAMPLES:
        result = inspect_guide(store, project, section='model_example', example=example)
        filename = {'ode': 'ode-plan', 'pde_system': 'pde-system'}.get(example, example) + '.json'
        assert result == json.loads((assets / filename).read_text())
    legacy = guidance.frozen_model_guidance()
    assert inspect_guide(store, project, section='capabilities') == legacy['capabilities']
    assert inspect_guide(store, project, section='scientific_procedures') == legacy['scientific_procedures']
    snapshot = projects.snapshot(store, project)
    manifest = bundles.export_bundle(store, snapshot, tmp_path / 'bundle')
    attempts, returned, _ = projects.history(store, project)
    for ref, attempt in attempts:
        if attempt['operation'] == 'inspect_research_guidance':
            assert ref in manifest['objects_and_blobs']
            assert returned[ref][0] in manifest['objects_and_blobs']


@pytest.mark.parametrize('args', [
    dict(section='unknown'), dict(section='action_schema', action='open_iteration'),
    dict(section='action_schema', action='private_files'), dict(section='action_schema', action=[]),
    dict(section='model_example', example='invented'), dict(section='model_example'),
    dict(section='capabilities', example='boolean'), dict(section='scientific_procedures', action='run_iteration'),
])
def test_guidance_rejects_invalid_selectors_and_unavailable_actions(tmp_path, args):
    store, project, _, _, _ = indexed(tmp_path)
    with pytest.raises(ValueError):
        projects.operate(store, project, 'inspect_research_guidance', args)
    assert projects.inspect(store, project)['state'] == 'ready'


def test_embedded_guidance_duplicate_gives_way_before_any_essential_overflow(tmp_path, monkeypatch):
    """Segment-22 regression (live run dsh48-normal-transcribe-20-01): the
    non-indexed planner context embedded byte copies of the guidance sections
    that inspect_research_guidance already serves (7k capabilities + 4k model
    examples + 2k procedures in the always-essential frozen message), and the
    next decision failed with ContextBudgetExceeded 64119>60000 once the
    retained stage state grew. Under essential pressure only that duplicate
    is dropped; every section stays reachable through the normal tool and the
    configured budget itself is never raised or truncated."""
    store, project, _, expt, _ = indexed(tmp_path)
    root = projects.create(store, dict(scenario='Reproduce one quoted parameter image.',
                                       mode='reproduction', source_refs=[expt]))
    monkeypatch.setattr(guidance, 'frozen_model_guidance', lambda: {
        key: 'x' * 18000 for key in ('native_model_schema_examples', 'ode_decision_plan_example',
                                     'capabilities', 'scientific_procedures', 'schema_notice')})
    messages = supervisor._messages(store, root)
    assert context.size(messages) <= 60000
    frozen = json.loads(messages[1]['content'])
    assert 'xxxx' not in messages[1]['content']
    assert frozen['guidance_sections']['retrieve'] == 'inspect_research_guidance'
    from biomodel_agent.capabilities import catalog
    assert inspect_guide(store, root, section='capabilities') == catalog()
    assert inspect_guide(store, root, section='model_example', example='boolean') == (
        guidance.model_examples()['native_model_schema_examples']['boolean'])
    # Scientific state is untouched by the fallback: project, policy and the
    # stage-specific action schemas remain in the retained latest state.
    state = json.loads(messages[-1]['content'])
    assert state['project']['project_ref'] == root
    assert 'revise_frame' in state['action_schemas']


def test_normal_chat_supervisor_uses_guide_then_executes_native_boolean(tmp_path, monkeypatch):
    store, project, mm, expt, _ = indexed(tmp_path)
    # Recreate with a real planner allowance while retaining the same source scope.
    project = projects.create(store, dict(scenario='Exercise an explicitly hypothetical Boolean model.',
        mode='reproduction', source_refs=[mm, expt], policy=dict(planner_max_calls=3)))
    projects.operate(store, project, 'index_mm_background', dict(source_ref=mm))
    projects.operate(store, project, 'revise_frame', dict(frame=frame(), reason='Use explicit development assumptions.'))

    def open_iteration():
        attempts, returned, _ = projects.history(store, project)
        guide = next(returned[ref][1]['result']['guidance'] for ref, attempt in reversed(attempts)
                     if attempt['operation'] == 'inspect_research_guidance')
        return dict(action='open_iteration', args=dict(reason='Use the retrieved syntax with explicit H1.',
            config=dict(model_type='boolean', execution=dict(settings=dict(steps=4, seed=1))),
            native_draft=deepcopy(guide), hypothesis_ids=['H1'], native_agent=False))

    def run_iteration():
        ref = projects.inspect(store, project)['iteration_refs'][-1]
        return dict(action='run_iteration', args=dict(iteration_ref=ref))

    provider, requests = adapter(monkeypatch, [
        dict(action='inspect_research_guidance', args=dict(section='model_example', example='boolean')),
        open_iteration, run_iteration])
    supervisor.run(store, provider, project)
    view = projects.inspect(store, project)
    assert len(requests) == 3 and view['state'] == 'planner_budget_exhausted'
    facts = projects.facts(store, view['iteration_refs'][0])
    assert facts['executed'] and facts['numeric_status'] == 'pass'
    assert facts['scientific_support'] == 'unknown'
    usage = supervisor.usage(store, project)
    assert usage['reported_total_tokens'] == 36 and usage['missing_usage_calls'] == 0
    assert all(context.size(request['messages']) <= 60000 for request in requests)


def test_completed_source_reading_without_mm_index_keeps_model_guidance_available(tmp_path):
    from test_research_reading import claim
    store, _, _, expt, _ = indexed(tmp_path)
    project = projects.create(store, dict(scenario='Screen this full-text article.',
                                         mode='reproduction', source_refs=[expt]))
    packet = projects.operate(store, project, 'start_expt_reading', dict(source_ref=expt))
    final = projects.operate(store, project, 'record_expt_chunk', claim(packet))
    assert final['summary']['fulltext_reviewed']
    projects.operate(store, project, 'revise_frame',
                     dict(frame=frame(), reason='Exercise the next coordinator stage.'), actor='operator')
    projects.operate(store, project, 'inspect_expt_reading', dict(reading_ref=final['reading_ref']))
    messages = supervisor._messages(store, project)
    assert context.size(messages) <= 60000
    frozen = json.loads(messages[1]['content'])
    assert {'native_model_schema_examples', 'ode_decision_plan_example',
            'capabilities', 'scientific_procedures'}.isdisjoint(frozen)
    state = json.loads(messages[-1]['content'])
    assert state['project']['background_index_refs'] == []
    assert 'inspect_research_guidance' in state['action_schemas']
    assert 'properties' not in state['action_schemas']['open_iteration']['properties']['args']
    assert inspect_guide(store, project, section='action_schema', action='open_iteration') == (
        research_schema.schemas(projects.inspect(store, project))['open_iteration'])
    assert inspect_guide(store, project, section='model_example', example='boolean') == (
        guidance.model_examples()['native_model_schema_examples']['boolean'])
