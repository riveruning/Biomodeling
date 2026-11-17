"""PMC HTML retains canonical text and original element locations."""
import pytest

from biomodel_agent import background_html as html, evidence, research_projects as projects
from biomodel_agent.storage import ArtifactStore


def article(doi, pmcid, body, references=''):
    return (f'<html><head><meta name="citation_doi" content="{doi}">'
            f'<meta name="citation_fulltext_html_url" content="https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/">'
            '</head><body><nav>Unrelated navigation</nav><article><h1>Study</h1>'
            f'<section class="body">{body}{references}</section></article></body></html>').encode()


def test_original_paths_and_canonical_spans_are_present():
    raw = article('10.1234/mm', 'PMC111',
                  '<section><h2>Introduction</h2><p>Cells &amp; tissues <a href="#B1">1</a>.</p></section>'
                  '<section><h2>Methods</h2><p>Measured tissues.</p></section>',
                  '<section class="ref-list"><ol><li id="B1"><cite>Experiment.</cite>'
                  '<a href="https://doi.org/10.1234/expt">DOI</a></li></ol></section>')
    document = html.extract_background(raw)
    page = document['pages'][0]['text']
    assert 'Unrelated navigation' not in page
    paragraph = next(b for b in document['blocks'] if b['text'] == 'Measured tissues.')
    assert paragraph['locator']['path'] == '/html[1]/body[1]/article[1]/section[1]/section[2]/p[1]'
    for block in document['blocks']:
        assert block['locator']['path']
        for span in block['source_spans']:
            assert page[span['start']:span['end']] == span['quote'] == block['text']
    reference = document['references'][0]
    assert reference['reference_id'] == 'B1' and reference['eligibility'] == 'pending_review'
    context = next(block for block in document['intro_blocks'] if 'Cells & tissues' in block['text'])
    assert reference['intro_context_locators'] == [context['locator']]


def test_html_ingestion_and_project_background_review(tmp_path):
    store = ArtifactStore(tmp_path/'store')
    context = 'Co-infection changes plasmid persistence.'
    method = 'We measured plasmid persistence in bacterial cultures.'
    result = 'Co-infection increased persistence in the studied cultures.'
    mm_path = tmp_path/'mm.html'
    mm_path.write_bytes(article('10.1234/mm', 'PMC111',
        f'<section><h2>Introduction</h2><p>{context}<a href="#B1">1</a></p></section>',
        '<section class="ref-list"><ol><li id="B1"><cite>Experiment.</cite>'
        '<a href="https://doi.org/10.1234/expt">DOI</a></li></ol></section>'))
    expt_path = tmp_path/'expt.html'
    expt_path.write_bytes(article('10.1234/expt', 'PMC222',
        '<section><h2>Discussion</h2><section><h3>Experimental Procedures</h3>'
        f'<p>{method}</p></section></section><section><h2>Results</h2><p>{result}</p></section>'))
    mm = evidence.ingest_source(store, mm_path)
    expt = evidence.ingest_source(store, expt_path)
    assert store.get(mm)['payload']['parser'] == html.FORMAT
    assert store.get_blob(store.get(mm)['payload']['blob_ref']) == mm_path.read_bytes()
    project = projects.create(store, dict(scenario='HTML evidence control.', mode='reproduction', source_refs=[mm,expt]))
    index = projects.operate(store, project, 'index_mm_background', dict(source_ref=mm))['index_ref']
    page = projects.operate(store, project, 'inspect_mm_background', dict(index_ref=index, reference_ids=['B1']))
    assert page['items'][0]['intro_contexts'][0]['locator']['kind'] == 'html'
    anchor = lambda ref, quote: dict(source_ref=ref, page=1, quote=quote)
    args = dict(index_ref=index,
        requirements=[dict(id='persistence', question='How does co-infection affect persistence?', mm_context=anchor(mm,context))],
        reviews=[dict(reference_id='B1', source_ref=expt, study_type='experimental', mathematical_modeling='absent',
            fulltext_reviewed=True, rationale='Experimental measurements with original method and result quotations.',
            methods_evidence=[anchor(expt,method)], results_evidence=[anchor(expt,result)], modeling_evidence=[],
            coverage=[dict(requirement_id='persistence', explanation='Observed persistence difference.', evidence=[anchor(expt,result)])])])
    review = projects.operate(store, project, 'review_expt_background', args)
    assert review['qualified_count'] == 1 and review['status'] == 'complete'


@pytest.mark.parametrize('raw', [b'<html>Checking your browser</html>', b'<html><p>No article</p></html>', b'\xff'])
def test_non_article_input_is_rejected(raw):
    with pytest.raises(ValueError):
        html.extract_background(raw)
