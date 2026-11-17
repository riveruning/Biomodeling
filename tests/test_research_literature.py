"""Literature discovery is scoped, quota-bound and distinct from source proof."""
import json
import httpx
import pytest

from biomodel_agent import research_projects as projects,research_literature as literature
from biomodel_agent.storage import ArtifactStore


def setup(tmp_path,handler,limit=4):
    store=ArtifactStore(tmp_path/'s');root=projects.create(store,dict(scenario='Find evidence for the stated problem.',
        policy=dict(max_literature_requests=limit)))
    return store,root,httpx.MockTransport(handler)


def search_body():
    return dict(hitCount=1,resultList=dict(result=[dict(id='123',source='MED',pmcid='PMC123',
        title='Synthetic literature control',isOpenAccess='Y',abstractText='Not a full-text proof.')]))


def test_full_text_projection_separates_peer_review_history_and_keeps_data():
    raw = (b'<article article-type="research-article"><body><sec><title>Results</title>'
           b'<p>Primary experimental finding.</p></sec></body>'
           b'<sub-article article-type="aggregated-review-documents"><body>'
           b'<p>Reviewer report about models.</p></body></sub-article>'
           b'<sub-article article-type="associated-data"><body><p>Associated data.</p>'
           b'</body></sub-article></article>')
    text, warnings = literature._extract(raw)
    assert 'Primary experimental finding.' in text and 'Associated data.' in text
    assert 'Reviewer report' not in text
    assert any('Peer-review correspondence excluded' in warning for warning in warnings)


FIG2B_JATS = (b'<article><front><article-meta>'
              b'<article-id pub-id-type="pmcid">PMC123</article-id>'
              b'<article-id pub-id-type="doi">10.1234/expected</article-id>'
              b'</article-meta><article-title>Formula image study</article-title></front>'
              b'<body><sec><title>Results</title>'
              b'<disp-formula id="e1"><label>(1)</label>'
              b'<graphic xmlns:xlink="http://www.w3.org/1999/xlink" content-type="image" '
              b'xlink:href="journal.e001.jpg">'
              b'<?cloudpmc-path blobs/547e/123/aaa111/journal.e001.jpg?>'
              b'<?cloudpmc-bucket cdn?></graphic></disp-formula>'
              b'<p>Integration of Eqs. (1)-(2) for parameter values (arbitrary units): '
              b'<inline-formula><inline-graphic '
              b'xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="journal.e002.jpg">'
              b'<?cloudpmc-path blobs/547e/123/bbb222/journal.e002.jpg?></inline-graphic>'
              b'</inline-formula>, '
              b'<inline-formula><inline-graphic '
              b'xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="journal.e003.jpg">'
              b'<?cloudpmc-path blobs/547e/123/ccc333/journal.e003.jpg?></inline-graphic>'
              b'</inline-formula>. The oscillation period is about 40 a.u.</p>'
              b'<disp-formula id="m1"><label>(3)</label>'
              b'<math><mi>x</mi><mo>=</mo><mn>4</mn></math></disp-formula>'
              b'</sec></body></article>')

JPEG = bytes.fromhex('ffd8ffe000104a4649460001') + b'\x11' * 64


def fig2b_transport(requests, media_bytes=JPEG):
    def handler(request):
        requests.append(request)
        if request.url.path.endswith('/search'):
            return httpx.Response(200, json=search_body())
        if request.url.host == 'www.ebi.ac.uk':
            return httpx.Response(200, content=FIG2B_JATS)
        if request.url.host == 'cdn.ncbi.nlm.nih.gov':
            return httpx.Response(200, content=media_bytes)
        raise AssertionError('unexpected asset host ' + request.url.host)
    return handler


def test_image_only_formulas_project_as_visible_unresolved_markers(tmp_path):
    """Regression (Gonze2013 Fig 2B): image-only equations must never vanish."""
    requests = []
    store, root, transport = setup(tmp_path, fig2b_transport(requests))
    found = search(store, root, transport)
    fetched = projects.operate(store, root, 'fetch_literature',
                               dict(search_ref=found['search_ref'], pmcid='PMC123'),
                               literature_transport=transport)
    src = store.get(fetched['source_ref'])['payload']
    page = src['pages'][0]['text']
    # The caption keeps its literal prose and gains exactly one marker per hole.
    assert 'parameter values (arbitrary units): [unreadable-formula:journal.e002.jpg], ' \
           '[unreadable-formula:journal.e003.jpg]. The oscillation period is about 40 a.u.' in page
    assert '(1)[unreadable-formula:journal.e001.jpg]' in page
    # MathML with textual content still projects unchanged and gains no marker.
    assert '(3)x=4' in page
    assert page.count('unreadable-formula') == 3
    assert src['unresolved_formula_count'] == 3
    assert {entry['asset'] for entry in src['unresolved_formula']} == \
        {'journal.e001.jpg', 'journal.e002.jpg', 'journal.e003.jpg'}
    # Warnings make the gap visible in normal reading packets, beside the
    # pre-existing generic formula warning (counted, not deduplicated away).
    assert any('no textual content' in warning and 'journal.e001.jpg' in warning
               for warning in src['warnings'])
    assert src['capabilities']['formula_asset_retrieval'] == 'fetch_literature_asset'


def test_reported_formula_asset_is_retrievable_through_the_normal_operation(tmp_path):
    requests = []
    store, root, transport = setup(tmp_path, fig2b_transport(requests))
    found = search(store, root, transport)
    fetched = projects.operate(store, root, 'fetch_literature',
                               dict(search_ref=found['search_ref'], pmcid='PMC123'),
                               literature_transport=transport)
    source_ref = fetched['source_ref']
    result = projects.operate(store, root, 'fetch_literature_asset',
                              dict(source_ref=source_ref, asset='journal.e002.jpg'),
                              literature_transport=transport)
    asset = store.get(result['asset_ref'])['payload']
    assert store.get_blob(asset['blob_ref']) == JPEG
    assert result['media_type'] == 'image/jpeg'
    assert result['url'] == 'https://cdn.ncbi.nlm.nih.gov/pmc/blobs/547e/123/bbb222/journal.e002.jpg'
    assert [r.url.host for r in requests] == ['www.ebi.ac.uk', 'www.ebi.ac.uk', 'cdn.ncbi.nlm.nih.gov']
    assert projects.inspect(store, root)['budget']['literature_requests'] == 3
    assert asset['source_ref'] == source_ref and asset['project_ref'] == root


def test_asset_operation_only_serves_reported_unresolved_assets(tmp_path):
    requests = []
    store, root, transport = setup(tmp_path, fig2b_transport(requests))
    found = search(store, root, transport)
    fetched = projects.operate(store, root, 'fetch_literature',
                               dict(search_ref=found['search_ref'], pmcid='PMC123'),
                               literature_transport=transport)
    source_ref = fetched['source_ref']
    before = len(requests)
    for asset in ('journal.e999.jpg', '../../etc/passwd', 'journal e002.jpg', 'e002.jpg'):
        with pytest.raises(ValueError):
            projects.operate(store, root, 'fetch_literature_asset',
                             dict(source_ref=source_ref, asset=asset),
                             literature_transport=transport)
    assert len(requests) == before  # validation rejects before any network call
    # The textual MathML equation created no unresolved record, so it has no asset.
    assert all(entry['asset'] for entry in
               store.get(source_ref)['payload']['unresolved_formula'])


def test_non_image_asset_response_is_retained_but_not_promoted(tmp_path):
    def handler(request):
        if request.url.path.endswith('/search'):
            return httpx.Response(200, json=search_body())
        if request.url.host == 'www.ebi.ac.uk':
            return httpx.Response(200, content=FIG2B_JATS)
        return httpx.Response(200, content=b'<!doctype html>captcha wall')
    store, root, transport = setup(tmp_path, handler)
    found = search(store, root, transport)
    fetched = projects.operate(store, root, 'fetch_literature',
                               dict(search_ref=found['search_ref'], pmcid='PMC123'),
                               literature_transport=transport)
    with pytest.raises(literature.LiteratureError):
        projects.operate(store, root, 'fetch_literature_asset',
                         dict(source_ref=fetched['source_ref'], asset='journal.e002.jpg'),
                         literature_transport=transport)
    assert projects.inspect(store, root)['state'] == 'ready'
    assert len(store.list(literature.REQUEST)) == 3  # failed asset GET keeps its receipt


def search(store,root,transport):
    return projects.operate(store,root,'search_literature',dict(query='explicit query'),literature_transport=transport)


