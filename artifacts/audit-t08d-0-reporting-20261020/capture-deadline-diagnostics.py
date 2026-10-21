import json
from pathlib import Path
import shutil

OUT = Path(__file__).resolve().parent
rows = []
for index, workflow in enumerate(('fixed_workflow', 'generic_tool_agent', 'domain_agent')):
    for run, parent in [('full', Path('/tmp/t08d0-full')),
                        ('isolated', Path('/tmp/t08d0-deadline-diagnostic'))]:
        source = parent/f'test_hard_deadline_also_bounds{index}'/'scientific-timeout'
        target = OUT/'deadline-diagnostics'/run/workflow
        shutil.copytree(source, target)
        result = json.loads((target/'result.json').read_text())
        record = result['record']
        rows.append({'run': run, 'workflow': workflow, 'status': record['status'],
                     'executed': record['observation']['executed'],
                     'tool_calls': record['telemetry']['tool_calls'],
                     'stop_reason': result['audit']['stop_reason']})
(OUT/'deadline-diagnostics.json').write_text(json.dumps({
    'rows': rows,
    'interpretation': 'All records timed out under the unchanged 0.6-second total deadline. In the first full suite generic/domain did not reach the scientific call; in isolation all three reached it. Scheduling sensitivity is consistent with these observations, not independently proven as the root cause. No timeout or old test was changed.',
}, indent=2)+'\n')
print(json.dumps(rows))
