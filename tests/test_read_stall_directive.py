"""Deterministic read-stall directive (no evidence, no history edit)."""
import json

from biomodel_agent.research_supervisor import read_stall_directive


def _attempts(*operations):
    return [(f'r{i}', {'operation': op}) for i, op in enumerate(operations)]


def _returns(attempts):
    return {ref: (1, {'state': 'returned'}) for ref, _ in attempts}


def test_three_reads_with_interleaved_planner_turns_produce_a_directive():
    attempts = _attempts('respond', 'inspect_source', 'respond', 'inspect_source',
                         'respond', 'inspect_source', 'respond')
    directive = read_stall_directive(attempts, _returns(attempts))
    payload = json.loads(directive['content'])
    assert payload['tool'] == 'planner_directive'
    assert payload['consecutive_reads'] == 3
    assert 'Stop reading the same source' in payload['directive']


def test_two_reads_or_a_frame_break_the_streak():
    two = _attempts('inspect_source', 'inspect_source')
    assert read_stall_directive(two, _returns(two)) is None
    framed = _attempts('inspect_source', 'inspect_source', 'revise_frame', 'inspect_source')
    assert read_stall_directive(framed, _returns(framed)) is None


def test_unreturned_attempts_are_ignored():
    attempts = _attempts('inspect_source', 'inspect_source', 'inspect_source')
    returns = {attempts[0][0]: (1, {'state': 'returned'})}
    assert read_stall_directive(attempts, returns) is None
