"""Loopback-only HTTP adapter for the research workbench, no runtime web deps."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import json
import sys
from urllib.parse import parse_qs,urlsplit

from . import model_contracts as c
from .workbench import MAX_BODY,Workbench,decode,ref

ASSETS=Path(__file__).parent/'workbench_assets'


class Server(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=True
    def __init__(self,workbench,port=0):
        c.integer(port,0,65535,'port')
        self.workbench=workbench
        super().__init__(('127.0.0.1',port),Handler)
        self.authority='127.0.0.1:'+str(self.server_port)
        self.origin='http://'+self.authority


class Handler(BaseHTTPRequestHandler):
    server_version='BioModelWorkbench'
    sys_version=''
    def log_message(self,*args):pass

    def _send(self,code,data,content_type='application/json; charset=utf-8',filename=None):
        if not isinstance(data,bytes):data=json.dumps(data,ensure_ascii=False,allow_nan=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if filename:self.send_header('Content-Disposition','attachment; filename="'+filename+'"')
        self.send_header('Connection','close');self.end_headers()
        try:self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError):pass
        self.close_connection=True

    def _guard(self,api=False):
        if len(self.headers.get_all('Host',[]))!=1 or self.headers.get('Host')!=self.server.authority:
            self._send(403,dict(error='仅接受启动时显示的本地地址'));return False
        origin=self.headers.get('Origin')
        if origin is not None and origin!=self.server.origin:
            self._send(403,dict(error='请求来源不匹配'));return False
        navigation=(not api and self.command=='GET' and urlsplit(self.path).path=='/'
            and self.headers.get('Sec-Fetch-Mode')=='navigate' and self.headers.get('Sec-Fetch-Dest')=='document')
        if self.headers.get('Sec-Fetch-Site')=='cross-site' and not navigation:
            self._send(403,dict(error='不接受跨站请求'));return False
        if api and not self.server.workbench.authenticate(self.headers.get('X-Workbench-Token')):
            self._send(403,dict(error='本地会话已失效，请从工作台首页重新打开'));return False
        return True

    def do_GET(self):
        parsed=urlsplit(self.path);path=parsed.path
        api=path.startswith('/api/') or path.startswith('/downloads/')
        if not self._guard(api):return
        try:
            if path in ('/','/app.js','/style.css','/projects','/projects.js'):
                filename={'/':'index.html','/app.js':'app.js','/style.css':'style.css','/projects':'projects.html','/projects.js':'projects.js'}[path]
                raw=(ASSETS/filename).read_bytes()
                if path in ('/','/projects'):raw=raw.replace(b'__WORKBENCH_TOKEN__',self.server.workbench.token.encode('ascii'))
                self._send(200,raw,'text/html; charset=utf-8' if path in ('/','/projects') else 'text/css; charset=utf-8' if path=='/style.css' else 'text/javascript; charset=utf-8');return
            app=self.server.workbench;parts=path.strip('/').split('/')
            query=parse_qs(parsed.query,keep_blank_values=True)
            if path=='/api/overview':out=app.overview()
            elif len(parts)==3 and parts[:2]==['api','project']:
                from . import research_projects,research_supervisor
                root=ref(parts[2]);out=research_projects.inspect(app.readonly,root)
                out['iteration_facts']={r:research_projects.facts(app.readonly,r) for r in out['iteration_refs']}
                out['questions']=[dict(question_ref=r,**research_projects.own(app.readonly,r,research_projects.QUESTION,root)) for r in out['pending_questions']]
                out['assessments']=[research_projects.own(app.readonly,r,research_projects.ASSESSMENT,root) for r in out['assessment_refs']]
                out['usage']=research_supervisor.usage(app.readonly,root)
            elif len(parts)==3 and parts[:2]==['api','task']:out=app.task(parts[2])
            elif len(parts)==3 and parts[:2]==['api','proposal']:
                from . import task_proposals
                out=task_proposals.inspect(app.readonly,ref(parts[2]))
            elif len(parts)==3 and parts[:2]==['api','source']:
                if set(query)-{'page','start','end'} or any(len(v)!=1 for v in query.values()):raise ValueError('来源参数无效')
                values={}
                for key,values_list in query.items():
                    value=values_list[0]
                    if not value.isascii() or not value.isdigit() or len(value)>9:raise ValueError('来源偏移须为非负整数')
                    values[key]=int(value)
                out=app.source(parts[2],**values)
            elif len(parts)==3 and parts[:2]==['api','job']:out=app.job(parts[2])
            elif len(parts)==3 and parts[:2]==['api','job-key']:out=app.job_by_key(parts[2])
            elif len(parts)==3 and parts[:2]==['api','report']:out=dict(text=app.report(parts[2]))
            elif len(parts)==3 and parts[:2]==['downloads','report'] and parts[2].endswith('.md'):
                r=ref(parts[2][:-3]);self._send(200,app.report(r).encode('utf-8'),'text/markdown; charset=utf-8',r+'.md');return
            elif len(parts)==3 and parts[:2]==['downloads','bundle'] and parts[2].endswith('.zip'):
                r=ref(parts[2][:-4]);self._send(200,app.bundle(r),'application/zip',r+'.zip');return
            else:self._send(404,dict(error='没有此页面'));return
            self._send(200,out)
        except (ValueError,KeyError,TypeError,OSError):self._send(409,dict(error='记录不可用、输入不合法或实现版本已变化，请检查任务和原记录。'))
        except Exception:self._send(500,dict(error='本地读取未完成，请检查服务端记录。'))

    def do_POST(self):
        if not self._guard(True):return
        try:
            if self.headers.get('Transfer-Encoding'):raise ValueError('不支持分块请求')
            lengths=self.headers.get_all('Content-Length',[])
            if len(lengths)!=1 or not lengths[0].isascii() or not lengths[0].isdigit():raise ValueError('请求需提供唯一长度')
            length=int(lengths[0])
            if not 0<length<=MAX_BODY:self._send(413,dict(error='请求超过大小限制'));return
            if self.headers.get('Content-Type','').split(';')[0].strip()!='application/json':raise ValueError('请发送 JSON 请求')
            self.connection.settimeout(10.)
            raw=self.rfile.read(length)
            if len(raw)!=length:raise ValueError('请求未完整接收')
            payload=decode(raw);path=urlsplit(self.path).path;app=self.server.workbench
            if path=='/api/preview':
                c.fields(payload,{'operation','params'});out=app.preview(**payload);code=200
            elif path=='/api/parse-json':
                c.fields(payload,{'text'})
                if not isinstance(payload['text'],str):raise ValueError('JSON 内容须为文本')
                out=dict(value=decode(payload['text'].encode('utf-8')));code=200
            elif path=='/api/jobs':
                c.fields(payload,{'key','operation','params'},{'provider_consent','scope_sha256'})
                out=app.submit(**payload);code=202
            else:self._send(404,dict(error='没有此操作'));return
            self._send(code,out)
        except (ValueError,KeyError,TypeError) as exc:self._send(409,dict(error=str(exc)[:2000]))
        except OSError:self._send(409,dict(error='本地操作未完成，请检查任务与作业记录后继续。'))
        except Exception:self._send(500,dict(error='操作结果未确认，请按原作业标识查询；不要重新提交。'))


def command(args):
    app=None;server=None
    try:
        app=Workbench(args.store,allow_provider=args.allow_provider,base_url=args.base_url,model=args.model,api_key_env=args.api_key_env)
        server=Server(app,args.port)
        print(json.dumps(dict(url=server.origin,store=str(app.store.root.absolute()),provider_enabled=app.allow_provider),ensure_ascii=False),flush=True)
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:return 0
    except (ValueError,OSError) as exc:
        print('workbench failed: '+str(exc),file=sys.stderr);return 1
    finally:
        if server:server.server_close()
        if app:app.close()
    return 0


def register(sub,add_store):
    p=sub.add_parser('workbench',help='serve the local research workspace at 127.0.0.1; provider calls disabled by default')
    add_store(p);p.add_argument('--port',type=int,default=8765)
    p.add_argument('--allow-provider',action='store_true');p.add_argument('--base-url');p.add_argument('--model')
    p.add_argument('--api-key-env',default='BIOMODEL_API_KEY');p.set_defaults(func=command)
