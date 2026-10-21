"""Three executable evaluation adapters with shared tools and audited requests.

The caller supplies a transport; this module never discovers credentials or
creates an online client. Scripted responses still drive real scientific tools.
"""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
import multiprocessing as mp
import os
from pathlib import Path
import queue
import signal
import time

from biomodel_agent import agent, agent_tools, application, time_units
from biomodel_agent.domain.contracts import ModelArtifact, SolverConfig
from biomodel_agent.domain.serialization import from_dict
from biomodel_agent.domain.contracts import ExperimentSpec
from biomodel_agent.invocations import InvocationJournal
from biomodel_agent.llm import ChatProvider, ModelResponseError, ProviderError
from biomodel_agent.recovery import execution_owner, implementation_fingerprint
from biomodel_agent.storage import ArtifactStore

_spec = importlib.util.spec_from_file_location('adapter_evaluation_protocol',Path(__file__).with_name('agent_evaluation.py'))
protocol = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(protocol)

ACTIONS = {'inspect_source','submit_draft','run','finish','ask_researcher'}
CHARGES = {'inspect_source':['source_read'], 'submit_draft':['build_sbml'], 'run':['simulate','validate']}
TOOL_SCHEMA = (
    'Return exactly one JSON object {"action":name,"args":object,"reason":optional_string}. '
    'Tools: inspect_source {source_ref,page,start,end} reads up to 8000 characters; '
    'submit_draft {draft,evidence}; run {}; finish {}; ask_researcher {question}. '
    'draft: {id,states:[{id,initial,unit,quantity_kind,nonnegative}],parameters:[{id,value,unit,origin}],'
    'compartment:{id,size,unit},rate_rules:[{state,expression}],reactions:[],derived:[],time_unit}. '
    'quantity_kind is variable/amount/concentration; unknown numeric fields are null and unknown units are "unknown". '
    'Evidence entries: {target,source_ref,page,quote}; target forms include <id>/initial, <id>/value, rule:<id>/expression. '
    'Tools return errors or results; choose your next step from those results. Ask a question when necessary. '
    'You have no shell, file access or network tool.'
)


class StopExecution(BaseException):
    def __init__(self,reason):
        self.reason=reason


def _write(path,value):
    path=Path(path); temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    os.replace(temporary,path)


def _message(value):
    return {'role':'user','content':json.dumps(value,ensure_ascii=False,sort_keys=True)}


def _experiment(text):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result: raise ValueError('duplicate experiment JSON key')
            result[key]=value
        return result
    try:
        value=json.loads(text,object_pairs_hook=unique)
        protocol.digest(value)
        return from_dict(ExperimentSpec,value)
    except (TypeError,ValueError,RecursionError) as exc:
        raise ValueError('invalid frozen experiment JSON') from exc


