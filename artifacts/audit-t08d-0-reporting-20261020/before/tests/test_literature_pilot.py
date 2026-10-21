"""Scientific scope and provenance boundaries of the real-paper pilot."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'benchmarks/development' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pilot = load('evaluate_literature')


def test_frozen_sources_gold_and_images_match_manifest():
    pilot.verify_inputs(pilot.read_json(pilot.MANIFEST))


def test_changed_input_rejected_before_model_calls():
    manifest = deepcopy(pilot.read_json(pilot.MANIFEST))
    manifest['files'][0]['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='hash mismatch'):
        pilot.verify_inputs(manifest)


def test_raw_jats_retains_image_equation_gaps():
    source = (ROOT / 'benchmarks/inputs/literature/gonze2013/raw-text.txt').read_text()
    assert '[IMAGE_FORMULA: info:doi/10.1371/journal.pone.0069573.e004]' in source
    assert 'k1*Ki^n' not in source
    gold = pilot.read_json(ROOT / 'benchmarks/inputs/literature/gonze2013/gold-raw.json')
    assert all(item['expected'] is None for item in gold['items'])
    assert gold['review']['human_reviewer'] is None


def test_assisted_input_never_supplies_original_initial_conditions():
    manifest = pilot.read_json(pilot.MANIFEST)
    config = manifest['tracks']['assisted']
    gold = pilot.read_json(ROOT / config['gold'])
    initial = [i for i in gold['items'] if i['target'].endswith('/initial')]
    assert len(initial) == 3
    assert all(i['expected'] is None for i in initial)
    assert manifest['numeric_control']['initial_origin'] == 'analyst_chosen_exploratory_not_paper'
    score = pilot.calibration(gold, {'assisted': (ROOT / config['source']).read_text()})
    assert score['positive']['counts']['fully_supported'] == 11
    assert score['positive']['counts']['correct_abstentions'] == 3
    assert score['deliberately_corrupted']['counts']['fully_supported'] == 9


def test_normalizer_keeps_rejected_attempt_and_filters_wrong_page_and_source():
    payload = {'state': 'incomplete', 'events': [{'type': 'llm_call', 'response': {
        'action': 'submit_draft', 'args': {
            'draft': {'parameters': [{'id': 'k1', 'value': 7.}]},
            'evidence': [
                {'target': 'k1/value', 'source_ref': 'wrong', 'page': 1, 'quote': 'k1=7'},
                {'target': 'k1/value', 'source_ref': 'ref', 'page': 2, 'quote': 'k1=7'},
            ]}}}]}
    prediction = pilot.normalize_attempt(payload, 'ref', 'paper', 'k1=1')
    assert prediction['facts']['k1/value'] == {'value': 7., 'evidence': []}
    assert prediction['proposal_status'] == 'last_attempt_not_necessarily_accepted'


def test_live_agent_receives_no_gold_reference_or_exploratory_initials(tmp_path, monkeypatch):
    """A request for missing data remains a stop, even with evaluator gold available."""
    from biomodel_agent.storage import ArtifactStore
    manifest = pilot.read_json(pilot.MANIFEST)
    captured = {}

    def run(store, provider, source_refs, experiment, **kwargs):
        captured.update(kwargs)
        text = store.get(source_refs[0])['payload']['pages'][0]['text']
        assert 'Initial X, Y, Z values are not specified' in text
        assert 'analyst_chosen' not in text
        assert not experiment.initial_overrides
        return store.put('agent_run', {'state': 'awaiting_input', 'run_ref': None,
            'call_count': 1, 'repair_count': 0, 'events': [
                {'type': 'awaiting_input', 'question': 'Please supply initial conditions'}]})

    monkeypatch.setattr(pilot.agent, 'run_agent', run)
    monkeypatch.setattr(pilot, 'ChatProvider', lambda *args, **kwargs: object())
    monkeypatch.setattr(pilot.reporting, 'render_report', lambda *args: 'test report')
    args = SimpleNamespace(base_url='https://example.org/v1', live_model='test', api_key_env='TEST_KEY')
    result = pilot.live_track('assisted', manifest['tracks']['assisted'], tmp_path, args)
    assert captured == {'max_calls': 8, 'max_repairs': 2}
    assert result['status'] == 'awaiting_input'
    assert result['score']['counts']['fully_supported'] == 0
    assert result['score']['counts']['correct_abstentions'] == 3
    assert result['run_ref'] is None


def test_real_agent_tools_stop_assisted_model_with_missing_initials(tmp_path):
    """Scripted integration fixture, not an LLM or extraction ability score."""
    from biomodel_agent import agent, evidence
    from biomodel_agent.domain.contracts import ExperimentSpec
    from biomodel_agent.storage import ArtifactStore

    manifest = pilot.read_json(pilot.MANIFEST)
    config = manifest['tracks']['assisted']
    gold = pilot.read_json(ROOT / config['gold'])
    store = ArtifactStore(tmp_path / 'store')
    source_ref = evidence.ingest_source(store, ROOT / config['source'])
    vals = {i['target']: i['expected'] for i in gold['items']}
    draft = {'id': 'scripted_control',
             'states': [{'id': k, 'initial': None, 'quantity_kind': 'variable'}
                        for k in ('X', 'Y', 'Z')],
             'parameters': [{'id': k, 'value': vals[k + '/value']}
                            for k in ('k1', 'k2', 'k3', 'k4', 'k5', 'k6', 'Ki', 'n')],
             'rate_rules': [{'state': k, 'expression': vals[f'rule:{k}/expression']}
                            for k in ('X', 'Y', 'Z')], 'time_unit': 'unknown'}
    citations = [{'target': i['target'], 'source_ref': source_ref, 'page': 1,
                  'quote': i['support'][0]['quote']}
                 for i in gold['items'] if i['expected'] is not None]

    class ScriptedProvider:
        last_usage = {}

        def respond(self, messages):
            return {'action': 'submit_draft', 'args': {'draft': draft, 'evidence': citations}}

    ref = agent.run_agent(store, ScriptedProvider(), [source_ref], ExperimentSpec((0., 1.)))
    result = store.get(ref)['payload']
    assert result['state'] == 'awaiting_input'
    assert result['model_ref'] is None and result['run_ref'] is None
    prediction = pilot.normalize_attempt(result, source_ref, 'assisted',
                                          (ROOT / config['source']).read_text())
    score = pilot.score_extraction(gold, prediction, {'assisted':
                                   (ROOT / config['source']).read_text()})
    assert score['counts']['fully_supported'] == 11
    assert score['counts']['correct_abstentions'] == 3
