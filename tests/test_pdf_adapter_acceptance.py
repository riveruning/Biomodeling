"""T06 acceptance: real PDF bytes and mature local parser, no cloud service."""
from copy import deepcopy
import importlib.util
import json
import math
import multiprocessing as mp
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from biomodel_agent import evidence, cli, bundles
from biomodel_agent.storage import ArtifactError, ArtifactStore
from test_storage_evidence import _write_pdf

pytestmark = pytest.mark.skipif(importlib.util.find_spec('pymupdf') is None,
                                reason='optional PDF backend: install requirements-pdf.lock')


def ingested(tmp_path, texts=None, **options):
    path = tmp_path/'paper.pdf'
    raw = _write_pdf(path, texts or ['Initial x = 1.', 'Parameter k = 0.4.'])
    store = ArtifactStore(tmp_path/'store')
    ref = evidence.ingest_source(store, path, pdf_parser='pymupdf', **options)
    return store, ref, raw


def test_mature_backend_preserves_pages_bytes_and_immutable_engine_output(tmp_path):
    store, ref, raw = ingested(tmp_path)
    source = store.get(ref)['payload']
    assert source['parser'] == 'pymupdf' and source['parser_version'] != 'unknown'
    assert store.get_blob(source['blob_ref']) == raw == (tmp_path/'paper.pdf').read_bytes()
    parsed = store.get(source['parser_output_ref'])
    assert parsed['kind'] == 'pdf_extraction' and source['blob_ref'] in parsed['refs']
    assert parsed['payload']['adapter_version'] == 1
    assert source['pages'] == parsed['payload']['pages']
    for number, page in enumerate(source['pages'], 1):
        assert page['number'] == number and page['status'] == 'text_extracted'
        assert page['width'] > 0 and page['height'] > 0
        for block in page['blocks']:
            assert page['text'][block['start']:block['end']] == block['text']
            assert len(block['bbox']) == 4 and block['text_layer'] == 'native_pdf_text'


def test_claim_locator_reaches_original_pdf_and_export_without_changing_offsets(tmp_path):
    store, ref, raw = ingested(tmp_path)
    source = store.get(ref)['payload']
    text = source['pages'][1]['text']
    start = text.index('Parameter')
    claim = evidence.add_claim(store, ref, 2, start, start+len('Parameter k = 0.4.'), 'k/value')
    payload = store.get(claim)['payload']
    assert payload['locator']['pdf_blob_ref'] == source['blob_ref']
    assert payload['locator']['parser_output_ref'] == source['parser_output_ref']
    assert payload['locator']['page'] == 2 and payload['locator']['blocks']
    assert payload['quote'] == 'Parameter k = 0.4.'
    bundles.export_bundle(store, claim, tmp_path/'bundle')
    exported = ArtifactStore(tmp_path/'bundle')
    assert exported.get(claim) == store.get(claim)
    assert exported.get_blob(source['blob_ref']) == raw


def test_textless_page_is_preserved_without_made_up_ocr(tmp_path):
    store, ref, _ = ingested(tmp_path, ['Page one.', None])
    source = store.get(ref)['payload']
    page = source['pages'][1]
    assert len(source['pages']) == 2 and page['text'] == '' and page['blocks'] == []
    assert page['status'] == 'textless'
    assert any('2' in w and 'text' in w.lower() for w in source['warnings'])
    assert source['capabilities']['ocr'] == 'not_run'
    assert source['capabilities']['formula_recognition'] == 'not_run'


@pytest.mark.parametrize('limits', [{'max_pages': 1}, {'max_chars': 3}, {'max_blocks': 1}])
def test_output_limits_refuse_incomplete_sources_but_preserve_raw_pdf(tmp_path, limits):
    path = tmp_path/'paper.pdf'
    raw = _write_pdf(path, ['Page one.', 'Page two.'])
    store = ArtifactStore(tmp_path/'store')
    with pytest.raises(ValueError, match='limit|budget'):
        evidence.ingest_source(store, path, pdf_parser='pymupdf', pdf_limits=limits)
    assert not store.list('source') and not store.list('pdf_extraction')
    import hashlib
    assert store.get_blob(hashlib.sha256(raw).hexdigest()) == raw


@pytest.mark.parametrize('limits', [{'max_pages': True}, {'max_chars': 0}, {'max_cells': -1}, {'other': 1}])
def test_invalid_limits_are_not_coerced(tmp_path, limits):
    with pytest.raises(ValueError, match='limit'):
        ingested(tmp_path, pdf_limits=limits)


