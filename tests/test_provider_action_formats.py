"""Real DeepSeek DSML normalization and unchanged JSON protocol guards.

Both observed production shapes must normalize to the original arguments with
their actual types; every malformed or ambiguous structure is refused with a
deterministic ModelResponseError. The normalization is bound to the observed
DeepSeek model names; all other models keep the previous strict single-JSON
behavior, and the base agent action table stays out of the parser.
"""
import json
from pathlib import Path

import httpx
import pytest

from biomodel_agent.llm import (ChatProvider, DSML_ACTION_MODELS, ModelResponseError,
                                action_content_source, parse_response_action)

FIXTURES = Path(__file__).parent / 'fixtures/provider-actions'
DSML_ARGS = (FIXTURES / 'deepseek-args.txt').read_text(encoding='utf-8')
DSML_FLAT = (FIXTURES / 'deepseek-flat.txt').read_text(encoding='utf-8')
SOURCE_REF = '760cd9935f5b6f295371c96eae12ae4bee5a1f789f36c8a3c081e995bc708563'
EXPECTED_ARGS = {'source_ref': SOURCE_REF, 'page': 1, 'start': 0, 'end': 3449}


def dsml(name, params):
    body = '<｜DSML｜ calls>\n<｜DSML｜ invoke name="%s">\n' % name
    for pname, flag, value in params:
        body += '<｜DSML｜ parameter name="%s" string="%s">%s</｜DSML｜ parameter>\n' % (pname, flag, value)
    return body + '</｜DSML｜ invoke>\n</｜DSML｜ calls>'


def test_real_deepseek_args_fixture_normalizes_with_original_types():
    for parse in (lambda text: parse_response_action(text, model='deepseek-flash-2'),
                  lambda text: ChatProvider('https://offline.invalid/v1', 'deepseek-flash-2')._parse(text)):
        action = parse(DSML_ARGS)
        assert action['action'] == 'inspect_source'
        assert action['args'] == EXPECTED_ARGS
        assert type(action['args']['page']) is int and type(action['args']['end']) is int
        assert 'reason' not in action


def test_real_deepseek_flat_fixture_normalizes_after_prose():
    action = parse_response_action(DSML_FLAT, model='deepseek-flash-2')
    assert action['action'] == 'inspect_source' and action['args'] == EXPECTED_ARGS
    assert action['args']['source_ref'] == SOURCE_REF and isinstance(action['args']['source_ref'], str)
    assert all(type(action['args'][k]) is int for k in ('page', 'start', 'end'))
    # Verbatim strings keep their exact content; JSON scalars keep their types.
    scalar = dsml('load_skill', [('name', 'false', 'true'), ('count', 'false', 'null'),
                                 ('flag', 'false', 'false'), ('ratio', 'false', '0.5')])
    parsed = parse_response_action(scalar, model='deepseek-flash-2')
    assert parsed['args'] == {'name': True, 'count': None, 'flag': False, 'ratio': 0.5}


def test_dsml_normalization_is_bound_to_observed_models_only():
    assert DSML_ACTION_MODELS == frozenset({'deepseek-flash-2'})
    for model in ('glm-5.3-flash', 'offline-control', ''):
        with pytest.raises(ModelResponseError):
            parse_response_action(DSML_FLAT, model=model)
    # The observed DeepSeek model still accepts the plain JSON protocol.
    action = parse_response_action(json.dumps(
        {'action': 'finish', 'args': {}, 'reason': 'done'}), model='deepseek-flash-2')
    assert action == {'action': 'finish', 'args': {}, 'reason': 'done'}


def test_action_content_source_selects_the_parser():
    assert action_content_source(DSML_FLAT, model='deepseek-flash-2') == 'dsml'
    assert action_content_source('{"action":"run","args":{}}', model='deepseek-flash-2') == 'json'
    assert action_content_source(DSML_FLAT, model='glm-5.3-flash') == 'json'
    assert action_content_source('   ', model='deepseek-flash-2') == 'json'


