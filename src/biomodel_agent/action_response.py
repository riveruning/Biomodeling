"""Execution projection of a provider response; raw receipts remain unchanged."""
from copy import deepcopy

from .model_contracts import fields


def normalize_action(response):
    """Validate optional provider metadata and return only executable fields."""
    fields(response, {'action', 'args'}, {'reason'})
    if not isinstance(response['action'], str) or not response['action'].strip():
        raise ValueError('action must be a nonempty string')
    if not isinstance(response['args'], dict):
        raise ValueError('action args must be an object')
    if response.get('reason') is not None and not isinstance(response['reason'], str):
        raise ValueError('reason must be a string when present')
    return dict(action=response['action'].strip(), args=deepcopy(response['args']))
