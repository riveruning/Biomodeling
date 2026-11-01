"""Local research workbench: read projections and journaled, serial actions.

This adapter delegates scientific decisions, budgets and recovery to their
existing authorities. Browser requests never choose credentials or file paths.
"""
from copy import deepcopy
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import queue
import re
import secrets
import tempfile
import threading
from types import SimpleNamespace
from urllib.parse import urlsplit
import zipfile

from . import evidence, model_contracts as c, recovery, research_tasks as tasks, task_proposals as proposals
from .storage import ArtifactStore, REF_RE, canonical_bytes
from .read_views import verified_read
from .capabilities import catalog as capability_catalog

OPERATION='workbench_operation'
TERMINAL='workbench_operation_terminal'
MAX_BODY=2_100_000
MAX_UPLOAD=1_000_000
MAX_BUNDLE=64*1024*1024
KEY=re.compile(r'[A-Za-z0-9_-]{16,80}')
PROVIDER_ACTIONS={'propose_task','run_task','resume_task','recover_task','run_project'}
FIELDS={
    'create_project':({'config'},set()),
    'run_project':({'project_ref'},set()),
    'reply_project':({'project_ref','question_ref','reply','respondent'},set()),
    'recover_project':({'project_ref','attempt_ref'},set()),
    'snapshot_project':({'project_ref'},set()),
    'import_source':({'filename','content'},set()),
    'import_model':({'filename','content','model_type'},set()),
    'create_task':({'config'},{'parent_ref','reason'}),
    'prepare_proposal':({'request_ref','proposal'},set()),
    'select_proposal':({'proposal_ref','candidate_id','reviewer','reason','acknowledged_unknowns'},set()),
    'recover_proposal':({'attempt_ref'},set()),
    'export_bundle':({'ref'},set()),
    'propose_task':({'request_ref'},set()),
    'run_task':({'request_ref'},set()),
    'resume_task':({'request_ref','reply','respondent'},set()),
    'recover_task':({'request_ref'},set())}


def decode(raw):
    """HTTP JSON accepts no duplicate keys, nonfinite numbers or deep nesting."""
    if len(raw)>MAX_BODY:raise ValueError('请求超过 2.1 MB')
    def pairs(items):
        out={}
        for k,v in items:
            if k in out:raise ValueError('JSON 字段重复: '+k)
            out[k]=v
        return out
    def constant(_):raise ValueError('JSON 数值必须有限')
    try:value=json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=constant)
    except (UnicodeError,RecursionError) as exc:raise ValueError('请求必须是有界 UTF-8 JSON') from exc
    canonical_bytes(value)
    stack=[(value,0)]
    while stack:
        item,depth=stack.pop()
        if depth>40:raise ValueError('JSON 嵌套过深')
        if isinstance(item,dict):stack.extend((v,depth+1) for v in item.values())
        elif isinstance(item,list):stack.extend((v,depth+1) for v in item)
    return value


def ref(value):
    if not isinstance(value,str) or not REF_RE.fullmatch(value):raise ValueError('需要完整的产物引用')
    return value


def _filename(value,suffixes):
    c.label(value,180)
    if value in ('.','..') or any(x in value for x in ('/','\\',':')) or Path(value).suffix.lower() not in suffixes:
        raise ValueError('请使用普通文件名和支持的文件后缀')
    return value


def _text(value,maximum=4000):
    if not isinstance(value,str) or not value.strip() or len(value)>maximum:raise ValueError('文本为空或超过长度限制')
    return value


