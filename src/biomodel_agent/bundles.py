"""Portable verified object closure; the computation replay never calls an LLM."""
from pathlib import Path
import json
from .storage import ArtifactStore


def export_bundle(store,root_ref,destination):
    destination=Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('export destination must be empty')
    root_object=store.get(root_ref)  # root must be a verified object, not an arbitrary file
    case_material=(root_ref if root_object['kind']=='case_material' else root_object['payload']['material_ref'] if root_object['kind']=='case_run' else None)
    if case_material is not None:
        if store.get(case_material)['kind']!='case_material' or (root_object['kind']=='case_run' and case_material not in root_object['refs']):
            raise ValueError('case replay material is missing from the exported closure')
    target=ArtifactStore(destination)
    seen=set()
    def visit(ref):
        if ref in seen:
            return
        seen.add(ref)
        if len(seen)>10000:
            raise ValueError('bundle object budget exceeded')
        if (store.objects_dir/f'{ref}.json').exists():
            obj=store.get(ref)
            for child in obj['refs']:
                visit(child)
            copied=target.put(obj['kind'],obj['payload'],refs=obj['refs'])
            # The same bytes may be both an envelope and an explicitly saved
            # raw file. Preserve both APIs; a file snapshot needs get_blob(ref).
            if (store.blobs_dir/ref).exists():
                if target.put_blob(store.get_blob(ref))!=ref:
                    raise ValueError('export changed raw object-file content')
        else:
            copied=target.put_blob(store.get_blob(ref))
        if copied!=ref:
            raise ValueError('export changed immutable content')
    visit(root_ref)
    plan_commands = {'modeling_plan': 'model-plan-show', 'modeling_plan_compilation': 'model-plan-compilation-show',
                     'research_project':'research-project-show','research_project_snapshot':'research-project-show',
                     'research_reproduction_batch':'research-batch-show','research_batch_snapshot':'research-batch-show',
                     'modeling_plan_run': 'model-plan-replay','research_task':'research-task-show',
                     'research_task_run':'research-task-result','research_task_proposal':'research-proposal-show'}
    plan_command = plan_commands.get(root_object['kind'])
    replay=(f'python -m biomodel_agent {plan_command} {root_ref} --store BUNDLE_DIRECTORY' if plan_command else
            f'python -m biomodel_agent case-run --material-ref {case_material} --store BUNDLE_DIRECTORY' if case_material else
            'python -m biomodel_agent replay ROOT_REF --store BUNDLE_DIRECTORY')
    manifest={'schema_version':'1','root_ref':root_ref,'objects_and_blobs':sorted(seen),
              'replay':replay,
              'note':'Install the same project version and requirements.lock; replay records environment differences.'}
    research_root=root_object['kind'] in ('research_task','research_task_run','research_task_proposal',
        'research_project','research_project_snapshot','research_reproduction_batch','research_batch_snapshot')
    if research_root:
        manifest['replay_mode']='read_only_task_inspection'
        manifest['note']='This command reads the saved task/result; a new Agent/scientific execution requires an explicit new task.'
    (destination/'bundle.json').write_text(json.dumps(manifest,indent=2))
    command=(f'python -m biomodel_agent {plan_command} {root_ref} --store .' if plan_command else
             f'python -m biomodel_agent case-run --material-ref {case_material} --store .' if case_material else f'python -m biomodel_agent replay {root_ref} --store .')
    extra='\nThis case command starts a new deterministic diagnostic; it does not resume an Agent or frozen study. Missing inputs remain blocked.\n' if case_material else ''
    if plan_command:
        extra = ('\nPlan inspection does not execute a model. A plan-run replay is an explicitly new scientific execution; '
                 'unknown effects are not retried. Install the same source version: stale compiler fingerprints require explicit recompilation.\n')
    label='Inspect the saved task without an LLM' if research_root else 'Replay without an LLM'
    (destination/'REPLAY.md').write_text(f'# Frozen research bundle\n\nRoot: `{root_ref}`\n\n{label}:\n\n```bash\n{command}\n```\n\nInstall the project and its pinned Python dependencies first. Replaying an agent result uses its `run_ref`; re-running inference is a separate task.\n'+extra)
    return manifest