def validate_projection(p,transport):
    protocol._fields(p,'trial model_config budget shared_tools task sources')
    t=p['trial']
    protocol._fields(t,'id workflow case_id repeat phase parent_trial_id manifest_sha256 toolchain_sha256 evidence_kind')
    if t['workflow'] not in protocol.WORKFLOWS or t['phase'] not in ('initial','clarified'):
        raise ValueError('invalid workflow or phase')
    for k in ('id','case_id'):
        if not isinstance(t[k],str) or not protocol.ID.fullmatch(t[k]):
            raise ValueError('invalid trial identifier')
    protocol._integer(t['repeat'],0,4)
    expected=f"{t['workflow']}--{t['case_id']}--{t['repeat']}--{t['phase']}"
    if t['id']!=expected:
        raise ValueError('trial identity does not match its components')
    parent=f"{t['workflow']}--{t['case_id']}--{t['repeat']}--initial" if t['phase']=='clarified' else None
    if t['parent_trial_id']!=parent:
        raise ValueError('wrong scheduled parent')
    for k in ('manifest_sha256','toolchain_sha256'):
        if not isinstance(t[k],str) or not protocol.HASH.fullmatch(t[k]): raise ValueError('invalid frozen digest')
    if t['toolchain_sha256']!=implementation_fingerprint(): raise ValueError('scientific toolchain changed')
    if t['evidence_kind'] not in ('scripted_control','live_model'): raise ValueError('invalid evidence kind')
    config=p['model_config']; protocol._fields(config,'provider model revision temperature top_p max_output_tokens seed')
    for k in ('provider','model','revision'): protocol._text(config[k],200)
    protocol._number(config['temperature'],0,2); protocol._number(config['top_p'],.000001,1)
    protocol._integer(config['max_output_tokens'],1,32000)
    if config['seed'] is not None: protocol._integer(config['seed'],0,2**32-1)
    if protocol.digest(transport.model_config)!=protocol.digest(config) or transport.evidence_kind!=t['evidence_kind']:
        raise ValueError('transport must declare the same frozen configuration and evidence kind')
    b=p['budget']; protocol._fields(b,'max_model_calls max_tool_calls max_request_chars max_elapsed_seconds')
    protocol._integer(b['max_model_calls'],1,50); protocol._integer(b['max_tool_calls'],1,500)
    protocol._integer(b['max_request_chars'],8000,500000); protocol._number(b['max_elapsed_seconds'],.01,3600)
    if p['shared_tools']!=['source_read','build_sbml','simulate','validate']: raise ValueError('different tool access')
    protocol._text(p['task'])
    if not isinstance(p['sources'],list) or len(p['sources'])!=2: raise ValueError('source and experiment required')
    roles={}
    for s in p['sources']:
        protocol._fields(s,'role sha256 text')
        if s['role'] not in ('source','experiment') or s['role'] in roles: raise ValueError('invalid input role')
        protocol._text(s['text'],2_000_000)
        encoded=s['text'].encode()
        if len(encoded)>2_000_000: raise ValueError('input byte budget exceeded')
        if s['sha256']!=protocol.hashlib.sha256(encoded).hexdigest(): raise ValueError('input text hash mismatch')
        roles[s['role']]=s['text']
    experiment=_experiment(roles['experiment'])
    if len(roles['source'])>2_000_000: raise ValueError('material too large')
    return roles['source'],experiment


def _transport_identity(transport,p):
    identity={'class':type(transport).__module__+'.'+type(transport).__qualname__,
              'config_sha256':protocol.digest(p['model_config']), 'evidence_kind':transport.evidence_kind}
    extra=getattr(transport,'comparison_identity',None)
    if extra is not None:
        identity['transport_sha256']=protocol.digest(extra)
    return identity


def _parent(store,ref,p,identity):
    if ref is None:
        if p['trial']['parent_trial_id']: raise ValueError('clarified phase requires a persisted parent session')
        return None
    if not p['trial']['parent_trial_id']: raise ValueError('initial phase cannot load a parent')
    record=store.get(ref)
    if record['kind']!='evaluation_session': raise ValueError('not an evaluation session')
    state=record['payload']
    if state['trial']['id']!=p['trial']['parent_trial_id'] or any(state['trial'][k]!=p['trial'][k] for k in
            ('workflow','case_id','repeat','manifest_sha256','toolchain_sha256','evidence_kind')):
        raise ValueError('parent belongs to another trial, plan or toolchain')
    if state['transport_identity']!=identity or state['model_config']!=p['model_config'] or state['budget']!=p['budget']:
        raise ValueError('parent provider/settings/budget changed')
    source=next(s['text'] for s in p['sources'] if s['role']=='source')
    experiment=next(s['text'] for s in p['sources'] if s['role']=='experiment')
    if not source.startswith(state['initial_source_text']) or experiment!=state['experiment_text']:
        raise ValueError('clarification changed the original material or experiment')
    return state


