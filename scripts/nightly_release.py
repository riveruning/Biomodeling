#!/usr/bin/env python3
"""Nightly release helper for the Biomodeling repository.

Purpose-limited assistant for the user-approved nightly routine: every day at
00:00 Beijing time the nightly task organizes the newest working-branch state,
validates it, commits and pushes it to the user's own GitHub repository
(riveruning/Biomodeling).

Three fixed CLI modes (nothing else):

    .venv/bin/python scripts/nightly_release.py inspect --date YYYY-MM-DD
    .venv/bin/python scripts/nightly_release.py verify  --date YYYY-MM-DD
    .venv/bin/python scripts/nightly_release.py publish --date YYYY-MM-DD

Safety properties:

* Repository root and origin identity are constants/derived values, never CLI
  options.  The origin must resolve to riveruning/Biomodeling.
* The branch is the current checked-out working branch (read from git, not a
  flag).  publish refuses to operate on a different branch than the one that
  was verified.
* All subprocesses use argv lists with shell=False.  Git inherits the existing
  authentication/identity configuration; interactive prompts are disabled so a
  nightly run can never hang waiting for credentials.  SSH private keys are
  never read; secrets are never printed (URLs with credentials are redacted).
* No force push, no remote mutation, no ``git add .``: only explicitly selected
  paths are staged and committed with ``git commit --only -- <paths>``, so
  previously staged unrelated content is preserved untouched.
* Verification failures are recorded honestly (status "failed") and are never
  reported as verified; a failed run is kept as evidence for the nightly agent
  to fix or judge.
* Every state transition (verified / committed / pushed) is stored in an
  atomic JSON state file, so a crashed run can tell commit and push apart and
  a same-date retry is idempotent (already-pushed SHAs are not recommitted).

This file is the reviewed deliverable.  Codex audits it and installs it as
``scripts/nightly_release.py`` pinned by sha256 in a root-owned trusted
manifest; nightly runs may only execute that audited copy.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

# ---------------------------------------------------------------------------
# Fixed constants (never CLI-overridable)
# ---------------------------------------------------------------------------

REPO_ROOT = Path("/home/riveruning/biologymodel_agent")
EXPECTED_OWNER = "riveruning"
EXPECTED_REPO = "Biomodeling"

GIT_TIMEOUT = 180  # seconds per git call
VALIDATION_TIMEOUT = 2700  # ceiling for one validation run (full pytest ~17 min)
# How long publish waits for the repository-level flock before reporting the
# contention as a blocker (the lock is only held for the seconds a commit+push
# takes, never during validation).
PUBLISH_LOCK_WAIT_S = 60.0
# Exact validation command for the production repository.  Tests monkeypatch
# this constant (never a CLI flag) so fixtures stay hermetic.
VALIDATION_COMMAND: tuple[str, ...] = (".venv/bin/python", "-m", "pytest", "-q")

UNTRACKED_MAX_BYTES = 5 * 1024 * 1024  # 5 MiB cap for newly added files
CHUNK = 100  # max paths per git argv (keeps commands well under ARGV_MAX)
ARGV_BOUND = 300  # above this, pathspec-from-file is used instead of one argv

# Only this host is an acceptable release destination.
GIT_HOST = "github.com"
# Production default: the *effective* push destination (after url.*.insteadOf
# rewriting) must itself be github.com/<owner>/<repo>.  Tests that redirect the
# transport to a throwaway local bare repo monkeypatch this constant (never a
# CLI flag); production keeps refusing rewritten/aliased destinations.
REQUIRE_GITHUB_TRANSPORT = True

# Directories whose tracked content counts as version source for validation
# (everything else is either run evidence, tooling state or external data).
RELEASE_SCOPE_DIRS = frozenset(
    {"src", "tests", "docs", "examples", "scripts", "config", "benchmarks", ".github"}
)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Untracked files are only candidates inside these top-level directories plus
# top-level regular version files that git already tracks.
UNTRACKED_TOP_DIRS = frozenset(
    {"src", "tests", "docs", "examples", "scripts", "config", ".github", "benchmarks"}
)
# Directory names never released, at any depth (run products, caches, secrets).
EXCLUDED_DIR_NAMES = frozenset(
    {
        ".git", ".venv", ".workflow", "artifacts", "runs", "tasks",
        "discussion-notes", "build", "dist", "htmlcov", "node_modules",
        "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
        ".agents", ".aws", ".codex", ".idea", ".vscode",
    }
)
# Untracked file patterns never released (credentials, private config, huge or
# opaque binary data).  Tracked files are already part of the repository and
# are selected regardless of these patterns.
EXCLUDED_SUFFIXES = (
    ".pem", ".key", ".p12", ".pfx", ".pkl", ".h5", ".npz", ".npy",
    ".parquet", ".sqlite", ".db", ".log", ".zip", ".tar", ".gz", ".xz",
    ".7z", ".dll", ".exe", ".so", ".dylib", ".pyc", ".pyo",
)
# Basenames are matched with an optional extension so credential files such as
# config/credentials.json, client_secret.json and token.json are caught whether
# they are tracked, newly added, or only staged by another task.
SECRET_BASENAMES = re.compile(
    r"(^|\.)(id_rsa|id_ed25519|id_ecdsa|credentials?|client[_-]?secret|secrets?|tokens?)(\.[A-Za-z0-9._-]+)*$"
)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2

# manifest id of an empty selection: after a commit the working delta is empty,
# so a retry must recognise this id instead of demanding a fresh verification.
EMPTY_MANIFEST_ID = hashlib.sha256(b"{}").hexdigest()


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def redact(text: str) -> str:
    """Remove credential-looking material before it can reach output/state."""
    if not text:
        return text
    text = re.sub(r"(?i)\b((?:https?|ssh|git)://)[^\s/@:]+:[^\s/@]+@", r"\1***@", text)
    text = re.sub(r"\b(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{16,}\b", "***", text)
    return text


def valid_date(value: str) -> str:
    if not DATE_RE.match(value or ""):
        raise ValueError(f"date must be YYYY-MM-DD, got {value!r}")
    datetime.strptime(value, "%Y-%m-%d")
    return value


def safe_relpath(path: str) -> bool:
    """True for a normalized repo-relative path we are willing to hand to git."""
    if not path or path.startswith("/") or "\\" in path:
        return False
    parts = Path(path).parts
    return ".." not in parts and "." not in parts and Path(path) == Path(*parts)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def sha256_file(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Git plumbing (argv + shell=False only)
# ---------------------------------------------------------------------------

def run_git(repo: Path, args: Sequence[str], timeout: int = GIT_TIMEOUT) -> subprocess.CompletedProcess:
    """Run one git command with argv (shell=False); never raises on exit code."""
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")  # fail instead of prompting
    env.setdefault("GIT_SSH_COMMAND", "ssh -oBatchMode=yes")  # keep existing value if set
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            ["git", *args], 124, stdout=exc.stdout or "", stderr=f"timeout after {timeout}s"
        )
    except OSError as exc:
        return subprocess.CompletedProcess(["git", *args], 127, stdout="", stderr=str(exc))


def git_ok(repo: Path, args: Sequence[str]) -> tuple[bool, str]:
    proc = run_git(repo, args)
    return proc.returncode == 0, redact(proc.stderr.strip() or proc.stdout.strip())


def git_out(repo: Path, args: Sequence[str]) -> str | None:
    proc = run_git(repo, args)
    return proc.stdout if proc.returncode == 0 else None


def credential_bearing(url: str) -> bool:
    """True when a URL carries user information or a token-shaped secret."""
    if not url:
        return False
    if re.search(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^/@\s]+:[^/@\s]+@", url):
        return True
    return bool(re.search(r"\b(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{16,}\b", url))


def parse_origin(url: str) -> tuple[str, str] | None:
    """(owner, repo) for github.com URLs only; None for anything else.

    Any other host (including enterprise forks and local paths) is refused, and
    a URL whose *credentials* look real is refused by ``credential_bearing`` —
    the userinfo part itself is tolerated here so redacted URLs stay comparable.
    """
    url = (url or "").strip().rstrip("/")
    if not url or credential_bearing(url):
        return None
    host = re.escape(GIT_HOST)  # exact hostname match: githubXcom etc. must not slip through
    patterns = (
        rf"^(?:https?|ssh|git)://(?:[^/@\s]+@)?{host}/([^/]+)/([^/]+?)(?:\.git)?/?$",
        rf"^git@{host}:/?([^/]+)/([^/]+?)(?:\.git)?/?$",
        rf"^{host}:([^/]+)/([^/]+?)(?:\.git)?/?$",
    )
    for pattern in patterns:
        match = re.match(pattern, url, re.IGNORECASE)
        if match:
            owner, name = match.group(1), match.group(2)
            if owner and name and "/" not in name:
                return owner, name
    return None


def effective_push_urls(repo: Path) -> tuple[list[str], str | None]:
    """Effective push destinations of origin (after url.*.insteadOf rewriting).

    Returns the raw URL strings (never print them unredacted) and an error
    string when git could not be asked.
    """
    proc = run_git(repo, ["remote", "get-url", "--push", "--all", "origin"])
    if proc.returncode != 0:
        return [], redact(proc.stderr.strip()) or "git remote get-url --push failed"
    urls = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return urls, None


def instead_of_rewrites(repo: Path) -> list[dict[str, str]]:
    """Configured url.<base>.insteadOf rewrites (redacted, for the record)."""
    proc = run_git(repo, ["config", "--get-regexp", r"^url\..*\.insteadOf$"])
    if proc.returncode != 0:
        return []
    rewrites: list[dict[str, str]] = []
    for line in proc.stdout.splitlines():
        key, _, value = line.partition(" ")
        rewrites.append({"config": key, "rewrites_to": redact(value.strip())})
    return rewrites


def push_destination_blockers(urls: list[str], query_error: str | None) -> list[str]:
    """Safety rules for the destination a push would actually reach."""
    if query_error:
        return [f"cannot read the effective push destination: {query_error}"]
    if not urls:
        return ["origin has no push destination configured"]
    if len(urls) > 1:
        return [f"origin has {len(urls)} push destinations; refusing an ambiguous push"]
    url = urls[0]
    if credential_bearing(url):
        return ["configured push destination carries credentials; refusing (destination not printed)"]
    if parse_origin(url) != (EXPECTED_OWNER, EXPECTED_REPO):
        if REQUIRE_GITHUB_TRANSPORT:
            return [
                f"effective push destination is not {EXPECTED_OWNER}/{EXPECTED_REPO} at {GIT_HOST}: {redact(url)}"
            ]
    return []


def repo_identity(repo: Path) -> dict[str, Any]:
    """Read-only identity facts: work tree, branch, HEAD, origin, push destination."""
    identity: dict[str, Any] = {
        "repo_root": str(repo),
        "is_git_repo": False,
        "is_repo_root": False,
        "branch": None,
        "head_sha": None,
        "head_subject": None,
        "origin_url": None,
        "origin_owner": None,
        "origin_repo": None,
        "origin_identity_ok": False,
        "push_urls": [],
        "push_query_error": None,
        "instead_of_rewrites": [],
        "push_destination_ok": False,
        "remotes": [],
        "errors": [],
    }
    if not repo.is_dir():
        identity["errors"].append(f"repo root does not exist: {repo}")
        return identity
    top = git_out(repo, ["rev-parse", "--show-toplevel"])
    if top is None:
        identity["errors"].append(redact(run_git(repo, ["rev-parse", "--show-toplevel"]).stderr.strip()))
        return identity
    identity["is_git_repo"] = True
    identity["is_repo_root"] = Path(top.strip()) == repo
    branch = git_out(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    identity["branch"] = branch.strip() if branch else None
    head = git_out(repo, ["rev-parse", "HEAD"])
    identity["head_sha"] = head.strip() if head else None
    subject = git_out(repo, ["log", "-1", "--format=%s"])
    identity["head_subject"] = subject.strip() if subject else None
    # Raw configured URL (identity source; `git remote get-url` would apply
    # url.*.insteadOf rewriting and hide the pinned GitHub identity).
    url = git_out(repo, ["config", "--get", "remote.origin.url"])
    if url:
        raw = url.strip()
        identity["origin_url"] = redact(raw)
        if credential_bearing(raw):
            identity["origin_credential_bearing"] = True
            identity["errors"].append("configured origin URL carries credentials; refusing (not printed)")
        else:
            parsed = parse_origin(raw)
            if parsed:
                identity["origin_owner"], identity["origin_repo"] = parsed
    identity["origin_identity_ok"] = (identity["origin_owner"], identity["origin_repo"]) == (
        EXPECTED_OWNER, EXPECTED_REPO)
    push_urls, push_error = effective_push_urls(repo)
    identity["push_urls"] = [redact(u) for u in push_urls]
    identity["push_query_error"] = push_error
    identity["instead_of_rewrites"] = instead_of_rewrites(repo)
    blockers = push_destination_blockers(push_urls, push_error)
    if identity.get("origin_credential_bearing"):
        blockers.append("configured origin URL carries credentials; refusing (not printed)")
    identity["push_destination_blockers"] = blockers
    identity["push_destination_ok"] = not blockers
    remotes = git_out(repo, ["remote", "-v"])
    if remotes:
        seen: set[str] = set()
        for line in remotes.splitlines():
            name, _, target = line.partition("\t")
            if name not in seen:
                seen.add(name)
                identity["remotes"].append({"name": name, "url": redact(target.split(" ")[0])})
    return identity


def identity_blockers(identity: dict[str, Any]) -> list[str]:
    """Hard checks that must pass before verify/publish touch anything."""
    blockers: list[str] = []
    if not identity["is_git_repo"]:
        blockers.append(f"not a git repository: {identity['repo_root']}")
        return blockers
    if not identity["is_repo_root"]:
        blockers.append("repo root constant does not match the git work-tree top level")
    if not identity["branch"] or identity["branch"] == "HEAD":
        blockers.append("detached HEAD; refusing to run on a non-branch state")
    if not identity["origin_identity_ok"]:
        blockers.append(
            f"origin must be {EXPECTED_OWNER}/{EXPECTED_REPO} at {GIT_HOST}; "
            f"got owner={identity['origin_owner']!r} repo={identity['origin_repo']!r} url={identity['origin_url']!r}"
        )
    blockers.extend(identity.get("push_destination_blockers") or [])
    return blockers


# ---------------------------------------------------------------------------
# Change selection and content manifest
# ---------------------------------------------------------------------------

def list_changes(repo: Path) -> list[dict[str, Any]]:
    """Parse `git status --porcelain=v1 -z --untracked-files=all`.

    Fixed-width parsing: the first two bytes are the XY status columns (X =
    index/staged, Y = worktree) and the third byte is a single space, so paths
    that begin with a space survive.  For a rename/copy the ORIG_PATH follows
    as its own NUL-terminated record; a second ``D`` (worktree deletion) is
    recorded explicitly.
    """
    proc = run_git(repo, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
    if proc.returncode != 0:
        raise RuntimeError(redact(proc.stderr.strip()) or "git status failed")
    entries: list[dict[str, Any]] = []
    tokens = proc.stdout.split("\0")
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if not token:
            continue
        if len(token) < 4:
            raise RuntimeError(f"malformed git status record: {token!r}")
        code = token[:2]
        path = token[3:]
        entry: dict[str, Any] = {
            "code": code,
            "index_status": code[0],
            "worktree_status": code[1],
            "path": path,
            "tracked": code != "??",
        }
        if "R" in code or "C" in code:  # rename/copy: ORIG_PATH is the next record
            if index >= len(tokens):
                raise RuntimeError("git status rename record without ORIG_PATH")
            entry["old_path"] = tokens[index]
            index += 1
        if "D" in code:
            entry["deleted"] = True
        entries.append(entry)
    return entries

def head_tree_paths(repo: Path) -> set[str]:
    """Paths present in the HEAD tree (one read-only call)."""
    out = git_out(repo, ["ls-tree", "-r", "--name-only", "-z", "HEAD"])
    return set(out.split("\0")) if out is not None else set()


def classify_protected_path(path: str) -> str | None:
    """Exclusion reason that applies to tracked and new paths alike.

    Run products, caches, private credentials and opaque data never enter a
    release — being tracked (or staged by another task) does not declassify
    them.
    """
    parts = Path(path).parts
    for part in parts:
        if part in EXCLUDED_DIR_NAMES:
            return f"excluded-dir:{part}"
    base = parts[-1]
    if base.startswith(".env") and base != ".env.example":
        return "secret-env-file"
    if SECRET_BASENAMES.search(base.lower()) and base != ".env.example":
        return "secret-or-credential-name"
    if base.endswith(EXCLUDED_SUFFIXES):
        return f"excluded-suffix:{base.rsplit('.', 1)[-1]}"
    return None


def is_symlink(path: Path) -> bool:
    """True for symlinks; unreadable entries count as unsafe."""
    try:
        return path.is_symlink()
    except OSError:
        return True


def classify_new_path(path: str, size: int | None) -> str | None:
    """Return an exclusion reason for a path that is not in HEAD, or None.

    Applied to untracked files and to files another task staged with
    ``git add -f``: protected content first, then the release-directory and
    size limits.
    """
    protected = classify_protected_path(path)
    if protected:
        return protected
    parts = Path(path).parts
    if parts[0] not in UNTRACKED_TOP_DIRS:
        return "new-file-outside-release-dirs"
    if size is not None and size > UNTRACKED_MAX_BYTES:
        return f"oversize:{size}-bytes"
    return None


def select_changes(
    repo: Path, entries: list[dict[str, Any]], head_paths: set[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split changes into release candidates and recorded exclusions.

    Tracked modifications/deletions/renames are candidates because they are
    part of the versioned tree — except protected content, which stays
    excluded even when tracked.  New files must additionally live in a release
    directory and respect the size cap.  Symlinks are never selected: their
    payload lives outside the repository.
    """
    selected: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for entry in entries:
        path = entry["path"]
        old_path = entry.get("old_path")
        tracked = path in head_paths or bool(old_path and old_path in head_paths)
        if not safe_relpath(path) or (old_path and not safe_relpath(old_path)):
            exclusions.append({"path": path, "reason": "unsafe-path"})
            continue
        for candidate in (path, old_path):
            if candidate and is_symlink(repo / candidate):
                exclusions.append({"path": path, "reason": "symlink"})
                break
        else:
            if tracked:
                reason = classify_protected_path(path)
                if reason is None and old_path:
                    reason = classify_protected_path(old_path)
                if reason is None:
                    selected.append(entry)
                else:
                    exclusions.append({"path": path, "reason": reason})
            else:
                try:
                    size = (repo / path).stat().st_size
                except OSError:
                    size = None
                reason = classify_new_path(path, size)
                if reason is None:
                    selected.append(entry)
                else:
                    exclusions.append({"path": path, "reason": reason})
    selected.sort(key=lambda item: item["path"])
    exclusions.sort(key=lambda item: item["path"])
    return selected, exclusions


