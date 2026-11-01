"""Single-section image gaps and attributed visual evidence, no OCR claims."""
from copy import deepcopy
from pathlib import Path
import json
import struct

import pytest
from biomodel_agent import evidence, jats_sources as jats
from biomodel_agent.storage import ArtifactStore, ArtifactError

XML=b'''<?xml version="1.0"?><article xmlns:xlink="http://www.w3.org/1999/xlink"><body>
<sec id="chosen"><title>Known development</title><p>Text before <inline-formula><inline-graphic xlink:href="urn:formula:1"/></inline-formula> text after.</p></sec>
<sec id="other"><p>Not in selected scope.</p></sec></body></article>'''


@pytest.fixture
def sample(tmp_path):
    # Parser stores bytes; it does not claim to decode or recognize the image.
    path=tmp_path/'article.xml';path.write_bytes(XML)
    (tmp_path/'formula.png').write_bytes(b'\x89PNG\r\n\x1a\n'+b'synthetic-signature-only-control')
    store=ArtifactStore(tmp_path/'store')
    ref=jats.ingest(store,path,'chosen',{'urn:formula:1':'formula.png'})
    projection=jats.validate(store,ref);gap=projection['gaps'][0]
    entries=[dict(gap_id=gap['id'],asset_ref=gap['asset']['blob_ref'],text='Parameter k = 0.4.')]
    return dict(store=store,path=path,ref=ref,projection=projection,entries=entries)


def derived(s):
    return jats.transcribe(s['store'],s['ref'],s['entries'],'Explicit synthetic analyst fixture','Synthetic visual text; not an actual image reading or independent reviewer.')


def test_native_projection_has_explicit_gap_and_scope(sample):
    p=sample['projection'];assert len(p['gaps'])==1
    assert p['gaps'][0]['kind']=='image_formula' and p['gaps'][0]['status']=='local_asset_untranscribed'
    assert 'Text before' in p['pages'][0]['text'] and 'Not in selected' not in p['pages'][0]['text']
    assert p['capabilities']['ocr']=='not_run' and p['capabilities']['formula_recognition']=='not_run'
    g=p['gaps'][0];assert p['pages'][0]['text'][g['start']:g['end']]=='[JATS_GAP:gap1:image_formula]'


def test_unavailable_image_is_not_an_author_omission(sample):
    p=jats.project(XML,'chosen',{})
    assert p['gaps'][0]['status']=='local_asset_unavailable'
    assert 'Parameter k' not in p['pages'][0]['text']


def test_derived_candidate_keeps_native_prefix_and_unknown_status(sample):
    store=sample['store'];before=store.get(sample['ref'])
    ref=derived(sample);p=store.get(ref)['payload'];projection=jats.validate(store,ref)
    assert p['pages'][0]['text'].startswith(before['payload']['pages'][0]['text'])
    assert 'ANALYST VISUAL CANDIDATE' in p['pages'][0]['text']
    assert p['independent_review']=='pending' and p['scientific_support']=='unknown'
    assert store.get(sample['ref'])==before
    assert len(projection['visual_blocks'])==1


def test_claim_has_image_locator_and_can_be_rechecked(sample):
    store=sample['store'];ref=derived(sample);text=store.get(ref)['payload']['pages'][0]['text'];quote='Parameter k = 0.4.'
    start=text.index(quote)
    claim=evidence.add_claim(store,ref,1,start,start+len(quote),'k/value')
    locator=store.get(claim)['payload']['locator']
    assert locator['kind']=='jats_visual_candidate' and locator['original_source_ref']==sample['ref']
    assert locator['visual_anchors'][0]['asset_ref']==sample['entries'][0]['asset_ref']
    assert evidence._claim_exists_quoted(store,claim)
    forged=store.get(claim);forged['payload']['locator']['visual_anchors']=[]
    bad=store.put('claim',forged['payload'],refs=forged['refs'])
    assert not evidence._claim_exists_quoted(store,bad)


def test_native_quote_locator_is_xml_not_pdf(sample):
    store=sample['store'];text=sample['projection']['pages'][0]['text'];start=text.index('Text before')
    claim=evidence.add_claim(store,sample['ref'],1,start,start+len('Text before'),'native text control')
    loc=store.get(claim)['payload']['locator']
    assert loc['kind']=='jats_section' and 'not a PDF page' in loc['page_semantics']
    assert evidence._claim_exists_quoted(store,claim)


