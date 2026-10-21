"""Offline tool-level replay of existing, already exposed model proposals.

No new model responses, no response editing and no claim of full-agent success.
Run in a separate process for each candidate with its PYTHONPATH set explicitly.
"""
import argparse
import json
from pathlib import Path
import shutil

from biomodel_agent import agent_tools, application
from biomodel_agent.llm import ChatProvider
from biomodel_agent.storage import ArtifactStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--original-runs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    rows = []
    selected = [('c13_mm_with_inhibitor_ambiguous', 'resume'),
                ('c18_two_pathway_unit_conflict', 'clarified'),
                ('c18_two_pathway_unit_conflict', 'resume')]
    for case, phase in selected:
        stage = 'incomplete' if phase == 'resume' else phase
        original = args.original_runs / case / stage
        payload = json.loads((original / phase / 'agent.json').read_text())
        work = args.output / case / phase
        shutil.copytree(original / 'store', work / 'store')
        store = ArtifactStore(work / 'store')
        for path in sorted((original / phase).glob('response-*.json')):
            response = json.loads(path.read_text())
            raw = response['choices'][0]['message']['content']
            action = ChatProvider('https://unused.invalid/v1', 'offline')._parse(raw)
            if action['action'] != 'submit_draft':
                continue
            result = agent_tools.submit_draft(store, action['args'], payload['source_refs'])
            rows.append({'case': case, 'phase': phase, 'response_file': str(path),
                         'state': result['state'], 'problems': result['problems'],
                         'source_issues': result['source_review']['issues'],
                         'model_ref': result['model_ref']})
    output = {'kind': 'offline_submit_draft_replay', 'model_calls': 0,
              'full_agent_success_measured': False,
              'modules_sha256': application.environment_fingerprint()['modules_sha256'],
              'rows': rows}
    (args.output / 'results.json').write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
