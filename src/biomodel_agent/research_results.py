"""Read-only, unit-labelled result projections and explicit parent model diffs."""
from copy import deepcopy
import math


def _diff(left,right,path='',limit=128):
    changes=[];truncated=False
    def visit(a,b,p):
        nonlocal truncated
        if a==b:return
        if len(changes)>=limit:truncated=True;return
        if isinstance(a,dict) and isinstance(b,dict):
            for key in sorted(set(a)|set(b)):
                visit(a.get(key,{'missing':True}),b.get(key,{'missing':True}),p+'/'+key.replace('~','~0').replace('/','~1'))
        elif isinstance(a,list) and isinstance(b,list) and len(a)==len(b):
            for i,(x,y) in enumerate(zip(a,b)):visit(x,y,p+'/'+str(i))
        else:
            # Bound display values; the original artifacts retain full values.
            def bounded(value):
                if isinstance(value,str) and len(value)>2000:return value[:2000]+' [truncated]'
                if isinstance(value,list) and len(value)>32:return dict(length=len(value),preview=deepcopy(value[:8]))
                return deepcopy(value)
            changes.append(dict(path=p or '/',before=bounded(a),after=bounded(b)))
    visit(left,right,path)
    return dict(changes=changes,truncated=truncated)


def _mathematics(store,it):
    if it['config'].get('model_ref'):
        return store.get(it['config']['model_ref'])['payload']['model']
    if it['executor']=='decision_plan':
        from . import modeling_plan,modeling_workflow
        task=modeling_workflow.load_task(store,it['task_ref'])
        plan,_,_=modeling_plan.compiled_model(store,task['compilation_ref'])
        return next(c['draft'] for c in plan['spec']['candidates'] if c['id']==plan['spec']['selected_candidate'])
    return None


def _plot(title,x,series,x_unit,y_unit,*,step=False,x_label='time',note=''):
    if not isinstance(x,list) or not x:return None
    selected=list(range(0,len(x),max(1,math.ceil(len(x)/500))))
    if selected[-1]!=len(x)-1:selected.append(len(x)-1)
    valid=[]
    for name,values in list(series.items())[:16]:
        if len(values)!=len(x):continue
        if any(type(v) not in (int,float,bool) or not math.isfinite(v) for v in values):continue
        valid.append(dict(name=name,values=[float(values[i]) for i in selected]))
    return dict(title=title,x=[x[i] for i in selected],series=valid,x_label=x_label,x_unit=x_unit,y_unit=y_unit,step=step,
        note=note,sampling=dict(original_points=len(x),shown_points=len(selected),omitted_series=max(0,len(series)-16)))


def collect(store,project_ref,view):
    from . import research_projects as p,typed_models
    items=[];models={}
    for ref in view['iteration_refs']:
        it=p.own(store,ref,p.ITERATION,project_ref);model=_mathematics(store,it);models[ref]=model
        parent=it['parent_iteration_ref'];facts=view.get('iteration_facts',{}).get(ref) or p.facts(store,ref)
        item=dict(iteration_ref=ref,parent_iteration_ref=parent,model_type=it['config']['model_type'],plots=[],facts=facts,
            model_diff=_diff(models.get(parent),model) if parent and parent in models else None,
            execution_diff=_diff(p.own(store,parent,p.ITERATION,project_ref)['config']['execution'],it['config']['execution']) if parent else None)
        run=facts['application_run_ref']
        if run and store.get(run)['kind']=='typed_run':
            _,native,out=typed_models.result(store,run);verified=typed_models.summary(store,run)
            data=out.get('data');kind=native['model_type'];units=native['units']
            if verified['status']=='succeeded' and data:
                if kind=='stochastic':
                    item['plots'].append(_plot('SSA ensemble mean',data['times'],{n:[row[j] for row in data['mean']] for j,n in enumerate(data['species'])},
                        units['time'],units['state'],note='Descriptive ensemble mean; variance is available in the receipt. No confidence interval is implied.'))
                elif kind=='pde':
                    from . import pde_system
                    if data.get('profile')==pde_system.PROFILE:
                        final=data['values'][-1]
                        if 'y' in data['axes']:
                            y_index=len(data['axes']['y'])//2;y_value=data['axes']['y'][y_index]
                            item['plots'].append(_plot('Final x-slice at fixed y',data['axes']['x'],
                                {field_id:[row[y_index] for row in final[index]] for index,field_id in enumerate(data['field_ids'])},
                                units['space'],units['state'],x_label='position',
                                note='Final time: '+str(data['times'][-1])+' '+units['time']+'; x-slice at fixed y index '
                                    +str(y_index)+' (y='+str(y_value)+' '+units['space']+'). Full 2D trajectories are available in the returned receipt.'))
                        else:
                            item['plots'].append(_plot('Final field per declared field',data['axes']['x'],
                                {field_id:final[index] for index,field_id in enumerate(data['field_ids'])},
                                units['space'],units['state'],x_label='position',
                                note='Final time: '+str(data['times'][-1])+' '+units['time']+'; cell-centred grid; one curve per declared field; full trajectories stay in the returned receipt.'))
                    else:
                        item['plots'].append(_plot('Final spatial field',data['x'],{'field':data['values'][-1]},units['space'],units['state'],x_label='position',
                            note='Final time: '+str(data['times'][-1])+' '+units['time']+'; '+data['grid_kind']+' grid.'))
                elif kind=='boolean':
                    item['plots'].append(_plot('Boolean states',data['steps'],{n:[row[j] for row in data['states']] for j,n in enumerate(data['nodes'])},units['time'],'Boolean (0/1)',step=True))
                else:item['plots'].append(_plot('Population',data['steps'],{'population':data['population']},units['time'],'cell count',step=True))
        elif run and store.get(run)['kind']=='run':
            simulation=store.get(run)['payload'].get('simulation') or {}
            if simulation.get('status')=='succeeded':
                state_units={s['id']:s.get('unit','unknown') for s in (model or {}).get('states',[])}
                # Separate panels prevent mixing incompatible state units.
                for name,values in simulation['values'].items():
                    item['plots'].append(_plot(name,simulation['times'],{name:values},simulation.get('time_unit','unknown'),state_units.get(name,'unknown')))
        elif run and store.get(run)['kind']=='fba_run':
            from . import fba
            summary=fba.summary(store,run)
            item['scalar_result']=dict(objective=summary['objective'],fluxes=summary.get('fluxes'),units=(model or {}).get('units','unknown'),
                note='Steady-state scalar and fluxes; no time trajectory.')
        item['plots']=[plot for plot in item['plots'] if plot and plot['series']]
        items.append(item)
    return dict(iterations=items,scientific_support='unknown',note='Plots display saved outputs. Diffs compare the explicitly linked parent; they do not establish biological equivalence.')
