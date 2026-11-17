"""On-demand syntax and capability guidance for literature-heavy projects."""
import json
from pathlib import Path

from . import model_contracts as contracts

EXAMPLES = ('ode', 'boolean', 'stochastic', 'pde', 'pde_system', 'individual', 'fba')
LARGE_ACTIONS = ('review_expt_background', 'open_iteration')


def model_examples():
    root = Path(__file__).parent / 'workspace_assets'
    templates = (root / 'templates').is_dir()
    assets = root / ('templates' if templates else 'examples')
    examples = {kind: json.loads((assets / ((kind if templates else 'research-' + kind) + '.json'))
                                .read_text(encoding='utf-8'))
                for kind in ('boolean', 'stochastic', 'pde', 'individual', 'fba')}
    examples['pde_system'] = json.loads((root / 'templates/pde-system.json').read_text(encoding='utf-8'))
    plan = json.loads((assets / ('ode-plan.json' if templates else 'complete-plan.json')).read_text(encoding='utf-8'))
    return dict(native_model_schema_examples=examples, ode_decision_plan_example=plan)


def frozen_model_guidance():
    from .capabilities import catalog
    from .research_advice import procedures
    return dict(**model_examples(), capabilities=catalog(), scientific_procedures=procedures(),
                schema_notice='Examples describe syntax only. Example numbers/biological relations are not task evidence.')


def coordinator_schemas(view):
    from .research_schema import schemas
    result = schemas(view)
    if view.get('background_index_refs') or view.get('background_reading_refs'):
        for name in LARGE_ACTIONS:
            if name in result:
                result[name]['properties']['args'] = dict(type='object', description=(
                    'Retrieve the complete argument schema with inspect_research_guidance, '
                    'section=action_schema and action=' + name + '. All original backend validators apply.'))
    return result


def perform(store, project_ref, args):
    from . import research_projects as projects
    from .research_schema import schemas
    contracts.fields(args, {'section'}, {'action', 'example'})
    section = args['section']
    selector = {}
    if section == 'action_schema':
        contracts.fields(args, {'section', 'action'})
        available = schemas(projects.inspect(store, project_ref))
        name = args['action']
        if not isinstance(name, str) or name not in available:
            raise ValueError('action guidance unavailable in the current project state')
        guidance = available[name]
        selector = dict(action=name)
    elif section == 'model_example':
        contracts.fields(args, {'section', 'example'})
        example = args['example']
        if example not in EXAMPLES:
            raise ValueError('unknown model example')
        examples = model_examples()
        guidance = (examples['ode_decision_plan_example'] if example == 'ode'
                    else examples['native_model_schema_examples'][example])
        selector = dict(example=example)
    elif section == 'capabilities':
        contracts.fields(args, {'section'})
        from .capabilities import catalog
        guidance = catalog()
    elif section == 'scientific_procedures':
        contracts.fields(args, {'section'})
        from .research_advice import procedures
        guidance = procedures()
    else:
        raise ValueError('unknown research guidance section')
    return dict(section=section, **selector, guidance=guidance,
                authority='syntax_and_capability_guidance', scientific_support='unknown',
                notice='Example values and biological relations are syntax illustrations. Verify task evidence separately.')
