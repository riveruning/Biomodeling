"""Explicit optional download of checksum-pinned local English OCR support.

No project documents, credentials or telemetry are transmitted. The product's
OCR path itself never downloads data. This helper never overwrites mismatches.
"""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

COMMIT='65727574dfcd264acbb0c3e07860e4e9e9b22185'
FILES={'eng.traineddata':('7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2',4_113_088),
       'LICENSE':('cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30',11_358)}


def prepare(output):
    output.mkdir(parents=True,exist_ok=True);records=[]
    for name,(expected,size) in FILES.items():
        path=output/name;url=f'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{COMMIT}/{name}'
        if path.exists():data=path.read_bytes()
        else:
            with urllib.request.urlopen(url,timeout=40) as response:data=response.read(size+1)
        if len(data)!=size or hashlib.sha256(data).hexdigest()!=expected:raise ValueError('OCR support checksum mismatch; existing data is not overwritten')
        if not path.exists():
            with path.open('xb') as handle:handle.write(data)
        records.append(dict(name=name,sha256=expected,bytes=size,url=url))
    manifest=dict(version=1,repository='tesseract-ocr/tessdata_fast',commit=COMMIT,license='Apache-2.0',files=records)
    target=output/'manifest.json'
    if not target.exists():target.write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    manifest=prepare(args.output);print(json.dumps(dict(output=str(args.output),commit=COMMIT,verified_files=len(manifest['files']))))
