"""Reviewable CSV/TSV mappings with raw cells and explicit affine conversions."""
from copy import deepcopy
import csv
import io
import re

from . import evidence,model_contracts as c
from .storage import canonical_bytes

REVIEW='observation_mapping_review'


def build(store,source_ref,spec):
    c.fields(spec,{'delimiter','time','observables','scales','threshold','conditions'})
    source=store.get(source_ref)
    if source['kind']!='source' or source['payload'].get('parser')!='utf8' or source['payload'].get('research_input_role') in ('scenario','researcher_reply'):
        raise ValueError('observation mappings require supplied UTF-8 table data')
    if spec['delimiter'] not in (',','\t'):raise ValueError('select CSV or TSV delimiter')
    raw=evidence._source_page_text(store,source_ref,1)
    if len(raw)>128000:raise ValueError('table exceeds bounded observation scope')
    rows=list(csv.reader(io.StringIO(raw),delimiter=spec['delimiter'],strict=True))
    if not 3<=len(rows)<=502:raise ValueError('table needs a header and 2..501 observations')
    header=rows[0]
    if not 2<=len(header)<=33 or len(set(header))!=len(header) or any(len(row)!=len(header) for row in rows):
        raise ValueError('table needs distinct columns and rectangular rows')
    if not isinstance(spec['observables'],dict) or not 1<=len(spec['observables'])<=8:raise ValueError('map 1..8 observables')
    if not isinstance(spec['conditions'],dict) or len(spec['conditions'])>16:raise ValueError('explicit condition mapping required')
    for key,value in spec['conditions'].items():c.label(key,100);c.label(value,300)
    ledger={};used=set()
    def column(target,mapping):
        c.fields(mapping,{'column','source_unit','target_unit','multiplier','offset'})
        name=mapping['column']
        if name not in header or name in used:raise ValueError('select distinct existing source columns')
        used.add(name)
        unit=re.fullmatch(r'[^\[\]]+\[([^\[\]]+)\]',name)
        if unit is None or unit[1]!=mapping['source_unit']:raise ValueError('source unit must match the literal column header')
        c.label(mapping['target_unit']);factor=c.number(mapping['multiplier'],1e-12,1e12,'multiplier')
        offset=c.number(mapping['offset'],-1e12,1e12,'offset')
        index=header.index(name);cells=[row[index] for row in rows[1:]];values=[]
        for cell in cells:
            try:value=float(cell)
            except (ValueError,OverflowError) as exc:raise ValueError('missing or nonnumeric cell; no imputation is performed') from exc
            c.number(value,-1e12,1e12,'source cell')
            values.append(c.number(value*factor+offset,-1e12,1e12,'transformed cell'))
        ledger[target]=dict(mapping=deepcopy(mapping),column_index=index,raw_cells=cells,values=values,
            row_indices=list(range(1,len(rows))))
        return values
    times=column('time',spec['time'])
    values={}
    for target,mapping in spec['observables'].items():c.identifier(target);values[target]=column(target,mapping)
    from .time_units import normalize_time_unit
    time_unit=normalize_time_unit(spec['time']['target_unit'])
    if time_unit=='unknown':raise ValueError('observation clock needs an explicit supported unit')
    config=dict(model_type='ode',note='Reviewed table mapping; source data quality and conversion assumptions require independent review.',
        reference=dict(status='succeeded',times=times,values=values,time_unit=time_unit,backend='reviewed_source_table'),
        scales=spec['scales'],threshold=spec['threshold'])
    # Reuse all existing ordering, finite-value and comparison-scale checks.
    from .research_projects import _reference_payload
    _reference_payload(config,'mapping_preflight')
    return config,ledger


def prepare(store,source_ref,spec,actor,note):
    c.label(actor,200);c.label(note,2000)
    config,ledger=build(store,source_ref,spec)
    review=store.put(REVIEW,dict(version=1,source_ref=source_ref,spec=deepcopy(spec),ledger=ledger,actor=actor,note=note,
        authority='attributed_mapping_review_not_independent_domain_review',scientific_support='unknown'),refs=(source_ref,))
    from .research_projects import REFERENCE,_reference_payload
    payload=_reference_payload(config,actor,'operator_reviewed_table_mapping_not_independent_review')
    payload['observation_review_ref']=review
    reference=store.put(REFERENCE,payload,refs=(review,))
    return dict(reference_ref=reference,review_ref=review)


def verify(store,reference):
    review_ref=reference['observation_review_ref'];review=store.get(review_ref);payload=review['payload']
    if review['kind']!=REVIEW or payload.get('version')!=1 or set(review['refs'])!={payload['source_ref']}:
        raise ValueError('observation review dependency mismatch')
    config,ledger=build(store,payload['source_ref'],payload['spec'])
    if canonical_bytes(ledger)!=canonical_bytes(payload['ledger']):raise ValueError('raw-cell conversion ledger differs from source')
    from .research_projects import _reference_payload
    expected=_reference_payload(config,payload['actor'],'operator_reviewed_table_mapping_not_independent_review')
    expected['observation_review_ref']=review_ref
    if canonical_bytes(reference)!=canonical_bytes(expected):raise ValueError('observation reference differs from reviewed mapping')