def selected_paths(selected: Sequence[dict[str, Any]]) -> list[str]:
    """Every path to hand to git: new/modified plus the old side of renames."""
    paths: list[str] = []
    for entry in selected:
        if "old_path" in entry:
            paths.append(entry["old_path"])
        paths.append(entry["path"])
    return sorted(set(paths))


def head_blob_sha(repo: Path, path: str) -> str | None:
    out = git_out(repo, ["rev-parse", f"HEAD:{path}"])
    return out.strip() if out else None


def build_manifest(repo: Path, selected: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Stable content manifest of the selected release delta.

    Keys are sorted repo-relative paths; values are sha256 of the current
    working-tree content (deleted paths keep the HEAD blob id instead).  The
    manifest id is the sha256 of the canonical JSON, so identical content
    always yields the same id.
    """
    manifest: dict[str, Any] = {}
    for entry in selected:
        path = entry["path"]
        if not safe_relpath(path):
            continue
        file_path = repo / path
        if entry.get("deleted") or "D" in entry["code"] or not file_path.exists():
            manifest[path] = {"status": "deleted", "head_git_blob_sha": head_blob_sha(repo, path)}
            continue
        if "old_path" in entry:
            manifest[path] = {"status": "renamed_from:" + entry["old_path"], **content_hash(file_path)}
        else:
            manifest[path] = {"status": entry["code"], **content_hash(file_path)}
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return {
        "manifest": manifest,
        "manifest_id": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "file_count": len(manifest),
    }


def content_hash(file_path: Path) -> dict[str, Any]:
    digest = sha256_file(file_path)
    if digest is None:
        return {"sha256": None, "bytes": None, "missing": True}
    return {"sha256": digest, "bytes": file_path.stat().st_size}


def tracked_source_paths(repo: Path) -> list[str]:
    """Every tracked file that belongs to the version source.

    Tracked files under the release directories plus top-level regular files:
    this is the complete input the validation run consumes, not only the
    selected delta, so drift in an unchanged-at-start file is visible too.
    """
    out = git_out(repo, ["ls-files", "-z"])
    if out is None:
        return []
    paths: list[str] = []
    for path in out.split("\0"):
        if not path or not safe_relpath(path):
            continue
        if "/" in path and path.split("/", 1)[0] not in RELEASE_SCOPE_DIRS:
            continue
        paths.append(path)
    return sorted(paths)


def source_snapshot(repo: Path, extra_paths: Sequence[str]) -> dict[str, str | None]:
    """sha256 of every version-source file plus the given new selected paths."""
    snapshot: dict[str, str | None] = {}
    for path in tracked_source_paths(repo):
        snapshot[path] = sha256_file(repo / path)
    for path in extra_paths:
        if path not in snapshot:
            snapshot[path] = sha256_file(repo / path)
    return snapshot


def snapshot_id(snapshot: dict[str, str | None]) -> str:
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def diff_snapshots(before: dict[str, str | None], after: dict[str, str | None]) -> list[str]:
    """Paths whose content changed (or appeared/vanished) between snapshots."""
    return sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))


def state_dir(repo: Path, date: str) -> Path:
    # Under artifacts/ (gitignored): evidence stays local, out of the release.
    return repo / "artifacts" / "runs" / "nightly-release" / date


def verify_state_path(repo: Path, date: str, manifest_id: str) -> Path:
    return state_dir(repo, date) / f"verify-{manifest_id}.json"


def publish_state_path(repo: Path, date: str) -> Path:
    return state_dir(repo, date) / "publish.json"


# ---------------------------------------------------------------------------
# Environment cross-checks (defense in depth with the purpose grant)
# ---------------------------------------------------------------------------

def env_guards(date: str) -> list[str]:
    blockers: list[str] = []
    repository = os.environ.get("NIGHTLY_REPOSITORY")
    if repository and repository != f"{EXPECTED_OWNER}/{EXPECTED_REPO}":
        blockers.append(f"NIGHTLY_REPOSITORY={repository!r} does not match {EXPECTED_OWNER}/{EXPECTED_REPO}")
    nightly_date = os.environ.get("NIGHTLY_DATE")
    if nightly_date and nightly_date != date:
        blockers.append(f"NIGHTLY_DATE={nightly_date!r} does not match --date {date!r}")
    return blockers


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def emit(payload: dict[str, Any], exit_code: int) -> int:
    payload.setdefault("mode", payload.get("mode"))
    payload["ok"] = exit_code == EXIT_OK
    payload["finished_at"] = utc_now_iso()
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


def chunked(items: Sequence[str], size: int = CHUNK) -> Iterable[Sequence[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


# ---------------------------------------------------------------------------
# inspect: read-only facts + safe network auth probe
# ---------------------------------------------------------------------------

def cmd_inspect(date: str) -> tuple[dict[str, Any], int]:
    repo = REPO_ROOT
    payload: dict[str, Any] = {"mode": "inspect", "date": date, "started_at": utc_now_iso()}
    identity = repo_identity(repo)
    payload["identity"] = identity
    payload["push_destination"] = {
        "effective": identity.get("push_urls"),
        "ok": identity.get("push_destination_ok"),
        "blockers": identity.get("push_destination_blockers"),
        "instead_of_rewrites": identity.get("instead_of_rewrites"),
    }
    if not identity["is_git_repo"]:
        return payload, EXIT_USAGE
    if not identity["is_repo_root"] or not identity["branch"] or not identity["origin_identity_ok"]:
        payload["identity_blockers"] = identity_blockers(identity)

    try:
        entries = list_changes(repo)
        selected, exclusions = select_changes(repo, entries, head_tree_paths(repo))
        payload["changes"] = [
            {"code": e["code"], "path": e["path"], **({"old_path": e["old_path"]} if "old_path" in e else {})}
            for e in entries
        ]
        payload["selection_summary"] = {
            "candidate_count": len(selected_paths(selected)),
            "excluded_count": len(exclusions),
            "excluded": exclusions,
        }
    except RuntimeError as exc:
        payload["changes_error"] = str(exc)
        return payload, EXIT_FAILED

    branch = identity["branch"]
    probe: dict[str, Any] = {"ls_remote_ok": False, "push_dry_run_ok": False}
    ls = run_git(repo, ["ls-remote", "origin", f"refs/heads/{branch}"])
    probe["ls_remote_ok"] = ls.returncode == 0
    if ls.returncode == 0:
        line = ls.stdout.split()
        probe["remote_sha"] = line[0] if line else None
        probe["branch_exists_on_remote"] = bool(line)
        if line:
            counts = git_out(repo, ["rev-list", "--left-right", "--count", f"{line[0]}...HEAD"])
            if counts:
                behind, ahead = counts.split()[0], counts.split()[1]
                probe["sync"] = {"behind": int(behind), "ahead": int(ahead), "method": "ls-remote-sha"}
        dry = run_git(repo, ["push", "--dry-run", "--porcelain", "origin", branch])
        probe["push_dry_run_ok"] = dry.returncode == 0
        probe["push_dry_run"] = redact(dry.stdout.strip())[:2000]
        if dry.returncode != 0:
            probe["push_dry_run_error"] = redact(dry.stderr.strip())[:2000]
    else:
        probe["error"] = redact(ls.stderr.strip())[:2000]
        tracking = git_out(repo, ["rev-list", "--left-right", "--count", f"origin/{branch}...HEAD"])
        if tracking:
            behind, ahead = tracking.split()[0], tracking.split()[1]
            probe["sync"] = {"behind": int(behind), "ahead": int(ahead), "method": "local-tracking-ref"}
    payload["remote"] = probe
    return payload, EXIT_OK


# ---------------------------------------------------------------------------
# verify: content manifest + real validation with kept evidence
# ---------------------------------------------------------------------------

def run_validation(repo: Path, log_path: Path) -> dict[str, Any]:
    started = utc_now_iso()
    started_ts = datetime.now(timezone.utc).timestamp()
    log_path.parent.mkdir(parents=True, exist_ok=True)  # never fail on a missing dir
    with open(log_path, "wb") as log:
        try:
            proc = subprocess.run(
                list(VALIDATION_COMMAND),
                cwd=str(repo),
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=VALIDATION_TIMEOUT,
                shell=False,
            )
            exit_code, note = proc.returncode, None
        except subprocess.TimeoutExpired:
            exit_code, note = 124, f"validation timed out after {VALIDATION_TIMEOUT}s"
        except OSError as exc:
            exit_code, note = 127, redact(str(exc))
    ended = utc_now_iso()
    duration = round(datetime.now(timezone.utc).timestamp() - started_ts, 1)
    return {
        "command": list(VALIDATION_COMMAND),
        "shell": False,
        "exit_code": exit_code,
        "note": note,
        "started_at": started,
        "ended_at": ended,
        "duration_s": duration,
        "log_path": str(log_path),
        "log_sha256": sha256_file(log_path),
    }


def cmd_verify(date: str, reuse: str | None) -> tuple[dict[str, Any], int]:
    repo = REPO_ROOT
    payload: dict[str, Any] = {"mode": "verify", "date": date, "started_at": utc_now_iso()}
    identity = repo_identity(repo)
    payload["identity"] = identity
    blockers = identity_blockers(identity) + env_guards(date)
    if blockers:
        payload["identity_blockers"] = blockers
        return payload, EXIT_USAGE

    selected, exclusions = select_changes(repo, list_changes(repo), head_tree_paths(repo))
    built = build_manifest(repo, selected)
    manifest, manifest_id = built["manifest"], built["manifest_id"]
    selected_before = selected_paths(selected)
    snapshot_before = source_snapshot(repo, selected_before)
    payload["selection"] = {
        "manifest_id": manifest_id,
        "file_count": built["file_count"],
        "manifest": manifest,
        "excluded": exclusions,
        "branch": identity["branch"],
        "head_sha": identity["head_sha"],
        "source_files": len(snapshot_before),
        "source_snapshot_id": snapshot_id(snapshot_before),
        "scope": "complete tracked version source plus the selected working-tree delta",
    }
    sdir = state_dir(repo, date)
    sdir.mkdir(parents=True, exist_ok=True)  # the validation log needs this dir

    if reuse:
        return reuse_evidence(repo, date, manifest_id, identity, Path(reuse), payload)

    log_path = sdir / f"pytest-{manifest_id[:12]}.log"
    validation = run_validation(repo, log_path)

    # Re-enumerate everything after the run: a passing test says nothing about
    # inputs that drifted underneath it (unchanged-at-start tracked files, new
    # files appearing mid-run, a selected file disappearing).
    selected_after, _exclusions_after = select_changes(repo, list_changes(repo), head_tree_paths(repo))
    built_after = build_manifest(repo, selected_after)
    selected_after_paths = selected_paths(selected_after)
    snapshot_after = source_snapshot(repo, selected_after_paths)
    drift = diff_snapshots(snapshot_before, snapshot_after)
    new_selected = sorted(set(selected_after_paths) - set(selected_before))
    dropped_selected = sorted(set(selected_before) - set(selected_after_paths))
    head_after = head_sha(repo)
    head_moved = head_after != identity["head_sha"]
    manifest_id = built_after["manifest_id"]
    if validation["exit_code"] != 0:
        # Real run, stable version: an honest failure.  It may be released as a
        # WIP commit that records the failure — never as verified work.
        status, invalid_reason = "failed", "validation_failed"
    elif drift or new_selected or dropped_selected:
        # What was validated is not what is on disk now: the evidence is stale
        # and can never be published; a fresh verify is required.
        status, invalid_reason = "stale", "source_changed_during_validation"
    elif head_moved:
        # A branch move during validation invalidates the HEAD the evidence was
        # captured at; publish will require a fresh verify at the new HEAD.
        status, invalid_reason = "stale", "head_changed_during_validation"
    else:
        status, invalid_reason = "verified", None
    evidence: dict[str, Any] = {
        "mode": "verify",
        "date": date,
        "repo_root": str(repo),
        "origin_owner": identity["origin_owner"],
        "origin_repo": identity["origin_repo"],
        "branch": identity["branch"],
        "head_sha": identity["head_sha"],
        "head_sha_after_validation": head_after,
        "head_moved_during_validation": head_moved,
        "manifest_id": manifest_id,
        "manifest": built_after["manifest"],
        "file_count": built_after["file_count"],
        "excluded": exclusions,
        "source_snapshot_id_before": snapshot_id(snapshot_before),
        "source_snapshot_id_after": snapshot_id(snapshot_after),
        "source_file_count": len(snapshot_after),
        "files_hash_before_validation": snapshot_before,
        "files_hash_after_validation": snapshot_after,
        "files_changed_during_validation": drift,
        "new_selected_during_validation": new_selected,
        "dropped_selected_during_validation": dropped_selected,
        "invalid_reason": invalid_reason,
        "validation": validation,
        "verification_status": status,
        "scope": "complete tracked version source plus the selected working-tree delta",
        "state_path": str(verify_state_path(repo, date, manifest_id)),
        "started_at": payload["started_at"],
        "finished_at": utc_now_iso(),
    }
    atomic_write_json(Path(evidence["state_path"]), evidence)
    payload["verification_status"] = status
    payload["invalid_reason"] = invalid_reason
    payload["manifest_id"] = manifest_id
    payload["head_sha_after_validation"] = head_after
    payload["head_moved_during_validation"] = head_moved
    payload["files_changed_during_validation"] = drift
    payload["new_selected_during_validation"] = new_selected
    payload["validation"] = {k: validation[k] for k in ("command", "exit_code", "note", "duration_s", "log_path")}
    payload["state_path"] = evidence["state_path"]
    return payload, (EXIT_OK if status == "verified" else EXIT_FAILED)


def reuse_evidence(
    repo: Path,
    date: str,
    manifest_id: str,
    identity: dict[str, Any],
    reuse_path: Path,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    """Reuse prior validation evidence only for the exact same source content."""
    prior = load_json(reuse_path)
    if prior is None:
        payload["reuse_error"] = f"cannot read reuse state: {reuse_path}"
        return payload, EXIT_USAGE
    drifted = bool(prior.get("files_changed_during_validation")) or bool(
        prior.get("new_selected_during_validation")) or bool(
        prior.get("dropped_selected_during_validation")) or bool(
        prior.get("head_moved_during_validation"))
    same = (
        prior.get("manifest_id") == manifest_id
        and prior.get("branch") == identity["branch"]
        and prior.get("head_sha") == identity["head_sha"]
        and prior.get("repo_root") == str(repo)
        and prior.get("verification_status") == "verified"
        and not drifted
    )
    if not same:
        payload["reuse_error"] = (
            "reuse rejected: manifest/branch/HEAD differ or prior evidence is not verified; "
            "validation must be re-run on this exact source"
        )
        return payload, EXIT_FAILED
    evidence = dict(prior)
    evidence["reused_from"] = str(reuse_path)
    evidence["reuse_scope_note"] = (
        "validation evidence reused only because manifest_id, branch and HEAD are identical "
        "and the prior run saw no source drift"
    )
    evidence["finished_at"] = utc_now_iso()
    atomic_write_json(Path(evidence["state_path"]), evidence)
    payload["verification_status"] = prior.get("verification_status")
    payload["manifest_id"] = manifest_id
    payload["reused_from"] = str(reuse_path)
    payload["state_path"] = evidence["state_path"]
    return payload, EXIT_OK


# ---------------------------------------------------------------------------
# publish: traceable commit of exactly the verified delta, then push
# ---------------------------------------------------------------------------

def commit_message(date: str, manifest_id: str, branch: str, file_count: int, status: str, evidence_rel: str) -> str:
    lines = [
        f"chore(release): nightly working-branch update {date}",
        "",
        f"Nightly-Release-Date: {date}",
        f"Nightly-Manifest-Id: {manifest_id}",
        f"Nightly-Verification: {status}",
    ]
    if status != "verified":
        # An honest failed/WIP release: the failure travels with the commit.
        lines.append("Nightly-Release-State: WIP-validation-failed (not verified)")
    lines += [
        f"Nightly-Branch: {branch}",
        f"Nightly-Selected-Files: {file_count}",
        "",
        f"Evidence: {evidence_rel}",
    ]
    return "\n".join(lines) + "\n"


def head_sha(repo: Path) -> str | None:
    return (git_out(repo, ["rev-parse", "HEAD"]) or "").strip() or None


def remote_branch_sha(repo: Path, branch: str) -> tuple[str | None, str | None, bool]:
    """(sha, error, ok) for the remote branch head, read from the *effective*
    push destination (after url.*.insteadOf rewriting) — the place a push would
    actually land, not an alias that could point somewhere else."""
    urls, error = effective_push_urls(repo)
    if error:
        return None, error, False
    if len(urls) != 1:
        return None, f"origin has {len(urls)} push destinations; refusing an ambiguous push", False
    url = urls[0]
    if credential_bearing(url):
        return None, "push destination carries credentials; refusing (not printed)", False
    ls = run_git(repo, ["ls-remote", url, f"refs/heads/{branch}"])
    if ls.returncode != 0:
        return None, redact(ls.stderr.strip())[:2000], False
    line = ls.stdout.split()
    return (line[0] if line else None), None, True


def write_nul_file(path: Path, items: Sequence[str]) -> None:
    with open(path, "wb") as fh:
        for item in items:
            fh.write(item.encode("utf-8") + b"\0")
        fh.flush()


def _option_unsupported(err: str) -> bool:
    lowered = (err or "").lower()
    return "unknown option" in lowered or "unrecognized argument" in lowered or lowered.startswith("usage:")


def stageable_paths(repo: Path, paths: Sequence[str]) -> list[str]:
    """Paths `git add -A` can match in the worktree or the current index.

    The old side of a rename may have already disappeared from both (for
    example after ``git mv``), and passing that absent pathspec would make git
    reject the entire add.  Such a path needs no staging: its rename is already
    in the index.  An unstaged deletion is still present in the index, so it is
    retained and ``-A`` records the removal.
    """
    out = git_out(repo, ["ls-files", "-z"])
    indexed = set(out.split("\0")) if out is not None else set()
    return [path for path in paths if (repo / path).exists() or path in indexed]


def stage_selected(repo: Path, paths: Sequence[str], workdir: Path) -> str | None:
    """Stage exactly the selected paths in one git call (no partial staging)."""
    paths = stageable_paths(repo, paths)
    if not paths:
        return None
    if len(paths) <= ARGV_BOUND:  # bounded full argv: one command, all paths
        ok, err = git_ok(repo, ["add", "-A", "--", *paths])
        if ok:
            return None
        if not _option_unsupported(err):
            return err
    spec = workdir / f"add-{os.getpid()}.spec"
    write_nul_file(spec, paths)
    try:
        ok, err = git_ok(repo, ["add", "-A", "--pathspec-from-file", str(spec), "--pathspec-file-nul"])
    finally:
        spec.unlink(missing_ok=True)
    return None if ok else (err or "git add --pathspec-from-file failed")


def commit_selected(repo: Path, paths: Sequence[str], message: str, workdir: Path) -> tuple[bool, str, str | None]:
    """Commit only the given paths in one commit; other staged content stays.

    ``git commit --only`` records the working-tree content of exactly these
    paths (deletions included) and leaves every other staged entry staged.
    All paths go into a single command, so the release stays one traceable
    commit and a failure can never leave a partially committed release.
    """
    proc: subprocess.CompletedProcess | None = None
    if len(paths) <= ARGV_BOUND:
        proc = run_git(repo, ["commit", "--only", "-m", message, "--", *paths])
        if proc.returncode == 0:
            return True, "", head_sha(repo)
        if not _option_unsupported(redact(proc.stderr)):
            return False, redact((proc.stderr or proc.stdout).strip()), None
    spec = workdir / f"commit-{os.getpid()}.spec"
    msg_file = workdir / f"commit-{os.getpid()}.msg"
    write_nul_file(spec, paths)
    msg_file.write_text(message, encoding="utf-8")
    try:
        proc = run_git(repo, [
            "commit", "--only", "-F", str(msg_file),
            "--pathspec-from-file", str(spec), "--pathspec-file-nul",
        ])
    finally:
        spec.unlink(missing_ok=True)
        msg_file.unlink(missing_ok=True)
    if proc.returncode != 0:
        return False, redact((proc.stderr or proc.stdout).strip()), None
    return True, "", head_sha(repo)


def tree_entries(repo: Path, commit_sha: str) -> dict[str, dict[str, str]]:
    """path -> {mode, blob id} for one commit tree (recursive)."""
    out = git_out(repo, ["ls-tree", "-r", "-z", commit_sha])
    if out is None:
        return {}
    entries: dict[str, dict[str, str]] = {}
    for record in out.split("\0"):
        if not record:
            continue
        meta, _, path = record.partition("\t")
        fields = meta.split()
        if len(fields) >= 3:
            entries[path] = {"mode": fields[0], "sha": fields[2]}
    return entries


def worktree_blob_ids(repo: Path, paths: Sequence[str]) -> dict[str, str]:
    """git blob ids of the verified worktree content (chunked argv)."""
    ids: dict[str, str] = {}
    for group in chunked(list(paths)):
        proc = run_git(repo, ["hash-object", "--", *group])
        if proc.returncode != 0:
            return {}
        lines = [line for line in proc.stdout.split() if line]
        if len(lines) != len(group):
            return {}
        for path, blob in zip(group, lines):
            ids[path] = blob
    return ids


def expected_file_mode(file_path: Path) -> str | None:
    """The tree mode git will store for this regular worktree file."""
    try:
        stat = file_path.stat()
    except OSError:
        return None
    return "100755" if stat.st_mode & 0o111 else "100644"


def manifest_worktree_problems(repo: Path, manifest: dict[str, Any]) -> list[str]:
    """Compare the current worktree with the sha256 values in *manifest*.

    The manifest is the verification claim.  Git blob ids and modes must never
    be captured from content that no longer matches it, otherwise a change made
    after verify (but before the commit) would silently be treated as verified.
    Known delete/rename semantics are checked explicitly: a deleted path must
    still be absent, and the old side of a rename must stay absent while the
    new content matches the recorded hash.
    """
    problems: list[str] = []
    for path in sorted(manifest):
        if not safe_relpath(path):
            problems.append(f"unsafe-manifest-path:{path}")
            continue
        spec = manifest[path]
        if not isinstance(spec, dict):
            problems.append(f"invalid-manifest-entry:{path}")
            continue
        target = repo / path
        status = str(spec.get("status", ""))
        if status.startswith("deleted"):
            if target.exists() or target.is_symlink():
                problems.append(f"deleted-path-present:{path}")
            continue
        if is_symlink(target):
            problems.append(f"symlink:{path}")
            continue
        expected_sha = spec.get("sha256")
        if not isinstance(expected_sha, str) or not expected_sha:
            problems.append(f"manifest-sha256-missing:{path}")
            continue
        if sha256_file(target) != expected_sha:
            problems.append(f"sha256-changed:{path}")
        if status.startswith("renamed_from:"):
            old_path = status[len("renamed_from:"):]
            if not old_path or not safe_relpath(old_path):
                problems.append(f"rename-origin-invalid:{path}")
            elif (repo / old_path).exists() or is_symlink(repo / old_path):
                problems.append(f"rename-origin-present:{old_path}")
    return sorted(set(problems))


def expected_commit_state(
    repo: Path, manifest: dict[str, Any]
) -> tuple[dict[str, Any] | None, list[str]]:
    """Everything the release commit must satisfy — captured BEFORE it runs.

    Parent HEAD, the exact selection scope, and the git blob ids/modes of the
    verified worktree content are recorded up front, but only after the current
    content has been checked against the verified manifest.  A change between
    verify and this capture is reported as a problem (a fresh verify is needed)
    instead of being adopted as expected content.  The hash is checked again
    after git has read the blobs, so a mutation racing the hash-object call is
    not silently accepted either.  A pre-commit hook or concurrent writer still
    cannot redefine the expectation after capture: the post-commit audit
    compares the created tree against these recorded ids/modes/parent/scope.
    """
    problems = manifest_worktree_problems(repo, manifest)
    if problems:
        return None, problems
    parent = head_sha(repo)
    if parent is None:
        return None, ["<HEAD unreadable>"]
    content_paths = sorted(
        p for p, spec in manifest.items() if not str(spec.get("status", "")).startswith("deleted")
    )
    blobs = worktree_blob_ids(repo, content_paths)
    if len(blobs) != len(content_paths):
        return None, ["<git blob ids unavailable>"]
    problems = manifest_worktree_problems(repo, manifest)
    if problems:
        return None, problems
    entries: dict[str, dict[str, str]] = {}
    for path in content_paths:
        mode = expected_file_mode(repo / path)
        if mode is None:
            return None, [f"mode-unreadable:{path}"]
        entries[path] = {"mode": mode, "sha": blobs[path]}
    absent: list[str] = []
    for path, spec in manifest.items():
        status = str(spec.get("status", ""))
        if status.startswith("deleted"):
            absent.append(path)
        if status.startswith("renamed_from:"):
            absent.append(status[len("renamed_from:"):])
    return {
        "parent": parent,
        "entries": entries,
        "absent": sorted(set(absent)),
        "selection": sorted(set(entries) | set(absent)),
    }, []


def commit_deviation(repo: Path, child_sha: str, expected: dict[str, Any]) -> list[str]:
    """Full parent→child audit of the created commit against the expectation.

    The complete parent→child diff must stay inside the recorded selection, and
    every selected path must land with exactly the verified blob id and mode
    (deletions/renames must really be absent).  Anything else — hook tampering,
    a sneaked extra file, a mode flip — is reported, never pushed.
    """
    child = tree_entries(repo, child_sha)
    if not child:
        return ["<commit tree unreadable>"]
    problems: list[str] = []
    parent_proc = run_git(repo, ["rev-parse", f"{child_sha}^"])
    if parent_proc.returncode != 0 or parent_proc.stdout.strip() != expected["parent"]:
        problems.append("<commit parent is not the verified HEAD>")
    parent_tree = tree_entries(repo, expected["parent"])
    for path, want in expected["entries"].items():
        got = child.get(path)
        if got is None:
            problems.append(f"missing:{path}")
        elif got["sha"] != want["sha"] or got["mode"] != want["mode"]:
            problems.append(f"content-or-mode:{path}")
    for path in expected["absent"]:
        if path in child:
            problems.append(f"still-present:{path}")
    allowed = set(expected["selection"])
    for path in set(parent_tree) | set(child):
        if path in allowed:
            continue
        if parent_tree.get(path) != child.get(path):
            problems.append(f"outside-selection:{path}")
    return sorted(set(problems))


def recorded_commit_audit(repo: Path, state: dict[str, Any]) -> list[str]:
    """Re-run the full pre-push audit for a commit recorded in publish state.

    A crash can happen after ``phase=committed`` is persisted but before the
    post-commit audit runs.  Recovery must not trust the record: the complete
    parent/tree/scope check is repeated from the ``expected_commit`` captured
    before the commit.  A missing or malformed expectation is itself a failure.
    """
    sha = state.get("commit_sha")
    if not isinstance(sha, str) or not sha:
        return ["<commit_sha missing from publish state>"]
    expected = state.get("expected_commit")
    if not isinstance(expected, dict):
        return ["<expected_commit missing from publish state>"]
    if not isinstance(expected.get("parent"), str) or not expected.get("parent"):
        return ["<expected_commit.parent missing from publish state>"]
    if not isinstance(expected.get("entries"), dict):
        return ["<expected_commit.entries missing from publish state>"]
    if not isinstance(expected.get("absent"), list):
        return ["<expected_commit.absent missing from publish state>"]
    if not isinstance(expected.get("selection"), list):
        return ["<expected_commit.selection missing from publish state>"]
    try:
        return commit_deviation(repo, sha, expected)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        return [f"<expected_commit malformed: {exc}>"]


def publish_lock_path(repo: Path) -> Path:
    """Repository-level lock file, above the per-date state directories."""
    return repo / "artifacts" / "runs" / "nightly-release" / "publish.lock"


class PublishLock:
    """Publish mutex backed by ``fcntl.flock`` on a repository-level lock file.

    One level above the per-date directories, so a scheduled run and a manual
    retry of a *different* date still cannot race on the git index.  The kernel
    releases the flock when the process exits or dies, so there is no stale
    pid-file to interpret and no delete-before-write race: the lock file is
    created once and never unlinked.
    """

    def __init__(self, path: Path, wait_s: float | None = None):
        self.path = path
        self.wait_s = PUBLISH_LOCK_WAIT_S if wait_s is None else wait_s
        self.fd: int | None = None

    def acquire(self) -> str | None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        except OSError as exc:
            return f"cannot open the publish lock file {self.path}: {exc}"
        deadline = time.monotonic() + max(0.0, self.wait_s)
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(self.fd)
                    self.fd = None
                    return (
                        f"another publish holds the flock {self.path}; "
                        "refusing a concurrent release (retry later)"
                    )
                time.sleep(0.2)
        os.ftruncate(self.fd, 0)
        os.lseek(self.fd, 0, os.SEEK_SET)
        os.write(self.fd, json.dumps({"pid": os.getpid(), "created_at": utc_now_iso()}).encode("utf-8"))
        return None

    def release(self) -> None:
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = None


def validation_ran(evidence: dict[str, Any]) -> bool:
    """True when the recorded validation really happened and its log is intact.

    A "failed" verification may only back an honest WIP release if a validation
    process actually ran (recorded integer exit code) and the kept raw log still
    matches the recorded hash — so evidence cannot be relabelled or forged.
    """
    validation = evidence.get("validation")
    if not isinstance(validation, dict):
        return False
    if not isinstance(validation.get("exit_code"), int):
        return False
    log = validation.get("log_path")
    if not log:
        return False
    log_path = Path(str(log))
    if not log_path.exists():
        return False
    recorded = validation.get("log_sha256")
    return bool(recorded) and recorded == sha256_file(log_path)


def evidence_chain_ok(evidence: dict[str, Any] | None, date: str, branch: str, repo: Path,
                      manifest_id: str | None = None, head: str | None = None) -> bool:
    """The evidence must describe exactly this date/branch/repo/manifest/HEAD.

    Publishable statuses: ``verified``, or an honestly ``failed`` run whose
    validation really executed and whose version is still stable (the commit
    then records the failure, it is never reported as verified).  ``stale``
    evidence (source drift) or evidence of a run that never happened is
    rejected — a fresh verify is required.
    """
    if not isinstance(evidence, dict):
        return False
    status = evidence.get("verification_status")
    checks = [
        evidence.get("date") == date,
        evidence.get("branch") == branch,
        evidence.get("repo_root") == str(repo),
        status in ("verified", "failed"),
        not evidence.get("files_changed_during_validation"),
        not evidence.get("new_selected_during_validation"),
        not evidence.get("dropped_selected_during_validation"),
        not evidence.get("head_moved_during_validation"),
    ]
    if status == "failed":
        checks.append(validation_ran(evidence))
    if manifest_id is not None:
        checks.append(evidence.get("manifest_id") == manifest_id)
    if head is not None:
        checks.append(evidence.get("head_sha") == head)
    return all(checks)


def cmd_publish(date: str) -> tuple[dict[str, Any], int]:
    repo = REPO_ROOT
    payload: dict[str, Any] = {"mode": "publish", "date": date, "started_at": utc_now_iso()}
    identity = repo_identity(repo)
    payload["identity"] = identity
    blockers = identity_blockers(identity) + env_guards(date)
    if blockers:
        payload["identity_blockers"] = blockers
        return payload, EXIT_USAGE
    branch = identity["branch"]

    sdir = state_dir(repo, date)
    sdir.mkdir(parents=True, exist_ok=True)
    ppath = publish_state_path(repo, date)
    lock = PublishLock(publish_lock_path(repo))
    held = lock.acquire()
    if held:
        payload["publish_blocker"] = held
        payload["publish_lock"] = str(lock.path)
        return payload, EXIT_FAILED
    try:
        return _publish_locked(repo, date, branch, identity, sdir, ppath, payload)
    finally:
        lock.release()


def _publish_locked(
    repo: Path,
    date: str,
    branch: str,
    identity: dict[str, Any],
    sdir: Path,
    ppath: Path,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    state = load_json(ppath) or {
        "mode": "publish", "date": date, "branch": branch, "manifest_id": None,
        "phase": "planned", "commit_sha": None, "pushed_sha": None, "attempts": [],
    }
    state.setdefault("attempts", [])
    head = head_sha(repo)
    remote_sha, remote_error, remote_ok = remote_branch_sha(repo, branch)

    selected, exclusions = select_changes(repo, list_changes(repo), head_tree_paths(repo))
    built = build_manifest(repo, selected)
    manifest_id = built["manifest_id"]
    payload["selection"] = {
        "manifest_id": manifest_id,
        "file_count": built["file_count"],
        "excluded": exclusions,
        "head_sha": head,
        "remote_sha": remote_sha,
        "remote_ok": remote_ok,
    }
    payload["manifest_id"] = manifest_id

    # Already pushed for this date and nothing new to release: no verify and
    # no commit needed, the recorded remote state is the evidence.
    if (
        state.get("phase") == "pushed"
        and state.get("pushed_sha")
        and head == state.get("pushed_sha")
        and remote_sha == state.get("pushed_sha")
        and manifest_id in (EMPTY_MANIFEST_ID, state.get("manifest_id"))
    ):
        state["updated_at"] = utc_now_iso()
        state["attempts"].append({"at": utc_now_iso(), "step": "already_pushed", "sha": head})
        atomic_write_json(ppath, state)
        payload["commit"] = {"action": "skipped", "reason": "already pushed for this date", "sha": head}
        payload["push"] = {"ok": True, "already_pushed": True, "remote_sha": remote_sha, "commit_sha": head}
        payload["phase"] = "pushed"
        payload["state_path"] = str(ppath)
        return payload, EXIT_OK

    # Resume a committed pending push from the recorded SHA and the evidence of
    # the recorded manifest — but only after re-running the full tree audit.
    # ``phase=committed`` is persisted before commit_deviation runs, so a crash
    # in that window must not let an unaudited/tampered tree reach the remote.
    evidence: dict[str, Any] | None = None
    resume_sha: str | None = None
    recorded_sha = (
        str(state["commit_sha"])
        if state.get("phase") in ("committed", "pushed") and state.get("commit_sha")
        else None
    )
    if recorded_sha:
        audit = recorded_commit_audit(repo, state)
        if audit:
            state["updated_at"] = utc_now_iso()
            state["attempts"].append({
                "at": utc_now_iso(), "step": "resume_tree_check",
                "sha": recorded_sha, "deviations": audit[:50],
            })
            atomic_write_json(ppath, state)  # local commit preserved; no push
            payload["resume_rejected"] = {
                "expected_ok": isinstance(state.get("expected_commit"), dict),
                "head_ok": head == recorded_sha,
                "tree_check": "mismatch",
                "deviations": audit[:50],
                "note": "recorded commit failed the pre-push tree audit; local commit preserved, not pushed",
            }
            payload["publish_blocker"] = (
                "recorded commit does not match the pre-commit expectation and cannot be pushed; "
                "local commit preserved; inspect/repair then run verify again: " + ", ".join(audit[:20])
            )
            payload["commit"] = {"action": "preserved", "sha": recorded_sha, "tree_check": "mismatch"}
            payload["phase"] = state.get("phase")
            payload["state_path"] = str(ppath)
            return payload, EXIT_FAILED
        if state.get("manifest_id"):
            candidate = load_json(verify_state_path(repo, date, str(state["manifest_id"])))
            delta_ok = manifest_id in (EMPTY_MANIFEST_ID, state.get("manifest_id"))
            head_ok = head == state.get("commit_sha")
            evidence_ok = evidence_chain_ok(candidate, date, branch, repo, str(state["manifest_id"]))
            if delta_ok and head_ok and evidence_ok:
                evidence = candidate
                resume_sha = str(state["commit_sha"])
                payload["resumed_from_state"] = True
                payload["commit"] = {
                    "action": "skipped",
                    "reason": "resuming the recorded committed release for this date",
                    "sha": resume_sha,
                }
            else:
                payload["resume_rejected"] = {
                    "delta_ok": delta_ok,
                    "head_ok": head_ok,
                    "evidence_ok": evidence_ok,
                    "note": "recorded commit is not resumable; a fresh verified release is required",
                }
        else:
            payload["resume_rejected"] = {
                "delta_ok": False,
                "head_ok": head == recorded_sha,
                "evidence_ok": False,
                "note": "recorded commit has no manifest evidence; a fresh verified release is required",
            }

    # Fresh release: exactly this content at exactly this HEAD must be verified.
    if evidence is None:
        current = load_json(verify_state_path(repo, date, manifest_id))
        if not evidence_chain_ok(current, date, branch, repo, manifest_id, head):
            payload["publish_blocker"] = (
                f"no publishable verification evidence for manifest {manifest_id} at HEAD {head} on {date} "
                f"(accepted: verified, or an honestly failed run on a stable version; "
                f"rejected: stale/missing/tampered evidence); run verify first "
                f"(expected {verify_state_path(repo, date, manifest_id)})"
            )
            state["phase"] = "blocked_unverified"
            state["updated_at"] = utc_now_iso()
            atomic_write_json(ppath, state)
            payload["phase"] = state["phase"]
            payload["state_path"] = str(ppath)
            return payload, EXIT_FAILED
        evidence = current
    payload["verification_status"] = evidence.get("verification_status")
    payload["verify_state"] = evidence.get("state_path")
    # An honest failed run is releasable, but it must never masquerade as
    # verified work: the flag and the commit trailer both say WIP/failed.
    payload["wip_release"] = evidence.get("verification_status") != "verified"

    state["manifest_id"] = evidence.get("manifest_id")
    state["branch"] = branch
    state["updated_at"] = utc_now_iso()

    commit_sha = resume_sha or state.get("commit_sha")
    need_commit = bool(selected_paths(selected)) and not resume_sha
    if need_commit:
        paths = selected_paths(selected)
        # Record, before the commit runs, the expected parent, the selection
        # scope and the verified blob ids/modes (kept in the publish state).
        expected, capture_problems = expected_commit_state(
            repo, evidence.get("manifest") or built["manifest"]
        )
        if expected is None:
            detail = "; ".join(capture_problems[:20]) or "unknown capture failure"
            payload["publish_blocker"] = (
                "verified content no longer matches its manifest before committing; nothing "
                f"committed or pushed; run verify again: {detail}"
            )
            payload["expected_capture_problems"] = capture_problems[:50]
            state["phase"] = "expected_capture_failed"
            state["attempts"].append({
                "at": utc_now_iso(), "step": "expected_capture",
                "problems": capture_problems[:50],
            })
            atomic_write_json(ppath, state)
            payload["phase"] = state["phase"]
            payload["state_path"] = str(ppath)
            return payload, EXIT_FAILED
        state["expected_commit"] = expected
        err = stage_selected(repo, paths, sdir)
        if err:
            payload["publish_blocker"] = f"git add failed (index left as git left it): {err}"
            state["phase"] = "stage_failed"
            state["attempts"].append({"at": utc_now_iso(), "step": "stage", "error": err[:2000]})
            atomic_write_json(ppath, state)
            payload["phase"] = state["phase"]
            payload["state_path"] = str(ppath)
            return payload, EXIT_FAILED
        message = commit_message(
            date, str(evidence.get("manifest_id")), branch, int(evidence.get("file_count") or built["file_count"]),
            str(evidence.get("verification_status")), str(evidence.get("state_path")),
        )
        ok, err, sha = commit_selected(repo, paths, message, sdir)
        if not ok or not sha:
            payload["publish_blocker"] = (
                "git commit --only failed (no reset performed, index preserved): " + (err or "no commit sha")
            )
            state["phase"] = "commit_failed"
            state["attempts"].append({"at": utc_now_iso(), "step": "commit", "error": (err or "")[:2000]})
            atomic_write_json(ppath, state)
            payload["phase"] = state["phase"]
            payload["state_path"] = str(ppath)
            return payload, EXIT_FAILED
        state["phase"] = "committed"
        state["commit_sha"] = sha
        commit_sha = sha  # bind the push to the SHA just created, not the stale state value
        state["planned_head"] = head
        state["attempts"].append({"at": utc_now_iso(), "step": "commit", "sha": sha})
        atomic_write_json(ppath, state)  # a crash here is distinguishable from a push
        deviations = commit_deviation(repo, sha, expected)
        if deviations:
            state["phase"] = "tree_mismatch"
            state["attempts"].append({"at": utc_now_iso(), "step": "tree_check", "deviations": deviations[:50]})
            state["updated_at"] = utc_now_iso()
            atomic_write_json(ppath, state)
            payload["publish_blocker"] = (
                "created commit does not match the pre-commit expectation (hook or concurrent change "
                "altered parent/scope/content/mode); not pushed: " + ", ".join(deviations[:20])
            )
            payload["commit"] = {"action": "created", "sha": sha, "tree_check": "mismatch"}
            payload["phase"] = state["phase"]
            payload["state_path"] = str(ppath)
            return payload, EXIT_FAILED
        payload["commit"] = {"action": "created", "sha": sha, "tree_check": "verified"}
    elif commit_sha:
        if "commit" not in payload:
            payload["commit"] = {"action": "skipped", "reason": "recorded commit already present", "sha": commit_sha}
    else:
        commit_sha = head
        payload["commit"] = {"action": "none", "reason": "no release delta; pushing existing commits only"}

    # HEAD is bound to the push: never push a SHA the branch no longer points at.
    current_head = head_sha(repo)
    if current_head != commit_sha:
        payload["publish_blocker"] = (
            f"branch HEAD moved during publish (planned {commit_sha}, now {current_head}); nothing pushed"
        )
        state["phase"] = "head_moved"
        state["updated_at"] = utc_now_iso()
        state["attempts"].append({"at": utc_now_iso(), "step": "head_check", "planned": commit_sha,
                                  "found": current_head})
        atomic_write_json(ppath, state)
        payload["phase"] = state["phase"]
        payload["state_path"] = str(ppath)
        return payload, EXIT_FAILED

    # Push the recorded SHA explicitly to refs/heads/<branch>; never forced.
    refspec = f"{commit_sha}:refs/heads/{branch}"
    push = run_git(repo, ["push", "origin", refspec])
    state["attempts"].append({"at": utc_now_iso(), "step": "push", "ref": refspec,
                              "ok": push.returncode == 0, "error": redact(push.stderr.strip())[:2000]})
    if push.returncode != 0:
        state["updated_at"] = utc_now_iso()
        atomic_write_json(ppath, state)
        payload["push"] = {"ok": False, "refspec": refspec, "error": redact(push.stderr.strip())[:2000],
                           "note": "local commit preserved; retry publish, never force push"}
        payload["phase"] = state["phase"]
        payload["state_path"] = str(ppath)
        return payload, EXIT_FAILED

    # Success only if the effective destination really holds the recorded SHA.
    remote_sha, remote_error, remote_ok = remote_branch_sha(repo, branch)
    if remote_ok and remote_sha == commit_sha:
        state["phase"] = "pushed"
        state["pushed_sha"] = remote_sha
        state["pushed_ref"] = f"refs/heads/{branch}"
        state["updated_at"] = utc_now_iso()
        atomic_write_json(ppath, state)
        push_urls, _push_err = effective_push_urls(repo)
        payload["push"] = {
            "ok": True,
            "refspec": refspec,
            "remote_sha": remote_sha,
            "commit_sha": commit_sha,
            "destination": {"urls": [redact(u) for u in push_urls],
                            "owner_repo": [parse_origin(u) for u in push_urls]},
        }
        payload["phase"] = "pushed"
        payload["state_path"] = str(ppath)
        return payload, EXIT_OK
    state["updated_at"] = utc_now_iso()
    atomic_write_json(ppath, state)
    payload["push"] = {
        "ok": False,
        "pushed_commit_sha": commit_sha,
        "remote_sha": remote_sha,
        "ls_remote_ok": remote_ok,
        "ls_remote_error": remote_error,
        "note": "push accepted but the remote branch SHA does not match; not reported as success",
    }
    payload["phase"] = state["phase"]
    payload["state_path"] = str(ppath)
    return payload, EXIT_FAILED


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nightly_release.py",
        description="Purpose-limited nightly release helper for riveruning/Biomodeling.",
    )
    sub = parser.add_subparsers(dest="mode", required=True)
    for name, help_text in (
        ("inspect", "read-only facts: branch, remote identity, changes, sync, auth probe"),
        ("verify", "content manifest + validation with kept evidence"),
        ("publish", "commit exactly the verified delta and push the current branch"),
    ):
        child = sub.add_parser(name, help=help_text)
        child.add_argument("--date", required=True, help="release date, YYYY-MM-DD")
    child_verify = sub.choices["verify"]
    child_verify.add_argument(
        "--reuse", default=None,
        help="path to a prior verify state; only reused when manifest, branch and HEAD are identical",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        valid_date(args.date)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return EXIT_USAGE
    if args.mode == "inspect":
        payload, code = cmd_inspect(args.date)
    elif args.mode == "verify":
        payload, code = cmd_verify(args.date, args.reuse)
    else:
        payload, code = cmd_publish(args.date)
    return emit(payload, code)


if __name__ == "__main__":
    sys.exit(main())
