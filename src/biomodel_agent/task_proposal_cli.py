"""Explicit preparation, bounded proposing, inspection and local selection."""
import json
import sys


def command(args):
    from . import task_proposals as proposals
    from .modeling_cli import read_json
    from .recovery import open_readonly_store
    from .research_cli import _provider
    from .storage import ArtifactStore
    try:
        store=open_readonly_store(args.store) if args.command=='research-proposal-show' else ArtifactStore(args.store)
        if args.command=='research-task-propose':
            ref=proposals.propose(store,_provider(args),args.ref)
        elif args.command=='research-proposal-create':
            ref=proposals.prepare(store,args.ref,read_json(args.config))
        elif args.command=='research-proposal-recover':ref=proposals.recover(store,args.ref)
        elif args.command=='research-proposal-select':
            from .research_tasks import status
            unknowns=read_json(args.acknowledged_unknowns_file) if args.acknowledged_unknowns_file else []
            ref=proposals.accept(store,args.ref,args.candidate,reviewer=args.reviewer,reason=args.reason,acknowledged_unknowns=unknowns)
            out=status(store,ref)
            print(json.dumps(out,ensure_ascii=False,allow_nan=False))
            return 0 if out['state']=='ready_to_run' else 2
        else:ref=args.ref
        out=proposals.inspect(store,ref)
        print(json.dumps(out,ensure_ascii=False,allow_nan=False))
        return 0 if args.command=='research-proposal-show' or out['state']=='proposed' else 2
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print('task proposal failed: '+str(exc),file=sys.stderr)
        return 1


def register(sub,add_store):
    for name in ('research-task-propose','research-proposal-create','research-proposal-show','research-proposal-select','research-proposal-recover'):
        p=sub.add_parser(name,help='explicit type/input proposal; never executes a scientific backend')
        add_store(p);p.add_argument('ref');p.set_defaults(func=command)
        if name=='research-task-propose':
            p.add_argument('--base-url');p.add_argument('--model');p.add_argument('--api-key-env',default='BIOMODEL_API_KEY')
        if name=='research-proposal-create':p.add_argument('--config',required=True)
        if name=='research-proposal-select':
            p.add_argument('--candidate',required=True);p.add_argument('--reviewer',required=True)
            p.add_argument('--reason',required=True);p.add_argument('--acknowledged-unknowns-file')