LITERAL_DSML = '<｜DSML｜ calls>\n<｜DSML｜ invoke name="run">\n</｜DSML｜ invoke>\n</｜DSML｜ calls>'


def test_json_with_literal_dsml_markup_stays_on_the_json_path():
    """Regression for the observed parser-review case: a valid JSON action
    whose args/reason carry literal DSML markup as data keeps the strict JSON
    protocol for every model, and the literal values survive verbatim."""
    for model in ('deepseek-flash-2', 'glm-5.3-flash', 'offline-control'):
        action = parse_response_action(json.dumps(
            {'action': 'inspect_source', 'args': {'quote': LITERAL_DSML},
             'reason': 'copies <｜DSML｜ calls> as untrusted data'}), model=model)
        assert action['args']['quote'] == LITERAL_DSML
        assert action['reason'] == 'copies <｜DSML｜ calls> as untrusted data'
    assert action_content_source(json.dumps({'args': {'quote': LITERAL_DSML}}),
                                 model='deepseek-flash-2') == 'json'
    fenced = '```json\n' + json.dumps(
        {'action': 'finish', 'args': {'markup': '<｜DSML｜ invoke name="x">'}}) + '\n```'
    assert action_content_source(fenced, model='deepseek-flash-2') == 'json'
    assert parse_response_action(fenced, model='deepseek-flash-2')['args']['markup'] == '<｜DSML｜ invoke name="x">'


def test_malformed_json_with_dsml_literal_is_never_repaired_into_a_dsml_action():
    body = json.dumps({'action': 'run', 'args': {'quote': LITERAL_DSML}})[:-2]  # truncated JSON
    assert action_content_source(body, model='deepseek-flash-2') == 'json'
    with pytest.raises(ModelResponseError, match='not a single JSON object'):
        parse_response_action(body, model='deepseek-flash-2')


def test_dsml_string_values_keep_literals_without_entity_conversion():
    raw = 'A & B < C > D "e" $x<y$ &amp; &#60; \\backslash'
    body = ('<｜DSML｜ calls>\n<｜DSML｜ invoke name="search_sources">\n'
            '<｜DSML｜ parameter name="query" string="true">' + raw + '</｜DSML｜ parameter>\n'
            '</｜DSML｜ invoke>\n</｜DSML｜ calls>')
    parsed = parse_response_action(body, model='deepseek-flash-2')
    assert parsed['args']['query'] == raw


def test_live_respond_json_literal_and_broken_json_stay_on_the_json_path(monkeypatch):
    """A successful call keeps last_content_source consistent with
    parse_response_action, and JSON-shaped content is never reclassified."""
    monkeypatch.setenv('TEST_DSML_KEY', 'offline-dummy')
    bodies = [json.dumps({'action': 'finish', 'args': {'quote': LITERAL_DSML}, 'reason': 'data'}),
              '{"action":' + LITERAL_DSML]
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(200, json=dict(choices=[dict(message=dict(content=bodies[len(seen)-1]),
            finish_reason='stop')], usage=dict(total_tokens=3)))
    provider = ChatProvider('https://offline.invalid/v1', 'deepseek-flash-2', api_key_env='TEST_DSML_KEY',
                            transport=httpx.MockTransport(handle))
    action = provider.respond([{'role': 'user', 'content': 'probe'}])
    assert action['args']['quote'] == LITERAL_DSML and action['reason'] == 'data'
    assert provider.last_content_source == 'json'
    assert parse_response_action(provider.last_model_content, model=provider.model) == action
    with pytest.raises(ModelResponseError):
        provider.respond([{'role': 'user', 'content': 'probe'}])
    assert provider.last_content_source == 'json'


