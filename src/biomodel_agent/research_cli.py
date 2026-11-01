"""Unified local research task commands; provider use follows explicit dispatch."""
import json
import os
import sys


def _provider(args,identity=None):
    from .llm import ChatProvider
    base=args.base_url or os.environ.get('BIOMODEL_BASE_URL')
    model=(identity or {}).get('model') or args.model or os.environ.get('BIOMODEL_MODEL')
    if not base or not model or not os.environ.get(args.api_key_env):
        raise ValueError('missing provider base URL/model/key environment variable; no service called')
    return ChatProvider(base,model,args.api_key_env,
        timeout_s=(identity or {}).get('timeout_s',60),max_tokens=(identity or {}).get('max_tokens',4000),
        json_mode=(identity or {}).get('json_mode',False))


def command(args):
    from . import research_tasks as tasks, typed_models
    from .modeling_cli import read_json
    from .storage import ArtifactStore
    from .recovery import open_readonly_store,diagnose_execution
    try:
        cmd=args.command
        if cmd=='research-task-capabilities':
            out=dict(model_types=typed_models.capabilities(),type_selection='explicit_selection_with_optional_evidenced_proposals',
                workflows=dict(ode='source-backed decision-plan reproduction',native='operator-prepared native model execution'),
                continuation='ODE, FBA v2 and typed v2; unknown effects never auto-retry',
                automatic_source_to_native_model=False,
                source_to_model='explicit source-bound unclamped Boolean or integer stochastic-combinations SSA drafts, then operator selection; no general conversion',
                proposal_budget='opt_in proposal_max_calls 0..3, separate from native max_calls; family-scoped',scientific_support='unknown')
        else:
            readonly=cmd in ('research-task-show','research-task-result')
            store=open_readonly_store(args.store) if readonly else ArtifactStore(args.store)
            if cmd=='research-task-create':
                ref=tasks.prepare(store,read_json(args.config),parent_ref=args.parent_ref,reason=args.reason)
                out=tasks.status(store,ref)
            elif cmd=='research-task-show':out=tasks.status(store,args.ref)
            elif cmd=='research-task-result':out=dict(result_ref=args.ref,**tasks.result(store,args.ref))
            else:
                view=tasks.status(store,args.ref)
                provider=None
                if cmd=='research-task-run':
                    if view['state']=='ready_to_run':provider=_provider(args)
                    ref=tasks.execute(store,provider,args.ref)
                else:
                    identity=None
                    if view.get('native_task_ref'):identity=store.get(view['native_task_ref'])['payload']['provider']
                    if cmd=='research-task-resume':
                        if view['state']!='awaiting_input':raise ValueError('task is not awaiting input')
                        from .cli import _read_text
                        reply=_read_text(args.reply_file)
                        ref=tasks.resume(store,_provider(args,identity),args.ref,reply,respondent=args.respondent)
                    else:
                        if not view.get('native_run_ref'):
                            if view['state']!='needs_recovery':raise ValueError(view['next_action'])
                            d=diagnose_execution(store,view['execution_ref'])
                            if d['status']!='ready':raise ValueError(', '.join(d['reasons']) or 'execution cannot recover')
                            provider=_provider(args,identity)
                        ref=tasks.recover(store,provider,args.ref)
                out=dict(result_ref=ref,**tasks.result(store,ref))
        print(json.dumps(out,ensure_ascii=False,allow_nan=False))
        if cmd in ('research-task-capabilities','research-task-show','research-task-result'):return 0
        return 0 if out.get('state') in ('ready_to_run','completed') else 2
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print('research task failed: '+str(exc),file=sys.stderr)
        return 1


def register(sub,add_store):
    p=sub.add_parser('research-task-capabilities',help='list explicit scientific workflows and input requirements')
    p.set_defaults(func=command)
    p=sub.add_parser('research-task-create',help='save intake or pre-dispatch revision; does not call a provider')
    add_store(p);p.add_argument('--config',required=True);p.add_argument('--parent-ref');p.add_argument('--reason')
    p.set_defaults(func=command)
    for name in ('research-task-show','research-task-result','research-task-run','research-task-resume','research-task-recover'):
        p=sub.add_parser(name,help='unified task status, execution or controlled continuation')
        add_store(p);p.add_argument('ref');p.set_defaults(func=command)
        if name in ('research-task-run','research-task-resume','research-task-recover'):
            p.add_argument('--base-url');p.add_argument('--model');p.add_argument('--api-key-env',default='BIOMODEL_API_KEY')
        if name=='research-task-resume':
            p.add_argument('--reply-file',required=True);p.add_argument('--respondent',required=True)
