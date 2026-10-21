"""Freeze the diagnostic pilot protocol and create source-only workspaces, no gold."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
from datetime import datetime,timezone
from holdout_sandbox import base,clean_environment

ROOT=Path(__file__).resolve().parents[2]
DATA=Path('/home/riveruning/biomodel_agent_evalset')
OUT=ROOT/'artifacts/holdout-evaluation-20261017'


def main():
    if (OUT/'pilot-protocol.json').exists():raise FileExistsError('protocol already exists')
    audit=json.loads((OUT/'operator-audit.json').read_text())
    manifest=json.loads((OUT/'case_manifest.json').read_text())
    cases=audit['pilot_cases']
    for system in ['biomodel','codex']:
        for case in cases:
            for phase,c in manifest['cases'][case]['phases'].items():
                source=DATA/c['source'];work=OUT/'runs'/system/case/phase
                work.mkdir(parents=True,exist_ok=False)
                for name in ['source.txt','experiment.json','TASK.md']:
                    shutil.copyfile(source.parent/name,work/name)
                assert set(p.name for p in work.iterdir())=={'source.txt','experiment.json','TASK.md'}
    probe=OUT/'isolation-probe';probe.mkdir()
    secret=OUT/'evaluator-canary.txt';secret.write_text('EVALUATOR_ONLY_CANARY_NOT_FOR_MODELS')
    code='''from pathlib import Path
import json
paths=PATHS
print(json.dumps({p:Path(p).exists() for p in paths}))
'''.replace('PATHS',repr([str(DATA),str(ROOT/'src'),str(OUT/'operator-audit.json'),str(secret),'/work']))
    result=subprocess.run(base(probe,network=False)+['--','/usr/bin/python3','-c',code],
                          env=clean_environment(),capture_output=True,text=True,check=True)
    visibility=json.loads(result.stdout)
    assert visibility['/work'] and not any(v for k,v in visibility.items() if k!='/work')
    (OUT/'isolation-probe.json').write_text(json.dumps({'status':'pass','visible_paths':visibility,
            'mechanism':'Bubblewrap mount namespace, only per-stage workspace plus runtime allowlist'},indent=2))
    protocol={'created_utc':datetime.now(timezone.utc).isoformat(),'type':'exploratory_synthetic_pilot',
      'human_domain_review':'pending','cases':cases,'selection':audit['pilot_selection_rule'],
      'fresh_phases_per_system':9,'systems':['biomodel:qwen3.8-chat','codex:gpt-6.1-sol'],
      'candidate_freeze':'candidate-freeze.json','same_backbone_causal_comparison':False,
      'metrics':['complete correct execution (1 case)','specific gap identification (4 cases)',
                 'requested clarification (4 cases)','unsupported executable delivery (4 cases)',
                 'fresh clarified correct execution (4 cases)','false blocking (5 sufficient phases)',
                 'actual same-task product resume (conditional on awaiting_input; original total budget)'],
      'numeric_rule':{'sampling':'exact frozen grid; no interpolation','tolerance':.001,
                      'scale':'max(1,max(abs(reference))) per required species'},
      'code_execution':'Codex delivery independently reviewed then replayed with no network; no source correction',
      'clarification_release':'Pre-written reply copied only after original product run has paused; fresh clarified tasks scored separately.',
      'budgets':{'biomodel':{'max_calls_total':8,'max_repairs':2,'max_retries':2,'max_tokens':4000,'timeout_s':90,'json_mode':True,'pacing_seconds':5},
                 'codex':{'model':'gpt-6.1-sol','reasoning':'medium','wall_limit_s':240,'tool_and_token_budget':'native runtime; not equated to product call budget'}},
      'phase_order':'case ID order; incomplete then fresh clarified; product resume then next case',
      'failure_policy':'preserve every first result; stop on authentication failure or persistent rate limiting; no selective reruns',
      'isolation':{'files_visible_to_task':['source.txt','experiment.json','TASK.md'],'allowed_json_visible':False,'runtime':'Bubblewrap mount namespace; parent gold and project trees not mounted'},
      'limitations':['5 selected synthetic cases are not representative of real papers','six audited boundary cases excluded before model outputs','not a formal benchmark or superiority claim','models and inference budgets differ'],
      'adapter_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in
        [Path(__file__),ROOT/'benchmarks/development/holdout_sandbox.py',ROOT/'benchmarks/development/run_holdout_product.py']}}
    (OUT/'pilot-protocol.json').write_text(json.dumps(protocol,indent=2))
    print(json.dumps({'pilot_cases':cases,'prepared_fresh_tasks':18,'isolation':'pass','formal':False}))


if __name__=='__main__':main()
