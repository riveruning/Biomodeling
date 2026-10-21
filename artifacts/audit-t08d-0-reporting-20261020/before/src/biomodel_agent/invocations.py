"""Persistent invocation journal: bounded budgets, flat refs, no auto-recovery.

Design rules (task T03a):

- Each execution/resume/alternative branch creates exactly one
  ``agent_execution`` record; it inherits the already-accounted call and run
  counts and never re-registers history from a previous execution.
- Every invocation persists a ``reserved`` record *before* its side effect;
  budgets are checked and reserved up-front, and errors/interruptions never
  refund budget.  A failed reservation must never let the callback run.
- The callback's stable ``operation_id`` is generated once and reused verbatim
  on the reserved record and on exactly one terminal record (returned /
  error / interrupted), so replays can match work without re-executing it.
- Every record refs only the execution record (reserved) or the execution
  record plus this operation's reserved record plus explicitly supplied
  result refs (terminal).  Refs never chain between operations, so export
  closure depth stays constant as call counts grow.
- No provider credentials, headers, transport objects or raw exception text
  ever enter the journal; ``details`` is explicit caller-supplied JSON and
  invalid JSON is rejected.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable, Iterable

from .storage import ArtifactError, canonical_bytes


def _is_count(value: Any, max_value: int) -> bool:
    """Strict int (bool rejected) within [0, max_value]."""
    return type(value) is int and 0 <= value <= max_value


def _require_json(value: Any, label: str) -> None:
    """Reject non-JSON data up front; the store rejects it too, but failing
    here keeps the rejection deterministic and outside any callback path."""
    try:
        canonical_bytes(value)
    except ArtifactError as exc:
        raise ValueError(f"{label} is not JSON-safe: {exc}") from exc


# name -> replay class (audit-only; never drives automatic retry)
REPLAY_CLASSES: dict[str, str] = {
    "respond": "external_unknown",
    "submit_draft": "artifact_write",
    "run": "scientific_execution",
    "run_study": "scientific_execution",
    "search_sources": "read_only",
    "inspect_source": "read_only",
    "recall_memory": "read_only",
    "load_skill": "read_only",
}


class InvocationJournal:
    """Records reserved/returned/error/interrupted invocations for one run.

    ``store`` is the shared :class:`ArtifactStore`; ``task_ref`` the existing
    ``agent_task`` ref.  Inherited ``calls``/``runs`` must already be
    accounted elsewhere (checkpoint/agent_run); they are inherited, not
    re-derived.  Every execution/resume/alternative branch creates a new
    execution record and starts from its inherited counts.
    """

    def __init__(self, store: Any, task_ref: str, *, parent_run_ref: str | None = None,
                 calls: int = 0, runs: int = 0, recovery: dict | None = None,
                 initial_refs: Iterable[str] = ()) -> None:
        self._store = store
        self._task_ref = task_ref
        record = store.get(task_ref)
        if record["kind"] != "agent_task":
            raise ValueError("journal task_ref must name an agent_task record")
        payload = record["payload"]
        max_calls = payload.get("max_calls")
        if type(max_calls) is not int or not 1 <= max_calls <= 50:
            raise ValueError("agent_task max_calls must be an int in 1..50")
        if not _is_count(calls, max_calls):
            raise ValueError(f"inherited calls must be an int in 0..{max_calls}, not a bool")
        if not _is_count(runs, 1):
            raise ValueError("inherited runs must be an int in 0..1, not a bool")
        if parent_run_ref is not None:
            parent_record = store.get(parent_run_ref)
            if parent_record["kind"] != "agent_run":
                raise ValueError("parent_run_ref must name an agent_run record")
        self._max_calls = max_calls
        self._max_runs = 1
        self._sequence = 0
        if recovery is not None:
            _require_json(recovery, 'recovery metadata')
        refs: list[str] = [task_ref, *initial_refs]
        if parent_run_ref is not None:
            refs.append(parent_run_ref)
        self.execution_ref: str = store.put("agent_execution", {
            "version": 1,
            "id": str(uuid.uuid4()),
            "task_ref": task_ref,
            "parent_run_ref": parent_run_ref,
            "base_call_count": calls,
            "base_run_count": runs,
            "max_calls": max_calls,
            "max_runs": 1,
            **({'recovery': recovery} if recovery is not None else {}),
        }, refs=tuple(dict.fromkeys(refs)))
        self.refs: list[str] = []
        self.calls: int = calls
        self.runs: int = runs

    @classmethod
    def reopen(cls, store: Any, execution_ref: str, *, refs: list[str], calls: int,
               runs: int, next_sequence: int) -> InvocationJournal:
        """Attach only after recovery diagnosis and acquiring the task owner.

        Does not invent another execution or spend an additional reservation.
        """
        execution = store.get(execution_ref)
        if execution['kind'] != 'agent_execution':
            raise ValueError('not an agent_execution')
        payload = execution['payload']
        task = store.get(payload['task_ref'])
        if task['kind'] != 'agent_task':
            raise ValueError('not an agent_task')
        maximum = task['payload']['max_calls']
        if (type(maximum) is not int or not 1 <= maximum <= 50
                or not _is_count(calls, maximum) or not _is_count(runs, 1)
                or type(next_sequence) is not int or next_sequence < 1):
            raise ValueError('invalid attached journal counters')
        journal = cls.__new__(cls)
        journal._store, journal._task_ref = store, payload['task_ref']
        journal._max_calls, journal._max_runs = maximum, 1
        journal._sequence = next_sequence - 1
        journal.execution_ref, journal.refs = execution_ref, list(refs)
        journal.calls, journal.runs = calls, runs
        return journal

    # -- budget ---------------------------------------------------------------

    def _reserve(self, name: str, details: dict) -> tuple[str, str, int, int]:
        """Check budgets, persist the reserved record, then bump local counts.

        The persist happens before the local mutation, so a failed
        reservation raises before the caller's callback runs, and a crash
        between reserve and callback loses no budget accounting.
        """
        if name == "respond":
            if self.calls >= self._max_calls:
                raise ValueError("LLM call budget is exhausted; start a separate task")
            call_count, run_count = self.calls + 1, self.runs
        elif name in ("run", "run_study"):
            if self.runs >= self._max_runs:
                raise ValueError("scientific execution budget is exhausted; start a separate task")
            call_count, run_count = self.calls, self.runs + 1
        else:
            call_count, run_count = self.calls, self.runs
        operation_id = str(uuid.uuid4())
        reserved_ref = self._store.put("agent_invocation", {
            "version": 1,
            "execution_ref": self.execution_ref,
            "operation_id": operation_id,
            "sequence": self._sequence + 1,
            "name": name,
            "phase": "reserved",
            "call_count": call_count,
            "run_count": run_count,
            "replay_class": REPLAY_CLASSES[name],
            "details": details,
        }, refs=(self.execution_ref,))
        self.calls, self.runs = call_count, run_count
        self._sequence += 1
        self.refs.append(reserved_ref)
        return reserved_ref, operation_id, call_count, run_count

    # -- public API ------------------------------------------------------------

    def invoke(self, name: str, callback: Callable[[], Any], *, details: Any = None,
               summarize: Callable[[Any], Any] | None = None,
               result_refs: Callable[[Any], Iterable[Any]] | None = None) -> Any:
        """Reserve budget, run ``callback`` once, persist a terminal record.

        - unknown ``name`` is rejected without executing the callback;
        - the reserved record is persisted before the callback runs;
        - after a normal return the ``returned`` record is persisted with the
          same operation_id and counts; if that persistence fails the error
          propagates -- the record is never faked and the callback never
          reruns;
        - ``Exception`` yields an ``error`` record and other
          ``BaseException`` (SystemExit, KeyboardInterrupt, ...) an
          ``interrupted`` record; details carry only the error type and the
          original exception is re-raised unchanged.
        """
        if name not in REPLAY_CLASSES:
            raise ValueError(f"unknown invocation name: {name!r}")
        _require_json(details if details is not None else {}, "details")
        reserved_ref, operation_id, call_count, run_count = self._reserve(
            name, {} if details is None else details)
        try:
            result = callback()
        except Exception as exc:
            self._terminal(name, operation_id, "error", call_count, run_count,
                           {"error_type": type(exc).__name__}, reserved_ref, ())
            raise
        except BaseException as exc:
            self._terminal(name, operation_id, "interrupted", call_count, run_count,
                           {"error_type": type(exc).__name__}, reserved_ref, ())
            raise
        summary = summarize(result) if summarize is not None else result
        if summary is None:
            summary = {}
        _require_json(summary, "returned details")
        refs = tuple(r for r in (result_refs(result) if result_refs is not None else ()) if r)
        self._terminal(name, operation_id, "returned", call_count, run_count,
                       summary, reserved_ref, refs)
        return result

    # -- terminal records -------------------------------------------------------

    def _terminal(self, name: str, operation_id: str, phase: str,
                  call_count: int, run_count: int, details: Any,
                  reserved_ref: str, result_refs: tuple[str, ...], *,
                  sequence: int | None = None) -> None:
        """Persist the single terminal record for an operation.

        Refs stay flat and shallow: the execution record, this operation's
        reserved record and the explicitly trusted result refs -- never the
        previous operation, so depth does not grow with operation count.
        Failure here propagates to the caller (no fake record, no retry).
        """
        refs = [self.execution_ref, reserved_ref]
        refs.extend(result_refs)
        terminal_ref = self._store.put("agent_invocation", {
            "version": 1,
            "execution_ref": self.execution_ref,
            "operation_id": operation_id,
            "sequence": self._sequence if sequence is None else sequence,
            "name": name,
            "phase": phase,
            "call_count": call_count,
            "run_count": run_count,
            "replay_class": REPLAY_CLASSES[name],
            "details": details,
        }, refs=tuple(dict.fromkeys(refs)))
        self.refs.append(terminal_ref)