class Meter:
    def __init__(self,p,store,directory,parent):
        self.p=p; self.store=store; self.directory=Path(directory); self.started=time.monotonic()
        self.base=deepcopy(parent['meter_totals']) if parent else {'model_calls':0,'tool_calls':0,'run_attempts':0,'elapsed_seconds':0.}
        self.counts={'model_calls':0,'tool_calls':0,'run_attempts':0}
        self.request_refs=[]; self.response_refs=[]; self.transport_refs=[]; self.usage=[]; self.reservations=[]
        self.provider_status=None; self.stop_reason=None
        self.snapshot()
    def elapsed(self): return max(0.,time.monotonic()-self.started)
    def remaining(self):
        left=self.p['budget']['max_elapsed_seconds']-self.base['elapsed_seconds']-self.elapsed()
        if left<=0: raise StopExecution('wall_deadline')
        return left
    def totals(self):
        return {**{k:self.base[k]+self.counts[k] for k in self.counts},
                'elapsed_seconds':self.base['elapsed_seconds']+self.elapsed()}
    def snapshot(self):
        _write(self.directory/'meter.json',{'base':self.base,'counts':self.counts,'request_refs':self.request_refs,
            'response_refs':self.response_refs,'transport_exchange_refs':self.transport_refs,
            'usage':self.usage,'reservation_refs':self.reservations,
            'provider_status':self.provider_status,'stop_reason':self.stop_reason,'elapsed_seconds':self.elapsed()})
    def reserve(self,name):
        self.remaining(); costs=CHARGES.get(name,[])
        if name=='respond':
            if self.base['model_calls']+self.counts['model_calls']>=self.p['budget']['max_model_calls']:
                raise StopExecution('model_budget_exhausted')
            delta={'model_calls':1,'tool_calls':0,'run_attempts':0}
        else:
            if name not in CHARGES: raise StopExecution('tool_outside_common_set')
            if self.base['tool_calls']+self.counts['tool_calls']+len(costs)>self.p['budget']['max_tool_calls']:
                raise StopExecution('tool_budget_exhausted')
            if name=='run' and self.base['run_attempts']+self.counts['run_attempts']:
                raise StopExecution('scientific_execution_already_attempted')
            delta={'model_calls':0,'tool_calls':len(costs),'run_attempts':int(name=='run')}
        next_counts={k:self.counts[k]+delta[k] for k in self.counts}
        ref=self.store.put('evaluation_reservation',{'trial_id':self.p['trial']['id'],'operation':name,
            'charged_tools':costs,'phase_counts':next_counts,'prior_totals':self.base})
        self.counts=next_counts; self.reservations.append(ref); self.snapshot()
        return ref


class MeteredStore:
    def __init__(self,store,meter): self.inner=store; self.meter=meter
    def __getattr__(self,name): return getattr(self.inner,name)
    def put(self,kind,payload,refs=()):
        if kind=='agent_invocation' and payload.get('phase')=='reserved' and payload['name']!='respond':
            self.meter.reserve(payload['name'])
        return self.inner.put(kind,payload,refs=refs)


