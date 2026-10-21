"""Immutable, content-addressed artifact store with a SQLite index.

Design rules (task v01-04-storage):

- Objects live at ``root/objects/<sha256>.json``; the SHA-256 is computed over
  the *exact* canonical-JSON UTF-8 bytes of the envelope.
- Binary blobs live at ``root/blobs/<sha256>``; their SHA-256 is over the raw
  bytes.  Same payload with different refs yields a different hash because the
  envelope includes the kind/payload/refs tuple (or raw bytes differ for
  blobs).
- Canonical JSON: ``json.dumps(..., sort_keys=True, separators=(",", ":"),
  ensure_ascii=False, allow_nan=False)``; any non-JSON-safe value (NaN/Inf,
  non-serializable types) raises :class:`ArtifactError` -- NaN is never
  silently coerced to 0.
- Refs are validated to exist (as object or blob) and re-verified on read;
  unknown refs are rejected at ``put`` time.
- Refs are full 64-hex strings; anything else (path traversal, truncation)
  is rejected.
- No mutable overwrite: writing to an existing ref with different content
  raises; writing identical content returns the existing ref, which the
  caller must re-``verify`` (the index row is untouched).
- Atomic single-writer writes: temp file in the same directory +
  ``os.replace``.
- The SQLite index is closed after each operation (context-managed);
  objects are landed before the index row is written (transactional in the
  put path).
- ``recover()`` re-hashes ``objects/*.json``, verifies filename hash /
  schema / refs, rebuilds missing index rows, and *reports* corrupt or
  orphan entries instead of deleting or overwriting them.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1"

REF_RE = re.compile(r"^[0-9a-f]{64}$")

# Bound on transitive ref-chain depth (cycle-safe: ``seen`` also guards).
_REF_DEPTH_LIMIT = 64

_INDEX_CREATE = """
CREATE TABLE IF NOT EXISTS artifact_index (
    ref TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    id TEXT,
    created_at TEXT NOT NULL
)
"""


class ArtifactError(ValueError):
    """Any store-level integrity, schema, or protocol violation."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_ref(ref: Any) -> str:
    """Refs must be exactly 64 lowercase hex chars; nothing else (blocks
    traversal, short forms, prefixes)."""
    if not isinstance(ref, str) or not REF_RE.fullmatch(ref):
        raise ArtifactError(f"invalid ref (must be 64 lowercase hex chars): {ref!r}")
    return ref