class Workbench:
    def __init__(self,root,*,allow_provider=False,base_url=None,model=None,api_key_env='BIOMODEL_API_KEY',provider_factory=None):
        if type(allow_provider) is not bool:raise ValueError('allow_provider 必须为布尔值')
        base=(base_url or os.environ.get('BIOMODEL_BASE_URL')) if allow_provider else None
        name=(model or os.environ.get('BIOMODEL_MODEL')) if allow_provider else None
        if base:
            parsed=urlsplit(base)
            if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('服务地址须为无凭证、查询或片段的 HTTP(S) URL')
        if allow_provider and (not base or not name):raise ValueError('启用模型服务需指定 base URL 和 model')
        if name:c.label(name,200)
        if not isinstance(api_key_env,str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*',api_key_env):raise ValueError('凭证环境变量名无效')
        self.store=ArtifactStore(root);self.readonly=recovery.open_readonly_store(self.store.root)
        self.token=secrets.token_hex(32);self.allow_provider=allow_provider
        self.provider_info=dict(enabled=allow_provider,base_url=base if allow_provider else None,model=name if allow_provider else None)
        self._provider_args=SimpleNamespace(base_url=base,model=name,api_key_env=api_key_env)
        self._provider_factory=provider_factory;self._lock=threading.RLock();self._states={};self._closed=False
        self._queue=queue.Queue();self._owner_fd=None
        self._acquire()
        self._worker=threading.Thread(target=self._loop,name='biomodel-workbench',daemon=True)
        self._worker.start()

    def _acquire(self):
        from . import local_ownership
        path=self.store.root/'.workbench-owner.lock'
        try:fd=local_ownership.acquire(path)
        except local_ownership.OwnerBusy as exc:raise ValueError('此 store 已有运行中的工作台') from exc
        self._owner_fd=fd

    def close(self,timeout=5.):
        with self._lock:
            if not self._closed:self._closed=True;self._queue.put(None)
        self._worker.join(timeout)

    def authenticate(self,token):
        return isinstance(token,str) and hmac.compare_digest(token,self.token)

    def _rows(self,kind,*,store=None,**matches):return tasks._records(self.readonly if store is None else store,kind,**matches)

    def overview(self):
        with verified_read(self.readonly) as store:
            return self._overview(store)

    def _overview(self,store):
        requests=[];sources=[];models=[];issues=[]
        rows=self._rows(tasks.REQUEST,store=store)
        # Display recency is file recency only; it never selects a task branch.
        rows.sort(key=lambda r:self.store.objects_dir.joinpath(r[0]+'.json').stat().st_mtime_ns,reverse=True)
        for r,p in rows[:100]:
            item=dict(request_ref=r,goal=p.get('spec',{}).get('goal','无法读取的任务'),revision=p.get('revision'),family_id=p.get('family_id'))
            try:item.update(tasks.status(store,r))
            except (ValueError,KeyError,TypeError,OSError):item.update(state='unavailable',error='记录或实现版本不匹配；保留原产物，使用对应版本检查。')
            requests.append(item)
        for r,p in self._rows('source',store=store)[:100]:
            try:sources.append(dict(source_ref=r,filename=p.get('filename','来源'),**evidence.source_summary(store,r)))
            except (ValueError,KeyError,TypeError,OSError):issues.append(dict(ref=r,message='来源记录不可用'))
        for kind in ('typed_model','fba_model'):
            for r,p in self._rows(kind,store=store)[:100]:
                try:brief=proposals._model_brief(store,r);models.append(brief)
                except (ValueError,KeyError,TypeError,OSError):issues.append(dict(ref=r,message='模型记录不可用'))
        jobs=self._rows(OPERATION,store=store)
        jobs.sort(key=lambda r:self.store.objects_dir.joinpath(r[0]+'.json').stat().st_mtime_ns,reverse=True)
        from . import research_projects
        projects=[]
        for r,p in self._rows(research_projects.PROJECT,store=store)[-100:]:
            try:projects.append(dict(project_ref=r,scenario=p['scenario'],state=research_projects.inspect(store,r)['state']))
            except (ValueError,KeyError,TypeError,OSError):projects.append(dict(project_ref=r,scenario=p.get('scenario','研究项目'),state='unavailable'))
        return dict(requests=requests,projects=projects,sources=sources,models=models,jobs=[self.job(r,store=store) for r,_ in jobs[:30]],issues=issues,
            request_total=len(rows),request_limit=100,provider=self.provider_info,model_types=list(tasks.TYPES),
            capabilities=capability_catalog(),scientific_support='unknown')

    def task(self,request_ref):
        ref(request_ref);request=tasks.load(self.readonly,request_ref);view=tasks.status(self.readonly,request_ref)
        related=[];selection=None
        if request.get('selection_ref'):
            review=self.readonly.get(request['selection_ref'])['payload']
            selection=dict(review_ref=request['selection_ref'],proposal_ref=review['proposal_ref'],candidate_id=review['candidate_id'],
                reviewer=review['reviewer'],reason=review['reason'])
        for r,p in self._rows(proposals.PROPOSAL):
            if p.get('request_ref')==request_ref or selection and r==selection['proposal_ref']:
                try:related.append(proposals.inspect(self.readonly,r))
                except (ValueError,KeyError,TypeError,OSError):related.append(dict(proposal_ref=r,state='unavailable',issues=['建议与当前实现不兼容，请保留原版本检查。']))
        planning=proposals.budget(self.readonly,request['family_id'])
        revisions=[dict(request_ref=r,revision=p['revision'],parent_ref=p['parent_ref'],goal=p['spec']['goal'])
            for r,p in self._rows(tasks.REQUEST,family_id=request['family_id'])]
        revisions.sort(key=lambda p:(p['revision'],p['request_ref']))
        out=dict(status=view,config=request['spec'],proposals=related,selection=selection,revisions=revisions,
            proposal_attempts=[dict(attempt_ref=r,request_ref=p['request_ref'],confirmed_return=bool(self._rows(proposals.RETURN,attempt_ref=r)),
                proposal_refs=[pr for pr,pp in self._rows(proposals.PROPOSAL,attempt_ref=r)])
                for r,p in self._rows(proposals.ATTEMPT,family_id=request['family_id'])],proposal_usage={k:planning[k] for k in
                    ('call_count','known_total_tokens','missing_total_usage_calls','total_tokens')},scientific_support='unknown')
        if view.get('native_run_ref'):
            from . import timeline
            out['timeline']=timeline.from_run(self.readonly,view.get('result_ref') or view['native_run_ref'])
        return out

    def source(self,source_ref,*,page=1,start=0,end=None):
        ref(source_ref);c.integer(page,1,100000,'page')
        text=evidence._source_page_text(self.readonly,source_ref,page)
        c.integer(start,0,len(text),'start');end=min(len(text),start+1600) if end is None else end
        c.integer(end,start,min(len(text),start+4000),'end')
        return dict(source_ref=source_ref,page=page,start=start,end=end,page_characters=len(text),
            before=text[max(0,start-180):start],quote=text[start:end],after=text[end:end+180],
            metadata=evidence.source_summary(self.readonly,source_ref))

    def report(self,artifact_ref):
        with verified_read(self.readonly) as store:
            from . import reporting
            ref(artifact_ref)
            if store.get(artifact_ref)['kind'] not in (tasks.REQUEST,tasks.RUN,proposals.PROPOSAL,'research_project','research_project_snapshot','research_reproduction_batch','research_batch_snapshot'):raise ValueError('请选择研究任务、建议或研究结果')
            return reporting.render_report(store,artifact_ref)

    def _validate(self,operation,params):
        if operation not in FIELDS:raise ValueError('不支持的工作台操作')
        required,optional=FIELDS[operation];c.fields(params,required,optional)
        for key in ('request_ref','proposal_ref','attempt_ref','ref','parent_ref','project_ref','question_ref'):
            if params.get(key) is not None:ref(params[key]);self.readonly.get(params[key])
        if operation=='create_project':
            from .research_projects import normalize
            normalize(self.readonly,params['config'])
        elif params.get('project_ref'):
            from . import research_projects
            research_projects.load(self.readonly,params['project_ref'])
            if operation=='reply_project':_text(params['reply'],4000);c.label(params['respondent'],200)
        elif operation.startswith('import_'):
            _filename(params['filename'],('.txt','.md') if operation=='import_source' else ('.json',))
            _text(params['content'],MAX_UPLOAD)
            if len(params['content'].encode('utf-8'))>MAX_UPLOAD:raise ValueError('上传文件最多 1 MB')
            if operation=='import_model' and params['model_type'] not in tasks.TYPES[1:]:raise ValueError('上传原生模型需指定 FBA 或四类原生类型')
        elif operation=='create_task':
            tasks._normalize(self.readonly,params['config'])
            if params.get('parent_ref'):tasks.load(self.readonly,params['parent_ref']);c.label(params.get('reason'),2000)
        elif operation=='prepare_proposal':proposals.normalize(self.readonly,params['request_ref'],params['proposal'])
        elif operation=='select_proposal':
            proposals.load(self.readonly,params['proposal_ref']);c.identifier(params['candidate_id'])
            c.label(params['reviewer'],200);c.label(params['reason'],2000);proposals._strings(params['acknowledged_unknowns'])
        elif operation=='resume_task':_text(params['reply'],12000);c.label(params['respondent'],200)
        elif operation=='recover_proposal':
            obj=self.readonly.get(params['attempt_ref'])
            if obj['kind']!=proposals.ATTEMPT:raise ValueError('请选择建议请求预留')
        elif operation=='export_bundle':self.report(params['ref'])
        return deepcopy(params)

    def _request_for(self,operation,params):
        if params.get('request_ref'):return params['request_ref']
        if operation=='create_task':return params.get('parent_ref')
        if operation=='select_proposal':return proposals.load(self.readonly,params['proposal_ref'])['request_ref']
        if operation=='recover_proposal':return self.readonly.get(params['attempt_ref'])['payload']['request_ref']
        return None

    def _service_needed(self,operation,params):
        if operation not in PROVIDER_ACTIONS:return False
        if operation=='run_project':
            from . import research_projects
            view=research_projects.inspect(self.readonly,params['project_ref'])
            if view['state'] in ('needs_recovery','awaiting_alignment'):raise ValueError('请先核对已确认返回或回答对齐问题')
            return view['continuation']['requires_provider']
        view=tasks.status(self.readonly,params['request_ref'])
        if operation=='run_task' and view['state']!='ready_to_run':raise ValueError('任务尚未就绪或已有执行；请查看状态与已有结果')
        if operation=='resume_task' and view['state']!='awaiting_input':raise ValueError('任务当前没有等待回答的问题')
        if operation=='propose_task':
            p=tasks.load(self.readonly,params['request_ref']);b=proposals.budget(self.readonly,p['family_id'])
            if tasks._execution(self.readonly,p) or b['unresolved_attempt_refs'] or b['call_count']>=p['spec'].get('proposal_max_calls',0):
                raise ValueError('建议额度未启用、已耗尽，或任务存在待核对的执行')
        if operation=='recover_task':
            if view.get('native_run_ref'):return False
            if view['state']!='needs_recovery':raise ValueError('任务没有可恢复的原生执行')
            diagnostic=recovery.diagnose_execution(self.readonly,view['execution_ref'])
            if diagnostic['status']!='ready':raise ValueError('原生执行恢复被阻断：'+', '.join(diagnostic['reasons']))
        return True

    def preview(self,operation,params):
        params=self._validate(operation,params)
        if operation not in PROVIDER_ACTIONS:raise ValueError('此操作不需要向模型服务发送材料')
        needed=self._service_needed(operation,params)
        if operation=='run_project':
            from . import research_projects,research_supervisor
            project=research_projects.load(self.readonly,params['project_ref'])
            scope=dict(version=1,operation=operation,project_ref=params['project_ref'],project=project,
                current=research_projects.inspect(self.readonly,params['project_ref']),provider=self.provider_info,
                initial_messages=research_supervisor._messages(self.readonly,params['project_ref']),
                data_categories=['应用场景与项目授权资料','Agent 后续读取的同项目材料、假设、模型及计算反馈',
                    '额度内检索并读取的 Europe PMC 开放全文（若文献额度启用）'],
                autonomy='Agent 决定研究对象、粒度、证据、假设和迭代；需要对齐时暂停。',scientific_support='unknown')
            digest=hashlib.sha256(canonical_bytes(scope)).hexdigest()
            return dict(requires_provider=needed,provider_enabled=self.allow_provider,scope_sha256=digest,scope=scope,
                note='此次确认覆盖本项目冻结额度内的研究循环；不要求逐个建模决定再次确认。')
        request=tasks.load(self.readonly,params['request_ref']);view=tasks.status(self.readonly,params['request_ref'])
        if request['implementation_sha256']!=recovery.implementation_fingerprint():raise ValueError('任务实现版本已变化，请先创建明确的修订')
        scope=dict(version=1,operation=operation,request_ref=params['request_ref'],request=request['spec'],
            provider=self.provider_info,native_task_ref=view.get('native_task_ref'),native_run_ref=view.get('native_run_ref'),
            execution_ref=view.get('execution_ref'),reply=params.get('reply'),respondent=params.get('respondent'),
            data_categories=['任务目标与已授权来源','冻结模型、运行设置与相关工具反馈',*(['本次回答'] if operation=='resume_task' else [])],
            scientific_support='unknown')
        if operation=='propose_task':scope['messages']=proposals.messages(self.readonly,params['request_ref'])
        digest=hashlib.sha256(canonical_bytes(scope)).hexdigest()
        return dict(requires_provider=needed,provider_enabled=self.allow_provider,scope_sha256=digest,scope=scope,
            note='启用后仅发送此任务范围和执行期间相关工具反馈；凭证留在服务端环境变量中。')

    def submit(self,key,operation,params,*,provider_consent=False,scope_sha256=None):
        if not isinstance(key,str) or not KEY.fullmatch(key):raise ValueError('作业标识无效')
        if type(provider_consent) is not bool:raise ValueError('provider_consent 必须为布尔值')
        params=self._validate(operation,params)
        with self._lock:
            if self._closed:raise ValueError('工作台正在关闭')
            existing=self._rows(OPERATION,key=key)
            if len(existing)>1:raise ValueError('作业标识存在冲突')
            if existing:
                r,p=existing[0]
                if p['operation']!=operation or p['params']!=params:raise ValueError('同一作业标识不能提交不同内容')
                return self.job(r)
            needed=self._service_needed(operation,params)
            preview=self.preview(operation,params) if operation in PROVIDER_ACTIONS else None
            if needed and (not self.allow_provider or provider_consent is not True or scope_sha256!=preview['scope_sha256']):
                raise ValueError('请先启用模型服务、检查发送范围并明确确认此次请求')
            request_ref=self._request_for(operation,params)
            family=tasks.load(self.readonly,request_ref)['family_id'] if request_ref else None
            if params.get('project_ref'):family='project:'+params['project_ref']
            if sum(s['state'] in ('queued','running') for s in self._states.values())>=16:raise ValueError('队列已满，请等待当前作业结束')
            if family and any(s.get('family_id')==family and s['state'] in ('queued','running') for s in self._states.values()):
                raise ValueError('此任务已有排队或执行中的操作，请查看作业状态')
            refs=[]
            for field in ('request_ref','proposal_ref','attempt_ref','ref','parent_ref','project_ref','question_ref'):
                if params.get(field):refs.append(params[field])
            if operation=='create_task':
                refs.extend(params['config'].get('source_refs',[]))
                if params['config'].get('model_ref'):refs.append(params['config']['model_ref'])
            if operation=='create_project':
                for input_field in ('source_refs','model_refs','reference_refs'):refs.extend(params['config'].get(input_field,[]))
            reservation=self.store.put(OPERATION,dict(version=1,key=key,operation=operation,params=params,family_id=family,request_ref=request_ref,
                phase='reserved',requires_provider=needed,provider_consent=needed and provider_consent,
                scope_sha256=preview['scope_sha256'] if preview else None,
                implementation_sha256=recovery.implementation_fingerprint()),refs=tuple(dict.fromkeys(refs)))
            self._states[reservation]=dict(state='queued',family_id=family);self._queue.put(reservation)
            return self.job(reservation)

    def job(self,operation_ref,*,store=None):
        store=self.readonly if store is None else store
        ref(operation_ref);obj=store.get(operation_ref);p=obj['payload']
        if obj['kind']!=OPERATION or type(p.get('version')) is not int or p['version']!=1:raise ValueError('不是工作台作业')
        # Capture live state before scanning: completion can arrive while the
        # scan is in progress. A terminal state always needs its saved receipt.
        state=self._states.get(operation_ref,{}).get('state','unknown_effects')
        if state in ('queued','running') and not self._worker.is_alive():state='unknown_effects'
        terminals=self._rows(TERMINAL,operation_ref=operation_ref,store=store)
        if len(terminals)>1:raise ValueError('作业存在多个结束记录')
        if not terminals and state in ('returned','failed'):state='unknown_effects'
        out=dict(job_ref=operation_ref,key=p['key'],operation=p['operation'],request_ref=p.get('request_ref'),
            state=state,result=None,
            input_summary={k:p['params'][k] for k in ('filename','request_ref','proposal_ref','attempt_ref','candidate_id','respondent','project_ref') if k in p['params']},
            note='未确认的操作不会在刷新或重启后重新提交。')
        if p['operation']=='create_task':out['input_summary']['goal']=p['params']['config']['goal']
        if terminals:
            r,t=terminals[0]
            if type(t.get('version')) is not int or t['version']!=1 or t['state'] not in ('returned','failed') or operation_ref not in store.get(r)['refs']:
                raise ValueError('作业结束记录无效')
            # The immutable object can become visible before its SQLite index
            # commit finishes. Do not publish completion ahead of our worker.
            final_state=state if state in ('queued','running') else t['state']
            out.update(state=final_state,result=t.get('result'),error=t.get('error'),terminal_ref=r)
        return out

    def job_by_key(self,key):
        if not isinstance(key,str) or not KEY.fullmatch(key):raise ValueError('作业标识无效')
        rows=self._rows(OPERATION,key=key)
        if len(rows)!=1:raise ValueError('没有唯一匹配的作业；请核对任务与账本后再决定下一步')
        return self.job(rows[0][0])

    def _provider(self,params):
        identity=None
        if params.get('request_ref'):
            view=tasks.status(self.readonly,params['request_ref'])
            if view.get('native_task_ref'):identity=self.readonly.get(view['native_task_ref'])['payload']['provider']
        try:
            if self._provider_factory:return self._provider_factory(identity)
            from .research_cli import _provider
            return _provider(self._provider_args,identity)
        except Exception:raise ValueError('模型服务初始化失败，请在服务端检查配置与凭证环境变量。') from None

    def _perform(self,operation,params):
        if operation in ('create_project','run_project','reply_project','recover_project','snapshot_project'):
            from . import research_projects,research_supervisor
            if operation=='create_project':return dict(project_ref=research_projects.create(self.store,params['config']))
            project=params['project_ref']
            if operation=='run_project':
                provider=self._provider(params) if self._service_needed(operation,params) else None
                return dict(project_ref=project,snapshot_ref=research_supervisor.run(self.store,provider,project))
            if operation=='reply_project':
                result=research_projects.operate(self.store,project,'reply_alignment',
                    {k:params[k] for k in ('question_ref','reply','respondent')},actor='operator')
                return dict(project_ref=project,**result)
            if operation=='recover_project':
                result=research_projects.recover(self.store,project,params['attempt_ref'])
                return dict(project_ref=project,**result)
            return dict(project_ref=project,snapshot_ref=research_projects.snapshot(self.store,project))
        if operation.startswith('import_'):
            with tempfile.TemporaryDirectory(prefix='biomodel-workbench-') as directory:
                path=Path(directory)/params['filename'];path.write_bytes(params['content'].encode('utf-8'))
                if operation=='import_source':return dict(source_ref=evidence.ingest_source(self.store,path))
                from . import fba,typed_models
                if params['model_type']=='fba':model=fba.import_model(self.store,path)
                else:
                    decoded=typed_models._decode(path.read_bytes())
                    if decoded['model_type']!=params['model_type']:raise ValueError('上传模型类型与所选类型不一致')
                    model=typed_models.import_model(self.store,path)
                return dict(model_ref=model)
        if operation=='create_task':return dict(request_ref=tasks.prepare(self.store,params['config'],parent_ref=params.get('parent_ref'),reason=params.get('reason')))
        if operation=='prepare_proposal':return dict(proposal_ref=proposals.prepare(self.store,params['request_ref'],params['proposal']),request_ref=params['request_ref'])
        if operation=='select_proposal':return dict(request_ref=proposals.accept(self.store,**params))
        if operation=='recover_proposal':return dict(proposal_ref=proposals.recover(self.store,params['attempt_ref']))
        if operation=='export_bundle':
            from .bundles import export_bundle
            with tempfile.TemporaryDirectory(prefix='biomodel-export-') as directory:
                bundle=Path(directory)/'bundle';export_bundle(self.store,params['ref'],bundle)
                files=sorted(p for p in bundle.rglob('*') if p.is_file())
                if sum(p.stat().st_size for p in files)>MAX_BUNDLE:raise ValueError('工作台导出超过 64 MiB，请使用 CLI 导出')
                output=io.BytesIO()
                with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as z:
                    for path in files:z.write(path,'bundle/'+str(path.relative_to(bundle)))
                return dict(bundle_blob_ref=self.store.put_blob(output.getvalue()),root_ref=params['ref'])
        needed=self._service_needed(operation,params);provider=self._provider(params) if needed else None
        request=params['request_ref']
        if operation=='propose_task':return dict(proposal_ref=proposals.propose(self.store,provider,request),request_ref=request)
        if operation=='run_task':r=tasks.execute(self.store,provider,request)
        elif operation=='resume_task':r=tasks.resume(self.store,provider,request,params['reply'],respondent=params['respondent'])
        else:r=tasks.recover(self.store,provider,request)
        return dict(result_ref=r,request_ref=request)

    def _loop(self):
        try:
            while True:
                r=self._queue.get()
                if r is None:return
                with self._lock:self._states[r]['state']='running'
                p=self.readonly.get(r)['payload']
                performed=False
                try:
                    if p['implementation_sha256']!=recovery.implementation_fingerprint():raise ValueError('产品源码已变化，请停止此作业并核对版本')
                    if p['requires_provider']:
                        preview=self.preview(p['operation'],p['params'])
                        if not self.allow_provider or preview['scope_sha256']!=p['scope_sha256']:raise ValueError('任务或发送范围已变化，需要重新检查')
                    result=self._perform(p['operation'],p['params'])
                    performed=True
                    refs=[r,*[v for k,v in result.items() if k.endswith('_ref') and isinstance(v,str)]]
                    self.store.put(TERMINAL,dict(version=1,operation_ref=r,state='returned',result=result),refs=tuple(dict.fromkeys(refs)))
                    state='returned'
                except Exception as exc:
                    # Provider errors are handled by their own journals. Never
                    # save arbitrary exception text (it may contain credentials).
                    message=str(exc)[:2000] if isinstance(exc,(ValueError,KeyError,TypeError)) else '操作未完成，请查看任务和已有账本；不会自动重试。'
                    if performed:state='unknown_effects'
                    else:
                        try:
                            self.store.put(TERMINAL,dict(version=1,operation_ref=r,state='failed',error=dict(type=type(exc).__name__,message=message)),refs=(r,))
                            state='failed'
                        except BaseException:state='unknown_effects'
                except BaseException:state='unknown_effects'
                with self._lock:self._states[r]['state']=state
        finally:
            with self._lock:
                if self._owner_fd is not None:os.close(self._owner_fd);self._owner_fd=None

    def bundle(self,operation_ref):
        job=self.job(operation_ref)
        if job['state']!='returned' or job['operation']!='export_bundle':raise ValueError('导出尚未确认完成')
        return self.readonly.get_blob(job['result']['bundle_blob_ref'])