class MeteredProvider:
    def __init__(self,p,transport,meter):
        self.p=p; self.transport=transport; self.meter=meter; self.model=p['model_config']['model']
        self.max_tokens=p['model_config']['max_output_tokens']; self.timeout_s=p['budget']['max_elapsed_seconds']; self.json_mode=False
        self.last_usage={}
    def _transport_receipt(self,request_ref):
        getter=getattr(self.transport,'exchange_audit',None)
        if not callable(getter): return
        receipt=getter()
        if receipt is None: return
        ref=self.meter.store.put('evaluation_transport_exchange',{
            'trial_id':self.p['trial']['id'],'exchange':receipt},refs=(request_ref,))
        self.meter.transport_refs.append(ref); self.meter.snapshot()
    def respond(self,messages):
        config=self.p['model_config']; packet=deepcopy(messages)
        if not packet or packet[0].get('role')!='system': raise ModelResponseError('system instructions required')
        notice='\nEvaluation task: '+self.p['task']+'\nOnly inspect_source, submit_draft, run, finish, ask_researcher are available in this comparison. Memory, retrieval, skills and study tools are disabled.'
        packet[0]['content']+=notice
        request={'model':self.model,'messages':packet,'temperature':config['temperature'],'top_p':config['top_p'],
                 'max_tokens':config['max_output_tokens']}
        if config['seed'] is not None: request['seed']=config['seed']
        size=len(json.dumps(packet,ensure_ascii=False,separators=(',',':'),allow_nan=False))
        if size>self.p['budget']['max_request_chars']: raise StopExecution('context_budget_exhausted')
        reservation=self.meter.reserve('respond'); self.last_usage={}
        ref=self.meter.store.put('evaluation_request',{'trial_id':self.p['trial']['id'],'request':request,
            'message_chars':size,'transport_identity':_transport_identity(self.transport,self.p)},refs=(reservation,))
        self.meter.request_refs.append(ref); self.meter.usage.append(None); self.meter.snapshot()
        try: body=self.transport.request(deepcopy(request),self.meter.remaining())
        except StopExecution: raise
        except Exception as exc:
            self.meter.provider_status=('timeout' if isinstance(exc,TimeoutError) else 'invalid_output'
                if isinstance(exc,ProviderError) and exc.kind in ('response_envelope','model_response') else 'provider_error')
            self.meter.snapshot()
            raise ProviderError('evaluation transport failed: '+type(exc).__name__,kind='transport',retryable=False) from None
        finally:
            self._transport_receipt(ref)
        usage={}
        if isinstance(body,dict) and isinstance(body.get('usage'),dict):
            usage={k:v for k,v in body['usage'].items() if k in ('prompt_tokens','completion_tokens','total_tokens') and type(v) is int and v>=0}
        self.last_usage=usage; self.meter.usage[-1]=usage
        observed=body.get('model') if isinstance(body,dict) else None
        try: content=ChatProvider._extract_content(self,body)
        except ProviderError:
            self.meter.provider_status='invalid_output'; self.meter.snapshot(); raise
        if len(content)>1_000_000:
            self.meter.provider_status='invalid_output'; self.meter.snapshot(); raise ModelResponseError('response text budget exceeded')
        response=self.meter.store.put('evaluation_response',{'trial_id':self.p['trial']['id'],'content':content,
            'usage':usage,'returned_model':observed if isinstance(observed,str) and len(observed)<=200 else None},refs=(ref,))
        self.meter.response_refs.append(response); self.meter.snapshot()
        if usage.get('completion_tokens',0)>config['max_output_tokens']:
            raise StopExecution('output_token_budget_exhausted')
        if observed is not None and observed!=self.model:
            self.meter.provider_status='invalid_output'; self.meter.snapshot(); raise ModelResponseError('returned model differs from frozen model')
        self.meter.remaining()
        try:
            action=ChatProvider._parse(self,content)
            if action['action'] not in ACTIONS: raise ModelResponseError('action outside common comparison tools')
        except ProviderError:
            self.meter.provider_status='invalid_output'; self.meter.snapshot(); raise
        return action


def _source(store,text,reply_parent=None):
    blob=store.put_blob(text.encode()); refs=[blob]; extra={}
    if reply_parent:
        clarification=store.put('researcher_clarification',{'reply':text,'authority':'researcher_assertion',
            'respondent':'evaluation prerecorded clarification','semantic_support':'unknown'},refs=(blob,reply_parent))
        refs.append(clarification); extra={'clarification_ref':clarification,'authority':'researcher_assertion'}
    return store.put('source',{'blob_ref':blob,'filename':'evaluation-material.txt','url':None,'parser':'utf8',
        'parser_version':'1','pages':[{'number':1,'text':text}],'warnings':[],**extra},refs=tuple(refs))


def _materials(store,sources):
    return {r:{'pages':[{'page':p['number'],'chars':len(p['text'])} for p in store.get(r)['payload']['pages']]} for r in sources}


