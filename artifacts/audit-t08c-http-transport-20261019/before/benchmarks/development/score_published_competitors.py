"""External scoring of published-source integration variants and controls."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np

from competitor_common import OUT, ROOT, verify_source, write_json
from score_comparison import compare_trajectory


def compare_imported(times, values, case, threshold=.001):
    observed_times=np.asarray(times,dtype=float)
    expected_times=np.asarray(case['times'],dtype=float)
    if (observed_times.shape!=expected_times.shape or
        not np.allclose(observed_times,expected_times,rtol=0,atol=1e-8)):
        return {'status':'fail','reason':'wrong_sampling_grid',
                'observed_samples':len(observed_times),'expected_samples':len(expected_times)}
    names={n.get('id'):n.get('name',n.get('id'))
           for n in ET.fromstring((ROOT/case['sbml']).read_text()).iter()
           if n.tag.endswith('}species')}
    errors={};mapping={}
    for species in case['species']:
        candidates=[species,names.get(species),f'[{species}]',f'[{names.get(species)}]']
        column=next((k for k in candidates if k in values),None)
        if column is None:return {'status':'fail','reason':'missing_species','species':species}
        data=np.asarray(values[column],dtype=float)
        reference=np.asarray(case['reference'][species],dtype=float)
        if data.shape!=reference.shape or not np.isfinite(data).all():
            return {'status':'fail','reason':'invalid_shape_or_nonfinite','species':species}
        errors[species]=float(np.max(np.abs(data-reference))/case['scales'][species])
        mapping[species]=column
    error=max(errors.values())
    return {'status':'pass' if error<=threshold else 'fail','max_scaled_error':error,
            'threshold':threshold,'species_scaled_errors':errors,'species_column_mapping':mapping}


def from_basico_split(data):
    columns=data['columns'];rows=data['data']
    times=[r[columns.index('Time')] for r in rows]
    values={c:[r[i] for r in rows] for i,c in enumerate(columns) if c!='Time'}
    return times,values


def from_t2b_state(data):
    if not data:raise ValueError('no simulation in graph state')
    columns=data[-1]['data']
    tkey='Time' if 'Time' in columns else 'time'
    indices=sorted(columns[tkey],key=float)
    times=[columns[tkey][i] for i in indices]
    values={k:[v[i] for i in indices] for k,v in columns.items() if k!=tkey}
    return times,values


def read(path):return json.loads(Path(path).read_text())


def score():
    verify_source('kinmodgpt');verify_source('talk2biomodels')
    protocol=read(OUT/'sbml-protocol.json');rows=[]
    for group in ['talk2biomodels','talk2biomodels-contract-clarified',
                  'talk2biomodels-tool-control','codex','biomodel_core']:
        for case_name,case in protocol['cases'].items():
            dest=OUT/group/case_name
            row={'system':group,'case':case_name,
                 'kind':'agent' if group in ['talk2biomodels','codex'] else
                        'assisted_agent_diagnostic' if 'contract-clarified' in group else 'numerical_tool_control'}
            try:
                if group.startswith('talk2biomodels') and group!='talk2biomodels-tool-control':
                    execution=read(dest/'result.json')
                    row.update(execution_status=execution['status'],calls=execution.get('calls'),
                               elapsed_seconds=execution['elapsed_seconds'])
                    if not (dest/'simulation-state.json').exists():
                        row.update(status='no_trajectory',error=execution.get('error'),
                                   deployment='graph_constructed' if execution.get('graph_import_and_construction')=='pass' else 'setup_failed')
                        rows.append(row);continue
                    times,values=from_t2b_state(read(dest/'simulation-state.json'))
                elif group=='talk2biomodels-tool-control':
                    times,values=from_basico_split(read(dest/'data.json'))
                else:
                    data=read(dest/'trajectory.json');times,values=data['times'],data['values']
                row.update(status='scored',numeric=compare_imported(times,values,case))
            except (FileNotFoundError,ValueError,KeyError,TypeError) as exc:
                row.update(status='not_scored',error=str(exc))
            rows.append(row)
    for name in ['complete','assisted']:
        dest=OUT/'kinmodgpt'/name
        row={'system':'kinmodgpt_alternative_model','case':name,'kind':'official_conversion_pipeline_variant'}
        try:
            record=read(dest/'result.json');row.update(execution_status=record['status'],
                initial_values=record.get('initial_values'),calls=record['calls'],
                elapsed_seconds=record['elapsed_seconds'],reported_tokens=record['reported_tokens'])
            if name=='complete':
                data=read(dest/'trajectory.json')
                row.update(status='scored',numeric=compare_trajectory(data['times'],data['values'],
                    read(ROOT/'benchmarks/inputs/comparison-pilot/complete/experiment.json')['times']))
            else:
                row.update(status='missing_initial_diagnostic',
                    source_initials='not_specified',actual_initial_origin='Antimony_default_zero',
                    strict_source_reproduction='not_verified',
                    numeric={'status':'not_applicable_without_source_initials'})
        except (FileNotFoundError,ValueError,KeyError,TypeError) as exc:
            row.update(status='not_scored',error=str(exc))
        rows.append(row)
    write_json(OUT/'results.json',{'rows':rows,
        'original_paper_model_reproduction':'not_run_original_models_unavailable_in_configured_provider',
        'variant_disclosure':'Both published systems executed official source under an alternative qwen LLM; KinMod uses modern Tellurium/Python runtime.',
        'no_global_ranking':True,'protocol_limits':protocol['limits']})
    print(json.dumps(rows,indent=2,ensure_ascii=False))
    return rows


if __name__=='__main__':score()
