"""Offline checks of evidence and score boundaries for competitor adapters."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
DEVELOPMENT=ROOT/'benchmarks/development'
sys.path.insert(0,str(DEVELOPMENT))
import competitor_common as common
from score_published_competitors import compare_imported, from_basico_split, from_t2b_state


@pytest.mark.skipif(not (common.OUT/'source-manifest.json').exists(),
                    reason='optional third-party source snapshots not downloaded')
def test_official_sources_remain_unmodified():
    common.verify_source('kinmodgpt')
    common.verify_source('talk2biomodels')


def test_changed_vendor_source_is_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(common,'OUT',tmp_path)
    path=tmp_path/'vendor/system/module.py';path.parent.mkdir(parents=True);path.write_text('changed')
    (tmp_path/'source-manifest.json').write_text(json.dumps({'files':[
        {'system':'system','path':'module.py','sha256':'0'*64}]}))
    with pytest.raises(ValueError,match='Vendor source changed'):
        common.verify_source('system')


def test_missing_system_cannot_pass_source_integrity_check(tmp_path,monkeypatch):
    monkeypatch.setattr(common,'OUT',tmp_path)
    (tmp_path/'source-manifest.json').write_text('{"files":[]}')
    with pytest.raises(ValueError,match='No pinned source'):
        common.verify_source('unrecorded_system')


def test_json_logging_survives_ascii_locale(tmp_path):
    target=tmp_path/'unicode.json'
    code=(f"import sys;sys.path.insert(0,{str(DEVELOPMENT)!r});"
          "from competitor_common import write_json;"
          f"write_json({str(target)!r}, {{'message':'\\u4e2d\\u6587 \\u2192'}})")
    result=subprocess.run([sys.executable,'-X','utf8=0','-c',code],
        env={'LC_ALL':'C','PYTHONCOERCECLOCALE':'0','PATH':'/usr/bin:/bin'},capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert json.loads(target.read_text(encoding='utf-8'))['message']=='中文 →'


def test_basico_time_column_is_not_mistaken_for_species():
    times,values=from_basico_split({'columns':['Time','P'],'data':[[0.,1.],[1.,.7]]})
    assert times==[0.,1.] and values=={'P':[1.,.7]}


def test_graph_dataframe_indices_sorted_numerically():
    times,values=from_t2b_state([{'data':{'Time':{'10':10.,'2':2.,'0':0.},
                                                'P':{'10':.1,'2':.8,'0':1.}}}])
    assert times==[0.,2.,10.] and values['P']==[1.,.8,.1]


def test_no_graph_trajectory_does_not_become_scientific_success():
    with pytest.raises(ValueError,match='no simulation'):
        from_t2b_state([])


def test_wrong_grid_fails_before_numeric_comparison():
    result=compare_imported([0.,2.],{'P':[1.,.5]}, {'times':[0.,1.]})
    assert result['status']=='fail' and result['reason']=='wrong_sampling_grid'


@pytest.mark.skipif(not (common.OUT/'kinmodgpt/complete/result.json').exists(),
                    reason='optional paid integration trial not run')
def test_complete_and_missing_initial_conditions_remain_distinct():
    base=ROOT/'artifacts/competitor-integration-20261017'
    complete=json.loads((base/'kinmodgpt/complete/result.json').read_text())
    missing=json.loads((base/'kinmodgpt/assisted/result.json').read_text())
    assert complete['initial_values']=={'X':.1,'Y':.2,'Z':.3}
    assert missing['initial_values']=={'X':0.,'Y':0.,'Z':0.}
    assert complete['variant']=='official_pipeline_alternative_LLM_modern_runtime'