def test_discovery_then_full_text_import_preserves_raw_xml_and_warnings(tmp_path):
    requests=[]
    raw=b'<article><front><article-title>Synthetic control</article-title></front><body><sec><title>Methods</title><p>Initial X = 4.</p><disp-formula><math><mi>x</mi><mo>=</mo><mn>4</mn></math></disp-formula><table><tr><td>k</td><td>0.5</td></tr></table></sec></body></article>'
    def handler(req):
        requests.append(req)
        return httpx.Response(200,json=search_body()) if req.url.path.endswith('/search') else httpx.Response(200,content=raw)
    store,root,transport=setup(tmp_path,handler);found=search(store,root,transport)
    assert not projects.inspect(store,root)['source_refs']
    result=projects.operate(store,root,'fetch_literature',dict(search_ref=found['search_ref'],pmcid='PMC123'),literature_transport=transport)
    src=store.get(result['source_ref'])['payload']
    assert store.get_blob(src['blob_ref'])==raw and 'Initial X = 4.' in src['pages'][0]['text']
    assert src['parser']==literature.VERSION and src['capabilities']['formula_transcription']=='transcribe_literature_asset'
    assert len(src['warnings'])==2 and projects.inspect(store,root)['budget']['literature_requests']==2
    assert all(r.url.host=='www.ebi.ac.uk' for r in requests)


def test_explicit_pmc_html_import_for_discovered_public_article(tmp_path):
    requests = []
    raw = (b'<html><head><meta name="citation_fulltext_html_url" '
           b'content="https://pmc.ncbi.nlm.nih.gov/articles/PMC123/"></head><body><article>'
           b'<h1>Public article</h1><section class="body"><section><h2>Methods</h2>'
           b'<p>Measured cells.</p></section><section><h2>Results</h2>'
           b'<p>Cell counts increased.</p></section></section></article></body></html>')

    def handler(request):
        requests.append(request)
        if request.url.path.endswith('/search'):
            body = search_body()
            body['resultList']['result'][0]['isOpenAccess'] = 'N'
            return httpx.Response(200, json=body)
        assert request.url.host == 'pmc.ncbi.nlm.nih.gov'
        assert request.url.path == '/articles/PMC123/'
        assert request.headers['Accept'] == 'text/html'
        return httpx.Response(200, content=raw)

    store, root, transport = setup(tmp_path, handler)
    found = search(store, root, transport)
    args = dict(search_ref=found['search_ref'], pmcid='PMC123')
    with pytest.raises(ValueError, match='open-access'):
        projects.operate(store, root, 'fetch_literature', args, literature_transport=transport)
    fetched = projects.operate(store, root, 'fetch_literature', dict(args, format='pmc_html'), literature_transport=transport)
    source = store.get(fetched['source_ref'])['payload']
    assert source['parser'] == 'pmc_html'
    assert store.get_blob(source['blob_ref']) == raw
    assert 'Measured cells.' in source['pages'][0]['text']
    assert source['capabilities']['image_transcription'] == 'unverified'
    assert len(requests) == 2 and projects.inspect(store, root)['budget']['literature_requests'] == 2


@pytest.mark.parametrize('returned_pmid', ['123', '999'])
def test_pmc_html_can_bind_a_discovered_pmid_when_pmcid_is_missing(tmp_path, returned_pmid):
    raw = (f'<html><head><meta name="citation_pmid" content="{returned_pmid}">'
           '<meta name="citation_fulltext_html_url" content="https://pmc.ncbi.nlm.nih.gov/articles/PMC123/">'
           '</head><body><article><h1>Study</h1><section class="body"><section>'
           '<h2>Methods</h2><p>Measured cells.</p></section></section></article></body></html>').encode()

    def handler(request):
        if request.url.path.endswith('/search'):
            body = search_body()
            body['resultList']['result'][0].update(pmcid=None, isOpenAccess='N')
            return httpx.Response(200, json=body)
        return httpx.Response(200, content=raw)

    store, root, transport = setup(tmp_path, handler)
    found = search(store, root, transport)
    args = dict(search_ref=found['search_ref'], pmcid='PMC123', format='pmc_html', publication_id='123')
    if returned_pmid == '999':
        with pytest.raises(literature.LiteratureError):
            projects.operate(store, root, 'fetch_literature', args, literature_transport=transport)
        assert projects.inspect(store, root)['source_refs'] == []
    else:
        fetched = projects.operate(store, root, 'fetch_literature', args, literature_transport=transport)
        assert fetched['source_ref'] in projects.inspect(store, root)['source_refs']


@pytest.mark.parametrize('raw', [
    b'<html>Checking your browser</html>',
    b'<html><head><meta name="citation_fulltext_html_url" content="https://pmc.ncbi.nlm.nih.gov/articles/PMC999/"></head><body><article><h1>Wrong article</h1></article></body></html>',
])
def test_pmc_html_failure_retains_receipt_without_admitting_source(tmp_path, raw):
    def handler(request):
        return httpx.Response(200, json=search_body()) if request.url.path.endswith('/search') else httpx.Response(200, content=raw)

    store, root, transport = setup(tmp_path, handler)
    found = search(store, root, transport)
    with pytest.raises(literature.LiteratureError):
        projects.operate(store, root, 'fetch_literature',
            dict(search_ref=found['search_ref'], pmcid='PMC123', format='pmc_html'), literature_transport=transport)
    assert projects.inspect(store, root)['state'] == 'ready'
    assert projects.inspect(store, root)['source_refs'] == []
    assert len(store.list(literature.REQUEST)) == 2


def test_no_literature_permission_makes_no_network_request(tmp_path):
    store,root,transport=setup(tmp_path,lambda _:pytest.fail('network must not run'),limit=0)
    with pytest.raises(ValueError,match='budget exhausted'):search(store,root,transport)


@pytest.mark.parametrize('pmcid',['https://evil.example/','PMC123/../../x','PMC000','PMC999'])
def test_fetch_is_limited_to_open_access_ids_from_this_project_search(tmp_path,pmcid):
    store,root,transport=setup(tmp_path,lambda _:httpx.Response(200,json=search_body()))
    found=search(store,root,transport)
    with pytest.raises(ValueError):projects.operate(store,root,'fetch_literature',dict(search_ref=found['search_ref'],pmcid=pmcid),literature_transport=transport)
    assert not projects.inspect(store,root)['source_refs']


def test_known_http_error_has_a_confirmed_receipt_and_does_not_wedge_the_project(tmp_path):
    store,root,transport=setup(tmp_path,lambda _:httpx.Response(404,content=b'missing'))
    with pytest.raises(literature.LiteratureError):search(store,root,transport)
    assert projects.inspect(store,root)['state']=='ready'
    assert projects.inspect(store,root)['budget']['literature_requests']==1
    assert store.list(literature.REQUEST)


def test_unconfirmed_transport_failure_is_not_retried(tmp_path):
    """Semantic change (architecture-trial-27, GET-only): a failed read-only
    literature GET is now receipted as a confirmed rejected attempt instead of
    freezing the project, because the GET is a public read-only download that
    writes no project artifact.  The FAILED request itself is still never
    replayed automatically: the budget account shows the attempt was spent, and
    any later request is a NEW counted action.  The paid transcription POST
    keeps its unknown-effect freeze (test_transport_failure_freezes_without_any_replay)."""
    def fail(request):raise httpx.ConnectError('secret local configuration',request=request)
    store,root,transport=setup(tmp_path,fail)
    with pytest.raises(literature.LiteratureError):search(store,root,transport)
    assert projects.inspect(store,root)['state']=='ready'
    attempts,returns,pending=projects.history(store,root)
    assert pending==[]
    saved=returns[attempts[-1][0]][1]
    assert saved['state']=='rejected'
    assert 'secret local configuration' not in ''.join(p.read_text() for p in store.objects_dir.glob('*.json'))
    # the failed attempt consumed its bounded literature request
    assert projects.inspect(store,root)['budget']['literature_requests']==1
    # and the exception carries its own attempt-bound failure receipt
    receipts=store.list(literature.REQUEST)
    assert len(receipts)==1
    receipt=store.get(receipts[0])['payload']
    assert receipt['response_received'] is False and receipt['error_kind']=='ConnectError'
    ret_ref,saved=returns[attempts[-1][0]]
    assert receipts[0] in set(store.get(ret_ref)['refs'])   # attempt-bound, never orphaned


@pytest.mark.parametrize('raw',[
    b'<!DOCTYPE article [<!ENTITY x SYSTEM "file:///etc/passwd">]><article><p>&x;</p></article>',
    b'<article xmlns:xi="http://www.w3.org/2001/XInclude"><xi:include href="file:///etc/passwd"/></article>',
    b'<article/>',b'<article><p>broken',b'\xff'])
def test_unsupported_xml_retains_receipt_without_promoting_a_source(tmp_path,raw):
    def handler(req):return httpx.Response(200,json=search_body()) if req.url.path.endswith('/search') else httpx.Response(200,content=raw)
    store,root,transport=setup(tmp_path,handler);found=search(store,root,transport)
    with pytest.raises(literature.LiteratureError):
        projects.operate(store,root,'fetch_literature',dict(search_ref=found['search_ref'],pmcid='PMC123'),literature_transport=transport)
    assert projects.inspect(store,root)['state']=='ready' and not projects.inspect(store,root)['source_refs']



def test_large_full_text_above_twenty_thousand_nodes_imports_with_raw_bytes(tmp_path):
    raw=('<article><front><article-title>Large structural control</article-title></front>'
         '<body><sec><title>Methods</title><p>'+('<italic>x</italic>'*25000)+
         '</p></sec></body></article>').encode()
    def handler(request):
        return httpx.Response(200,json=search_body()) if request.url.path.endswith('/search') else httpx.Response(200,content=raw)
    store,root,transport=setup(tmp_path,handler)
    found=search(store,root,transport)
    imported=projects.operate(store,root,'fetch_literature',dict(search_ref=found['search_ref'],pmcid='PMC123'),literature_transport=transport)
    assert store.get_blob(store.get(imported['source_ref'])['payload']['blob_ref'])==raw


