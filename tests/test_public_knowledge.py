import hashlib
import json

import httpx
import pytest

from biomodel_agent import knowledge
from biomodel_agent.storage import ArtifactStore


@pytest.fixture
def store(tmp_path):
    return ArtifactStore(tmp_path / 'store')


def transport(handler):
    return httpx.MockTransport(handler)


def sabio_doc():
    return {'EntryID': 4510, 'ReactionEquation': 'A = B', 'Organism': ['organism'],
            'Json': json.dumps({'experimental_conditions': {'envvar_ph': {'start_value': 8}},
                               'kineticlaw': {'parameter': [
                                   {'name': 'Km', 'role': 'Constant', 'start_value': 2,
                                    'unit': {'name': 'mM', 'n_name': 'M'}, 'n_start_value': .002},
                                   {'name': 'S', 'role': 'Variable', 'start_value': 5}]}})}


def test_search_preserves_raw_response_and_limits(store):
    body = {'matches': 2, 'models': [{'id': 'BIOMD0000000012', 'name': 'oscillator'},
                                     {'id': 'BIOMD0000000010', 'name': 'other'}]}
    def handler(req):
        assert req.url.host == 'www.biomodels.org'
        assert req.url.params['query'] == 'gene expression'
        assert req.url.params['numResults'] == '1'
        assert 'authorization' not in req.headers
        return httpx.Response(200, json=body)
    result = knowledge.search(store, 'biomodels', 'gene expression', 1, transport=transport(handler))
    assert len(result['results']) == 1 and result['total'] == 2
    receipt = store.get(result['request_ref'])['payload']
    assert json.loads(store.get_blob(receipt['blob_ref'])) == body
    assert store.get(result['search_ref'])['refs'] == [result['request_ref']]


def test_sabio_search_and_import_keep_conditions_and_units(store):
    doc = sabio_doc()
    def handler(req):
        assert req.url.path == '/api/ft/proxy-select'
        return httpx.Response(200, json={'response': {'numFound': 1, 'docs': [doc]}})
    tr = transport(handler)
    result = knowledge.search(store, 'sabiork', 'ECNumber:1.1.1.1', transport=tr)
    record = result['results'][0]
    assert record['parameters'][0]['value'] == 2
    assert record['parameters'][0]['unit']['name'] == 'mM'
    assert record['parameters'][0]['normalized_value'] == .002
    assert len(record['parameters']) == 1
    assert record['conditions']['envvar_ph']['start_value'] == 8
    imported = knowledge.fetch(store, 'sabiork', '4510', transport=tr)
    source = store.get(imported['source_ref'])['payload']
    assert json.loads(source['pages'][0]['text']) == doc
    assert json.loads(store.get_blob(source['blob_ref']))['response']['docs'] == [doc]


@pytest.mark.parametrize('status,body', [(429, b'rate limited'), (302, b'redirect'),
                                       (200, b'<html>error</html>'), (200, b'')])
def test_failures_are_not_empty_search_results(store, status, body):
    tr = transport(lambda req: httpx.Response(status, content=body, headers={'location': 'https://other.test/'}))
    with pytest.raises(ValueError, match='failure_ref='):
        knowledge.search(store, 'biomodels', 'x', transport=tr)


def test_invalid_schema_is_not_no_matches(store):
    with pytest.raises(ValueError, match='schema'):
        knowledge.search(store, 'sabiork', 'x', transport=transport(lambda r: httpx.Response(200, json={})))


def test_size_and_connection_failure(store, monkeypatch):
    monkeypatch.setattr(knowledge, 'MAX_BYTES', 10)
    with pytest.raises(ValueError, match='size or time'):
        knowledge.search(store, 'biomodels', 'x', transport=transport(lambda r: httpx.Response(200, content=b'x'*11)))
    def fail(req):
        raise httpx.ConnectError('offline')
    with pytest.raises(ValueError, match='failure_ref='):
        knowledge.search(store, 'biomodels', 'x', transport=transport(fail))


@pytest.mark.parametrize('database,query,limit', [('other', 'x', 1), ('biomodels', '', 1),
                                               ('sabiork', 'x', True), ('biomodels', 'x', 11)])
def test_bad_inputs_do_not_send_requests(store, database, query, limit):
    def fail(req):
        pytest.fail('network must not be called')
    with pytest.raises(ValueError):
        knowledge.search(store, database, query, limit, transport=transport(fail))


def model_transport(*, checksum=None, content=None, multiple=False):
    xml = content or b'''<?xml version="1.0"?><sbml xmlns="http://www.sbml.org/sbml/level3/version2/core" level="3" version="2"><model id="empty"/></sbml>'''
    def handler(req):
        if '/files/' in req.url.path:
            main = [{'name': 'model.xml', 'sha256sum': checksum or hashlib.sha256(xml).hexdigest()}]
            if multiple:
                main.append({'name': 'other.xml'})
            return httpx.Response(200, json={'main': main, 'additional': []})
        assert req.url.params['filename'] == 'model.xml'
        return httpx.Response(200, content=xml)
    return transport(handler)


def test_biomodel_download_is_stored_and_checked_not_executed(store):
    result = knowledge.fetch(store, 'biomodels', 'BIOMD0000000012', transport=model_transport())
    assert result['scientific_status'] == 'not_verified'
    assert isinstance(result['can_execute'], bool)
    assert store.get(result['model_ref'])['kind'] == 'model'
    source = store.get(result['source_ref'])['payload']
    assert '<sbml' in source['pages'][0]['text']


@pytest.mark.parametrize('options,match', [({'checksum': '0'*64}, 'SHA-256'),
                                          ({'multiple': True}, 'explicit'),
                                          ({'content': b'<root/>'}, 'not SBML')])
