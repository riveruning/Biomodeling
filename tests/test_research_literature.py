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
    assert src['parser']==literature.VERSION and src['capabilities']['formula_transcription']=='unverified'
    assert len(src['warnings'])==2 and projects.inspect(store,root)['budget']['literature_requests']==2
    assert all(r.url.host=='www.ebi.ac.uk' for r in requests)


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
    def fail(request):raise httpx.ConnectError('secret local configuration',request=request)
    store,root,transport=setup(tmp_path,fail)
    with pytest.raises(literature.LiteratureError):search(store,root,transport)
    assert projects.inspect(store,root)['state']=='needs_recovery'
    assert 'secret local configuration' not in ''.join(p.read_text() for p in store.objects_dir.glob('*.json'))


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

