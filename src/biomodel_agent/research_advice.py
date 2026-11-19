"""Explicitly scoped multi-family advice, never a source or an active lesson."""
from copy import deepcopy

from . import memory,model_contracts as c


def freeze(store,config,source_refs,model_refs):
    c.fields(config,{'project_id','query'},{'model_ref','conditions'})
    model=config.get('model_ref')
    if model is not None and model not in model_refs:raise ValueError('advice model must be an explicitly supplied project model')
    scope=memory.snapshot(store,project_id=config['project_id'],source_refs=source_refs,model_ref=model,context=config.get('conditions'))
    memory.recall(store,scope,config['query'],source_refs=source_refs)
    return dict(config=deepcopy(config),scope=scope,authority='advisory_only_not_evidence')


def view(store,project):
    frozen=project.get('advice')
    if frozen is None:return dict(status='disabled',results=[],authority='advisory_only_not_evidence')
    try:
        result=memory.recall(store,frozen['scope'],frozen['config']['query'],source_refs=project['source_refs'])
    except ValueError:
        return dict(status='stale_or_invalidated',results=[],authority='advisory_only_not_evidence',
            instruction='Retain original project and records; explicitly create a revised project to choose new advice. No stale advice is sent.')
    return dict(status='available',authority='advisory_only_not_evidence',**result)


def procedures():
    return {
        'stochastic':dict(profile='ensemble_moments_v1',steps=['Verify integer counts, stochastic-combinations rate convention, units and complete source declarations.',
            'Freeze seed, trajectory count, sampling grid and external moment scales before execution.',
            'Compare mean and descriptive ddof=0 variance on the identical grid; do not claim a p-value or biological validation.']),
        'boolean':dict(profile='exact_states_v1',steps=['Verify complete initial/rule/update/clamp declarations without inferring rules from qualitative biology.',
            'Freeze update mode, seed, intervention clamps and exact observed step sequence.',
            'Compare exact Boolean states, and keep intervention/context changes in a new linked iteration.']),
        'pde':dict(profile='field_rmse_v1',profiles=['field_rmse_v1','field_system_rmse_v1'],steps=['Check spatial and time units, boundary conditions, field order and grid kind.',
            'Freeze external field coordinates, per-field scales and thresholds before comparison.',
            'Use separate linked runs for spatial convergence and a continuum analytic reference where available.']),
        'individual':dict(profile='population_rmse_v1',steps=['Check lattice boundary, initial occupancy and start-of-sweep activation semantics.',
            'Freeze seed, interventions and observed population steps.',
            'Population agreement does not establish matching spatial arrangements or biological mechanisms.']),
        'fba':dict(profile='existing_fba_objective',steps=['Verify steady-state balances, flux bounds and objective coefficients.',
            'Retain LP optimality certificate and compare the frozen scalar objective.',
            'An objective match alone does not establish a unique flux solution.']),
        'ode':dict(profile='explicit_ode_decisions_v1',steps=['Distinguish qualitative mechanisms from explicitly selected rate laws.',
            'Account for every initial, parameter and rate-expression target with conditions and exact citations or exploratory assumptions.',
            'Freeze observation grid, units, scales and validation criteria before numerical execution.'])}