def test_download_rejects_mismatch_ambiguity_and_non_sbml(store, options, match):
    with pytest.raises(ValueError, match=match):
        knowledge.fetch(store, 'biomodels', 'BIOMD0000000012', transport=model_transport(**options))


def test_bad_identifiers_and_unlisted_filename(store):
    with pytest.raises(ValueError, match='identifier'):
        knowledge.fetch(store, 'biomodels', '../secret')
    with pytest.raises(ValueError, match='filename'):
        knowledge.fetch(store, 'biomodels', 'BIOMD0000000012', '../model.xml', transport=model_transport())


def test_missing_record_is_not_imported(store):
    tr = transport(lambda r: httpx.Response(200, json={'response': {'numFound': 0, 'docs': []}}))
    with pytest.raises(ValueError, match='missing'):
        knowledge.fetch(store, 'sabiork', '4510', transport=tr)


def test_cli_search_and_fetch_output(tmp_path, monkeypatch, capsys):
    from biomodel_agent.cli import main
    monkeypatch.setattr(knowledge, 'search', lambda *a: {'results': [], 'total': 0})
    assert main(['knowledge-search', '--database', 'biomodels', '--query', 'x', '--store', str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)['total'] == 0


def test_cli_model_output_refuses_overwrite(tmp_path, monkeypatch, capsys):
    from biomodel_agent.cli import main
    store_path = tmp_path / 'store'
    store = ArtifactStore(store_path)
    blob = store.put_blob(b'<sbml/>')
    source = store.put('source', {'blob_ref': blob}, refs=(blob,))
    monkeypatch.setattr(knowledge, 'fetch', lambda *a: {'source_ref': source})
    output = tmp_path / 'model.xml'
    args = ['knowledge-fetch', '--database', 'biomodels', 'BIOMD0000000012',
            '--store', str(store_path), '--output', str(output)]
    assert main(args) == 0
    assert output.read_bytes() == b'<sbml/>'
    output.write_bytes(b'keep existing model')
    assert main(args) != 0
    assert output.read_bytes() == b'keep existing model'


@pytest.mark.parametrize('enabled', [False, True])
def test_agent_search_is_opt_in_and_does_not_extend_source_scope(tmp_path, monkeypatch, enabled):
    from biomodel_agent.agent import run_agent
    from biomodel_agent.evidence import ingest_source
    from biomodel_agent.domain.contracts import ExperimentSpec
    store = ArtifactStore(tmp_path / 'store')
    paper = tmp_path / 'paper.txt'
    paper.write_text('k = 1')
    source = ingest_source(store, paper)
    calls = []
    def search(*args):
        calls.append(args)
        return {'search_ref': store.put('knowledge_search', {'results': []}), 'results': []}
    monkeypatch.setattr(knowledge, 'search', search)
    class Provider:
        def respond(self, messages):
            if len(calls) or any(m['role'] == 'user' and 'enabled database and query' in m['content'] for m in messages):
                return {'action': 'ask_researcher', 'args': {'question': 'Need a source.'}}
            return {'action': 'search_knowledge', 'args': {'database': 'biomodels', 'query': 'x'}}
    ref = run_agent(store, Provider(), [source], ExperimentSpec(times=(0., 1.)),
                    knowledge_databases=['biomodels'] if enabled else [], max_calls=4)
    payload = store.get(ref)['payload']
    assert bool(calls) == enabled
    assert payload['state'] == 'awaiting_input'
    assert payload['source_refs'] == [source]
    if enabled:
        result_event = next(e for e in payload['events'] if e.get('tool') == 'search_knowledge' and e['type'] == 'tool_result')
        search_ref = result_event['search_ref']
        returned = [store.get(r) for r in payload['invocation_refs']
                    if store.get(r)['payload'].get('phase') == 'returned'
                    and store.get(r)['payload'].get('name') == 'search_knowledge']
        assert search_ref in returned[0]['refs']


def test_agent_resume_keeps_database_scope_and_budget(tmp_path, monkeypatch):
    from biomodel_agent.agent import run_agent, resume_agent
    from biomodel_agent.evidence import ingest_source
    from biomodel_agent.domain.contracts import ExperimentSpec
    store = ArtifactStore(tmp_path / 'store')
    path = tmp_path / 'source.txt'
    path.write_text('Parameter k = 1.')
    source = ingest_source(store, path)
    class Pause:
        search_next = False
        def respond(self, messages):
            if self.search_next and not calls:
                return {'action': 'search_knowledge', 'args': {'database': 'biomodels', 'query': 'x'}}
            return {'action': 'ask_researcher', 'args': {'question': 'Need more context.'}}
    parent = run_agent(store, Pause(), [source], ExperimentSpec((0., 1.)),
                       knowledge_databases=['biomodels'], max_calls=6)
    calls = []
    def search(*args):
        calls.append(args)
        return {'search_ref': store.put('knowledge_search', {'results': []}), 'results': []}
    monkeypatch.setattr(knowledge, 'search', search)
    provider = Pause()
    provider.search_next = True
    result = store.get(resume_agent(store, provider, parent, 'Please search.', respondent='operator'))['payload']
    assert len(calls) == 1
    assert result['call_count'] > store.get(parent)['payload']['call_count']
    assert result['source_refs'][0] == source


@pytest.mark.parametrize('content', [b'{"response":{"numFound":1,"docs":[{"Json":"{\\"kineticlaw\\": []}"}]}}',
                                     b'{"matches":1e999,"models":[]}'])
def test_bad_remote_numeric_or_nested_shape_is_reported(store, content):
    db = 'sabiork' if b'response' in content else 'biomodels'
    with pytest.raises(ValueError):
        knowledge.search(store, db, 'x', transport=transport(lambda r: httpx.Response(200, content=content)))
