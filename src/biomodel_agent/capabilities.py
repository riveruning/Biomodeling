"""Shared, bounded capability descriptions; numerical backends retain authority."""
import importlib

TYPES = ('ode', 'fba', 'stochastic', 'pde', 'boolean', 'individual')
TYPED_TYPES = TYPES[2:]
VERSION = 'research_capabilities_v1'

_DESCRIPTIONS = {
    'ode': ('常微分方程', '小规模 SBML 动力学、显式建模方案与受限估参。',
            '可按来源构建受限 ODE；复杂公式、单位和实验条件仍需核查。'),
    'fba': ('稳态代谢通量', '稳态线性规划与目标值检查。',
            '可运行已有网络或提出显式假设网络；严格来源自动构建尚未接通。'),
    'stochastic': ('随机反应', '整数分子数、充分混合的质量作用 SSA。',
                   '可核对完整文本反应声明；不自动换算浓度速率常数。'),
    'pde': ('空间扩散', '一维单场、常数系数的线性扩散与衰减。',
            '使用已有输入或显式假设；尚不支持非线性、多维或多场。'),
    'boolean': ('布尔网络', '显式逻辑规则、同步或随机顺序更新。',
                '可核对完整文本规则；不从含糊的激活/抑制描述推断规则。'),
    'individual': ('格点细胞', '二维格点中的死亡、迁移、分裂与空间占位。',
                   '使用已有输入或显式假设；尚不支持连续空间、细胞力学或化学场耦合。'),
}


def catalog():
    """Fresh records shared by CLI, prompts and workbench; never scientific proof."""
    result=[]
    for kind in TYPES:
        label,summary,source_note=_DESCRIPTIONS[kind]
        strict_source=kind in ('ode','boolean','stochastic')
        row=dict(model_type=kind,label=label,summary=summary,source_note=source_note,
            capability_version=VERSION,scientific_support='unknown',
            research_modes=dict(strict_reproduction=strict_source,reconstruction=True,
                                application_modeling=True,mechanism_exploration=True),
            source_construction=dict(strict_supported=strict_source,
                route='bounded_ode_draft_or_decision_plan' if kind=='ode' else
                    'literal_complete_declarations' if strict_source else 'prepared_or_explicit_hypothesis_only'),
            project_reference_types=['time_series'] if kind=='ode' else ['fba_objective'] if kind=='fba' else [],
            tools=dict(project_literature=['europe_pmc'],
                       native_knowledge=['biomodels','sabiork'] if kind=='ode' else []))
        if kind=='ode':
            row.update(profile='existing bounded SBML ODE',entrypoint='agent/run and existing ODE CLI',
                input_formats=['sbml','model_draft','decision_plan'],agent_recovery='existing_ode_recovery',
                limitations=['Bounded SBML semantics and explicit rate-rule decisions; no general paper semantics certification.'])
        elif kind=='fba':
            row.update(profile='existing bounded steady-state LP/FBC v2',entrypoint='agent-fba/fba-run',
                input_formats=['native_json','sbml_fbc_v2'],agent_recovery='opt_in_fba_agent_v2',
                limitations=['Steady-state LP only; no dynamic FBA, FVA, MILP or general gene interventions.'])
        else:
            backend=importlib.import_module('.'+kind,__package__)
            row.update(profile=backend.PROFILE,input_formats=['native_json'],
                entrypoint='typed-import/typed-prepare/typed-execute/typed-run/typed-replay/agent-typed',
                limitations=list(backend.LIMITATIONS),
                agent_recovery='opt_in_typed_agent_v2; legacy_not_supported; unknown effects block retry')
        result.append(row)
    return result


def get(model_type):
    if model_type not in TYPES:raise ValueError('unsupported model_type; choose '+', '.join(TYPES))
    return next(row for row in catalog() if row['model_type']==model_type)
