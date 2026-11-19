"""Thin external-Agent CLI over decision plans and confirmed execution facts."""
import json
from pathlib import Path
import sys


def read_json(path):
    path = Path(path)
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('workspace input exceeds 1 MiB')
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise ValueError(f'duplicate JSON field: {key}')
            obj[key] = value
        return obj
    def constant(value):
        raise ValueError(f'nonfinite JSON constant: {value}')
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=pairs, parse_constant=constant)


def command(args):
    from . import modeling_plan as plans, modeling_workflow as workflow, timeline
    from .recovery import open_readonly_store
    from .storage import ArtifactStore
    try:
        cmd = args.command
        readonly = cmd in ('model-plan-show', 'model-plan-compilation-show', 'model-plan-status',
                           'model-plan-summary', 'agent-timeline')
        store = open_readonly_store(args.store) if readonly else ArtifactStore(args.store)
        code = 0
        if cmd == 'model-plan-prepare':
            ref = plans.prepare(store, read_json(args.config), args.source_ref,
                parent_ref=args.parent_ref, reason=args.reason)
            result = plans.inspect(store, ref)
            code = 0 if result['state'] == 'ready_to_compile' else 2
        elif cmd == 'model-plan-show':
            result = dict(**plans.inspect(store, args.ref), plan=plans.load(store, args.ref))
        elif cmd == 'model-plan-compile':
            ref = plans.compile_plan(store, args.ref)
            result = dict(compilation_ref=ref, **store.get(ref)['payload'])
            code = 0 if result['state'] == 'ready' else 2
        elif cmd == 'model-plan-compilation-show':
            obj = store.get(args.ref)
            if obj['kind'] != plans.COMPILATION:
                raise ValueError('expected a modeling-plan compilation')
            result = dict(compilation_ref=args.ref, saved_receipt=obj['payload'],
                          current_readiness=plans.inspect(store, obj['payload']['plan_ref']))
        elif cmd == 'model-plan-execution-prepare':
            ref = workflow.prepare(store, args.ref, read_json(args.config))
            result = workflow.status(store, ref)
        elif cmd == 'model-plan-status':
            result = workflow.status(store, args.ref)
        elif cmd in ('model-plan-execute', 'model-plan-replay'):
            ref = workflow.execute(store, args.ref) if cmd == 'model-plan-execute' else workflow.replay(store, args.ref)
            result = workflow.summary(store, ref)
            code = 0 if result['completion_eligible'] else 1
        elif cmd == 'model-plan-summary':
            result = workflow.summary(store, args.ref)
        elif cmd == 'agent-timeline':
            view = timeline.from_run(store, args.ref)
            text = timeline.render_markdown(view) if args.format == 'markdown' else json.dumps(view, ensure_ascii=False, indent=2) + '\n'
            if args.output:
                # Presentation output is an explicitly requested new file.
                with open(args.output, 'x', encoding='utf-8') as f:
                    f.write(text)
            else:
                print(text, end='')
            return 0
        else:
            raise ValueError('unsupported modeling command')
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return code
    except Exception as exc:
        print(f'error: {args.command} failed: {exc}', file=sys.stderr)
        return 1


def register(sub, add_store):
    p = sub.add_parser('model-plan-prepare', help='freeze explicit ODE evidence, mechanisms and mathematical choices')
    add_store(p); p.add_argument('--config', required=True); p.add_argument('--source-ref', action='append', required=True)
    p.add_argument('--parent-ref'); p.add_argument('--reason'); p.set_defaults(func=command)
    for name in ('model-plan-show', 'model-plan-compile', 'model-plan-compilation-show', 'model-plan-execution-prepare',
                 'model-plan-execute', 'model-plan-status', 'model-plan-summary', 'model-plan-replay'):
        p = sub.add_parser(name, help='explicit decision-plan inspection, compilation or frozen execution')
        add_store(p); p.add_argument('ref')
        if name == 'model-plan-execution-prepare':
            p.add_argument('--config', required=True)
        p.set_defaults(func=command)
    p = sub.add_parser('agent-timeline', help='render saved events; no model execution or inferred timestamps')
    add_store(p); p.add_argument('ref'); p.add_argument('--format', choices=('json', 'markdown'), default='markdown')
    p.add_argument('--output'); p.set_defaults(func=command)