def _generic(p,provider,store,meter,parent,parent_ref,text,experiment,solver):
    sources=parent['source_refs'][:] if parent else [_source(store,text)]
    messages=deepcopy(parent['messages']) if parent else [{'role':'system','content':TOOL_SCHEMA},
        _message({'task':p['task'],'materials':_materials(store,sources),'experiment':next(s['text'] for s in p['sources'] if s['role']=='experiment')})]
    if parent:
        reply=text[len(parent['initial_source_text']):]
        if not reply.strip() or len(reply)>8000: raise ValueError('clarification must add 1..8000 characters')
        reply_ref=_source(store,reply,parent_ref); sources.append(reply_ref)
        messages.append(agent._reply_message(parent['questions'][-1],'evaluation prerecorded clarification',reply_ref,reply))
    task_ref=parent['task_ref'] if parent else store.put('agent_task',{'evaluation_only':True,'max_calls':p['budget']['max_model_calls']})
    model_ref=run_ref=draft_ref=None; questions=[]; state='failed'; claimed=False
    with execution_owner(store,task_ref):
        journal=InvocationJournal(store,task_ref,calls=meter.base['model_calls'],runs=meter.base['run_attempts'])
        fixed=p['trial']['workflow']=='fixed_workflow'
        if fixed:
            messages[0]['content']=TOOL_SCHEMA+' Fixed extraction stage: respond only with submit_draft or ask_researcher; subsequent build/run/finish are deterministic.'
            for source in sources:
                source_text=store.get(source)['payload']['pages'][0]['text']
                for start in range(0,len(source_text),8000):
                    args={'source_ref':source,'page':1,'start':start,'end':min(start+8000,len(source_text))}
                    excerpt=journal.invoke('inspect_source',lambda a=args:store.get(a['source_ref'])['payload']['pages'][0]['text'][a['start']:a['end']],details={'args':args})
                    messages.append(_message({'tool':'inspect_source','ok':True,**args,'text':excerpt}))
        while True:
            response=journal.invoke('respond',lambda:provider.respond(messages),details={'request_messages':messages},summarize=lambda r:r)
            messages.append({'role':'assistant','content':json.dumps(response,ensure_ascii=False)})
            name,args=response['action'],response['args']
            if fixed and name not in ('submit_draft','ask_researcher'): raise ModelResponseError('fixed extraction returned a different stage')
            if name=='ask_researcher':
                if set(args)!={'question'}: raise ModelResponseError('question arguments invalid')
                protocol._text(args['question']); questions.append(args['question']); state='awaiting_input'; break
            if name=='inspect_source':
                if set(args)!={'source_ref','page','start','end'} or args['source_ref'] not in sources or any(type(args[k]) is not int for k in ('page','start','end')) or not 0<=args['start']<args['end'] or args['end']-args['start']>8000:
                    raise ModelResponseError('source read outside authorized task')
                pages=store.get(args['source_ref'])['payload']['pages']
                page=journal.invoke(name,lambda:next((v['text'] for v in pages if v['number']==args['page']),None),details={'args':args})
                valid=page is not None and args['end']<=len(page)
                messages.append(_message({'tool':name,'ok':valid,**args,'text':page[args['start']:args['end']] if valid else ''})); continue
            if name=='submit_draft':
                if run_ref: raise StopExecution('scientific_execution_already_attempted')
                result=journal.invoke(name,lambda:agent_tools.submit_draft(store,args,sources),details={'args':args},
                    summarize=lambda r:{k:v for k,v in r.items() if k!='artifact'},result_refs=lambda r:[r[k] for k in ('draft_ref','model_ref','source_review_ref') if r.get(k)])
                draft_ref=result.get('draft_ref')
                if result['state']=='ready':
                    # Product Agent performs this task/clock check after the
                    # shared builder; apply the same condition to both baselines.
                    clock=time_units.check_experiment_time(result['artifact'].sbml,experiment.time_unit)
                    if clock.status=='fail':
                        result.update(state='invalid',model_ref=None,problems=[clock.message])
                model_ref=result.get('model_ref'); ready=result['state']=='ready'
                messages.append(_message({'tool':name,'ok':ready,'state':result['state'],'problems':result['problems']}))
                if result['state']=='awaiting_input':
                    questions=['Please resolve: '+'; '.join(result['problems'])]; state='awaiting_input'; break
                if not ready:
                    if fixed: break
                    continue
                if not fixed: continue
                name,args='run',{}
            if name=='run':
                if args or model_ref is None: raise ModelResponseError('run needs a ready model and empty arguments')
                artifact=from_dict(ModelArtifact,store.get(model_ref)['payload'])
                draft_data=store.get(draft_ref)['payload']
                nonnegative=tuple(s['id'] for s in draft_data['states'] if s.get('nonnegative'))
                run_ref=journal.invoke('run',lambda:application.run_reproduction(store,artifact,experiment,solver=solver,strict=True,nonnegative=nonnegative),
                    details={'model_ref':model_ref},summarize=lambda r:{'result_ref':r},result_refs=lambda r:[r])
                run=store.get(run_ref)['payload']
                messages.append(_message({'tool':'run','ok':True,'run_ref':run_ref,**agent._run_facts(run)}))
                if not fixed: continue
                name,args='finish',{}
            if name=='finish':
                if args: raise ModelResponseError('finish arguments must be empty')
                claimed=True; run=store.get(run_ref)['payload'] if run_ref else {}
                state='completed' if run.get('status')=='succeeded' and application.derive_scientific_status(run)!='failed' else 'failed'
                break
    return {'state':state,'source_refs':sources,'messages':messages,'questions':questions,
            'task_ref':task_ref,'model_ref':model_ref,'draft_ref':draft_ref,'run_ref':run_ref,'claimed_complete':claimed,'native_agent_run_ref':None}