def canonical_bytes(envelope: dict[str, Any]) -> bytes:
    """Canonical JSON: sorted keys, compact separators, raw UTF-8, no NaN/Inf."""
    try:
        text = json.dumps(
            envelope,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (ValueError, TypeError) as exc:
        raise ArtifactError(f"payload is not JSON-safe (no NaN/Inf or non-serializable values): {exc}") from exc
    return text.encode("utf-8")


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write(path: Path, data: bytes) -> None:
    """Write bytes atomically: temp file in the same directory, fsync,
    ``os.replace``.  Single-writer assumption: one store owner per root."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class ArtifactStore:
    """Content-addressed artifact store rooted at ``root``.

    Layout::

        root/
          objects/<sha256>.json   # canonical-JSON envelopes
          blobs/<sha256>          # raw bytes
          index.sqlite            # ref -> kind/id/created_at
    """

    def __init__(self, root: str | os.PathLike) -> None:
        self.root = Path(root)
        self.objects_dir = self.root / "objects"
        self.blobs_dir = self.root / "blobs"
        self.index_path = self.root / "index.sqlite"
        self.objects_dir.mkdir(parents=True, exist_ok=True)
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        with self._db() as conn:
            conn.execute(_INDEX_CREATE)

    # -- index plumbing -----------------------------------------------------

    def _db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.index_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _index_exists(self, ref: str) -> bool:
        with self._db() as conn:
            row = conn.execute(
                "SELECT 1 FROM artifact_index WHERE ref = ?", (ref,)
            ).fetchone()
        return row is not None

    def _index_row(self, ref: str) -> dict[str, Any] | None:
        with self._db() as conn:
            row = conn.execute(
                "SELECT ref, kind, id, created_at FROM artifact_index WHERE ref = ?",
                (ref,),
            ).fetchone()
        return dict(row) if row is not None else None

    def _index_insert(self, ref: str, kind: str, obj_id: str | None) -> None:
        with self._db() as conn:
            conn.execute(
                "INSERT INTO artifact_index (ref, kind, id, created_at) VALUES (?, ?, ?, ?)",
                (ref, kind, obj_id, _utcnow()),
            )

    # -- blob API -----------------------------------------------------------

    def put_blob(self, data: bytes) -> str:
        """Store raw bytes; returns the sha256 ref of the blob."""
        if not isinstance(data, (bytes, bytearray)):
            raise ArtifactError(f"put_blob expects bytes, got {type(data).__name__}")
        data = bytes(data)
        ref = sha256_of(data)
        path = self.blobs_dir / ref
        if path.exists():
            if path.read_bytes() == data:
                return ref  # identical content: immutable, return existing
            raise ArtifactError(f"blob {ref} already exists with different content")
        _atomic_write(path, data)
        return ref

    def get_blob(self, ref: str) -> bytes:
        """Read raw bytes, verifying the hash first.  Refused on any
        mismatch (returns nothing rather than corrupt data)."""
        ref = _validate_ref(ref)
        path = self.blobs_dir / ref
        if not path.exists():
            raise ArtifactError(f"unknown blob ref {ref}")
        data = path.read_bytes()
        if sha256_of(data) != ref:
            raise ArtifactError(f"blob {ref} failed hash verification (tampered or corrupt)")
        return data

    # -- object API ---------------------------------------------------------

    def _read_envelope(self, ref: str) -> dict[str, Any]:
        path = self.objects_dir / f"{ref}.json"
        if not path.exists():
            raise ArtifactError(f"unknown object ref {ref}")
        raw = path.read_bytes()
        if sha256_of(raw) != ref:
            raise ArtifactError(f"object {ref} failed hash verification (tampered or corrupt)")
        try:
            env = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactError(f"object {ref} is not valid JSON: {exc}") from exc
        self._check_envelope(ref, env)
        return env

    @staticmethod
    def _check_envelope(ref: str, env: dict[str, Any]) -> None:
        """Envelope must be *exactly* schema_version/kind/payload/refs.

        Unknown keys are rejected; ``kind`` must be a non-empty string and
        every entry of ``refs`` a full 64-lowercase-hex ref.
        """
        if not isinstance(env, dict):
            raise ArtifactError(f"object {ref}: envelope is not a JSON object")
        if env.get("schema_version") != SCHEMA_VERSION:
            raise ArtifactError(f"object {ref}: unsupported schema_version {env.get('schema_version')!r}")
        unknown = set(env) - {"schema_version", "kind", "payload", "refs"}
        if unknown:
            raise ArtifactError(f"object {ref}: unknown envelope key(s) {sorted(unknown)}")
        for key in ("kind", "payload", "refs"):
            if key not in env:
                raise ArtifactError(f"object {ref}: missing envelope key {key!r}")
        if not isinstance(env["kind"], str) or not env["kind"]:
            raise ArtifactError(f"object {ref}: kind must be a non-empty string, got {env['kind']!r}")
        refs = env["refs"]
        if not isinstance(refs, list):
            raise ArtifactError(f"object {ref}: refs must be a list")
        for r in refs:
            try:
                _validate_ref(r)
            except ArtifactError as exc:
                raise ArtifactError(f"object {ref}: bad ref entry: {exc}") from exc

    def _verify_refs(self, refs: list[str], ref_prefix: str = "") -> None:
        """Every ref (and, recursively, every ref it transitively cites) must
        exist as a verified object or blob.

        ``seen`` prevents cycles / repeated work and ``_REF_DEPTH_LIMIT``
        bounds chain length so a malicious or corrupt chain cannot recurse
        without end.
        """
        self._verify_refs_rec(refs, ref_prefix, set(), 0)

    def _verify_refs_rec(
        self,
        refs: list[str],
        ref_prefix: str,
        seen: set[str],
        depth: int,
    ) -> None:
        if depth > _REF_DEPTH_LIMIT:
            raise ArtifactError(f"{ref_prefix}ref chain exceeds depth limit {_REF_DEPTH_LIMIT}")
        for r in refs:
            r = _validate_ref(r)
            if r in seen:
                continue
            seen.add(r)
            child = self._object_refs_if_verified(r)
            if child is not None:
                self._verify_refs_rec(child, f"object {r} ", seen, depth + 1)
                continue
            if self._blob_exists_verified(r):
                continue
            raise ArtifactError(f"{ref_prefix}references unknown ref {r}")

    def _object_refs_if_verified(self, ref: str) -> list[str] | None:
        """If ``ref`` names a verifiable object, return its ``refs`` (for
        recursive walk); otherwise ``None``.  Raises on a *present but
        invalid* object (hash/schema failure) rather than treating it as
        absent."""
        path = self.objects_dir / f"{ref}.json"
        if not path.exists():
            return None
        raw = path.read_bytes()
        if sha256_of(raw) != ref:
            raise ArtifactError(f"object {ref} failed hash verification")
        try:
            env = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactError(f"object {ref} is not valid JSON: {exc}") from exc
        self._check_envelope(ref, env)
        return list(env["refs"])

    def _blob_exists_verified(self, ref: str) -> bool:
        path = self.blobs_dir / ref
        if not path.exists():
            return False
        data = path.read_bytes()
        if sha256_of(data) != ref:
            raise ArtifactError(f"blob {ref} failed hash verification")
        return True

    def _ensure_index_row(self, ref: str, kind: str, payload: Any) -> None:
        """Add a missing index row for an already-verified object.

        Reusing an existing identical object must still leave the index
        consistent (e.g. after an index loss or a crashed put), so a missing
        row is repaired rather than silently skipped.
        """
        if not self._index_exists(ref):
            self._index_insert(ref, kind, _extract_id(payload))

    def put(self, kind: str, payload: Any, refs: tuple | list | None = None) -> str:
        """Store an immutable object.

        - ``kind`` must be a non-empty string.
        - ``refs`` (default ``()``) must all exist as verified objects or
          blobs; unknown refs are rejected.
        - Identical envelope content returns the existing ref (caller must
          re-``verify``); different content at the same ref raises.
        - The object file is landed *before* the index row is written.
        """
        if not isinstance(kind, str) or not kind:
            raise ArtifactError(f"kind must be a non-empty string, got {kind!r}")
        if refs is None:
            refs = ()
        if isinstance(refs, (list, tuple)):
            refs_list: list[str] = list(refs)
        else:
            raise ArtifactError(f"refs must be a list or tuple, got {type(refs).__name__}")
        for r in refs_list:
            _validate_ref(r)  # reject bad ref shapes before hashing
        self._verify_refs(refs_list, ref_prefix=f"kind={kind} ")

        envelope = {
            "schema_version": SCHEMA_VERSION,
            "kind": kind,
            "payload": payload,
            "refs": refs_list,
        }
        data = canonical_bytes(envelope)  # raises ArtifactError on NaN/Inf
        ref = sha256_of(data)
        path = self.objects_dir / f"{ref}.json"
        if path.exists():
            if path.read_bytes() != data:
                raise ArtifactError(f"object {ref} already exists with different content")
            # Immutable identity: same content, same ref.  The existing object
            # is already verified (hash + recursive refs above); a missing
            # index row is repaired so the reuse is not silently inconsistent.
            self._ensure_index_row(ref, kind, payload)
            return ref
        _atomic_write(path, data)
        # Object landed; now index (same single-writer path).
        self._ensure_index_row(ref, kind, payload)
        return ref

    def get(self, ref: str) -> dict[str, Any]:
        """Return a *fresh* envelope dict (never a shared cached object),
        after re-verifying the hash, envelope schema, and the whole
        transitive ref chain (not just the direct refs)."""
        ref = _validate_ref(ref)
        env = self._read_envelope(ref)  # hash + schema verified
        self._verify_refs(env["refs"], ref_prefix=f"object {ref} ")
        # Return a freshly decoded copy each call; the store keeps no
        # mutable cache.
        return json.loads(json.dumps(env, ensure_ascii=False, allow_nan=False))

    def verify(self, ref: str) -> bool:
        """Re-verify an existing ref end-to-end (hash + schema + full ref
        chain); raises ArtifactError on any problem, returns True when clean."""
        _validate_ref(ref)
        if (self.objects_dir / f"{ref}.json").exists():
            env = self._read_envelope(ref)
            self._verify_refs(env["refs"], ref_prefix=f"object {ref} ")
            return True
        if (self.blobs_dir / ref).exists():
            self.get_blob(ref)
            return True
        raise ArtifactError(f"unknown ref {ref}")

    def list(self, kind: str | None = None) -> list[str]:
        """List indexed refs, optionally filtered by kind, oldest first."""
        with self._db() as conn:
            if kind is None:
                rows = conn.execute(
                    "SELECT ref FROM artifact_index ORDER BY created_at, ref"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT ref FROM artifact_index WHERE kind = ? ORDER BY created_at, ref",
                    (kind,),
                ).fetchall()
        return [row["ref"] for row in rows]

    # -- recovery -----------------------------------------------------------

    def recover(self) -> dict[str, list[str]]:
        """Scan ``objects/*.json``, verify filename hash / schema / refs,
        rebuild missing index rows, and *report* (not delete/overwrite)
        corrupt or orphan entries.

        Returns ``{"rebuilt": [...], "corrupt": [...], "orphans": [...]}``
        where *corrupt* fails hash/schema/ref verification and *orphans*
        are verified objects missing from the index (rebuilt into it).
        """
        rebuilt: list[str] = []
        corrupt: list[str] = []
        orphans: list[str] = []
        for path in sorted(self.objects_dir.glob("*.json")):
            name = path.name[:-5]  # strip ".json"
            try:
                if not REF_RE.fullmatch(name):
                    raise ArtifactError("filename is not a valid ref")
                raw = path.read_bytes()
                if sha256_of(raw) != name:
                    raise ArtifactError("filename hash mismatch")
                env = json.loads(raw.decode("utf-8"))
                self._check_envelope(name, env)
                self._verify_refs(env["refs"], ref_prefix=f"object {name} ")
            except (ArtifactError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                # Report, never delete or overwrite: undecodable bytes,
                # malformed JSON, bad schema, or bad refs all count as corrupt.
                corrupt.append(f"{name}: {exc}")
                continue
            if not self._index_exists(name):
                self._index_insert(name, env["kind"], _extract_id(env["payload"]))
                orphans.append(name)
            rebuilt.append(name)
        return {"rebuilt": rebuilt, "corrupt": corrupt, "orphans": orphans}


def _extract_id(payload: Any) -> str | None:
    """Best-effort stable identifier from a payload dict; None otherwise."""
    if isinstance(payload, dict):
        for key in ("id", "run_id", "source_ref", "claim_id"):
            v = payload.get(key)
            if isinstance(v, str) and v:
                return v
    return None