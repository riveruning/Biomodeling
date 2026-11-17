"""Production regressions for the reviewed read-only literature GET recovery.

Scope of the semantic change (intentionally narrow, architecture-trial-27):
- ONLY the bounded public GET calls inside research_literature._request
  (search_literature / fetch_literature / fetch_literature_asset) stop freezing
  the project on transport failure. They are read-only, unauthenticated,
  unbilled downloads that write their artifact (blob/source/asset) only after a
  complete response, so a failed GET leaves no project artifact behind. The
  failure is receipted as a confirmed rejected attempt the planner can branch
  on; the failed request itself is never replayed and still consumes budget.
- The model-paid transcription POST keeps its unknown-effect freeze when no
  response was confirmed (test_transport_failure_freezes_without_any_replay and
  test_unresolved_incomplete_output_keep_markers_visible in
  test_research_literature.py, unchanged).
- Scientific execution, respond attempts, receipt closure,
  publication-identity gates and every budget stay exactly as strict.
- Review addition: a transport failure can also surface while the response
  context closes AFTER a complete transfer, when the response bytes and the
  attempt's request receipt were already stored. That failure receipt names
  the retained bytes/receipt instead of claiming no artifact exists.
"""
import httpx
import pytest

from biomodel_agent import research_projects as projects, research_literature as literature
from biomodel_agent.storage import ArtifactStore

from test_research_literature import (FIG2B_JATS, JPEG, fig2b_transport, publication_response,
                                      search, search_body)


def setup_project(tmp_path, handler, limit=4):
    store = ArtifactStore(tmp_path / 's')
    root = projects.create(store, dict(scenario='Find evidence for the stated problem.',
                                       policy=dict(max_literature_requests=limit)))
    return store, root, httpx.MockTransport(handler)


def failed_attempt_receipts(store):
    rows = []
    for ref in store.list(literature.REQUEST):
        payload = store.get(ref)['payload']
        if payload.get('outcome') == 'failed_attempt':
            rows.append((ref, payload))
    return rows


class CloseFailTransport(httpx.BaseTransport):
    """A complete transfer whose transport dies only when the client closes.

    The response context exits cleanly after the full body was consumed, so the
    blob and the attempt's request receipt were already stored when the
    transport error surfaces — the reviewed retained-artifact case.
    """

    def __init__(self, body):
        self.body = body

    def handle_request(self, request):
        return httpx.Response(200, stream=SingleStream(self.body))

    def close(self):
        raise httpx.ReadError('transport dropped while closing')


def test_mid_get_read_timeout_rejected_then_new_format_fetch_succeeds(tmp_path):
    """The coexist-24-03 failure, end to end: the JATS GET dies mid-stream, the
    project must NOT freeze; the planner's next bounded action (here pmc_html,
    a NEW request, not a replay) completes the fetch."""
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith('/search'):
            return httpx.Response(200, json=search_body())
        if request.url.path.endswith('/fullTextXML'):
            raise httpx.ReadTimeout('private proxy details must not leak', request=request)
        return httpx.Response(200, content=publication_response('pmc_html'))

    store, root, transport = setup_project(tmp_path, handler)
    found = search(store, root, transport)
    with pytest.raises(literature.LiteratureError) as err:
        projects.operate(store, root, 'fetch_literature',
                         dict(search_ref=found['search_ref'], pmcid='PMC123'),
                         literature_transport=transport)
    view = projects.inspect(store, root)
    assert view['state'] == 'ready'                       # was: needs_recovery
    assert view['source_refs'] == []                      # no partial artifact admitted
    attempts, returns, pending = projects.history(store, root)
    assert pending == []
    ret_ref, saved = returns[attempts[-1][0]]
    assert saved['state'] == 'rejected'
    assert 'never replayed automatically' in saved['diagnostic']['message']
    assert err.value.receipt_ref in set(store.get(ret_ref)['refs'])   # closure, never orphaned
    raw = ''.join(p.read_text() for p in store.objects_dir.glob('*.json'))
    assert 'private proxy details' not in raw             # transport text stays out
    # attempt-bound receipt with honest facts
    failed = failed_attempt_receipts(store)
    assert len(failed) == 1
    assert failed[0][1]['error_kind'] == 'ReadTimeout'
    # The mock raises before any status line arrives, so the receipt honestly
    # reports no response; a mid-stream stop AFTER headers would carry
    # http_status and response_received=True (see the byte-budget test).
    assert failed[0][1]['response_received'] is False
    assert 'http_status' not in failed[0][1]
    assert failed[0][1]['url'].endswith('/fullTextXML')
    # the failed attempt consumed budget but the project continues: NEW request
    assert projects.inspect(store, root)['budget']['literature_requests'] == 2
    fetched = projects.operate(store, root, 'fetch_literature',
                               dict(search_ref=found['search_ref'], pmcid='PMC123',
                                    format='pmc_html'), literature_transport=transport)
    assert fetched['source_ref'] in projects.inspect(store, root)['source_refs']
    assert len([c for c in calls if c.url.path.endswith('/fullTextXML')]) == 1  # never replayed
    assert projects.inspect(store, root)['budget']['literature_requests'] == 3
    assert projects.inspect(store, root)['state'] == 'ready'