@pytest.mark.parametrize('mutation',['text','capabilities','review','parser','original_ref','refs'])
def test_forged_visual_source_cannot_support_a_claim(sample,mutation):
    store=sample['store'];ref=derived(sample);obj=store.get(ref);p=obj['payload']
    if mutation=='text':p['pages'][0]['text']+='Parameter k = 999.'
    elif mutation=='capabilities':p['capabilities']['ocr']='completed'
    elif mutation=='review':p['scientific_support']='supported'
    elif mutation=='parser':p['parser']='utf8'
    elif mutation=='original_ref':p['original_source_ref']=p['blob_ref']
    else:obj['refs']=obj['refs'][:-1]
    bad=store.put('source',p,refs=obj['refs'])
    with pytest.raises((ArtifactError,ValueError,KeyError)):
        evidence.add_claim(store,bad,1,0,5,'forged source')


@pytest.mark.parametrize('mutation',['pages','section','assets','refs'])
def test_raw_projection_and_dependency_changes_rejected(sample,mutation):
    store=sample['store'];obj=store.get(sample['ref'])
    if mutation=='pages':obj['payload']['pages'][0]['text']='different'
    elif mutation=='refs':obj['refs']=obj['refs'][:1]
    else:
        ext=store.get(obj['payload']['parser_output_ref'])
        if mutation=='section':ext['payload']['projection']['section_id']='other'
        else:ext['payload']['asset_bindings']={}
        new=store.put(ext['kind'],ext['payload'],refs=ext['refs']);obj['payload']['parser_output_ref']=new
        obj['refs']=[obj['payload']['blob_ref'],new]
    ref=store.put('source',obj['payload'],refs=obj['refs'])
    with pytest.raises(ValueError):jats.validate(store,ref)


@pytest.mark.parametrize('mutation',['wrong_gap','wrong_asset','empty','duplicate','figure'])
def test_candidate_binds_an_actual_local_formula_gap(sample,mutation):
    entries=deepcopy(sample['entries'])
    if mutation=='wrong_gap':entries[0]['gap_id']='missing'
    elif mutation=='wrong_asset':entries[0]['asset_ref']='0'*64
    elif mutation=='empty':entries[0]['text']=''
    elif mutation=='duplicate':entries*=2
    else:
        p=sample['projection'];p['gaps'][0]['kind']='image_figure'
        with pytest.raises(ValueError):jats._visual(p,entries)
        return
    with pytest.raises(ValueError):jats.transcribe(sample['store'],sample['ref'],entries,'Fixture analyst','Fixture notes')


@pytest.mark.parametrize('xml',[
 b'<!DOCTYPE article [<!ENTITY x "expanded">]><article><sec id="chosen">&x;</sec></article>',
 b'<article><sec id="chosen"/><sec id="chosen"/></article>',
 b'<article><sec id="not-chosen"/></article>',
 b'<article><sec id="chosen">',
 b'\xff\xfe\x00a',
])
def test_unsupported_or_unsafe_xml_refused(xml):
    with pytest.raises(ValueError):jats.project(xml,'chosen',{})


def test_external_doctype_is_not_fetched_and_scope_is_explicit():
    raw=b'<!DOCTYPE article SYSTEM "https://must-not-be-fetched.invalid/DTD"><article><sec id="chosen"><p>literal</p></sec></article>'
    assert 'literal' in jats.project(raw,'chosen',{})['pages'][0]['text']


def test_mathml_and_table_are_preserved_uninterpreted():
    raw=b'<article xmlns:m="http://www.w3.org/1998/Math/MathML"><sec id="chosen"><m:math><m:mi>x</m:mi></m:math><table-wrap><table><tr><td>1</td><td>k</td></tr></table></table-wrap></sec></article>'
    p=jats.project(raw,'chosen',{})
    assert [g['kind'] for g in p['gaps']]==['mathml_uninterpreted','table_uninterpreted']
    assert p['gaps'][0]['node']['children'][0]['node']['text']=='x'
    assert p['capabilities']['table_interpretation']=='not_run'


@pytest.mark.parametrize('name',['../outside.png','/absolute.png','missing.png'])
def test_asset_path_and_missing_file_refused(sample,name):
    with pytest.raises(ValueError):jats.ingest(sample['store'],sample['path'],'chosen',{'urn:formula:1':name})