def test_unrecognized_backend_does_not_silently_fall_back(tmp_path):
    path = tmp_path/'paper.pdf'
    _write_pdf(path, ['Native text.'])
    store = ArtifactStore(tmp_path/'store')
    with pytest.raises(ValueError, match='parser'):
        evidence.ingest_source(store, path, pdf_parser='cloud_guess')
    assert not store.list('source')


def test_invalid_pdf_does_not_become_a_successful_source(tmp_path):
    path = tmp_path/'bad.pdf'; path.write_bytes(b'%PDF-1.7\nnot a document')
    store = ArtifactStore(tmp_path/'store')
    with pytest.raises(ValueError, match='PDF|pdf'):
        evidence.ingest_source(store, path, pdf_parser='pymupdf')
    assert not store.list('source') and not store.list('pdf_extraction')


def test_offline_cli_ingests_without_provider_settings(tmp_path, capsys):
    path = tmp_path/'paper.pdf'; _write_pdf(path, ['Initial x = 1.'])
    code = cli.main(['source-ingest', str(path), '--pdf-parser', 'pymupdf', '--store', str(tmp_path/'store')])
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result['parser'] == 'pymupdf' and result['ref']
    assert ArtifactStore(tmp_path/'store').get(result['ref'])['kind'] == 'source'


def rich_pdf(path, *, image=False, table=False, rotate=0):
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page(width=450, height=400)
    for y, text in [(40, 'Initial x = 1.'), (65, 'Parameter k = 0.4.'), (90, 'Equation dx/dt = -k*x.')]:
        page.insert_text((25, y), text)
    if image:
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), 0)
        pixmap.clear_with(128)
        page.insert_image(pymupdf.Rect(280, 30, 370, 120), stream=pixmap.tobytes('png'))
    if table:
        xs, ys = [30, 150, 270], [160, 190, 220, 250]
        for x in xs:
            page.draw_line((x, ys[0]), (x, ys[-1]))
        for y in ys:
            page.draw_line((xs[0], y), (xs[-1], y))
        for row, values in enumerate([['Symbol', 'Value'], ['k', '0.4'], ['x', '1']]):
            for column, value in enumerate(values):
                page.insert_text((xs[column]+8, ys[row]+20), value)
    page.set_rotation(rotate)
    doc.save(path)
    doc.close()
    return path.read_bytes()


def test_native_table_results_keep_cells_separate_from_citable_page_offsets(tmp_path):
    path = tmp_path/'table.pdf'; rich_pdf(path, table=True)
    store = ArtifactStore(tmp_path/'store')
    source = store.get(evidence.ingest_source(store, path, pdf_parser='pymupdf'))['payload']
    page = source['pages'][0]
    assert page['table_status'] == 'completed' and len(page['tables']) == 1
    table = page['tables'][0]
    assert table['rows'] == [['Symbol', 'Value'], ['k', '0.4'], ['x', '1']]
    assert len(table['cells']) == 6 and len(table['bbox']) == 4
    assert table['citation_role'] == 'auxiliary_not_page_text' and table['status'] == 'extracted_unverified'
    assert all(page['text'][b['start']:b['end']] == b['text'] for b in page['blocks'])


def test_mixed_images_are_disclosed_without_formula_transcription(tmp_path):
    path = tmp_path/'mixed.pdf'; rich_pdf(path, image=True)
    store = ArtifactStore(tmp_path/'store')
    source = store.get(evidence.ingest_source(store, path, pdf_parser='pymupdf'))['payload']
    assert source['pages'][0]['status'] == 'text_with_images'
    assert len(source['pages'][0]['images']) == 1
    assert any('image' in text for text in source['warnings'])
    assert source['capabilities']['formula_recognition'] == 'not_run'
    assert 'not transcribed' in ' '.join(source['warnings'])


def test_actual_image_only_scan_preserves_empty_text_and_image_location(tmp_path):
    import pymupdf
    path = tmp_path/'scan.pdf'
    doc = pymupdf.open(); page = doc.new_page()
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), 0); pixmap.clear_with(128)
    page.insert_image(page.rect, stream=pixmap.tobytes('png')); doc.save(path); doc.close()
    store = ArtifactStore(tmp_path/'store')
    source = store.get(evidence.ingest_source(store, path, pdf_parser='pymupdf'))['payload']
    assert source['pages'][0]['status'] == 'textless'
    assert source['pages'][0]['text'] == '' and source['pages'][0]['images']
    assert source['capabilities']['ocr'] == 'not_run'