def test_live_respond_normalizes_dsml_and_keeps_extracted_content_traceable(monkeypatch):
    monkeypatch.setenv('TEST_DSML_KEY', 'offline-dummy')
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        body = DSML_FLAT if len(requests) == 1 else json.dumps({'action': 'run', 'args': {}})
        return httpx.Response(200, json=dict(choices=[dict(message=dict(content=body), finish_reason='stop')],
                                            usage=dict(total_tokens=9)))

    provider = ChatProvider('https://offline.invalid/v1', 'deepseek-flash-2', api_key_env='TEST_DSML_KEY',
                            transport=httpx.MockTransport(handle))
    assert provider.respond([{'role': 'user', 'content': 'probe'}])['action'] == 'inspect_source'
    assert provider.last_content_source == 'dsml'
    assert provider.last_model_content == DSML_FLAT
    assert provider.respond([{'role': 'user', 'content': 'probe'}])['action'] == 'run'
    assert provider.last_content_source == 'json'
    assert provider.last_model_content == '{"action": "run", "args": {}}'


@pytest.mark.parametrize('raw', [
    # Two invokes are two actions.
    dsml('finish', []) + '\n' + '<｜DSML｜ invoke name="run">\n</｜DSML｜ invoke>',
    dsml('finish', []).replace('name="finish"', 'name=" "'),
    # Two calls blocks and a close-before-open block.
    dsml('finish', []) + '\n' + dsml('run', []),
    '</｜DSML｜ calls>\n<｜DSML｜ calls>',
    # Truncated structures.
    '<｜DSML｜ calls>\n<｜DSML｜ invoke name="finish">\n',
    '<｜DSML｜ calls>\n<｜DSML｜ invoke name="finish">\n</｜DSML｜ invoke>\n',
    '<｜DSML｜ calls>\n<｜DSML｜ invoke name="finish">\n<｜DSML｜ parameter name="a" string="true">x\n</｜DSML｜ invoke>\n</｜DSML｜ calls>',
    # Duplicate parameter, duplicate and unknown attributes, illegal string flag.
    dsml('finish', [('a', 'true', '1'), ('a', 'true', '2')]),
    '<｜DSML｜ calls>\n<｜DSML｜ invoke name="run" name="finish">\n</｜DSML｜ invoke>\n</｜DSML｜ calls>',
    dsml('finish', [('a', 'true', '1', ), ('b', 'true', '2')]).replace('name="b" string="true"', 'name="b" string="true" extra="x"'),
    dsml('finish', [('a', 'true', '1')]).replace(' name="a" string="true"', ' name="a"'),
    dsml('finish', [('a', 'maybe', '1')]),
    # Mixed forms and illegal args payloads.
    dsml('finish', [('args', 'false', '{"a": 1}'), ('b', 'true', 'x')]),
    dsml('finish', [('args', 'true', '{"a": 1}')]),
    dsml('finish', [('args', 'false', '[1, 2]')]),
    dsml('finish', [('a', 'false', '"just a string"')]),
    # Strict JSON body problems: duplicate keys, nonfinite constants, budgets.
    dsml('finish', [('a', 'false', '{"v": 1, "v": 2}')]),
    dsml('finish', [('a', 'false', '{"v": NaN}')]),
    dsml('finish', [('a', 'false', '{"v": 1e999}')]),
    dsml('finish', [('a', 'false', '{"v": ' + '9' * 400 + '}')]),
    dsml('finish', [('a', 'false', '[' * 80 + '0' + ']' * 80)]),
    # Malformed markup and nesting.
    dsml('finish', [('a', 'true', 'x')]).replace('<｜DSML｜ invoke name="finish">', '<｜DSML｜ invoke>'),
    '<｜DSML｜ calls>\n<｜DSML｜ invoke name="finish">\n<｜DSML｜ parameter name="a" string="true">'
    '<｜DSML｜ parameter name="b" string="true">y</｜DSML｜ parameter></｜DSML｜ parameter>\n'
    '</｜DSML｜ invoke>\n</｜DSML｜ calls>',
    dsml('finish', [('a', 'true', 'x')]).replace('name="a" string="true"', 'name="a" string=true'),
    dsml('finish', [('a', 'true', 'x')]).replace('</｜DSML｜ invoke>', '</｜DSML｜ parameter>'),
    'prose\n</｜DSML｜ calls>\n</｜DSML｜ calls>',
    '<｜DSML｜ calls>\n</｜DSML｜ calls>',
    # Adjacent attributes without a separating space and malformed values.
    dsml('finish', [('a', 'true', '1')]).replace(' name="a" string="true"', ' name="a"string="true"'),
    '<｜DSML｜ calls>\n<｜DSML｜ invoke name="finish"excess="x">\n</｜DSML｜ invoke>\n</｜DSML｜ calls>',
    '<｜DSML｜ calls>\n<｜DSML｜ invoke name="finish">\n<｜DSML｜ parameter name= finish string="true">x'
    '</｜DSML｜ parameter>\n</｜DSML｜ invoke>\n</｜DSML｜ calls>',
    '<｜DSML｜ calls>\n<｜DSML｜ invoke name="finish">\n<｜DSML｜ parameter name="a" string="true"x'
    '</｜DSML｜ parameter>\n</｜DSML｜ invoke>\n</｜DSML｜ calls>',
    # Close tags nested inside a parameter body.
    '<｜DSML｜ calls>\n<｜DSML｜ invoke name="finish">\n<｜DSML｜ parameter name="a" string="true">'
    '</｜DSML｜ invoke>x</｜DSML｜ parameter>\n</｜DSML｜ invoke>\n</｜DSML｜ calls>',
    '<｜DSML｜ calls>\n<｜DSML｜ invoke name="finish">\n<｜DSML｜ parameter name="a" string="true">'
    'x</｜DSML｜ calls></｜DSML｜ parameter>\n</｜DSML｜ invoke>\n</｜DSML｜ calls>',
])
def test_malformed_dsml_structures_are_never_normalized(raw):
    with pytest.raises(ModelResponseError):
        parse_response_action(raw, model='deepseek-flash-2')