def _execute(p,transport,store_root,directory,parent,parent_ref):
    raw=ArtifactStore(store_root); meter=Meter(p,raw,directory,parent); store=MeteredStore(raw,meter)
    provider=MeteredProvider(p,transport,meter)
    text=next(s['text'] for s in p['sources'] if s['role']=='source')
    experiment=_experiment(next(s['text'] for s in p['sources'] if s['role']=='experiment'))
    solver=SolverConfig(timeout_s=min(30.,p['budget']['max_elapsed_seconds']))
    output={'state':'failed','source_refs':parent['source_refs'][:] if parent else [],'messages':[],
            'questions':[],'task_ref':None,'model_ref':None,'draft_ref':None,'run_ref':None,'claimed_complete':False,'native_agent_run_ref':None}
    status='finished'; reason=None
    try:
        if parent and parent['state']!='awaiting_input':
            status='not_run'; reason='parent_not_awaiting_input'
        elif p['trial']['workflow']=='domain_agent':
            if parent:
                reply=text[len(parent['initial_source_text']):]
                ref=agent.resume_agent(store,provider,parent['native_agent_run_ref'],reply,respondent='evaluation prerecorded clarification')
            else:
                source=_source(store,text)
                ref=agent.run_agent(store,provider,[source],experiment,solver=solver,
                    max_calls=p['budget']['max_model_calls'],max_repairs=2,max_retries=0,context_max_chars=p['budget']['max_request_chars'])
            payload=store.get(ref)['payload']
            prior_events=len(store.get(parent['native_agent_run_ref'])['payload']['events']) if parent else 0
            phase_events=payload['events'][prior_events:]
            questions=[e['question'] for e in phase_events if e.get('type')=='awaiting_input']
            output.update(state=payload['state'] if payload['state'] in ('completed','awaiting_input') else 'failed',
                source_refs=payload['source_refs'],questions=questions,task_ref=payload['task_ref'],model_ref=payload['model_ref'],
                draft_ref=store.get(payload['checkpoint_ref'])['payload'].get('draft_ref') if payload.get('checkpoint_ref') else None,
                run_ref=payload['run_ref'],native_agent_run_ref=ref,
                claimed_complete=any(e.get('type')=='llm_call' and e.get('response',{}).get('action')=='finish' for e in phase_events))
            if output['state']=='failed' and meter.totals()['model_calls']>=p['budget']['max_model_calls']:
                reason='model_budget_exhausted'; status='adapter_error'
        else:
            output=_generic(p,provider,store,meter,parent,parent_ref,text,experiment,solver)
        if status!='not_run': meter.remaining()
    except StopExecution as exc:
        reason=exc.reason; status='timeout' if reason=='wall_deadline' else 'adapter_error'
    except ProviderError:
        status=meter.provider_status or 'invalid_output'
    except Exception as exc:
        status='adapter_error'; reason=type(exc).__name__
    if meter.provider_status: status=meter.provider_status
    meter.stop_reason=reason; meter.snapshot()
    if status!='finished': output['state']='not_run' if status=='not_run' else 'failed'
    state={**output,'trial':deepcopy(p['trial']),'model_config':deepcopy(p['model_config']),'budget':deepcopy(p['budget']),
           'transport_identity':_transport_identity(transport,p),'meter_totals':meter.totals(),
           'initial_source_text':parent['initial_source_text'] if parent else text,
           'experiment_text':next(s['text'] for s in p['sources'] if s['role']=='experiment')}
    return state,status,reason


