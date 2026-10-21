"""Explicit synthetic result records; no agents, providers or solvers execute here."""
from contextlib import redirect_stdout
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
RUN = OUT/'final-controls'
spec = importlib.util.spec_from_file_location('t08_evaluator', ROOT/'benchmarks/development/agent_evaluation.py')
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)


def main():
    RUN.mkdir(exist_ok=False)
    m = ev._read(OUT/'frozen-plan/manifest.json')
    nominal = []
    for t in ev.schedule(m):
        gap = {'translation':'P/initial', 'logistic':'rule:X/expression'}.get(t['case_id'])
        blocked = gap is not None and t['phase'] == 'initial'
        case = next(c for c in m['cases'] if c['id'] == t['case_id'])
        phase = next(p for p in case['phases'] if p['id'] == t['phase'])
        nominal.append({'trial_id': t['id'], 'manifest_sha256':ev.digest(m),
            'toolchain_sha256': m['toolchain_sha256'], 'model_config':deepcopy(m['model_config']),
            'evidence_kind':'scripted_control', 'status':'finished',
            'continuation':'resumed' if t['parent_trial_id'] else 'none', 'parent_trial_id':t['parent_trial_id'],
            'observation':{'state':'awaiting_input' if blocked else 'completed',
                'executed':not blocked, 'claimed_complete':not blocked,
                'identified_gaps':[gap] if blocked else [], 'question_targets':[gap] if blocked else [],
                'question_texts':['Please provide or resolve '+gap+'.'] if blocked else []},
            'assessment':{'authority':'scripted_fixture', 'reviewer':'Explicit synthetic fixture author; not an expert review',
                'evidence_refs':[phase['files'][0]['sha256']], 'behavior_labels_reviewed':True,
                'numeric':'unknown' if blocked else 'pass', 'source_support':'unknown' if blocked else 'pass'},
            'telemetry':{'model_calls':1, 'tool_calls':2, 'max_request_chars':1000, 'max_response_tokens':100,
                'elapsed_seconds':1.0, 'human_interventions':1 if t['parent_trial_id'] else 0,
                'human_seconds':5.0 if t['parent_trial_id'] else 0.0, 'input_tokens':None, 'output_tokens':None}})
    adverse = deepcopy(nominal)
    trials = {t['id']:t for t in ev.schedule(m)}
    for r in adverse:
        t = trials[r['trial_id']]; repeat, case, phase = t['repeat'],t['case_id'],t['phase']
        if repeat == 0 and case == 'decay':
            r['status'] = 'provider_error'
            r['observation'].update(state='failed',executed=False,claimed_complete=False)
        elif repeat == 0 and case == 'translation' and phase == 'initial':
            r['observation'].update(state='completed',executed=True,claimed_complete=True)
        elif repeat == 0 and case == 'translation':
            r.update(continuation='fresh',parent_trial_id=None)
        elif repeat == 0 and case == 'logistic' and phase == 'initial':
            r['observation'].update(identified_gaps=['unrelated'],question_targets=['unrelated'],question_texts=['What is the unrelated quantity?'])
        elif repeat == 0 and case == 'logistic':
            r['assessment']['source_support'] = 'unknown'
        elif repeat == 1 and case == 'decay':
            r['observation'].update(state='awaiting_input',executed=False,claimed_complete=False,
                question_texts=['Please provide the already-reported initial A.'])
        elif repeat == 1 and case == 'translation' and phase == 'clarified':
            r['telemetry']['model_calls'] = 8
        elif repeat == 1 and case == 'logistic' and phase == 'clarified':
            r['model_config']['model'] = 'deliberately-different-control'

    observations = {}
    for name, records in [('nominal',nominal),('adverse',adverse),('missing',[])]:
        input_path = RUN/f'{name}-records.json'; ev._write(input_path,records)
        with (RUN/f'{name}-score.log').open('w') as log, redirect_stdout(log):
            exit_code = ev.main(['score','--manifest',str(OUT/'frozen-plan/manifest.json'),
                                  '--records',str(input_path),'--output',str(RUN/f'{name}-results')])
        expected_exit = 1 if name == 'adverse' else 0
        assert exit_code == expected_exit
        result = ev._read(RUN/f'{name}-results/results.json')
        assert len(result['rows']) == 30 and result['semantic_support'] == 'unknown'
        assert len({ev.digest(v) for v in result['summary'].values()}) == 1
        for summary in result['summary'].values():
            assert summary['planned_trials'] == 10
            assert summary['correct_completion']['denominator'] == 6
            assert summary['input_tokens']['total_if_complete'] is None
            if name == 'nominal':
                assert summary['correct_completion']['numerator'] == 6
                assert summary['clarification_recovery']['numerator'] == 4
            elif name == 'adverse':
                assert summary['correct_completion']['numerator'] == 2
                assert summary['within_budget_completion']['numerator'] == 1
                assert summary['unsupported_execution']['numerator'] == 1
                assert summary['unsupported_completion']['numerator'] == 1
                assert summary['overblocking']['numerator'] == 1
                assert summary['invalid_trials'] == summary['budget_exceeded_trials'] == 1
                assert summary['failure_causes']['provider_error'] == 1
            else:
                assert summary['missing_trials'] == 10
                assert summary['correct_completion']['numerator'] == 0
        observations[name] = {'exit_code':exit_code,'summary':result['summary'],
                              'label':'Fixture observations and counters only; no real model, tool execution, human work or scientific result.'}
    ev._write(RUN/'controls.json', {'status':'passed','control_sets':observations,
        'actual_model_calls':0,'actual_scientific_tool_calls':0,'actual_human_review_decisions':0})
    print(json.dumps({'status':'passed','control_sets':list(observations),'planned_rows_per_set':30,
                      'actual_model_calls':0,'actual_scientific_tool_calls':0}))


if __name__ == '__main__':
    main()