def test_prose_is_allowed_only_before_the_single_calls_block():
    assert parse_response_action('Sure.\n\n' + DSML_ARGS, model='deepseek-flash-2')['args'] == EXPECTED_ARGS
    with pytest.raises(ModelResponseError, match='after the DSML calls block'):
        parse_response_action(DSML_ARGS + '\nDone.', model='deepseek-flash-2')


@pytest.mark.parametrize('text', [
    '{"action":"finish","args":{}}}',
    '{"action":"run","action":"finish","args":{}}',
    '{"action":"submit_draft","args":{"draft":{"value":NaN}}}',
    '{"action":"finish","args":{}} {"action":"run","args":{}}',
    '{"action":"finish","args":null}',
    '{"action":"finish","args":{},"reference_ref":"x"}',
    '{"action":"submit_draft","args":{"value":1e999}}',
    '{"action":"finish","args":{"value":' + '9' * 5000 + '}}',
    '{"action":"finish","args":{"value":' + '[' * 1200 + '0' + ']' * 1200 + '}}',
])
def test_existing_json_protocol_guards_are_unchanged_for_every_model(text):
    for model in ('deepseek-flash-2', 'glm-5.3-flash', 'offline-control'):
        with pytest.raises(ModelResponseError):
            parse_response_action(text, model=model)


def test_json_reason_and_fence_behavior_are_preserved():
    fenced = '```json\n{"action":"run","args":{},"reason":null}\n```'
    assert parse_response_action(fenced, model='offline-control') == {'action': 'run', 'args': {}, 'reason': ''}
    assert parse_response_action('  {"action":" run ","args":{}}  ', model='offline-control')['action'] == 'run'
    with pytest.raises(ModelResponseError, match="must be a JSON object, got list"):
        parse_response_action('["action"]', model='offline-control')