@pytest.mark.parametrize('defect', ['bbox', 'parser_output_ref', 'source_span', 'missing_locator'])
def test_claim_geometry_cannot_be_forged_or_detached_from_parser_output(tmp_path, defect):
    store, source, _ = ingested(tmp_path)
    text = store.get(source)['payload']['pages'][0]['text']
    claim = evidence.add_claim(store, source, 1, 0, len(text), 'x/initial')
    payload = deepcopy(store.get(claim)['payload'])
    if defect == 'bbox':
        payload['locator']['blocks'][0]['bbox'][0] += 100
    elif defect == 'parser_output_ref':
        payload['locator']['parser_output_ref'] = source
    elif defect == 'source_span':
        payload['locator']['end'] -= 1
    else:
        payload.pop('locator')
    forged = store.put('claim', payload, refs=(source,))
    assert evidence.validate_links(store, {'x/initial': (forged,)}, ['x/initial'])['status'] == 'incomplete'
    assert evidence.validate_links(store, {'x/initial': (claim,)}, ['x/initial'])['coverage'] == 1.


def test_source_text_cannot_be_detached_from_recorded_parser_output(tmp_path):
    store, ref, _ = ingested(tmp_path)
    env = store.get(ref)
    payload = deepcopy(env['payload']); payload['pages'][0]['text'] = 'Initial x = 999.'
    forged = store.put('source', payload, refs=env['refs'])
    with pytest.raises(ValueError, match='parser output'):
        evidence.add_claim(store, forged, 1, 0, len(payload['pages'][0]['text']), 'x/initial')


def test_rotated_page_keeps_engine_coordinate_system_and_rotation(tmp_path):
    path = tmp_path/'rotate.pdf'; rich_pdf(path, rotate=90)
    store = ArtifactStore(tmp_path/'store')
    ref = evidence.ingest_source(store, path, pdf_parser='pymupdf')
    page = store.get(ref)['payload']['pages'][0]
    assert page['rotation'] == 90 and page['width'] == 400 and page['height'] == 450
    claim = store.get(evidence.add_claim(store, ref, 1, 0, 14, 'initial'))['payload']
    assert claim['locator']['coordinate_space'] == 'pymupdf_unrotated_page_points'


def _hang(payload, out):
    while True:
        time.sleep(.1)


def _die(payload, out):
    os._exit(7)


@pytest.mark.parametrize('worker', [_hang, _die])
def test_parser_timeout_and_native_crash_leave_no_successful_source_or_child(tmp_path, monkeypatch, worker):
    from biomodel_agent import pdf_adapter
    path = tmp_path/'paper.pdf'; _write_pdf(path, ['Native text.'])
    store = ArtifactStore(tmp_path/'store')
    children = {p.pid for p in mp.active_children()}
    monkeypatch.setattr(pdf_adapter, '_worker', worker)
    began = time.monotonic()
    with pytest.raises(ValueError, match='timeout|without a complete result'):
        evidence.ingest_source(store, path, pdf_parser='pymupdf', pdf_timeout_s=.5 if worker is _hang else 5)
    assert time.monotonic()-began < 8
    assert {p.pid for p in mp.active_children()} <= children
    assert not store.list('source') and not store.list('pdf_extraction')
    assert len(store.list('pdf_parse_failure')) == 1


@pytest.mark.parametrize('timeout', [True, 0, -1, float('nan'), float('inf'), 301])
def test_invalid_timeouts_are_not_coerced(tmp_path, timeout):
    with pytest.raises(ValueError, match='timeout'):
        ingested(tmp_path, pdf_timeout_s=timeout)


def test_encrypted_pdf_is_not_silently_read_as_empty(tmp_path):
    import pymupdf
    path = tmp_path/'encrypted.pdf'
    doc = pymupdf.open(); doc.new_page()
    doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='reader'); doc.close()
    store = ArtifactStore(tmp_path/'store')
    with pytest.raises(ValueError, match='encrypted PDF'):
        evidence.ingest_source(store, path, pdf_parser='pymupdf')
    assert not store.list('source')


