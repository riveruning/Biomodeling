"""Reuse verified bytes within one read query, then verify them again.

There is no persistent cache and no SQLite dependency. This is not an atomic
filesystem snapshot: new records can appear during a query. Every observed
object/blob is checked again before a successful query returns. Write paths
continue to use the original store and its fresh verification rules.
"""
from collections import OrderedDict
from contextlib import contextmanager
import json

from .storage import ArtifactError, ArtifactStore, _validate_ref, sha256_of


class _ReadView(ArtifactStore):
    def __init__(self, store, max_bytes):
        # Deliberately bypass ArtifactStore.__init__: no mkdir, DB or WAL writes.
        self.root = store.root
        self.objects_dir = store.objects_dir
        self.blobs_dir = store.blobs_dir
        self.index_path = store.index_path
        self._limit = max_bytes
        self._bytes = 0
        self._cache = OrderedDict()
        self._observed = {}
        self._closed = False

    def _check_open(self):
        if self._closed:
            raise ArtifactError('read query has ended; open a fresh read view')

    def _raw(self, path, ref):
        self._check_open()
        if path in self._cache:
            self._cache.move_to_end(path)
            return self._cache[path]
        try:
            data = path.read_bytes()
        except FileNotFoundError as exc:
            raise ArtifactError(f'unknown ref {ref}') from exc
        if sha256_of(data) != ref:
            raise ArtifactError(f'ref {ref} failed hash verification')
        self._observed[path] = ref
        if len(data) <= self._limit:
            while self._cache and self._bytes + len(data) > self._limit:
                _, removed = self._cache.popitem(last=False)
                self._bytes -= len(removed)
            self._cache[path] = data
            self._bytes += len(data)
        return data

    def _read_envelope(self, ref):
        ref = _validate_ref(ref)
        raw = self._raw(self.objects_dir / (ref + '.json'), ref)
        try:
            envelope = json.loads(raw.decode('utf-8'))
        except (UnicodeError, ValueError) as exc:
            raise ArtifactError(f'object {ref} is not valid JSON') from exc
        self._check_envelope(ref, envelope)
        # Decoding each time keeps returned dictionaries independent.
        return envelope

    def _object_refs_if_verified(self, ref):
        self._check_open()
        path = self.objects_dir / (ref + '.json')
        if path not in self._cache and not path.exists():
            return None
        return self._read_envelope(ref)['refs']

    def get_blob(self, ref):
        ref = _validate_ref(ref)
        return self._raw(self.blobs_dir / ref, ref)

    def _blob_exists_verified(self, ref):
        self._check_open()
        path = self.blobs_dir / ref
        if path not in self._cache and not path.exists():
            return False
        self.get_blob(ref)
        return True

    def _revalidate(self):
        for path, ref in self._observed.items():
            try:
                current = path.read_bytes()
            except FileNotFoundError as exc:
                raise ArtifactError(f'ref {ref} disappeared during read query') from exc
            if sha256_of(current) != ref:
                raise ArtifactError(f'ref {ref} changed during read query')

    def _close(self):
        self._closed = True
        self._cache.clear()
        self._observed.clear()
        self._bytes = 0

    def _read_only(self, *args, **kwargs):
        raise ArtifactError('read views cannot write or access the SQLite index')

    put = put_blob = recover = _db = _read_only


@contextmanager
def verified_read(store, *, max_bytes=8 * 1024 * 1024):
    """A bounded, query-local byte cache with a final integrity check.

    The usual transitive-ref walk, including its depth limit, is retained.
    Nested read helpers share the caller's view; no cache crosses requests.
    """
    if isinstance(store, _ReadView):
        store._check_open()
        yield store
        return
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError('read cache byte limit must be a nonnegative integer')
    view = _ReadView(store, max_bytes)
    try:
        yield view
        view._revalidate()
    finally:
        view._close()
