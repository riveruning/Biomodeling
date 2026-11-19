"""Safe provider identity; accounting proxies preserve the underlying contract."""
import hashlib


def identity(provider):
    from .research_usage import MeteredProvider
    while isinstance(provider,MeteredProvider):provider=provider.provider
    endpoint=getattr(provider,'base_url',None)
    out=dict(endpoint_sha256=hashlib.sha256(endpoint.encode()).hexdigest() if endpoint else None,
        **{'class':type(provider).__module__+'.'+type(provider).__qualname__},
        **{key:getattr(provider,key) for key in ('model','json_mode','timeout_s','max_tokens') if hasattr(provider,key)})
    if getattr(provider,'stream',False):
        out['stream']=True
    reasoning_effort=getattr(provider,'reasoning_effort',None)
    if reasoning_effort is not None:
        out['reasoning_effort']=reasoning_effort
    return out
