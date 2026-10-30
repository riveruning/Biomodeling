"""Agent-selected literal data copies; never independent scientific validation."""
import csv
import io
import json
import re

from . import evidence,model_contracts as c
from .storage import canonical_bytes

VERSION='literal_research_reference_v1'


def _pairs(items):
    out={}
    for key,value in items:
        if key in out:raise ValueError('duplicate reference JSON key')
        out[key]=value
    return out


def _constant(value):raise ValueError('nonfinite reference JSON constant')


def _extract(store,args):
    from . import research_projects as p
    c.fields(args,{'source_ref','page','start','end','format','note'},
        {'model_type','time_column','columns','threshold'})
    src=store.get(args['source_ref'])
    if (src['kind']!='source' or src['payload'].get('parser')!='utf8' or src['payload'].get('reply_ref')
            or src['payload'].get('research_input_role') in ('scenario','researcher_reply')):
        raise ValueError('literal references need supplied UTF-8 data, not scenario/reply text or unverified XML/PDF extraction')
    c.integer(args['page'],1,10000,'page')
    page=evidence._source_page_text(store,args['source_ref'],args['page'])
    start=c.integer(args['start'],0,len(page));end=c.integer(args['end'],start+1,len(page))
    raw=page[start:end]
    if len(canonical_bytes(raw))>128000:raise ValueError('reference data excerpt exceeds 128KB')
    config=dict(note=args['note'])
    if args['format']=='json_reference':
        if 'time_column' in args or 'columns' in args:raise ValueError('JSON references do not use CSV mappings')
        config.update(reference=json.loads(raw,object_pairs_hook=_pairs,parse_constant=_constant),model_type=args.get('model_type','ode'))
    elif args['format']=='csv_series':
        if args.get('model_type','ode')!='ode':raise ValueError('CSV series require ODE references')
        records=list(csv.reader(io.StringIO(raw),strict=True))
        if not 3<=len(records)<=502:raise ValueError('CSV needs a header and 2..501 observations')
        header=records[0]
        if not 2<=len(header)<=33 or len(set(header))!=len(header) or any(not x for x in header):raise ValueError('invalid CSV header')
        if any(len(row)!=len(header) for row in records[1:]):raise ValueError('CSV row width mismatch')
        clock=args.get('time_column');columns=args.get('columns')
        if clock not in header or not isinstance(columns,dict) or not 1<=len(columns)<=8:raise ValueError('select actual CSV time and value columns')
        if len(set(columns.values()))!=len(columns) or any(x not in header or x==clock for x in columns.values()):raise ValueError('invalid value column mapping')
        for name in columns:c.identifier(name)
        units=re.fullmatch(r'[^\[\]]+\[([^\[\]]+)\]',clock)
        if units is None:raise ValueError('time column needs explicit [unit], such as time [second]')
        times=[float(row[header.index(clock)]) for row in records[1:]]
        values={name:[float(row[header.index(column)]) for row in records[1:]] for name,column in columns.items()}
        config['reference']=dict(times=times,values=values,time_unit=units[1],backend='source_literal_csv',status='succeeded')
    else:raise ValueError('reference format must be json_reference or csv_series')
    if config.get('model_type')!='fba':
        config['scales']={key:1. for key in config['reference']['values']}
        if 'threshold' in args:config['threshold']=args['threshold']
    elif 'threshold' in args:raise ValueError('FBA uses its existing fixed scalar tolerance')
    return p._reference_payload(config,'research_agent','agent_source_literal_copy_not_independent_review')


def verify(store,payload):
    provenance=payload['source_provenance']
    if provenance.get('version')!=VERSION:raise ValueError('unsupported source reference parser')
    expected=_extract(store,provenance['extraction'])
    if any(payload.get(key)!=value for key,value in expected.items()):raise ValueError('reference differs from its source data excerpt')


def metadata(store,ref):
    from . import research_projects as p
    value=p._reference(store,ref)
    out=dict(reference_ref=ref,reference_type=value['reference_type'],note=value['note'],authority=value['authority'],independent=False)
    if value['reference_type']=='time_series':
        out.update(times=value['reference']['times'],time_unit=value['reference']['time_unit'],observables=list(value['reference']['values']))
    else:out['observables']=['objective']
    if value.get('source_provenance'):out['source_provenance']=value['source_provenance']
    return out


def create(store,project_ref,args):
    from . import research_projects as p
    view=p.inspect(store,project_ref)
    if len(view['reference_refs'])>=8:raise ValueError('project reference limit exceeded')
    if args.get('source_ref') not in view['source_refs']:raise ValueError('reference source outside project')
    value=_extract(store,args)
    value.update(project_ref=project_ref,source_provenance=dict(version=VERSION,extraction=args),
        interpretation_status='literal_values_verified_mapping_and_data_quality_agent_assessed')
    ref=store.put(p.REFERENCE,value,refs=(project_ref,args['source_ref']))
    return dict(reference_ref=ref,metadata=metadata(store,ref))