def _worker(p,transport,store_root,directory,parent,parent_ref,channel):
    os.setsid()
    def interrupt(signum,frame): raise StopExecution('wall_deadline')
    signal.signal(signal.SIGTERM,interrupt)
    try:
        result=_execute(p,transport,store_root,directory,parent,parent_ref)
        channel.put(result)
    except BaseException:
        pass  # Parent preserves durable reservations and reports a worker failure.
    finally:
        for child in mp.active_children():
            if child.is_alive(): child.terminate()
            child.join(timeout=.5)
            if child.is_alive(): child.kill(); child.join(timeout=.5)


def _stop(proc):
    if not proc.is_alive(): proc.join(); return
    try: os.killpg(proc.pid,signal.SIGTERM)
    except ProcessLookupError: proc.terminate()
    proc.join(timeout=.75)
    if proc.is_alive():
        try: os.killpg(proc.pid,signal.SIGKILL)
        except ProcessLookupError: proc.kill()
        proc.join(timeout=.75)


def run_trial(p,transport,directory,*,store_root=None,parent_session_ref=None):
    """Run one projected phase in a supervised POSIX process; never auto-retry."""
    text,experiment=validate_projection(p,transport)
    if os.name!='posix' or 'fork' not in mp.get_all_start_methods(): raise ValueError('POSIX fork supervisor required')
    directory=Path(directory)
    if directory.exists(): raise ValueError('use a new phase output directory')
    if parent_session_ref and store_root is None: raise ValueError('parent store is required')
    store_root=Path(store_root) if store_root is not None else directory/'store'
    parent_store=ArtifactStore(store_root) if parent_session_ref else None
    parent=_parent(parent_store,parent_session_ref,p,_transport_identity(transport,p)) if parent_session_ref else None
    if p['trial']['parent_trial_id'] and parent is None: raise ValueError('persisted parent required; no fresh fallback')
    # A reservation is never erased after an unknown/partial execution. A new
    # output path must not replay the same trial and cherry-pick its result.
    reservation_dir=store_root/'.evaluation-trials'
    marker=reservation_dir/(p['trial']['id']+'.json')
    if marker.exists(): raise ValueError('trial already reserved; no implicit retry or duplicate execution')
    directory.mkdir(parents=True,exist_ok=False); _write(directory/'input.json',p)
    reservation_dir.mkdir(parents=True,exist_ok=True)
    try:
        with marker.open('x',encoding='utf-8') as stream:
            json.dump({'trial':p['trial'],'projection_sha256':protocol.digest(p),'parent_session_ref':parent_session_ref},stream)
            stream.flush(); os.fsync(stream.fileno())
    except FileExistsError:
        raise ValueError('trial already reserved; no implicit retry or duplicate execution') from None
    store=parent_store or ArtifactStore(store_root)
    base_elapsed=parent['meter_totals']['elapsed_seconds'] if parent else 0.
    remaining=p['budget']['max_elapsed_seconds']-base_elapsed
    started=time.monotonic(); result=None; timed_out=remaining<=0
    context=mp.get_context('fork'); channel=context.Queue()
    proc=None
    try:
        if not timed_out:
            proc=context.Process(target=_worker,args=(p,transport,str(store_root),str(directory),parent,parent_session_ref,channel))
            proc.start()
            while True:
                left=remaining-(time.monotonic()-started)
                if left<=0: timed_out=True; break
                try: result=channel.get(timeout=min(.05,left)); break
                except queue.Empty:
                    if not proc.is_alive():
                        try: result=channel.get(timeout=.1)
                        except queue.Empty: pass
                        break
            if result is not None: proc.join(timeout=.5)
    finally:
        if proc is not None: _stop(proc)
        channel.close(); channel.join_thread()
    elapsed=max(0.,time.monotonic()-started)
    meter_path=directory/'meter.json'
    snapshot=protocol._read(meter_path) if meter_path.exists() else {
        'base':parent['meter_totals'] if parent else {'model_calls':0,'tool_calls':0,'run_attempts':0,'elapsed_seconds':0.},
        'counts':{'model_calls':0,'tool_calls':0,'run_attempts':0},'request_refs':[],'response_refs':[],
        'usage':[],'reservation_refs':[],'stop_reason':None,'provider_status':None}
    if timed_out or result is None:
        status='timeout' if timed_out else 'adapter_error'; reason='wall_deadline' if timed_out else 'worker_exited_without_result'
        state={'state':'failed','source_refs':[],'messages':[],'questions':[],'task_ref':None,'model_ref':None,'draft_ref':None,'run_ref':None,
            'claimed_complete':False,'native_agent_run_ref':None,'trial':deepcopy(p['trial']),
            'model_config':deepcopy(p['model_config']),'budget':deepcopy(p['budget']),
            'transport_identity':_transport_identity(transport,p),'initial_source_text':parent['initial_source_text'] if parent else text,
            'experiment_text':next(s['text'] for s in p['sources'] if s['role']=='experiment')}
    else: state,status,reason=result
    state['meter_totals']={**{k:snapshot['base'][k]+snapshot['counts'][k] for k in ('model_calls','tool_calls','run_attempts')},
                           'elapsed_seconds':base_elapsed+elapsed}
    state['parent_session_ref']=parent_session_ref
    session=store.put('evaluation_session',state,refs=tuple(r for r in [parent_session_ref,state['native_agent_run_ref'],state['task_ref'],state['model_ref'],state['draft_ref'],state['run_ref'],*state['source_refs'],*snapshot['reservation_refs'],*snapshot['request_refs'],*snapshot['response_refs'],*snapshot.get('transport_exchange_refs',[])] if r))
    requests=[store.get(r)['payload'] for r in snapshot['request_refs']]
    usage=snapshot['usage']; calls=snapshot['counts']['model_calls']
    def tokens(name):
        return sum(v[name] for v in usage) if len(usage)==calls and all(isinstance(v,dict) and name in v for v in usage) else None
    output_counts=[v.get('completion_tokens') if isinstance(v,dict) else None for v in usage]
    maximum=max(output_counts,default=0) if all(v is not None for v in output_counts) else None
    resumed=bool(parent and parent['state']=='awaiting_input' and status!='not_run')
    record={'trial_id':p['trial']['id'],'manifest_sha256':p['trial']['manifest_sha256'],
        'evidence_kind':p['trial']['evidence_kind'],'model_config':deepcopy(p['model_config']),'toolchain_sha256':p['trial']['toolchain_sha256'],
        'status':status,'continuation':'resumed' if resumed else 'fresh' if parent else 'none',
        'parent_trial_id':p['trial']['parent_trial_id'] if resumed else None,
        'observation':{'state':state['state'],'executed':bool(snapshot['counts']['run_attempts']),
            'claimed_complete':state['claimed_complete'],'identified_gaps':[],'question_targets':[],'question_texts':state['questions']},
        'assessment':{'authority':'unreviewed','reviewer':None,'evidence_refs':[],'numeric':'unknown','source_support':'unknown','behavior_labels_reviewed':False},
        'telemetry':{'model_calls':calls,'tool_calls':snapshot['counts']['tool_calls'],
            'max_request_chars':max((r['message_chars'] for r in requests),default=0),'max_response_tokens':maximum,
            'elapsed_seconds':elapsed,'human_interventions':0,'human_seconds':0.,
            'input_tokens':tokens('prompt_tokens'),'output_tokens':tokens('completion_tokens')}}
    audit={'supervised':True,'stop_reason':reason,'run_ref':state['run_ref'],'native_agent_run_ref':state['native_agent_run_ref'],
           'request_refs':snapshot['request_refs'],'response_refs':snapshot['response_refs'],
           'transport_exchange_refs':snapshot.get('transport_exchange_refs',[]),
           'reservation_refs':snapshot['reservation_refs'],'clarification_origin':'prerecorded_protocol' if resumed else None,
           'runtime_adapter_sha256':protocol.hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
           'scientific_support':'unknown','same_returned_model_verified':bool(snapshot['response_refs']) and all(
               store.get(r)['payload']['returned_model']==p['model_config']['model'] for r in snapshot['response_refs'])}
    result={'record':record,'session_ref':session,'store_root':str(store_root.resolve()),'audit':audit}
    _write(directory/'result.json',result)
    return result
