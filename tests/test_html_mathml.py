import xml.etree.ElementTree as ET

from biomodel_agent import background_html as html, evidence, research_projects as projects
from biomodel_agent.storage import ArtifactStore
from test_background_html import article


FRACTION=('<math id="eq" display="block"><mrow><mi>Y</mi><mo>=</mo><mfrac>'
          '<msup><mi>X</mi><mn>2</mn></msup><mrow><mn>1</mn><mo>+</mo>'
          '<msub><mi>K</mi><mi>d</mi></msub></mrow></mfrac></mrow></math>')


def document(body):
    return html.extract_background(article('10.1234/math','PMC123',
        '<section><h2>Results</h2>'+body+'</section>'))


def test_display_fraction_and_subscripts_keep_structure_and_original_location():
    result=document('<p>The kinetic equations become:</p>'
        '<table class="disp-formula p" id="FD1"><tr><td>'+FRACTION+'</td><td>(4)</td></tr></table>')
    block=next(block for block in result['blocks'] if block['locator']['id']=='FD1')
    assert '<mfrac>' in block['text'] and '<msub>' in block['text'] and '<msup>' in block['text']
    assert '(4)' in block['text']
    record=result['formula_sources'][0]
    assert record['status']=='unreviewed' and record['representation']=='parsed_mathml_serialization'
    assert record['locator']['id']=='eq' and '/table[1]/tr[1]/td[1]/math[1]' in record['locator']['path']
    span=record['source_spans'][0]
    assert result['pages'][0]['text'][span['start']:span['end']]==span['quote']
    tree=ET.fromstring(span['quote'].removeprefix('[unreviewed-mathml] '))
    fraction=tree.find('.//mfrac')
    assert fraction[0].tag=='msup' and fraction[1].tag=='mrow'
    assert result['status']=='needs_review'


def test_inline_and_direct_math_are_not_concatenated_into_false_plain_equations():
    result=document('<p>Rate '+FRACTION+' is source notation.</p>'+FRACTION)
    assert result['formula_source_count']==2 and len(result['formula_sources'])==2
    spans=[row['source_spans'][0] for row in result['formula_sources']]
    assert spans[0]['start']!=spans[1]['start']
    assert all('<mfrac>' in row['quote'] for row in spans)


def test_math_projection_does_not_execute_or_retain_active_content():
    result=document('<p><math id="safe" onclick="EVIL()" href="javascript:EVIL()" style="hidden">'
        '<mi>X</mi><mo>&lt;</mo><mtext>&amp;</mtext><script>PRIVATE_SCRIPT</script></math></p>')
    text=result['pages'][0]['text']
    assert 'PRIVATE_SCRIPT' not in text and 'EVIL' not in text and 'onclick' not in text
    assert '&lt;' in text and '&amp;' in text and 'id="safe"' in text
    assert all(row['status']=='unreviewed' for row in result['formula_sources'])


def test_image_only_formula_stays_explicit_and_empty_math_is_not_certified():
    result=document('<table class="disp-formula" id="image"><tr><td><img src="equation.png"></td></tr></table>'
        '<math id="empty"></math>')
    assert next(b for b in result['blocks'] if b['locator']['id']=='image')['text'].startswith('[unreviewed-formula]')
    assert any(i['code']=='image_not_transcribed' for i in result['issues'])
    assert result['formula_sources'][0]['status']=='unreviewed'


def test_math_in_ignored_subtree_is_reported_unprojected_not_invented():
    result=document('<figure><svg>'+FRACTION+'</svg><figcaption>Untranscribed diagram.</figcaption></figure>')
    record=result['formula_sources'][0]
    assert record['status']=='not_projected' and record['source_spans']==[]
    assert any(i['code']=='mathml_not_projected' for i in result['issues'])


def test_math_metadata_cap_is_explicit_without_truncating_source_notation():
    result=document(''.join('<p>'+FRACTION+'</p>' for _ in range(html.MAX_FORMULA_RECORDS+1)))
    assert result['formula_source_count']==html.MAX_FORMULA_RECORDS+1
    assert result['formula_sources_truncated'] is True
    assert len(result['formula_sources'])==html.MAX_FORMULA_RECORDS
    assert result['pages'][0]['text'].count('[unreviewed-mathml]')==html.MAX_FORMULA_RECORDS+1


def test_math_source_is_readable_via_normal_project_entrypoint(tmp_path):
    raw=article('10.1234/math','PMC123','<section><h2>Results</h2><p>'+FRACTION+'</p></section>')
    path=tmp_path/'paper.html';path.write_bytes(raw);store=ArtifactStore(tmp_path/'store')
    source=evidence.ingest_source(store,path);payload=store.get(source)['payload']
    assert payload['parser_version']==html.VERSION
    assert payload['formula_sources'][0]['status']=='unreviewed'
    project=projects.create(store,dict(scenario='Read the supplied equation.',source_refs=[source]))
    span=payload['formula_sources'][0]['source_spans'][0]
    result=projects.operate(store,project,'inspect_source',dict(source_ref=source,page=1,start=span['start'],end=span['end']))
    assert result['text']==span['quote'] and '<mfrac>' in result['text']
    assert projects.inspect(store,project)['budget']['planner_calls']==0