def test_full_text_over_structural_budget_retains_receipt(tmp_path):
    raw=b'<article>'+b'<x/>'*200000+b'</article>'
    def handler(request):
        return httpx.Response(200,json=search_body()) if request.url.path.endswith('/search') else httpx.Response(200,content=raw)
    store,root,transport=setup(tmp_path,handler)
    found=search(store,root,transport)
    with pytest.raises(literature.LiteratureError) as error:
        projects.operate(store,root,'fetch_literature',dict(search_ref=found['search_ref'],pmcid='PMC123'),literature_transport=transport)
    assert error.value.receipt_ref and not projects.inspect(store,root)['source_refs']
    assert projects.inspect(store,root)['state']=='ready'


def publication_response(source_format, pmcid='PMC123', pmid='123', doi='10.1234/expected'):
    if source_format == 'jats':
        return (f'<article><front><article-meta><article-id pub-id-type="pmcid">{pmcid}</article-id>'
                f'<article-id pub-id-type="pmid">{pmid}</article-id>'
                f'<article-id pub-id-type="doi">{doi}</article-id></article-meta></front>'
                '<body><sec><title>Methods</title><p>Measured cells.</p></sec>'
                '<sec><title>Results</title><p>Cell counts increased.</p></sec></body></article>').encode()
    return (f'<html><head><meta name="citation_pmid" content="{pmid}">'
            f'<meta name="citation_doi" content="{doi}">'
            '<meta name="citation_fulltext_html_url" '
            f'content="https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"></head><body><article>'
            '<h1>Experimental study</h1><section class="body"><section><h2>Methods</h2>'
            '<p>Measured cells.</p></section><section><h2>Results</h2>'
            '<p>Cell counts increased.</p></section></section></article></body></html>').encode()


@pytest.mark.parametrize('source_format', ['jats', 'pmc_html'])
@pytest.mark.parametrize('field,value', [('pmcid', 'PMC999'), ('pmid', '999'), ('doi', '10.1234/other')])
def test_full_text_publication_conflict_keeps_raw_receipt_without_source(tmp_path, source_format, field, value):
    raw = publication_response(source_format, **{field: value})
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith('/search'):
            body = search_body()
            body['resultList']['result'][0]['doi'] = '10.1234/expected'
            return httpx.Response(200, json=body)
        return httpx.Response(200, content=raw)

    store, project, transport = setup(tmp_path, handler)
    found = search(store, project, transport)
    with pytest.raises(literature.LiteratureError) as error:
        projects.operate(store, project, 'fetch_literature',
                         dict(search_ref=found['search_ref'], pmcid='PMC123', format=source_format),
                         literature_transport=transport)
    assert error.value.receipt_ref
    receipt = store.get(error.value.receipt_ref)['payload']
    assert store.get_blob(receipt['blob_ref']) == raw
    assert projects.inspect(store, project)['state'] == 'ready'
    assert projects.inspect(store, project)['source_refs'] == []
    assert projects.inspect(store, project)['budget']['literature_requests'] == 2
    assert len(calls) == 2


@pytest.mark.parametrize('source_format', ['jats', 'pmc_html'])
def test_matching_publication_identifiers_admit_full_text(tmp_path, source_format):
    raw = publication_response(source_format)

    def handler(request):
        if request.url.path.endswith('/search'):
            body = search_body()
            body['resultList']['result'][0]['doi'] = '10.1234/expected'
            return httpx.Response(200, json=body)
        return httpx.Response(200, content=raw)

    store, project, transport = setup(tmp_path, handler)
    found = search(store, project, transport)
    fetched = projects.operate(store, project, 'fetch_literature',
        dict(search_ref=found['search_ref'], pmcid='PMC123', format=source_format),
        literature_transport=transport)
    assert store.get_blob(store.get(fetched['source_ref'])['payload']['blob_ref']) == raw
    assert fetched['source_ref'] in projects.inspect(store, project)['source_refs']


@pytest.mark.parametrize('source_format', ['jats', 'pmc_html'])
@pytest.mark.parametrize('conflicting', [False, True])
def test_duplicate_full_text_identifiers_distinguish_repetition_and_conflict(tmp_path, source_format, conflicting):
    raw = publication_response(source_format).decode()
    repeated = '10.1234/other' if conflicting else '10.1234/expected'
    if source_format == 'jats':
        raw = raw.replace('</article-meta>', f'<article-id pub-id-type="doi">{repeated}</article-id></article-meta>')
    else:
        raw = raw.replace('</head>', f'<meta name="citation_doi" content="{repeated}"></head>')
    raw = raw.encode()

    def handler(request):
        if request.url.path.endswith('/search'):
            body = search_body()
            body['resultList']['result'][0]['doi'] = 'https://doi.org/10.1234/EXPECTED'
            return httpx.Response(200, json=body)
        return httpx.Response(200, content=raw)

    store, project, transport = setup(tmp_path, handler)
    found = search(store, project, transport)
    args = dict(search_ref=found['search_ref'], pmcid='PMC123', format=source_format)
    if conflicting:
        with pytest.raises(literature.LiteratureError) as error:
            projects.operate(store, project, 'fetch_literature', args, literature_transport=transport)
        receipt = store.get(error.value.receipt_ref)['payload']
        assert store.get_blob(receipt['blob_ref']) == raw
        assert projects.inspect(store, project)['source_refs'] == []
    else:
        fetched = projects.operate(store, project, 'fetch_literature', args, literature_transport=transport)
        assert store.get_blob(store.get(fetched['source_ref'])['payload']['blob_ref']) == raw
    assert projects.inspect(store, project)['state'] == 'ready'


# ---------------------------------------------------------------------------
# Segment-16 completeness regressions (root-cause family of the Gonze2013
# silent-loss defect: the gap report itself must never silently lie).

def many_formula_jats(count):
    items = ['<disp-formula id="e%d"><graphic xmlns:xlink="http://www.w3.org/1999/xlink" '
             'xlink:href="art.e%03d.jpg"><?cloudpmc-path blobs/x/123/aaa/art.e%03d.jpg?>'
             '</graphic></disp-formula>' % (i, i, i) for i in range(1, count + 1)]
    return ('<article><front><article-meta><article-id pub-id-type="pmcid">PMC123</article-id>'
            '</article-meta></front><body><sec><title>R</title><p>x</p>%s</sec></body></article>'
            % ''.join(items)).encode()


def test_more_gaps_than_the_record_bound_report_true_total_and_say_truncated(tmp_path):
    """Audit fix: the old code dropped records past 256 SILENTLY, so the page
    carried 300 markers while the count claimed 256 and assets e257+ were
    undiscoverable — the same completeness root cause that hid the Fig-2B
    parameter values."""
    raw = many_formula_jats(300)
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith('/search'):
            return httpx.Response(200, json=search_body())
        if request.url.host == 'www.ebi.ac.uk':
            return httpx.Response(200, content=raw)
        return httpx.Response(200, content=JPEG)

    store, root, transport = setup(tmp_path, handler, limit=5)
    found = search(store, root, transport)
    fetched = projects.operate(store, root, 'fetch_literature',
                               dict(search_ref=found['search_ref'], pmcid='PMC123'),
                               literature_transport=transport)
    src = store.get(fetched['source_ref'])['payload']
    page = src['pages'][0]['text']
    assert page.count('[unreadable-formula:') == 300
    assert src['unresolved_formula_count'] == 300            # true total, never capped
    assert src['unresolved_formula_records'] == literature.MAX_UNRESOLVED_RECORDS
    assert src['unresolved_formula_truncated'] is True        # truncation is explicit
    assert any('truncated to %d of' % literature.MAX_UNRESOLVED_RECORDS in w
               for w in src['warnings'])
    # Assets beyond the bounded record list stay discoverable through the
    # authoritative text markers and remain retrievable via the official route.
    result = projects.operate(store, root, 'fetch_literature_asset',
                              dict(source_ref=fetched['source_ref'], asset='art.e300.jpg'),
                              literature_transport=transport)
    assert store.get_blob(store.get(result['asset_ref'])['payload']['blob_ref']) == JPEG
    with pytest.raises(ValueError):
        projects.operate(store, root, 'fetch_literature_asset',
                         dict(source_ref=fetched['source_ref'], asset='art.e999.jpg'),
                         literature_transport=transport)


def test_whitespace_only_formula_still_creates_a_visible_gap(tmp_path):
    """Audit fix: a whitespace text node is not content; the pretty-printed
    empty formula used to project as complete prose with no marker."""
    raw = (b'<article><body><sec><title>R</title><p>with constant <inline-formula id="w">'
           b'\n        </inline-formula> fixed</p></sec></body></article>')
    projection = literature.extract_projection(raw)
    assert projection['text'].count('[unreadable-formula:unidentified]') == 1
    assert projection['unresolved_formula_count'] == 1
    assert 'unreadable-formula' in projection['warnings'][0]


