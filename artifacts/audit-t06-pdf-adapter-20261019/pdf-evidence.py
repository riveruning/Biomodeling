"""Offline synthetic PDF controls; actual PyMuPDF and ODE tools, scripted Agent."""
from copy import deepcopy
import json
import math
import multiprocessing as mp
import os
from pathlib import Path
import time
from unittest.mock import patch

import pymupdf

from biomodel_agent import agent, agent_tools, application, bundles, evidence, pdf_adapter, recovery, reporting, retrieval
from biomodel_agent.domain.contracts import ExperimentSpec, ModelDraft, Parameter, RateRule, State
from biomodel_agent.domain.serialization import to_dict
from biomodel_agent.storage import ArtifactStore

ROOT = Path(__file__).resolve().parent/'final-evidence'


class Scripted:
    last_usage = {'total_tokens': 3}
    def __init__(self, actions):
        self.actions = iter(actions)
        self.calls = []
    def respond(self, messages):
        self.calls.append(deepcopy(messages))
        return deepcopy(next(self.actions))


def act(name, **args):
    return {'action': name, 'args': args}


def create_fixture():
    path = ROOT/'synthetic-paper.pdf'
    doc = pymupdf.open(); page = doc.new_page(width=450, height=400)
    for y, text in [(35, 'Synthetic PDF development control'), (65, 'Initial x = 1.'),
                    (90, 'Parameter k = 0.4.'), (115, 'Equation dx/dt = -k*x.')]:
        page.insert_text((25, y), text)
    xs, ys = [30, 150, 270], [160, 190, 220, 250]
    for x in xs:
        page.draw_line((x, ys[0]), (x, ys[-1]))
    for y in ys:
        page.draw_line((xs[0], y), (xs[-1], y))
    for row, values in enumerate([['Symbol', 'Value'], ['k', '0.4'], ['x', '1']]):
        for column, value in enumerate(values):
            page.insert_text((xs[column]+8, ys[row]+20), value)
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), 0); pixmap.clear_with(128)
    page.insert_image(pymupdf.Rect(310, 160, 400, 250), stream=pixmap.tobytes('png'))
    page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5)).save(ROOT/'synthetic-page-1.png')
    scan = doc.new_page(width=450, height=400)
    scan.insert_image(scan.rect, stream=pixmap.tobytes('png'))
    doc.save(path); doc.close()
    return path


def proposal(source):
    draft = ModelDraft('decay', (State('x', 1.),), (Parameter('k', .4),), rate_rules=(RateRule('x', '-k*x'),))
    return {'draft': to_dict(draft), 'evidence': [
        {'target': target, 'source_ref': source, 'page': 1, 'quote': quote}
        for target, quote in [('x/initial', 'Initial x = 1.'), ('k/value', 'Parameter k = 0.4.'),
                              ('rule:x/expression', 'Equation dx/dt = -k*x.')]]}


def hang(payload, out):
    while True:
        time.sleep(.1)


def die(payload, out):
    os._exit(7)


