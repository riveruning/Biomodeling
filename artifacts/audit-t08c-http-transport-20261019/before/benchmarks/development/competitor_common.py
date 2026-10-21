"""Version and result helpers for third-party integration experiments."""
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'artifacts/competitor-integration-20261017'


def write_json(path, value):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')


def verify_source(system):
    manifest=json.loads((OUT/'source-manifest.json').read_text())
    items=[item for item in manifest['files'] if item['system']==system]
    if not items:
        raise ValueError(f'No pinned source entries for {system}')
    for item in items:
        path=OUT/'vendor'/system/item['path']
        if sha256(path.read_bytes()).hexdigest()!=item['sha256']:
            raise ValueError(f"Vendor source changed: {item['path']}")


def environment():
    import importlib.metadata as metadata
    import sys
    result={'python':sys.version,'packages':{}}
    for package in ['tellurium','antimony','libroadrunner','openai','copasi-basico',
                    'langgraph','langchain-core','langchain-openai','numpy','scipy']:
        try:result['packages'][package]=metadata.version(package)
        except metadata.PackageNotFoundError:result['packages'][package]=None
    return result