def test_offline_cli_subprocess_stdout_is_a_single_json_record(tmp_path):
    path = tmp_path/'table.pdf'; rich_pdf(path, table=True)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    completed = subprocess.run([sys.executable, '-m', 'biomodel_agent', 'source-ingest', str(path),
                                '--pdf-parser', 'pymupdf', '--store', str(tmp_path/'store')],
                               capture_output=True, text=True, env=env, timeout=15)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)['parser'] == 'pymupdf'


def test_pdf_source_flows_through_retrieval_binding_solver_export_and_recovery(tmp_path, monkeypatch):
    from biomodel_agent import agent, agent_tools, application, retrieval
    from biomodel_agent.domain.contracts import ModelDraft, State, Parameter, RateRule, ExperimentSpec
    from biomodel_agent.domain.serialization import to_dict
    from test_invocation_acceptance import Scripted, act
    from test_recovery_acceptance import post_result_crash
    path = tmp_path/'model.pdf'; raw = rich_pdf(path)
    store = ArtifactStore(tmp_path/'store')
    source = evidence.ingest_source(store, path, pdf_parser='pymupdf')
    draft = ModelDraft('decay', (State('x', 1.),), (Parameter('k', .4),), rate_rules=(RateRule('x', '-k*x'),))
    proposal = {'draft': to_dict(draft), 'evidence': [
        {'target': target, 'source_ref': source, 'page': 1, 'quote': quote}
        for target, quote in [('x/initial', 'Initial x = 1.'), ('k/value', 'Parameter k = 0.4.'),
                              ('rule:x/expression', 'Equation dx/dt = -k*x.')]]}
    # Existing scoped retrieval uses the same extracted character coordinates.
    hits = retrieval.search_sources(store, [source], 'Parameter k')['results']
    assert hits and all(h['source_ref'] == source for h in hits)
    _, execution = post_result_crash(store, source, proposal, monkeypatch)
    runs = store.list('run')
    monkeypatch.setattr(application, 'run_reproduction', lambda *a, **k: pytest.fail('simulation repeated'))
    result_ref = agent.recover_agent(store, Scripted([act('finish')]), execution)
    result = store.get(result_ref)['payload']
    assert result['state'] == 'completed' and store.list('run') == runs
    facts = store.get(result['run_ref'])['payload']
    assert facts['simulation']['values']['x'][-1] == pytest.approx(math.exp(-.4), abs=1e-6)
    assert facts['source_validation']['binding_status'] == 'matched' and facts['scientific_status'] == 'not_verified'
    assert all(store.get(r)['payload']['locator']['blocks'] for r in store.list('claim'))
    bundles.export_bundle(store, result_ref, tmp_path/'bundle')
    assert ArtifactStore(tmp_path/'bundle').get_blob(store.get(source)['payload']['blob_ref']) == raw


def _native_table_failure(payload, out):
    from unittest.mock import patch
    import pymupdf
    from biomodel_agent import pdf_adapter
    with patch.object(pymupdf.Page, 'find_tables', side_effect=RuntimeError('injected table failure')):
        pdf_adapter._worker(payload, out)


def _native_page_failure(payload, out):
    from unittest.mock import patch
    import pymupdf
    from biomodel_agent import pdf_adapter
    with patch.object(pymupdf.Page, 'get_text', side_effect=RuntimeError('injected native page failure')):
        pdf_adapter._worker(payload, out)


def test_table_failure_retains_native_text_and_discloses_unknown_table_coverage(tmp_path, monkeypatch):
    from biomodel_agent import pdf_adapter
    path = tmp_path/'table.pdf'; rich_pdf(path, table=True)
    store = ArtifactStore(tmp_path/'store')
    monkeypatch.setattr(pdf_adapter, '_worker', _native_table_failure)
    source = store.get(evidence.ingest_source(store, path, pdf_parser='pymupdf'))['payload']
    assert source['pages'][0]['table_status'] == 'failed' and source['pages'][0]['tables'] == []
    assert 'Parameter k = 0.4.' in source['pages'][0]['text']
    assert any('table' in w and 'unknown' in w for w in source['warnings'])


def test_native_page_failure_cannot_leave_a_successful_partial_source(tmp_path, monkeypatch):
    from biomodel_agent import pdf_adapter
    path = tmp_path/'paper.pdf'; _write_pdf(path, ['First page.', 'Second page.'])
    store = ArtifactStore(tmp_path/'store')
    monkeypatch.setattr(pdf_adapter, '_worker', _native_page_failure)
    with pytest.raises(ValueError, match='injected native page failure'):
        evidence.ingest_source(store, path, pdf_parser='pymupdf')
    assert not store.list('source') and not store.list('pdf_extraction')
    assert len(store.list('pdf_parse_failure')) == 1


