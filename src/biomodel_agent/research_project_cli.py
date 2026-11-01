"""Research-project and development reproduction-pipeline CLI."""
import json
import sys

from . import research_projects as projects,research_pipeline as pipeline,research_supervisor as supervisor


def command(args):
    from .modeling_cli import read_json
    from .research_cli import _provider
    from .storage import ArtifactStore
    from .recovery import open_readonly_store
    try:
        cmd=args.command
        readonly=cmd in ('research-project-show','research-batch-show')
        store=open_readonly_store(args.store) if readonly else ArtifactStore(args.store)
        if cmd=='research-project-create':
            ref=projects.create(store,read_json(args.config));out=projects.inspect(store,ref)
        elif cmd=='research-project-reference':
            ref=projects.register_reference(store,read_json(args.config),args.actor);out=dict(reference_ref=ref)
        elif cmd=='research-project-show':
            obj=store.get(args.ref)
            if obj['kind']==projects.SNAPSHOT:
                projects.load(store,obj['payload']['project_ref']);out=dict(snapshot_ref=args.ref,**obj['payload']['view'])
            else:out=projects.inspect(store,args.ref)
        elif cmd=='research-project-step':
            action=read_json(args.action_file)
            projects.c.fields(action,{'action','args'})
            out=projects.operate(store,args.ref,action['action'],action['args'],key=args.key,actor='operator')
        elif cmd=='research-project-reply':
            from pathlib import Path
            view=projects.inspect(store,args.ref)
            if not view['pending_questions']:raise ValueError('no pending alignment question')
            out=projects.operate(store,args.ref,'reply_alignment',dict(question_ref=view['pending_questions'][-1],
                reply=Path(args.reply_file).read_text(encoding='utf-8'),respondent=args.respondent),key=args.key,actor='operator')
        elif cmd=='research-project-recover':out=projects.recover(store,args.ref,args.attempt_ref)
        elif cmd=='research-project-snapshot':out=dict(snapshot_ref=projects.snapshot(store,args.ref))
        elif cmd=='research-project-run':
            project=projects.load(store,args.ref)
            provider=_provider(args) if projects.inspect(store,args.ref)['continuation']['requires_provider'] else None
            ref=supervisor.run(store,provider,args.ref);out=dict(snapshot_ref=ref,**projects.inspect(store,args.ref),usage=supervisor.usage(store,args.ref))
        elif cmd=='research-batch-create':
            ref=pipeline.create(store,read_json(args.config));out=pipeline.inspect(store,ref)
        elif cmd=='research-batch-show':
            obj=store.get(args.ref)
            if obj['kind']==pipeline.SNAPSHOT:
                pipeline.load(store,obj['payload']['batch_ref']);out=dict(snapshot_ref=args.ref,**obj['payload']['view'])
            else:out=pipeline.inspect(store,args.ref)
        elif cmd=='research-batch-run':
            ref=pipeline.run(store,args.ref,lambda case:_provider(args) if projects.load(store,case['project_ref'])['policy']['planner_max_calls'] else None)
            out=dict(snapshot_ref=ref,**pipeline.inspect(store,args.ref))
        elif cmd=='research-batch-lessons':out=dict(experience_refs=pipeline.lessons(store,args.ref,_provider(args)))
        else:raise ValueError('unknown research project command')
        print(json.dumps(out,ensure_ascii=False,allow_nan=False))
        return 0
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print('research project operation failed: '+str(exc),file=sys.stderr);return 1


def register(sub,add_store):
    for name in ('research-project-create','research-project-reference','research-batch-create'):
        parser=sub.add_parser(name,help='save a research scenario, frozen reference, or development paper queue')
        add_store(parser);parser.add_argument('--config',required=True);parser.set_defaults(func=command)
        if name=='research-project-reference':parser.add_argument('--actor',required=True)
    for name in ('research-project-show','research-project-run','research-project-step','research-project-reply',
                 'research-project-recover','research-project-snapshot','research-batch-show','research-batch-run','research-batch-lessons'):
        parser=sub.add_parser(name,help='inspect or explicitly advance the agent-led research workflow')
        add_store(parser);parser.add_argument('ref');parser.set_defaults(func=command)
        if name in ('research-project-run','research-batch-run','research-batch-lessons'):
            parser.add_argument('--base-url');parser.add_argument('--model');parser.add_argument('--api-key-env',default='BIOMODEL_API_KEY')
        if name in ('research-project-step','research-project-reply'):parser.add_argument('--key')
        if name=='research-project-step':parser.add_argument('--action-file',required=True)
        if name=='research-project-reply':
            parser.add_argument('--reply-file',required=True);parser.add_argument('--respondent',required=True)
        if name=='research-project-recover':parser.add_argument('--attempt-ref',required=True)