def test_readable_alternatives_prefer_text_and_keep_the_asset_unread_but_available(tmp_path):
    """Audit fix: a disp-formula whose <alternatives> offers MathML plus a
    graphic must project the readable text, must NOT count as a textless gap,
    and must keep the graphic discoverable as an UNREAD asset."""
    raw = (b'<article><front><article-meta><article-id pub-id-type="pmcid">PMC123</article-id>'
           b'</article-meta></front><body><sec><title>R</title>'
           b'<disp-formula id="a1"><label>(1)</label><alternatives>'
           b'<graphic xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="art.f001.jpg">'
           b'<?cloudpmc-path blobs/x/123/aaa/art.f001.jpg?></graphic>'
           b'<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi><mo>=</mo>'
           b'<mn>4</mn></math></alternatives></disp-formula></sec></body></article>')
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith('/search'):
            return httpx.Response(200, json=search_body())
        if request.url.host == 'www.ebi.ac.uk':
            return httpx.Response(200, content=raw)
        return httpx.Response(200, content=JPEG)

    store, root, transport = setup(tmp_path, handler, limit=5)
    found = search(store, root, transport)
    fetched = projects.operate(store, root, 'fetch_literature',
                               dict(search_ref=found['search_ref'], pmcid='PMC123'),
                               literature_transport=transport)
    src = store.get(fetched['source_ref'])['payload']
    page = src['pages'][0]['text']
    assert '(1)x=4' in page.replace(' ', '')
    assert 'unreadable-formula' not in page                 # not a fake textless hole
    assert src['unresolved_formula_count'] == 0
    assert [e['asset'] for e in src['formula_assets']] == ['art.f001.jpg']
    assert src['formula_assets'][0]['status'] == 'unread_asset_text_available'
    result = projects.operate(store, root, 'fetch_literature_asset',
                              dict(source_ref=fetched['source_ref'], asset='art.f001.jpg'),
                              literature_transport=transport)
    assert result['media_type'] == 'image/jpeg'


def test_asset_fetch_is_attachment_to_existing_source_not_new_source_admission(tmp_path):
    """Audit fix: an asset attaches to an EXISTING source, so the 32-new-source
    project gate must not block it; the literature budget remains the limit."""
    requests = []
    store, root, transport = setup(tmp_path, fig2b_transport(requests), limit=4)
    found = search(store, root, transport)
    fetched = projects.operate(store, root, 'fetch_literature',
                               dict(search_ref=found['search_ref'], pmcid='PMC123'),
                               literature_transport=transport)
    for i in range(31):  # bring the project to its 32-source admission limit
        extra = store.put('source', dict(blob_ref='unused', filename='s%d.txt' % i,
                                         parser='utf8', parser_version='1', url=None,
                                         pages=[dict(number=1, text='source %d' % i)],
                                         warnings=[], project_ref=root),
                                         refs=(root,))
        projects.operate(store, root, 'admit_source',
                         dict(source_ref=extra, reason='audit filler %d' % i), actor='operator')
    assert len(projects.inspect(store, root)['source_refs']) == 32
    result = projects.operate(store, root, 'fetch_literature_asset',
                              dict(source_ref=fetched['source_ref'], asset='journal.e002.jpg'),
                              literature_transport=transport)
    assert result['media_type'] == 'image/jpeg'
    # But a genuinely NEW source is still refused at the admission gate.
    assert projects.inspect(store, root)['budget']['literature_requests'] == 3


# ---------------------------------------------------------------------------
# Segment 18: bounded, planner-visible asset transcription through the normal
# operation layer (gap -> asset -> agent-assessed transcription -> decision).
# ---------------------------------------------------------------------------

from biomodel_agent.llm import ChatProvider, ProviderError  # noqa: E402