def test_table_cell_budget_is_enforced_before_exporting_large_auxiliary_data(tmp_path):
    path = tmp_path/'table.pdf'; rich_pdf(path, table=True)
    store = ArtifactStore(tmp_path/'store')
    with pytest.raises(ValueError, match='max_cells'):
        evidence.ingest_source(store, path, pdf_parser='pymupdf', pdf_limits={'max_cells': 2})
    assert not store.list('source')


def test_native_backend_is_deterministic_and_never_rewrites_legacy_sources(tmp_path):
    path = tmp_path/'paper.pdf'; raw = _write_pdf(path, ['Initial x = 1.'])
    store = ArtifactStore(tmp_path/'store')
    legacy = evidence.ingest_source(store, path)
    saved = store.get(legacy)
    modern = evidence.ingest_source(store, path, pdf_parser='pymupdf')
    assert evidence.ingest_source(store, path, pdf_parser='pymupdf') == modern
    assert legacy != modern and store.get(legacy) == saved and path.read_bytes() == raw
    assert store.get(legacy)['payload']['parser'] == 'pypdf'


def test_agent_sees_pdf_capability_warnings_and_exact_locator_when_inspecting(tmp_path):
    from biomodel_agent import agent
    from biomodel_agent.domain.contracts import ExperimentSpec
    from test_invocation_acceptance import Scripted, act
    path = tmp_path/'mixed.pdf'; rich_pdf(path, image=True)
    store = ArtifactStore(tmp_path/'store')
    source = evidence.ingest_source(store, path, pdf_parser='pymupdf')
    text = store.get(source)['payload']['pages'][0]['text']
    provider = Scripted([act('inspect_source', source_ref=source, page=1, start=0, end=len(text)),
                         act('ask_researcher', question='Please verify the image formula.')])
    result = store.get(agent.run_agent(store, provider, [source], ExperimentSpec((0., 1.))))['payload']
    assert result['state'] == 'awaiting_input' and not store.list('run')
    material = json.loads(provider.calls[0][1]['content'])['materials'][source]
    assert material['capabilities']['formula_recognition'] == 'not_run' and material['warnings']
    feedback = json.loads(provider.calls[1][-1]['content'])
    assert feedback['locator']['blocks'] and feedback['text'] == text


def test_agent_ingest_backend_option_reaches_the_same_local_tool(tmp_path, monkeypatch, capsys):
    from biomodel_agent import agent
    path = tmp_path/'paper.pdf'; _write_pdf(path, ['Initial x = 1.'])
    seen = []
    def offline_agent(store, provider, materials, experiment, **kwargs):
        seen.append(store.get(materials[0])['payload']['parser'])
        return store.put('agent_run', {'state': 'awaiting_input'}, refs=materials)
    monkeypatch.setenv('BIOMODEL_BASE_URL', 'https://offline.invalid')
    monkeypatch.setenv('BIOMODEL_MODEL', 'test')
    monkeypatch.setenv('BIOMODEL_API_KEY', 'local-test-placeholder')
    monkeypatch.setattr(agent, 'run_agent', offline_agent)
    exp = tmp_path/'experiment.json'; exp.write_text(json.dumps({'times': [0, 1]}))
    code = cli.main(['agent', '--source', str(path), '--experiment', str(exp), '--pdf-parser', 'pymupdf',
                     '--store', str(tmp_path/'store')])
    assert code == 2 and seen == ['pymupdf']
    assert json.loads(capsys.readouterr().out)['state'] == 'awaiting_input'


def _noisy_native_worker(payload, out):
    from unittest.mock import patch
    from biomodel_agent import pdf_adapter
    original = pdf_adapter._extract
    def noisy(data, limits):
        print('injected native diagnostic on stdout', flush=True)
        return original(data, limits)
    with patch.object(pdf_adapter, '_extract', side_effect=noisy):
        pdf_adapter._worker(payload, out)


def test_native_stdout_diagnostics_do_not_corrupt_application_stdout(tmp_path, monkeypatch, capfd):
    from biomodel_agent import pdf_adapter
    monkeypatch.setattr(pdf_adapter, '_worker', _noisy_native_worker)
    store, source, _ = ingested(tmp_path)
    assert store.get(source)['kind'] == 'source'
    output = capfd.readouterr()
    assert output.out == ''
    assert 'injected native diagnostic' in output.err
