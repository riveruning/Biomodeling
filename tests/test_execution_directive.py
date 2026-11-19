"""A frame without an iteration must be told to execute, not to keep reading."""
import json

from biomodel_agent.research_supervisor import execution_directive


def _history(*ops):
    attempts = [(f'r{i}', {'operation': op}) for i, op in enumerate(ops)]
    return attempts, {ref: (1, {'state': 'returned'}) for ref, _ in attempts}


def test_frame_without_iteration_requires_execution():
    attempts, returns = _history('respond', 'inspect_source', 'revise_frame')
    directive = execution_directive({'frame_ref': 'f', 'iteration_refs': []},
                                    attempts, returns)
    payload = json.loads(directive['content'])
    assert payload['reason'] == 'frame_exists_without_iteration'
    assert 'open an iteration' in payload['directive']


def test_existing_iteration_or_missing_frame_has_no_directive():
    attempts, returns = _history('respond')
    assert execution_directive({'frame_ref': 'f', 'iteration_refs': ['i']},
                               attempts, returns) is None
    assert execution_directive({'frame_ref': None, 'iteration_refs': []},
                               attempts, returns) is None