class FakeVisionProvider:
    """Provider double exposing the bounded content channel only."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.base_url = 'https://vision.invalid/v1'
        self.model = 'planner-chat-profile'
        self.last_usage = {}
        self.last_response_diagnostic = {}
        self.last_model_content = None

    def respond_content(self, messages, *, model=None, max_tokens=None):
        self.calls.append(dict(messages=messages, model=model, max_tokens=max_tokens))
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            self.last_usage = dict(prompt_tokens=12, completion_tokens=0, total_tokens=12)
            if getattr(reply, 'kind', '') == 'http':
                self.last_response_diagnostic = {'http_status': 503}
            raise reply
        self.last_usage = dict(prompt_tokens=210, completion_tokens=18, total_tokens=228)
        self.last_response_diagnostic = {'finish_reason': 'stop', 'content_characters': len(reply)}
        self.last_model_content = reply
        return reply


def transcept_project(tmp_path, policy=None):
    """Project with one fetched Fig-2B-style asset (search -> fetch -> asset)."""
    requests = []
    store = ArtifactStore(tmp_path / 's')
    config = dict(scenario='Reproduce the stated figure target.',
                  policy=dict(max_literature_requests=4, **(policy or {})))
    root = projects.create(store, config)
    transport = httpx.MockTransport(fig2b_transport(requests))
    found = search(store, root, transport)
    fetched = projects.operate(store, root, 'fetch_literature',
                               dict(search_ref=found['search_ref'], pmcid='PMC123'),
                               literature_transport=transport)
    result = projects.operate(store, root, 'fetch_literature_asset',
                              dict(source_ref=fetched['source_ref'], asset='journal.e002.jpg'),
                              literature_transport=transport)
    return store, root, fetched['source_ref'], result['asset_ref'], requests


def test_transcription_binds_asset_blob_and_returns_content_to_the_agent(tmp_path):
    store, root, source_ref, asset_ref, _ = transcept_project(
        tmp_path, dict(max_transcription_calls=2))
    provider = FakeVisionProvider(['k1=1 (arbitrary units)'])
    result = projects.operate(store, root, 'transcribe_literature_asset',
                              dict(asset_ref=asset_ref), provider=provider)
    # The verified minimal message form [image_url, text(OCR)] on the
    # policy-configured vision profile, NOT the planner chat model.
    assert provider.calls[0]['model'] == 'unlimited-ocr'
    parts = provider.calls[0]['messages'][0]['content']
    assert [part['type'] for part in parts] == ['image_url', 'text']
    assert parts[1]['text'] == 'OCR'
    import base64 as _b64
    assert _b64.b64decode(parts[0]['image_url']['url'].split(';base64,', 1)[1]) == JPEG
    assert result['status'] == 'transcribed' and result['transcription'] == 'k1=1 (arbitrary units)'
    assert result['usage'] == dict(prompt_tokens=210, completion_tokens=18, total_tokens=228)
    record = store.get(result['transcription_ref'])['payload']
    asset = store.get(asset_ref)['payload']
    assert (record['asset_ref'], record['source_ref'], record['blob_ref']) == \
        (asset_ref, source_ref, asset['blob_ref'])
    assert record['blob_ref'] == result['blob_sha256']       # content-addressed binding
    assert record['status'] == 'transcribed' and record['message_form'] == literature.VISION_MESSAGE_FORM
    assert record['profile']['model'] == 'unlimited-ocr' and record['profile']['planner_model'] == 'planner-chat-profile'
    assert record['output_authority'] == 'agent_assessed_visual_transcription_not_independent_verification'
    budget = projects.inspect(store, root)['budget']
    assert budget['transcription_calls'] == 1 and budget['literature_requests'] == 3
    assert projects.inspect(store, root)['state'] == 'ready'


def test_transcription_model_profile_is_configurable(tmp_path):
    store, root, _, asset_ref, _ = transcept_project(
        tmp_path, dict(max_transcription_calls=2, transcription_model='other-vision-x'))
    provider = FakeVisionProvider(['n=10'])
    projects.operate(store, root, 'transcribe_literature_asset',
                     dict(asset_ref=asset_ref), provider=provider)
    assert provider.calls[0]['model'] == 'other-vision-x'


def test_transcription_requires_project_owned_fetched_asset(tmp_path):
    store, root, _, asset_ref, _ = transcept_project(
        tmp_path, dict(max_transcription_calls=4))
    provider = FakeVisionProvider(['should not be called'])
    # A second project in the SAME store may not transcribe the first
    # project's asset: ownership is bound at the ASSET record.
    other = projects.create(store, dict(scenario='Unrelated project.',
                                        policy=dict(max_transcription_calls=2)))
    for bad in (other, 'f' * 64):
        with pytest.raises(ValueError):
            projects.operate(store, root, 'transcribe_literature_asset',
                             dict(asset_ref=bad), provider=provider)
    with pytest.raises(ValueError, match='another project'):
        projects.operate(store, other, 'transcribe_literature_asset',
                         dict(asset_ref=asset_ref), provider=provider)
    assert provider.calls == []  # attribution rejected before any provider request


def test_capability_unavailable_returns_actionable_branch(tmp_path):
    store, root, _, asset_ref, _ = transcept_project(
        tmp_path, dict(max_transcription_calls=2))
    with pytest.raises(ValueError, match='transcription capability unavailable'):
        projects.operate(store, root, 'transcribe_literature_asset',
                         dict(asset_ref=asset_ref), provider=None)
    # Rejected with guidance; the project stays usable for the gap decision.
    assert projects.inspect(store, root)['state'] == 'ready'
    attempts, returns, _ = projects.history(store, root)
    last = returns[attempts[-1][0]][1]
    assert last['state'] == 'rejected'
    assert 'never invent formula content' in last['diagnostic']['message']


def test_empty_visual_output_returns_unresolved_status_to_planner(tmp_path):
    store, root, _, asset_ref, _ = transcept_project(
        tmp_path, dict(max_transcription_calls=2))
    provider = FakeVisionProvider([''])
    result = projects.operate(store, root, 'transcribe_literature_asset',
                              dict(asset_ref=asset_ref), provider=provider)
    assert result['status'] == 'unresolved_empty_output'
    assert store.get(result['transcription_ref'])['payload']['status'] == 'unresolved_empty_output'
    assert projects.inspect(store, root)['state'] == 'ready'


def test_http_error_is_receipted_rejected_without_replay(tmp_path):
    store, root, _, asset_ref, _ = transcept_project(
        tmp_path, dict(max_transcription_calls=2))
    provider = FakeVisionProvider([ProviderError('HTTP 503 from provider', kind='http')])
    with pytest.raises(ProviderError):
        projects.operate(store, root, 'transcribe_literature_asset',
                         dict(asset_ref=asset_ref), provider=provider)
    attempts, returns, _ = projects.history(store, root)
    last = returns[attempts[-1][0]][1]
    assert last['state'] == 'rejected'          # response received: effect confirmed
    assert projects.inspect(store, root)['state'] == 'ready'
    receipts = store.list(literature.TRANSCRIPTION_REQUEST)
    assert len(receipts) == 1
    payload = store.get(receipts[0])['payload']
    assert payload['http_status'] == 503 and payload['response_received'] is True
    assert payload['asset_ref'] == asset_ref and payload['usage']['total_tokens'] == 12


def test_transport_failure_freezes_without_any_replay(tmp_path):
    store, root, _, asset_ref, _ = transcept_project(
        tmp_path, dict(max_transcription_calls=2))
    provider = FakeVisionProvider([ProviderError('transport failure: ConnectTimeout', kind='transport')])
    with pytest.raises(ProviderError):
        projects.operate(store, root, 'transcribe_literature_asset',
                         dict(asset_ref=asset_ref), provider=provider)
    view = projects.inspect(store, root)
    assert view['state'] == 'needs_recovery' and view['unresolved_operation_refs']
    assert store.list(literature.TRANSCRIPTION) == []
    pending = store.list(literature.TRANSCRIPTION_REQUEST)
    assert len(pending) == 1
    assert store.get(pending[0])['payload']['response_received'] is False


def test_transcription_budget_defaults_closed_and_is_enforced(tmp_path):
    # Old/legacy configuration without the new policy keys keeps the default:
    # transcription stays closed with an explicit actionable reason and no
    # provider request, exactly like the old zero literature budget behaviour.
    store, root, _, asset_ref, _ = transcept_project(tmp_path)
    provider = FakeVisionProvider(['must not be called'])
    with pytest.raises(ValueError, match='transcription call budget exhausted'):
        projects.operate(store, root, 'transcribe_literature_asset',
                         dict(asset_ref=asset_ref), provider=provider)
    assert provider.calls == []
    assert projects.inspect(store, root)['state'] == 'ready'
    # A configured budget allows exactly that many attempts, then blocks again.
    store, root, _, asset_ref, _ = transcept_project(tmp_path / 'p2', dict(max_transcription_calls=1))
    provider = FakeVisionProvider(['k2=0.1', 'also'])
    projects.operate(store, root, 'transcribe_literature_asset',
                     dict(asset_ref=asset_ref), provider=provider)
    with pytest.raises(ValueError, match='transcription call budget exhausted'):
        projects.operate(store, root, 'transcribe_literature_asset',
                         dict(asset_ref=asset_ref), provider=provider)
    assert len(provider.calls) == 1


def test_respond_content_uses_bounded_multimodal_form_without_action_prompt(monkeypatch):
    monkeypatch.setenv('BIOMODEL_API_KEY', 'test-key')
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=dict(
            choices=[{'message': {'content': 'Ki=1'}, 'finish_reason': 'stop'}],
            usage=dict(prompt_tokens=9, completion_tokens=2, total_tokens=11)))

    provider = ChatProvider('https://provider.invalid/v1', 'planner-model',
                            transport=httpx.MockTransport(handler))
    content = provider.respond_content(
        [dict(role='user', content=[dict(type='image_url',
                                         image_url={'url': 'data:image/png;base64,aW1n'}),
                                    dict(type='text', text='OCR')])],
        model='vision-x', max_tokens=100)
    assert content == 'Ki=1' and provider.last_usage['total_tokens'] == 11
    body = seen[0]
    assert body['model'] == 'vision-x' and body['max_tokens'] == 100
    assert [message['role'] for message in body['messages']] == ['user']  # no action-schema prompt
    with pytest.raises(ProviderError):
        provider.respond_content([dict(role='user', content=[dict(
            type='image_url', image_url={'url': 'https://remote.invalid/x.png'})])])
    with pytest.raises(ProviderError):
        provider.respond_content([dict(role='user', content=[dict(type='text', text='x' * 3000)])])
    with pytest.raises(ProviderError):
        provider.respond_content([dict(role='user', content=[dict(type='text', text='OCR')])],
                                 max_tokens=5000)


# ---------------------------------------------------------------------------
# Segment 19: finish-reason honesty, responded-vs-unresponded failure receipts
# with no orphan diagnostics, and legacy project policy compatibility without
# any migration or relaxed gate.

from copy import deepcopy  # noqa: E402
from biomodel_agent import recovery  # noqa: E402


def chat_provider(handler, monkeypatch, seen=None):
    monkeypatch.setenv('TEST_VISION_KEY', 'unit-vision-secret')
    def recording(request):
        if seen is not None:
            seen.append(request)
        return handler(request)
    return ChatProvider('https://provider.invalid/v1', 'planner-chat-profile',
                        api_key_env='TEST_VISION_KEY', transport=httpx.MockTransport(recording))


def completion(content, finish_reason, usage=None):
    return httpx.Response(200, json=dict(
        choices=[{'message': {'content': content}, 'finish_reason': finish_reason}],
        usage=usage or dict(prompt_tokens=300, completion_tokens=25, total_tokens=325)))


@pytest.mark.parametrize('finish_reason,expected_status', [
    ('stop', 'transcribed'),
    ('length', 'partial_truncated_output'),
    ('content_filter', 'unresolved_incomplete_output'),
])
def test_finish_reason_gates_whether_the_reply_is_a_complete_formula(tmp_path, monkeypatch,
                                                                     finish_reason, expected_status):
    """Audit fix: any non-empty string used to become status=transcribed.  A
    reply cut at the bounded output-token limit or stopped by a filter must
    reach the planner as partial/unresolved with its real finish_reason."""
    store, root, _, asset_ref, _ = transcept_project(tmp_path, dict(max_transcription_calls=2))
    provider = chat_provider(lambda request: completion('k1=0.5 (a.u.), k2=', finish_reason),
                             monkeypatch)
    result = projects.operate(store, root, 'transcribe_literature_asset',
                              dict(asset_ref=asset_ref), provider=provider)
    assert result['status'] == expected_status
    assert result['finish_reason'] == finish_reason
    assert result['output_token_limit'] == literature.TRANSCRIPTION_OUTPUT_TOKEN_LIMIT
    assert result['transcription'] == 'k1=0.5 (a.u.), k2='   # raw reply preserved for the agent
    if finish_reason == 'length':
        assert 'PARTIAL' in result['notice']
    elif finish_reason != 'stop':
        assert 'UNRESOLVED' in result['notice']
    record = store.get(result['transcription_ref'])['payload']
    assert record['status'] == expected_status
    assert record['finish_reason'] == finish_reason
    assert record['raw_output'] == 'k1=0.5 (a.u.), k2='
    # agent-assessed / independent verification stays pending in every case
    assert record['output_authority'] == 'agent_assessed_visual_transcription_not_independent_verification'
    # The status and body are visible through the normal project history
    # (this is what the supervisor context replays to the planner).
    attempts, returns, _ = projects.history(store, root)
    saved = returns[attempts[-1][0]][1]
    assert saved['state'] == 'returned' and saved['result']['status'] == expected_status
    assert projects.inspect(store, root)['state'] == 'ready'
    assert projects.inspect(store, root)['budget']['transcription_calls'] == 1


def test_http200_protocol_failures_are_confirmed_response_not_unknown(tmp_path, monkeypatch):
    """Audit fix: after HTTP 200, a malformed body or invalid choice used to
    lose last_response_diagnostic.http_status, so the received (possibly
    billed) response looked like an unresponded transport failure."""
    bodies = [('<!doctype html>GARBAGE-BODY-WALL',
               lambda request: httpx.Response(200, text='<!doctype html>GARBAGE-BODY-WALL')),
              (None, lambda request: httpx.Response(200, json=dict(choices=[]))),
              (None, lambda request: httpx.Response(200, json=dict(
                   choices=[{'message': {'content': None}, 'finish_reason': 'stop'}])))]
    store, root, _, asset_ref, _ = transcept_project(tmp_path, dict(max_transcription_calls=4))
    calls = []
    for index, (_, handler) in enumerate(bodies):
        provider = chat_provider(handler, monkeypatch, seen=calls)
        with pytest.raises(ProviderError) as error:
            projects.operate(store, root, 'transcribe_literature_asset',
                             dict(asset_ref=asset_ref), provider=provider, key='bad-%d' % index)
        attempts, returns, pending = projects.history(store, root)
        assert pending == []                     # response received: not unknown
        ret_ref, saved = returns[attempts[-1][0]]
        assert saved['state'] == 'rejected'      # actionable planner branch, no freeze
        receipt_ref = error.value.receipt_ref
        receipt = store.get(receipt_ref)['payload']
        assert receipt['http_status'] == 200 and receipt['response_received'] is True
        assert receipt['error_kind'] == 'response_envelope' and receipt['asset_ref'] == asset_ref
        assert receipt_ref in store.get(ret_ref)['refs']    # never an orphan receipt
    assert projects.inspect(store, root)['state'] == 'ready'
    assert projects.inspect(store, root)['budget']['transcription_calls'] == 3
    raw = ''.join(path.read_text() for path in store.objects_dir.glob('*.json'))
    assert 'GARBAGE-BODY-WALL' not in raw and 'unit-vision-secret' not in raw


def test_http429_is_rejected_with_bound_receipt_and_counts_budget(tmp_path, monkeypatch):
    store, root, _, asset_ref, _ = transcept_project(tmp_path, dict(max_transcription_calls=2))
    calls = []
    provider = chat_provider(lambda request: httpx.Response(429, text='rate limited PRIVATE BODY',
                                                            headers={'Retry-After': '30'}),
                             monkeypatch, seen=calls)
    with pytest.raises(ProviderError):
        projects.operate(store, root, 'transcribe_literature_asset',
                         dict(asset_ref=asset_ref), provider=provider)
    attempts, returns, pending = projects.history(store, root)
    assert pending == [] and returns[attempts[-1][0]][1]['state'] == 'rejected'
    assert 'HTTP 429' in returns[attempts[-1][0]][1]['diagnostic']['message']
    receipt = store.list(literature.TRANSCRIPTION_REQUEST)
    assert len(receipt) == 1
    payload = store.get(receipt[0])['payload']
    assert payload['http_status'] == 429 and payload['response_received'] is True
    assert payload['error_kind'] == 'http' and payload['asset_ref'] == asset_ref
    # A received-error attempt still consumed its bounded call.
    assert projects.inspect(store, root)['budget']['transcription_calls'] == 1
    raw = ''.join(path.read_text() for path in store.objects_dir.glob('*.json'))
    assert 'PRIVATE BODY' not in raw and 'unit-vision-secret' not in raw


def test_unresponded_timeout_keeps_unknown_effect_with_bound_receipt(tmp_path, monkeypatch):
    """Audit fix: the timeout attempt created a TRANSCRIPTION_REQUEST that no
    return referenced (orphan diagnostic) because exc.receipt_ref was only set
    for confirmed responses.  The receipt must belong to THIS attempt while the
    effect stays unknown and never replays."""
    store, root, _, asset_ref, _ = transcept_project(tmp_path, dict(max_transcription_calls=2))
    calls = []

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout('private proxy configuration', request=request)

    provider = chat_provider(timeout, monkeypatch)
    with pytest.raises(ProviderError):
        projects.operate(store, root, 'transcribe_literature_asset',
                         dict(asset_ref=asset_ref), provider=provider)
    view = projects.inspect(store, root)
    assert view['state'] == 'needs_recovery' and view['unresolved_operation_refs']
    attempts, returns, pending = projects.history(store, root)
    assert pending == [attempts[-1][0]]
    ret_ref, saved = returns[attempts[-1][0]]
    assert saved['state'] == 'unknown'           # never falsely marked rejected
    receipts = store.list(literature.TRANSCRIPTION_REQUEST)
    assert len(receipts) == 1
    assert store.get(receipts[0])['payload']['response_received'] is False
    assert receipts[0] in store.get(ret_ref)['refs']    # attempt-bound, not orphaned
    # Unknown effect forbids automatic replay and cannot double-bill.
    with pytest.raises(ValueError, match='no automatic replay'):
        projects.operate(store, root, 'transcribe_literature_asset',
                         dict(asset_ref=asset_ref), provider=provider, key='second-try')
    assert len(calls) == 1


def test_failed_attempts_keep_consuming_the_transcription_budget(tmp_path, monkeypatch):
    """No duplicate paid attempts behind an exhausted budget: every received
    (rejected) attempt counts, and the gate rejects before any provider call."""
    store, root, _, asset_ref, _ = transcept_project(tmp_path, dict(max_transcription_calls=1))
    provider = chat_provider(lambda request: httpx.Response(500, text='server'), monkeypatch)
    with pytest.raises(ProviderError):
        projects.operate(store, root, 'transcribe_literature_asset',
                         dict(asset_ref=asset_ref), provider=provider)
    assert projects.inspect(store, root)['budget']['transcription_calls'] == 1
    calls = []
    blocked = chat_provider(lambda request: pytest.fail('provider must not be called'), monkeypatch,
                            seen=calls)
    with pytest.raises(ValueError, match='transcription call budget exhausted'):
        projects.operate(store, root, 'transcribe_literature_asset',
                         dict(asset_ref=asset_ref), provider=blocked, key='after-exhausted')
    assert calls == []


# ---- legacy policy compatibility (audit point 1) ---------------------------

LEGACY_POLICY = dict(planner_max_calls=0, max_iterations=3, max_native_calls=18,
                     native_calls_per_iteration=6, max_literature_requests=0,
                     context_max_chars=60000, assumptions='agent_scoped', total_timeout_s=None)


def legacy_project(store, fingerprint, policy=None):
    """Minimal pre-transcription-field project exactly as the older source
    version stored it: new optional fields simply do not exist."""
    payload = dict(version=1, id='a'*32, scenario='Read-only legacy research project.',
                   mode='application', source_refs=[], model_refs=[], reference_refs=[],
                   policy=dict(LEGACY_POLICY if policy is None else policy),
                   implementation_sha256=fingerprint, created_at=1763000000.0,
                   deadline_at=None, advice=None, scientific_support='unknown')
    return store.put(projects.PROJECT, payload)


def test_legacy_policy_without_new_fields_reads_and_reports_defaults_without_migration(tmp_path):
    store = ArtifactStore(tmp_path/'s')
    ref = legacy_project(store, recovery.implementation_fingerprint())
    saved = deepcopy(store.get(ref))
    p = projects.load(store, ref)                          # no 'project policy changed'
    assert p['policy'] == LEGACY_POLICY                    # stored shape validated as-is
    view = projects.inspect(store, ref)                    # normal diagnostic entry works
    assert view['budget']['max_transcription_calls'] == 0  # DEFAULTS live in the READ view only
    assert view['budget']['transcription_calls'] == 0
    assert store.get(ref) == saved                         # payload never rewritten/migrated
    # The default read view keeps transcription closed for a legacy policy,
    # and reading never rewrites the stored project.
    assert projects.effective_policy(store.get(ref)['payload']['policy'])['max_transcription_calls'] == 0
    assert store.get(ref) == saved


def test_historical_legacy_project_stays_read_only(tmp_path):
    """recover()/history(historical=True) can still read a project frozen by
    the older source version even though its policy predates the new fields,
    while the implementation fingerprint gate keeps it non-executable."""
    store = ArtifactStore(tmp_path/'s')
    old_fingerprint = '0'*64
    assert old_fingerprint != recovery.implementation_fingerprint()
    ref = legacy_project(store, old_fingerprint)
    with pytest.raises(ValueError, match='implementation changed'):
        projects.load(store, ref)
    with pytest.raises(ValueError, match='implementation changed'):
        projects.operate(store, ref, 'inspect_source',
                         dict(source_ref='f'*64, page=1, start=0, end=1))
    p = projects.load(store, ref, historical=True)         # receipt-adoption read path
    assert p['policy'] == LEGACY_POLICY
    attempts, returned, pending = projects.history(store, ref, historical=True)
    assert attempts == [] and returned == {} and pending == []


@pytest.mark.parametrize('mutation', ['unknown_key', 'invalid_new_value', 'invalid_new_model',
                                      'missing_old_key'])
def test_policy_gate_stays_strict_for_unknown_and_invalid_fields(tmp_path, mutation):
    store = ArtifactStore(tmp_path/'s')
    policy = dict(LEGACY_POLICY)
    if mutation == 'unknown_key':
        policy['max_transcription_calls'] = 2
        policy['transcription_model'] = 'vision-x'
        policy['surprise_field'] = True
    elif mutation == 'invalid_new_value':
        policy.update(max_transcription_calls='2', transcription_model='vision-x')
    elif mutation == 'invalid_new_model':
        policy.update(max_transcription_calls=2, transcription_model='')
    else:
        policy.pop('planner_max_calls')
    ref = legacy_project(store, recovery.implementation_fingerprint(), policy)
    with pytest.raises(ValueError):
        projects.load(store, ref)
    with pytest.raises(ValueError):
        projects.load(store, ref, historical=True)


def test_new_project_policy_still_round_trips_exactly(tmp_path):
    # The compatibility path must not weaken the normal gate: a project
    # created by this source version carries every field and must equal the
    # validated policy exactly (same behaviour as before the fix).
    store = ArtifactStore(tmp_path/'s')
    ref = projects.create(store, dict(scenario='Current-shape project.',
                                      policy=dict(max_transcription_calls=3,
                                                  transcription_model='vision-y')))
    p = projects.load(store, ref)
    assert projects.effective_policy(p['policy']) == p['policy']
    assert p['policy']['max_transcription_calls'] == 3 and p['policy']['transcription_model'] == 'vision-y'
    assert projects.inspect(store, ref)['budget']['max_transcription_calls'] == 3


# ---------------------------------------------------------------------------
# Segment 20: provenance-bound frame citation of transcription artifacts.
# Root cause of the segment-19 semantic-use failure: the frame contract had no
# way to cite the verified transcription receipt, so the planner was pushed to
# quote OCR text as literal page text and was (correctly) rejected.  The fix
# adds ONE new evidence kind whose fields are re-verified deterministically;
# the literal source-quote rule, the agent-assessed authority and the strict
# declaration-binding gate stay exactly as strict as before.

from biomodel_agent import boolean_proposals, research_schema  # noqa: E402


def fig2b_frame(evidence, decision='defer'):
    return dict(objective='Reproduce the stated figure target with disclosed provenance.',
                mode='reconstruction',
                granularity='Three-ODE oscillator at the reported parameter set.',
                modeling_targets=['Oscillation period near the stated 40 a.u.'],
                rationale='Formula images are transcribed artifacts, not literal page text.',
                evidence_needs=[dict(id='need_params', question='Figure parameter values.',
                                     kind='mathematics', queries=['Fig 2B parameter values'])],
                assumptions=[dict(id='param_assumption',
                                  statement='Transcribed parameter values guide the reconstruction.',
                                  rationale='Agent-assessed transcription bound to the official asset '
                                            'blob; independent review pending, scientific_support unknown.',
                                  evidence_ids=['marker_tx', 'tx_params'], decision=decision)],
                criteria=[dict(id='crit_period', kind='native_checks',
                               description='Simulated period computed from the declared parameters.',
                               required=True)],
                evidence=evidence,
                unresolved=['Transcription is agent-assessed; scientific_support unknown.'])


MARKER_QUOTE = ('parameter values (arbitrary units): '
                '[unreadable-formula:journal.e002.jpg]')


def test_frame_cites_transcription_provenance_without_laundering_its_text(tmp_path):
    store, root, source_ref, asset_ref, _ = transcept_project(
        tmp_path, dict(max_transcription_calls=2))
    provider = FakeVisionProvider(['Ki=1 (arbitrary units)'])
    tx = projects.operate(store, root, 'transcribe_literature_asset',
                          dict(asset_ref=asset_ref), provider=provider)
    evidence = [dict(id='marker_tx', kind='source', source_ref=source_ref, page=1, quote=MARKER_QUOTE),
                dict(id='tx_params', kind='transcription', transcription_ref=tx['transcription_ref'])]
    result = projects.operate(store, root, 'revise_frame',
                              dict(reason='Record the transcribed parameter with full provenance.',
                                   frame=fig2b_frame(evidence)))
    frame = store.get(result['frame_ref'])
    anchor = next(a for a in frame['payload']['spec']['evidence'] if a['id'] == 'tx_params')
    asset = store.get(asset_ref)['payload']
    assert anchor['kind'] == 'transcription'
    # every provenance field is copied from the VERIFIED receipt, not from the agent
    assert (anchor['transcription_ref'], anchor['source_ref'], anchor['asset_ref'], anchor['blob_ref']) \
        == (tx['transcription_ref'], source_ref, asset_ref, asset['blob_ref'])
    assert anchor['status'] == 'transcribed'
    assert anchor['finish_reason'] == 'stop'
    assert anchor['output_authority'] == 'agent_assessed_visual_transcription_not_independent_verification'
    # the transcribed text stays in the receipt: the frame cites provenance only
    assert 'Ki=1' not in json.dumps(frame['payload'])
    # same-project closure: frame -> transcription -> asset/blob/source
    assert tx['transcription_ref'] in frame['refs']
    tx_refs = set(store.get(tx['transcription_ref'])['refs'])
    assert {project_ref for project_ref in [root]} <= tx_refs and asset_ref in tx_refs
    assert asset['blob_ref'] in tx_refs and source_ref in tx_refs
    # the decision layer reuses the EXISTING assumption mechanism over the new anchor IDs
    assumption = frame['payload']['spec']['assumptions'][0]
    assert assumption['decision'] == 'defer' and set(assumption['evidence_ids']) == {'marker_tx', 'tx_params'}
    assert frame['payload']['scientific_support'] == 'unknown'


def test_ocr_text_submitted_as_source_quote_stays_rejected(tmp_path):
    """Segment-19 negative example kept as a regression: the literal page-quote
    rule is NOT relaxed for transcription content."""
    store, root, source_ref, asset_ref, _ = transcept_project(
        tmp_path, dict(max_transcription_calls=2))
    provider = FakeVisionProvider(['Ki=1 (arbitrary units)'])
    projects.operate(store, root, 'transcribe_literature_asset',
                     dict(asset_ref=asset_ref), provider=provider)
    evidence = [dict(id='tx_as_quote', kind='source', source_ref=source_ref, page=1,
                     quote='Ki=1 (arbitrary units)')]
    with pytest.raises(ValueError, match='citation quote is absent or repeated'):
        projects.operate(store, root, 'revise_frame',
                         dict(reason='Cite the transcription as page text.', frame=fig2b_frame(evidence)))
    view = projects.inspect(store, root)
    assert view['frame_ref'] is None          # nothing adopted
    assert view['state'] == 'ready'           # actionable rejection, project continues


def test_transcription_evidence_must_be_project_owned_and_real(tmp_path):
    store, root, _, asset_ref, _ = transcept_project(tmp_path, dict(max_transcription_calls=2))
    provider = FakeVisionProvider(['Ki=1'])
    tx = projects.operate(store, root, 'transcribe_literature_asset',
                          dict(asset_ref=asset_ref), provider=provider)
    other = projects.create(store, dict(scenario='Unrelated project.'))
    for bad_ref in ('f' * 64, tx['transcription_ref']):
        # the first is nonexistent, the second belongs to the FIRST project only
        evidence = [dict(id='tx_params', kind='transcription', transcription_ref=bad_ref)]
        with pytest.raises(ValueError):
            projects.operate(store, other, 'revise_frame',
                             dict(reason='Foreign citation attempt.', frame=fig2b_frame(evidence)))
    assert other not in [root] and store.get(tx['transcription_ref'])['payload']['project_ref'] == root


def test_non_semantic_output_recorded_and_rejected_not_adopted(tmp_path):
    """Honesty about OCR quality: a finish-stop reply that carries no scientific
    semantics (the retained e013-style junk) still comes back with its real
    status and CAN be recorded as a REJECTED assumption with its provenance -
    the negative example is expressible without inventing content."""
    store, root, source_ref, asset_ref, _ = transcept_project(tmp_path, dict(max_transcription_calls=2))
    provider = FakeVisionProvider([' text [0, 0, 999, 999]10'])
    tx = projects.operate(store, root, 'transcribe_literature_asset',
                          dict(asset_ref=asset_ref), provider=provider)
    assert tx['status'] == 'transcribed'      # honest about what the provider returned
    evidence = [dict(id='marker_tx', kind='source', source_ref=source_ref, page=1, quote=MARKER_QUOTE),
                dict(id='tx_params', kind='transcription', transcription_ref=tx['transcription_ref'])]
    result = projects.operate(store, root, 'revise_frame',
                              dict(reason='Junk output cannot ground a model.',
                                   frame=fig2b_frame(evidence, decision='reject')))
    frame = store.get(result['frame_ref'])['payload']
    assert frame['spec']['assumptions'][0]['decision'] == 'reject'
    anchor = next(a for a in frame['spec']['evidence'] if a['id'] == 'tx_params')
    assert anchor['status'] == 'transcribed'  # provenance keeps what was really returned
    assert frame['spec']['unresolved']


def test_revise_frame_schema_offers_transcription_only_after_a_receipt(tmp_path):
    store, root, _, asset_ref, _ = transcept_project(tmp_path, dict(max_transcription_calls=2))

    def branches(view):
        revise = research_schema.schemas(view)['revise_frame']
        return revise['properties']['args']['properties']['frame']['properties']['evidence']['items']['oneOf']

    view = projects.inspect(store, root)
    assert view['transcription_refs'] == []
    assert [branch['properties']['kind']['const'] for branch in branches(view)] == ['source', 'goal']
    provider = FakeVisionProvider(['Ki=1'])
    tx = projects.operate(store, root, 'transcribe_literature_asset',
                          dict(asset_ref=asset_ref), provider=provider)
    view = projects.inspect(store, root)
    assert view['transcription_refs'] == [tx['transcription_ref']]
    option = branches(view)[2]
    assert option['properties']['kind'] == {'const': 'transcription'}
    assert option['properties']['transcription_ref']['enum'] == [tx['transcription_ref']]


def test_transcription_anchor_can_never_satisfy_a_strict_declaration_binding(tmp_path):
    """The strict literal-declaration gate keeps its guarantee after the fix:
    OCR output, however plausible, is not a declaration source."""
    store, root, source_ref, asset_ref, _ = transcept_project(tmp_path, dict(max_transcription_calls=2))
    provider = FakeVisionProvider(['Initial A = True.'])
    tx = projects.operate(store, root, 'transcribe_literature_asset',
                          dict(asset_ref=asset_ref), provider=provider)
    evidence = [dict(id='marker_tx', kind='source', source_ref=source_ref, page=1, quote=MARKER_QUOTE),
                dict(id='tx_params', kind='transcription', transcription_ref=tx['transcription_ref'])]
    result = projects.operate(store, root, 'revise_frame',
                              dict(reason='Try the transcription as declaration evidence.',
                                   frame=fig2b_frame(evidence)))
    anchors = store.get(result['frame_ref'])['payload']['spec']['evidence']
    model = dict(schema_version=1, model_type='boolean', id='net', units=dict(time='sweep'),
                 nodes=[dict(id='A', initial=True, rule='not A')], update='synchronous', clamps={})
    evidence_map = {target: ['tx_params'] for target in
                    ('A/initial', 'A/rule', 'update', 'time_unit', 'clamps')}
    check = boolean_proposals.inspect(store, model, anchors, evidence_map, [])
    assert check['state'] == 'blocked'


def test_transcription_text_stays_reachable_in_later_planner_states(tmp_path):
    """Segment-22 regression: a frame can cite the transcription receipt, but
    the metered vision call is never replayed for its text, and the bounded
    tool result ages out of the compacted request packet. The retained latest
    state must project the receipted text itself (within the tool's own
    bounded window) with ref, status and authority, so the next planner
    decision can read exactly what was returned without a new call."""
    from biomodel_agent import research_supervisor as supervisor
    store, root, _, asset_ref, _ = transcept_project(tmp_path, dict(max_transcription_calls=1))
    provider = FakeVisionProvider(['K_{i}=1, n=2.5'])
    tx = projects.operate(store, root, 'transcribe_literature_asset',
                          dict(asset_ref=asset_ref), provider=provider)
    assert len(provider.calls) == 1
    state = json.loads(supervisor._messages(store, root)[-1]['content'])
    rows = state['transcriptions']
    assert len(rows) == 1
    row = rows[0]
    assert row['transcription_ref'] == tx['transcription_ref']
    assert row['transcription'] == 'K_{i}=1, n=2.5'
    assert row['status'] == tx['status'] and row['finish_reason'] == tx['finish_reason']
    assert row['output_authority'] == 'agent_assessed_visual_transcription_not_independent_verification'
    assert row['transcription_projection_truncated'] is False
    assert 'Agent-assessed' in state['transcriptions_notice']
    # the projection never re-bills: no further vision call happened
    assert len(provider.calls) == 1


# ---------------------------------------------------------------------------
# Segment 23: general reachability of retained transcription texts.
# The segment-22 latest-state projection windows the retained texts (latest
# four rows inside a fixed total text budget), so once more receipts exist
# than the window holds, an older PAID-FOR transcription would otherwise be
# metadata-only with no normal way back. inspect_transcription is the minimal
# pagination reusing the existing inspect-operation mechanism: one normal,
# bounded, zero-budget project operation returns the exact retained text of
# any project-owned receipt and never replays the paid vision call. Agent-
# assessed authority, the literal source-quote rule and every strict
# declaration gate stay exactly as strict as before.


def test_older_evicted_transcription_text_is_reachable_by_normal_operation(tmp_path):
    from biomodel_agent import research_supervisor as supervisor
    store, root, _, asset_ref, _ = transcept_project(
        tmp_path / 'ev', dict(max_transcription_calls=5))
    replies = ['ki_1=1 (a.u.)', 'ki_2=2 (a.u.)', 'ki_3=3 (a.u.)', 'ki_4=4 (a.u.)', 'ki_5=5 (a.u.)']
    provider = FakeVisionProvider(list(replies))
    refs = []
    for _ in replies:
        result = projects.operate(store, root, 'transcribe_literature_asset',
                                  dict(asset_ref=asset_ref), provider=provider)
        refs.append(result['transcription_ref'])
    assert len(provider.calls) == 5
    assert projects.inspect(store, root)['transcription_refs'] == refs
    state = json.loads(supervisor._messages(store, root)[-1]['content'])
    rows = state['transcriptions']
    assert [row['transcription_ref'] for row in rows] == refs      # oldest first
    # Only the newest four rows carry text in the projection; the evicted
    # oldest row keeps metadata plus an explicit truncation marker: nothing
    # paid for is silently dropped or replaced by a fabricated summary.
    assert 'transcription' not in rows[0]
    assert rows[0]['transcription_projection_truncated'] is True
    assert rows[0]['status'] == 'transcribed'
    assert rows[0]['output_characters'] == len(replies[0])
    assert rows[0]['output_authority'] == 'agent_assessed_visual_transcription_not_independent_verification'
    for row, reply in zip(rows[1:], replies[1:]):
        assert row['transcription'] == reply
        assert row['transcription_projection_truncated'] is False
    # The notice teaches the retrieval route for truncated rows.
    assert 'inspect_transcription' in state['transcriptions_notice']
    # A normal planner operation returns the exact evicted text with its real
    # recorded status, without replaying any paid vision call.
    result = projects.operate(store, root, 'inspect_transcription',
                              dict(transcription_ref=refs[0]))
    assert result['transcription'] == replies[0]
    assert result['status'] == 'transcribed' and result['finish_reason'] == 'stop'
    assert result['output_authority'] == 'agent_assessed_visual_transcription_not_independent_verification'
    assert result['next_start'] is None
    assert result['source_ref'] in projects.inspect(store, root)['source_refs']
    assert len(provider.calls) == 5
    budget = projects.inspect(store, root)['budget']
    assert budget['transcription_calls'] == 5 and budget['literature_requests'] == 3
    assert budget['planner_calls'] == 0
    assert projects.inspect(store, root)['state'] == 'ready'


def test_inspect_transcription_pages_bounds_and_ownership(tmp_path):
    store, root, _, asset_ref, _ = transcept_project(
        tmp_path / 'page', dict(max_transcription_calls=2))
    long_reply = 'A' * 8000 + 'B' * 4000
    provider = FakeVisionProvider([long_reply, 'C' * 70000])
    first = projects.operate(store, root, 'transcribe_literature_asset',
                             dict(asset_ref=asset_ref), provider=provider)
    page = projects.operate(store, root, 'inspect_transcription',
                            dict(transcription_ref=first['transcription_ref']))
    assert page['start'] == 0 and page['end'] == 8000 and page['transcription'] == 'A' * 8000
    assert page['next_start'] == 8000 and page['stored_characters'] == 12000
    assert page['output_characters'] == 12000 and page['output_truncated'] is False
    second = projects.operate(store, root, 'inspect_transcription',
                              dict(transcription_ref=first['transcription_ref'], start=8000))
    assert second['transcription'] == 'B' * 4000 and second['next_start'] is None
    with pytest.raises(ValueError, match='transcription read exceeds 8000 characters'):
        projects.operate(store, root, 'inspect_transcription',
                         dict(transcription_ref=first['transcription_ref'], start=0, end=8001))
    # The retained receipt itself has a storage bound (64000 characters); the
    # read exposes the stored length and the recorded truncation honestly
    # instead of pretending the full reply is stored.
    next_tx = projects.operate(store, root, 'transcribe_literature_asset',
                               dict(asset_ref=asset_ref), provider=provider)
    tail = projects.operate(store, root, 'inspect_transcription',
                            dict(transcription_ref=next_tx['transcription_ref'], start=63900))
    assert tail['stored_characters'] == 64000 and tail['output_characters'] == 70000
    assert tail['output_truncated'] is True and tail['next_start'] is None
    assert tail['transcription'] == 'C' * 100
    # Ownership gates: a nonexistent ref and another project's receipt are
    # both rejected for the foreign project; the owning project stays ready.
    other = projects.create(store, dict(scenario='Unrelated project.'))
    for bad_ref in ('f' * 64, first['transcription_ref']):
        with pytest.raises(ValueError):
            projects.operate(store, other, 'inspect_transcription',
                             dict(transcription_ref=bad_ref))
    assert projects.inspect(store, root)['state'] == 'ready'
    # A rejected read never costs budget either.
    assert projects.inspect(store, root)['budget']['transcription_calls'] == 2
