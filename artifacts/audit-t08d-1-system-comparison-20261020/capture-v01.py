import json
from pathlib import Path
import shutil

OUT = Path(__file__).resolve().parent
rows = []
for stage, folder in [('original-full', '/tmp/t08d1-full'),
                      ('original-isolated', '/tmp/t08d1-v01-original-isolated'),
                      ('deterministic', '/tmp/t08d1-v01-deterministic')]:
    for i, workflow in enumerate(('fixed_workflow', 'generic_tool_agent', 'domain_agent')):
        root = Path(folder)/f'test_hard_deadline_also_bounds{i}'
        target = OUT/'v01-evidence'/stage/workflow
        shutil.copytree(root, target)
        result = json.loads((target/'scientific-timeout/result.json').read_text())
        rows.append({'stage': stage, 'workflow': workflow, 'status': result['record']['status'],
                     'executed': result['record']['observation']['executed'],
                     'tool_calls': result['record']['telemetry']['tool_calls'],
                     'stop_reason': result['audit']['stop_reason'],
                     'clock_is_fault_injected': stage == 'deterministic'})
(OUT/'v01-evidence.json').write_text(json.dumps({'rows': rows,
    'scope_amendment': 'Only the old scientific-timeout test was modified. Production deadline and adapter sources unchanged.',
    'interpretation': 'The original full domain probe expired before admission; isolated old probes passed. New control waits for confirmed admission then injects elapsed-budget exhaustion in adapter-local clock only, retaining real-time termination and execution/count assertions. Its logical elapsed telemetry is not a performance measurement.'}, indent=2)+'\n')
print(json.dumps(rows))
