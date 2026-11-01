"""Build the runtime-only distribution; development materials never enter it."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

ROOT=Path(__file__).resolve().parents[1]
EXCLUDED_DEPENDENCIES={'pytest','pluggy','iniconfig','pygments','colorama'}
# This development evaluator is colocated with the source, but no runtime
# module imports it. The existing runtime distribution excludes it as well.
EXCLUDED_MODULES={'literature_scoring.py'}


def runtime_lock(text):
    lines=['# Python 3.12 runtime dependencies. Development/test packages are excluded.']
    for line in text.splitlines():
        if '==' in line and line.split('==',1)[0].lower() not in EXCLUDED_DEPENDENCIES:lines.append(line)
    return '\n'.join(lines)+'\n'


def build(destination):
    destination=Path(destination).absolute()
    if destination.exists():raise ValueError('runtime destination must be a new path')
    destination.mkdir(parents=True)
    package=ROOT/'src/biomodel_agent';target=destination/'src/biomodel_agent'
    def copy(source,relative):
        path=destination/relative;path.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,path)
    for file in package.rglob('*.py'):
        if '__pycache__' not in file.parts and file.name not in EXCLUDED_MODULES:
            copy(file,Path('src/biomodel_agent')/file.relative_to(package))
    for file in (package/'workbench_assets').iterdir():
        if file.is_file():copy(file,Path('src/biomodel_agent/workbench_assets')/file.name)
    for file in (package/'workspace_assets/templates').glob('*.json'):
        copy(file,Path('src/biomodel_agent/workspace_assets/templates')/file.name)
    for file in (ROOT/'scripts/runtime/assets').rglob('*.md'):
        copy(file,Path('src/biomodel_agent/workspace_assets')/file.relative_to(ROOT/'scripts/runtime/assets'))
    lock=runtime_lock((ROOT/'requirements.lock').read_text())
    (destination/'requirements.lock').write_text(lock)
    (target/'workspace_assets/requirements.lock').write_text(lock)
    (destination/'requirements-pdf.lock').write_text('-r requirements.lock\npymupdf==1.28.2\n')
    project=(ROOT/'pyproject.toml').read_text().replace('test = ["pytest"]\n','').replace('"workspace_assets/examples/*", ','')
    (destination/'pyproject.toml').write_text(project)
    for name in ('setup.ps1','start.ps1','setup.cmd','start.cmd','setup.sh','start.sh'):
        copy(ROOT/'scripts/runtime'/name,name)
    copy(ROOT/'scripts/runtime/README.md','README.md')
    (destination/'.gitignore').write_text('.venv/\nruns/\n__pycache__/\n*.py[cod]\n*.egg-info/\nbuild/\ndist/\n.env\n.env.*\n')
    paths=[p for p in destination.rglob('*') if p.is_file()]
    forbidden={'tests','artifacts','runs','tasks','discussion-notes','benchmarks','examples','.git','.venv','__pycache__'}
    for file in paths:
        if any(part in forbidden for part in file.relative_to(destination).parts):raise ValueError('development material in runtime: '+str(file))
        if file.name in EXCLUDED_MODULES:raise ValueError('development evaluator in runtime')
    if len(list((target/'workspace_assets/templates').glob('*.json')))!=6:raise ValueError('runtime needs six structural templates')
    archive=destination.with_suffix('.zip')
    if archive.exists():raise ValueError('runtime ZIP already exists')
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as output:
        for file in sorted(paths):output.write(file,Path(destination.name)/file.relative_to(destination))
    manifest={str(p.relative_to(destination)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    return dict(directory=str(destination),zip=str(archive),files=len(paths),sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),file_sha256=manifest)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--manifest')
    args=parser.parse_args();result=build(args.output)
    if args.manifest:Path(args.manifest).write_text(json.dumps(result,indent=2))
    print(json.dumps({key:value for key,value in result.items() if key!='file_sha256'}))