def test_connect_failure_writes_no_blob_or_artifact(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith('/search'):
            return httpx.Response(200, json=search_body())
        raise httpx.ConnectError('refusing connection', request=request)

    store, root, transport = setup_project(tmp_path, handler)
    found = search(store, root, transport)
    blobs_before = sorted(p.name for p in store.blobs_dir.iterdir())
    with pytest.raises(literature.LiteratureError):
        projects.operate(store, root, 'fetch_literature',
                         dict(search_ref=found['search_ref'], pmcid='PMC123'),
                         literature_transport=transport)
    assert sorted(p.name for p in store.blobs_dir.iterdir()) == blobs_before  # zero durable effect
    assert projects.inspect(store, root)['state'] == 'ready'


def test_byte_budget_stop_is_rejected_not_unknown(tmp_path):
    def handler(request):
        if request.url.path.endswith('/search'):
            return httpx.Response(200, json=search_body())
        return httpx.Response(200, content=b'x' * 2_500_000)

    store, root, transport = setup_project(tmp_path, handler)
    found = search(store, root, transport)
    with pytest.raises(literature.LiteratureError, match='byte/time budget'):
        projects.operate(store, root, 'fetch_literature',
                         dict(search_ref=found['search_ref'], pmcid='PMC123'),
                         literature_transport=transport)
    attempts, returns, pending = projects.history(store, root)
    assert pending == []
    assert returns[attempts[-1][0]][1]['state'] == 'rejected'
    assert projects.inspect(store, root)['state'] == 'ready'
    assert projects.inspect(store, root)['source_refs'] == []
    failed = failed_attempt_receipts(store)
    assert failed and failed[0][1]['error_kind'] == 'byte_time_budget_exceeded'
    assert failed[0][1]['http_status'] == 200 and failed[0][1]['bytes_received'] > 2_000_000


def test_failed_get_still_consumes_budget_and_blocked_before_network(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectTimeout('nope', request=request)

    store, root, transport = setup_project(tmp_path, handler, limit=1)
    with pytest.raises(literature.LiteratureError):
        search(store, root, transport)
    assert projects.inspect(store, root)['budget']['literature_requests'] == 1
    before = len(calls)
    with pytest.raises(ValueError, match='budget exhausted'):
        search(store, root, transport)
    assert len(calls) == before                            # refused BEFORE any request
    assert projects.inspect(store, root)['state'] == 'ready'


def test_validation_rejects_stay_pre_network_and_never_unknown(tmp_path):
    """Frozen semantics kept: a gate rejection (PMCID outside this project's
    search) is rejected before any request; only GET transport outcomes moved
    from unknown to rejected."""
    def handler(request):
        return httpx.Response(200, json=search_body())

    store, root, transport = setup_project(tmp_path, handler)
    found = search(store, root, transport)
    with pytest.raises(ValueError):
        projects.operate(store, root, 'fetch_literature',
                         dict(search_ref=found['search_ref'], pmcid='PMC999'),
                         literature_transport=transport)
    attempts, returns, pending = projects.history(store, root)
    # the gate fires before any attempt record exists (validate precedes the
    # reservation), so only the original search attempt remains: never any
    # unknown effect, and no extra network call for the rejected fetch
    assert pending == []
    assert [a['operation'] for _, a in attempts] == ['search_literature']
    assert returns[attempts[-1][0]][1]['state'] == 'returned'
    assert projects.inspect(store, root)['state'] == 'ready'


class SingleStream(httpx.SyncByteStream):
    def __init__(self, body):
        self.body = body

    def __iter__(self):
        yield self.body


def test_close_after_complete_transfer_names_the_retained_artifact(tmp_path):
    """Reviewed close-failure case: the transport can also fail while the
    client context closes AFTER a complete transfer, when the response bytes
    and the attempt's request receipt were already stored. The failure receipt
    must reference them and say so, never claim no artifact exists, and the
    planner can still issue a NEW counted request that succeeds."""
    body = FIG2B_JATS

    def handler(request):
        if request.url.path.endswith('/search'):
            return httpx.Response(200, json=search_body())
        return httpx.Response(200, content=body)

    store, root, _ = setup_project(tmp_path, handler)
    found = search(store, root, httpx.MockTransport(handler))
    with pytest.raises(literature.LiteratureError) as err:
        projects.operate(store, root, 'fetch_literature',
                         dict(search_ref=found['search_ref'], pmcid='PMC123'),
                         literature_transport=CloseFailTransport(body))
    view = projects.inspect(store, root)
    assert view['state'] == 'ready' and view['source_refs'] == []
    attempts, returns, pending = projects.history(store, root)
    assert pending == []
    ret_ref, saved = returns[attempts[-1][0]]
    assert saved['state'] == 'rejected'
    assert err.value.receipt_ref in set(store.get(ret_ref)['refs'])
    # three receipts: the search, the completed transfer's request receipt,
    # and the failed attempt receipt that references that transfer
    receipts = store.list(literature.REQUEST)
    assert len(receipts) == 3
    response_rows = [ref for ref in receipts
                     if store.get(ref)['payload'].get('outcome') != 'failed_attempt'
                     and store.get(ref)['payload']['url'].endswith('/fullTextXML')]
    assert len(response_rows) == 1
    transfer_ref = response_rows[0]
    transfer = store.get(transfer_ref)['payload']
    assert transfer['response_received'] is True and transfer['http_status'] == 200
    assert store.get_blob(transfer['blob_ref']) == body
    failed_ref, failed = failed_attempt_receipts(store)[0]
    assert failed['error_kind'] == 'ReadError' and failed['response_received'] is True
    assert failed['http_status'] == 200 and failed['bytes_received'] == len(body)
    assert failed['retained_blob_ref'] == transfer['blob_ref']
    assert failed['retained_request_ref'] == transfer_ref
    assert {transfer_ref, transfer['blob_ref']} <= set(store.get(failed_ref)['refs'])
    assert 'no project source or asset was admitted' in failed['notice']
    raw = ''.join(p.read_text() for p in store.objects_dir.glob('*.json'))
    assert 'dropped while closing' not in raw              # transport text stays out
    assert projects.inspect(store, root)['budget']['literature_requests'] == 2
    # a NEW counted request under a working transport completes the fetch
    fetched = projects.operate(store, root, 'fetch_literature',
                               dict(search_ref=found['search_ref'], pmcid='PMC123'),
                               literature_transport=httpx.MockTransport(handler))
    assert fetched['source_ref'] in projects.inspect(store, root)['source_refs']
    assert projects.inspect(store, root)['budget']['literature_requests'] == 3
    assert projects.inspect(store, root)['state'] == 'ready'


def test_asset_get_failure_is_rejected_and_a_new_asset_request_succeeds(tmp_path):
    """The asset GET is also a read-only download: a failed attempt stays
    rejected and a NEW counted asset request within the budget still works."""
    requests = []
    store, root, transport = setup_project(tmp_path, fig2b_transport(requests))
    found = search(store, root, transport)
    fetched = projects.operate(store, root, 'fetch_literature',
                               dict(search_ref=found['search_ref'], pmcid='PMC123'),
                               literature_transport=transport)
    source_ref = fetched['source_ref']

    def failing_asset(request):
        requests.append(request)
        if request.url.host == 'cdn.ncbi.nlm.nih.gov':
            raise httpx.ReadTimeout('asset stream dropped', request=request)
        return fig2b_transport(requests)(request)

    with pytest.raises(literature.LiteratureError) as err:
        projects.operate(store, root, 'fetch_literature_asset',
                         dict(source_ref=source_ref, asset='journal.e002.jpg'),
                         literature_transport=httpx.MockTransport(failing_asset))
    assert projects.inspect(store, root)['state'] == 'ready'
    assert projects.inspect(store, root)['source_refs'] == [source_ref]
    failed = failed_attempt_receipts(store)
    assert len(failed) == 1 and failed[0][1]['error_kind'] == 'ReadTimeout'
    assert err.value.receipt_ref in {ref for ref, _ in failed}
    result = projects.operate(store, root, 'fetch_literature_asset',
                              dict(source_ref=source_ref, asset='journal.e002.jpg'),
                              literature_transport=transport)
    assert store.get_blob(store.get(result['asset_ref'])['payload']['blob_ref']) == JPEG
    # search + fetch + failed asset attempt + successful asset request
    assert projects.inspect(store, root)['budget']['literature_requests'] == 4