def test_symlink_asset_and_out_of_section_href_refused(sample,tmp_path):
    (tmp_path/'alias.png').symlink_to(tmp_path/'formula.png')
    with pytest.raises(ValueError):jats.ingest(sample['store'],sample['path'],'chosen',{'urn:formula:1':'alias.png'})
    with pytest.raises(ValueError):jats.ingest(sample['store'],sample['path'],'chosen',{'urn:other':'formula.png'})


def test_roundtrip_portable_claim_closure(sample,tmp_path):
    from biomodel_agent.bundles import export_bundle
    store=sample['store'];ref=derived(sample);p=store.get(ref)['payload'];text=p['pages'][0]['text'];start=text.index('Parameter k')
    claim=evidence.add_claim(store,ref,1,start,start+len('Parameter k = 0.4.'),'k/value')
    export_bundle(store,claim,tmp_path/'bundle')
    assert evidence._claim_exists_quoted(ArtifactStore(tmp_path/'bundle'),claim)


def test_legacy_text_claims_unchanged(tmp_path):
    source=tmp_path/'source.txt';source.write_text('Parameter k = 0.4.')
    store=ArtifactStore(tmp_path/'store');ref=evidence.ingest_source(store,source)
    claim=evidence.add_claim(store,ref,1,0,len(source.read_text()),'k/value')
    assert 'locator' not in store.get(claim)['payload'] and evidence._claim_exists_quoted(store,claim)


def test_cli_explicit_jats_and_visual_commands(sample,tmp_path,capsys):
    from biomodel_agent.cli import main
    assets=tmp_path/'assets.json';assets.write_text(json.dumps({'urn:formula:1':'formula.png'}))
    assert main(['source-jats','--store',str(sample['store'].root),'--path',str(sample['path']),'--section','chosen','--assets',str(assets)])==0
    native=json.loads(capsys.readouterr().out)['source_ref']
    candidates=tmp_path/'candidates.json';candidates.write_text(json.dumps(sample['entries']))
    assert main(['source-transcript','--store',str(sample['store'].root),'--source-ref',native,'--candidates',str(candidates),'--actor','Synthetic fixture','--notes','Not an independent review'])==0
    ref=json.loads(capsys.readouterr().out)['source_ref'];assert jats.validate(sample['store'],ref)['visual_blocks']


def test_native_subscripts_superscripts_and_unicode_minus_are_not_silently_rewritten():
    raw='<article><sec id="chosen"><p>k = −0.1; X<sub>1</sub> and Y<sup>2</sup></p></sec></article>'.encode()
    p=jats.project(raw,'chosen',{})
    assert '−0.1' in p['pages'][0]['text']
    assert [g['kind'] for g in p['gaps']]==['subscript_uninterpreted','superscript_uninterpreted']
    assert [g['node']['text'] for g in p['gaps']]==['1','2']


def test_external_xml_include_is_not_followed():
    raw=b'<article xmlns:xi="http://www.w3.org/2001/XInclude"><sec id="chosen"><xi:include href="file:///never-read"/></sec></article>'
    with pytest.raises(ValueError,match='inclusions'):jats.project(raw,'chosen',{})


def test_bound_analyst_assertion_is_not_a_scientific_truth_certificate(sample):
    sample['entries'][0]['text']='Parameter k = 0.9.'
    store=sample['store'];ref=derived(sample);text=store.get(ref)['payload']['pages'][0]['text'];quote='Parameter k = 0.9.';start=text.index(quote)
    claim=evidence.add_claim(store,ref,1,start,start+len(quote),'k/value',expected=.9,allowed_symbols={'k'})
    p=store.get(claim)['payload']
    assert p['binding']['status']=='matched' and p['binding']['semantic_support']=='unknown'
    assert store.get(ref)['payload']['independent_review']=='pending'


def test_tree_and_candidate_budgets_are_explicit(sample):
    raw=('<article><sec id="chosen">'+'<p>'*70+'x'+'</p>'*70+'</sec></article>').encode()
    with pytest.raises(ValueError,match='tree budget'):jats.project(raw,'chosen',{})
    entries=deepcopy(sample['entries']);entries[0]['text']='x'*8001
    with pytest.raises(ValueError,match='bounded visual'):jats.transcribe(sample['store'],sample['ref'],entries,'analyst','notes')


def test_candidate_without_actor_or_notes_is_not_adopted(sample):
    for actor,notes in [('', 'notes'),('analyst','')]:
        with pytest.raises(ValueError,match='attributed'):jats.transcribe(sample['store'],sample['ref'],sample['entries'],actor,notes)