def main():
    ROOT.mkdir(exist_ok=False)
    path = create_fixture()
    store = ArtifactStore(ROOT/'native-store')
    source = evidence.ingest_source(store, path, pdf_parser='pymupdf')
    document = store.get(source)['payload']
    assert store.get_blob(document['blob_ref']) == path.read_bytes()
    assert document['pages'][0]['tables'][0]['rows'] == [['Symbol', 'Value'], ['k', '0.4'], ['x', '1']]
    assert document['pages'][1]['text'] == '' and document['capabilities']['ocr'] == 'not_run'
    text = document['pages'][0]['text']
    provider = Scripted([act('inspect_source', source_ref=source, page=1, start=0, end=len(text)),
                         act('submit_draft', **proposal(source)), act('run'), act('finish')])
    root = agent.run_agent(store, provider, [source], ExperimentSpec((0., 1.)))
    result = store.get(root)['payload']
    assert result['state'] == 'completed'
    run = store.get(result['run_ref'])['payload']
    endpoint = run['simulation']['values']['x'][-1]
    assert abs(endpoint-math.exp(-.4)) < 1e-6 and run['scientific_status'] == 'not_verified'
    claims = {ref: store.get(ref)['payload'] for ref in store.list('claim')}
    assert all(c['binding']['status'] == 'matched' and c['locator']['blocks'] for c in claims.values())
    manifest = bundles.export_bundle(store, root, ROOT/'native-bundle')
    copied = ArtifactStore(ROOT/'native-bundle')
    assert copied.get_blob(document['blob_ref']) == path.read_bytes()
    assert copied.get(document['parser_output_ref']) == store.get(document['parser_output_ref'])
    (ROOT/'native-run-report.md').write_text(reporting.render_report(store, result['run_ref']))
    results = {'evidence_kind': 'synthetic development PDF; scripted Agent; real local PyMuPDF and ODE tools',
               'source_ref': source, 'parser': document['parser'], 'parser_version': document['parser_version'],
               'parser_output_ref': document['parser_output_ref'], 'pdf_blob_ref': document['blob_ref'],
               'page_status': [{'number': p['number'], 'status': p['status'], 'tables': len(p['tables']),
                                'images': len(p['images']), 'chars': len(p['text'])} for p in document['pages']],
               'warnings': document['warnings'], 'table': document['pages'][0]['tables'][0],
               'agent': {'root_ref': root, 'state': result['state'], 'call_count': result['call_count'],
                         'run_ref': result['run_ref'], 'endpoint': endpoint, 'expected_endpoint': math.exp(-.4),
                         'absolute_error': abs(endpoint-math.exp(-.4)), 'scientific_status': run['scientific_status'],
                         'initial_materials': json.loads(provider.calls[0][1]['content'])['materials'],
                         'inspection': json.loads(provider.calls[1][-1]['content'])},
               'claims': claims, 'retrieval': retrieval.search_sources(store, [source], 'Parameter k'),
               'bundle': {'root_ref': root, 'objects_and_blobs': len(manifest['objects_and_blobs']),
                          'pdf_and_parser_output_unchanged': True}}

    failures = {}
    for name, worker in [('timeout', hang), ('worker_crash', die)]:
        failed_store = ArtifactStore(ROOT/(name+'-store'))
        children = {p.pid for p in mp.active_children()}
        began = time.monotonic()
        with patch.object(pdf_adapter, '_worker', worker):
            try:
                evidence.ingest_source(failed_store, path, pdf_parser='pymupdf', pdf_timeout_s=.5 if name == 'timeout' else 5)
            except ValueError as exc:
                error = str(exc)
            else:
                raise AssertionError('failure was not injected')
        assert not failed_store.list('source') and not failed_store.list('pdf_extraction')
        assert {p.pid for p in mp.active_children()} <= children
        failure_ref = failed_store.list('pdf_parse_failure')[0]
        failures[name] = {'failure_ref': failure_ref, 'error': error, 'wall_time_s': time.monotonic()-began,
                          'successful_sources': 0, 'no_child_left': True,
                          'raw_bytes_retained': failed_store.get_blob(document['blob_ref']) == path.read_bytes()}
    results['injected_failures'] = failures

    resumed_store = ArtifactStore(ROOT/'recovery-store')
    source2 = evidence.ingest_source(resumed_store, path, pdf_parser='pymupdf')
    with patch.object(agent, '_run_facts', side_effect=SystemExit('injected after saved scientific result')):
        try:
            agent.run_agent(resumed_store, Scripted([act('submit_draft', **proposal(source2)), act('run')]),
                            [source2], ExperimentSpec((0., 1.)))
        except SystemExit:
            pass
        else:
            raise AssertionError('missing fault')
    execution = resumed_store.list('agent_execution')[-1]
    assert recovery.diagnose_execution(resumed_store, execution)['status'] == 'ready'
    old_runs, old_claims = resumed_store.list('run'), resumed_store.list('claim')
    with patch.object(application, 'run_reproduction', side_effect=AssertionError('solver repeated')), \
         patch.object(agent_tools, 'submit_draft', side_effect=AssertionError('claims repeated')):
        final = agent.recover_agent(resumed_store, Scripted([act('finish')]), execution)
    state = resumed_store.get(final)['payload']['state']
    assert state == 'completed' and old_runs == resumed_store.list('run') and old_claims == resumed_store.list('claim')
    results['recovery'] = {'execution_ref': execution, 'root_ref': final, 'state': state,
                           'no_solver_repeat': True, 'no_claim_repeat': True}
    (ROOT/'pdf-results.json').write_text(json.dumps(results, indent=2, ensure_ascii=False)+'\n')
    print(json.dumps({'state': result['state'], 'pages': len(document['pages']), 'native_tables': 1,
                      'scientific_status': run['scientific_status'], 'recovery': state,
                      'injected_failures': list(failures)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
