"""Portable verified object closure; the computation replay never calls an LLM."""
from pathlib import Path
import json
from .storage import ArtifactStore


def export_bundle(store,root_ref,destination):
    destination=Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('export destination must be empty')
    store.get(root_ref)  # root must be a verified object, not an arbitrary file
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
        else:
            copied=target.put_blob(store.get_blob(ref))
        if copied!=ref:
            raise ValueError('export changed immutable content')
    visit(root_ref)
    manifest={'schema_version':'1','root_ref':root_ref,'objects_and_blobs':sorted(seen),
              'replay':'python -m biomodel_agent replay ROOT_REF --store BUNDLE_DIRECTORY',
              'note':'Install the same project version and requirements.lock; replay records environment differences.'}
    (destination/'bundle.json').write_text(json.dumps(manifest,indent=2))
    (destination/'REPLAY.md').write_text(f'# Frozen research bundle\n\nRoot: `{root_ref}`\n\nReplay without an LLM:\n\n```bash\npython -m biomodel_agent replay {root_ref} --store .\n```\n\nInstall the project and its pinned Python dependencies first. Replaying an agent result uses its `run_ref`; re-running inference is a separate task.\n')
    return manifest
