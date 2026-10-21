"""Isolated libRoadRunner simulation of validated SBML artifacts.

Parent process: never imports roadrunner. It validates the artifact, inspects
the SBML with libSBML only, then spawns a worker and polls a queue with a
timeout (never join-before-read). Worker: top-level picklable function that
loads the SBML in roadrunner and integrates exactly onto the union of sample
and segment times with ``variable_step_size = False``.

Time protocol: start at ``t0 = times[0]`` with declared initial values plus
``initial_overrides`` (no implicit pre-equilibration, no ``integrate(0, t0)``).
Segments change external parameters only, are applied exactly at their instant,
and are right-continuous while states stay continuous.

Symbol protocol (empirically verified against libRoadRunner):

* a *species* state is read as amount (``rr[sid]``) when
  ``hasOnlySubstanceUnits`` is set and as concentration (``rr['[sid]']``)
  otherwise;
* a *rate-rule parameter* state (``quantity_kind='variable'`` is compiled to a
  non-constant SBML parameter) has no concentration form at all: ``rr[name]``
  is the one correct accessor, and its initial value is ``init(name)``.  Such a
  state's initial value **is** overridable -- a rate rule defines a derivative,
  not a value.  Only assignment-rule targets and boundary/constant species are
  refused.

Schedule protocol: sample times and segment times are kept as an exact sorted
set.  Sampling happens exactly when ``target == times[sample_index]``; no
tolerance ever merges two distinct requested times, and every positive
``target > current`` interval -- however small -- is integrated with oneStep.
"""

from __future__ import annotations

import json
import math
import multiprocessing
import queue as _queue
import time as _time

from . import expressions
from . import modeling
from .domain.contracts import (
    Check,
    ExperimentSpec,
    FrozenMap,
    ModelArtifact,
    ObservationSpec,
    SimulationResult,
    SolverConfig,
    ValidationReport,
)

_BACKEND = "libRoadRunner/cvode"

#: Tolerance used *only* to verify the instant actually reached by the
#: integrator and to bound parameter-override rounding.  It never merges two
#: distinct requested times or skips a legitimate integration interval.
_TOL = 1e-8

#: Hard ceiling on requested samples: a larger experiment would be a memory
#: hazard, so it is refused statically before the worker is spawned.
_MAX_SAMPLES = 10000

#: Iterator sentinels for the parent-side queue handshake.
_EMPTY = object()
_SENTINEL = None


class _SimulationError(Exception):
    """Worker-side error that must surface as a failed result."""


def _finite_check(message: str, status: str = "pass") -> ValidationReport:
    return ValidationReport((Check(code="finite", status=status, message=message),))


def _samples_check(message: str) -> ValidationReport:
    return ValidationReport((Check(code="samples", status="fail", message=message),))


# ---------------------------------------------------------------------------
# Parent-side SBML inspection (libSBML only)
# ---------------------------------------------------------------------------

def _inspect_sbml(sbml: str) -> dict:
    """Extract state/parameter metadata needed for override and column rules.

    ``state[sid] = {"substance": hasOnlySubstanceUnits, "rate": is a rate-rule
    target, "assign": is an assignment-rule target, "boundary": boundary or
    constant species, "param": the state is a non-constant parameter (a
    rate-rule/variable state), "species": the state is an SBML species}``.

    Parameters that are assignment-rule targets are not settable; every other
    parameter is a candidate constant parameter.  A rate-rule *parameter* is a
    state, never a settable parameter.
    """
    import libsbml

    doc = libsbml.readSBMLFromString(sbml)
    model = doc.getModel() if doc is not None else None
    if model is None:
        return {"state": {}, "param": {}, "assign": set(), "comp": set()}

    state: dict[str, dict] = {}
    for i in range(model.getNumSpecies()):
        sp = model.getSpecies(i)
        state[sp.getId()] = {
            "substance": bool(sp.getHasOnlySubstanceUnits()),
            "rate": False,
            "assign": False,
            "boundary": bool(sp.getBoundaryCondition()) or bool(sp.getConstant()),
            "param": False,
            "species": True,
        }

    param: dict[str, bool] = {}
    for i in range(model.getNumParameters()):
        p = model.getParameter(i)
        param[p.getId()] = bool(p.getConstant())

    assign: set[str] = set()
    rate_targets: set[str] = set()
    for i in range(model.getNumRules()):
        rule = model.getListOfRules().get(i)
        target = rule.getVariable()
        if rule.isAssignment():
            assign.add(target)
        elif rule.isRate():
            rate_targets.add(target)

    for target in assign:
        if target in param:
            param.pop(target)          # assignment target: not settable at all
        elif target in state:
            state[target]["assign"] = True

    for target in rate_targets:
        if target in param:
            state[target] = {
                "substance": False,    # no concentration form exists
                "rate": True,
                "assign": False,
                "boundary": False,
                "param": True,         # read as rr[name], initial via init(name)
                "species": False,
            }
        elif target in state:
            state[target]["rate"] = True

    for sid, info in state.items():
        if info["param"] and sid in param:
            param.pop(sid, None)       # rate-rule parameter state is not a parameter

    comp = {model.getCompartment(i).getId() for i in range(model.getNumCompartments())}
    return {
        "state": state,
        "param": param,
        "assign": assign,
        "comp": comp,
        "derived": set(assign) - set(state) - set(param) - comp,
    }


def _segment_check(name: str, ctx: dict, where: str) -> str | None:
    """Return an error message when ``name`` may not be changed externally."""
    info = ctx["state"].get(name)
    if info is not None:
        return f"{where}: {name!r} is a state; states cannot be changed by an external input"
    if name in ctx["assign"]:
        return f"{where}: {name!r} is an assignment-rule target and cannot be set"
    if name in ctx["comp"]:
        return f"{where}: {name!r} is a compartment and cannot be set"
    if not ctx["param"].get(name, False):
        return f"{where}: {name!r} is not a settable constant parameter"
    return None


def _initial_check(name: str, ctx: dict, where: str) -> str | None:
    """Return an error message when an initial value may not be overridden.

    A rate-rule state is explicitly allowed: the rule defines its derivative,
    and the initial value is recomputed by ``reset`` after the override.
    """
    info = ctx["state"].get(name)
    if info is None:
        return (f"{where}: {name!r} is not a state/species of the model; only state "
                "initial values may be overridden")
    if info["boundary"]:
        return f"{where}: {name!r} is a constant/boundary species and cannot be overridden"
    if info["assign"]:
        return (f"{where}: {name!r} is fixed by an assignment rule; its value is computed "
                "and cannot be overridden")
    return None


# ---------------------------------------------------------------------------
# Worker (top-level, picklable; imports roadrunner here only)
# ---------------------------------------------------------------------------

def _species_value(rr, sid: str, substance: bool) -> float:
    """Amount via ``rr[sid]``, concentration via ``rr['[sid]']``."""
    if substance:
        return float(rr[sid])
    return float(rr[f"[{sid}]"])


def _read_symbol(rr, name: str, state: dict) -> float:
    """Read one state's current value with the accessor its kind requires.

    A rate-rule parameter state has no ``[name]`` concentration symbol, so it
    is read through ``rr[name]``.  Species keep their amount/concentration
    distinction.
    """
    info = state.get(name)
    if info is not None:
        if info.get("param"):
            return float(rr[name])
        return _species_value(rr, name, bool(info["substance"]))
    return float(rr[name])


def _environment(rr, state: dict, ctx: dict) -> dict:
    """Resolve every observable symbol: states, parameters, compartment, time.

    Time is taken from ``rr.model.getTime()`` (the actual integrator instant), never from
    the requested schedule target.
    """
    env: dict[str, float] = {"time": float(rr.model.getTime())}
    for name in state:
        env[name] = _read_symbol(rr, name, state)
    for name in ctx["param"]:
        env[name] = float(rr[name])
    for name in ctx["comp"]:
        env[name] = float(rr[name])
    for name in ctx["derived"]:
        if name not in env:
            env[name] = float(rr[name])
    return env


def _simulation_worker(payload: dict, queue) -> None:
    started = _time.monotonic()
    try:
        out = _run_simulation(payload)
    except Exception as exc:  # defensive: the worker must always answer
        out = {
            "status": "failed",
            "diagnostics": [f"worker error: {type(exc).__name__}: {exc}"],
            "backend": _BACKEND,
            "values": None,
        }
    out["elapsed_s"] = _time.monotonic() - started
    try:
        queue.put(json.dumps(out))
    except Exception:
        pass


def _run_simulation(payload: dict) -> dict:
    import roadrunner

    times = [float(t) for t in payload["times"]]
    state = payload["state"]
    ctx = {
        "param": payload["param"],
        "assign": set(payload["assign"]),
        "comp": set(payload["comp"]),
        "derived": set(payload.get("derived", ())),
    }
    diag = [f"backend={_BACKEND}", f"roadrunner={getattr(roadrunner, '__version__', 'unknown')}"]

    rr = roadrunner.RoadRunner(payload["sbml"])
    rr.integrator = "cvode"
    integ = rr.integrator
    integ.relative_tolerance = float(payload["rtol"])
    integ.absolute_tolerance = float(payload["atol"])
    integ.maximum_num_steps = int(payload["max_steps"])
    integ.variable_step_size = False
    diag.append(f"rtol={float(payload['rtol'])} atol={float(payload['atol'])} "
                f"max_steps={int(payload['max_steps'])}")

    t0 = times[0]
    # Override constant initial parameters first; reset recomputes initial assignments.
    initial_parameters = dict(payload["parameter_overrides"])
    for segment_time, segment_values in payload["segments"]:
        if segment_time == t0:
            initial_parameters.update(segment_values)
    for name, value in initial_parameters.items():
        rr[f"init({name})"] = float(value)
    rr.reset(roadrunner.SelectionRecord.ALL)
    for name, value in payload["initial_overrides"].items():
        info = state[name]
        key = name if info["param"] or info["substance"] else f"[{name}]"
        rr[key] = float(value)
        if not math.isclose(float(rr[key]), float(value), rel_tol=1e-12, abs_tol=1e-12):
            raise _SimulationError(f"initial override did not take effect: {name}")
    rr.model.setTime(t0)  # initial time labels the declared state, no pre-equilibration

    schedule = sorted({float(t) for t in times} | {float(t) for t, _ in payload["segments"]})
    segment_by_time = {float(t): v for t, v in payload["segments"]}
    sample_ix = 0
    obs_specs = payload["observations"]
    columns: list[str] = list(state) + [o["id"] for o in obs_specs]
    values: dict[str, list[float]] = {c: [] for c in columns}
    current = t0

    for target in schedule:
        elapsed = target - current
        if elapsed > 0.0:
            rr.oneStep(current, elapsed)
            reached = float(rr.model.getTime())
            if not math.isfinite(reached) or abs(reached - target) > max(_TOL, 1e-9 * abs(target)):
                raise _SimulationError(
                    f"integrator stopped at t={reached!r} instead of requested t={target!r}"
                )
            current = reached
        for name, value in segment_by_time.get(target, {}).items():
            rr[name] = float(value)  # right-continuous external input at the boundary
        if sample_ix < len(times) and target == times[sample_ix]:
            env = _environment(rr, state, ctx)
            for name in state:
                value = float(env[name])
                if not math.isfinite(value):
                    raise _SimulationError(f"non-finite value for state {name!r} at t={target!r}")
                values[name].append(value)
            for obs in obs_specs:
                try:
                    value = float(expressions.evaluate(obs["expression"], env))
                except Exception as exc:
                    raise _SimulationError(f"observation {obs['id']!r} failed: {exc}") from exc
                if not math.isfinite(value):
                    raise _SimulationError(f"non-finite observation {obs['id']!r} at t={target!r}")
                values[obs["id"]].append(value)  # exactly one value per sample
            sample_ix += 1

    if sample_ix != len(times):
        raise _SimulationError(f"internal: sampled {sample_ix} of {len(times)} requested times")

    bad = [f"{c}[{i}]={v!r}" for c in columns for i, v in enumerate(values[c]) if not math.isfinite(v)]
    if bad:
        return {
            "status": "failed",
            "diagnostics": diag + ["non-finite simulation output: " + ", ".join(bad[:8])],
            "backend": _BACKEND,
            "values": None,
        }
    return {
        "status": "succeeded",
        "times": times,  # the requested experiment grid is the output grid
        "values": {c: list(values[c]) for c in columns},
        "diagnostics": diag,
        "backend": _BACKEND,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _failure(message: str, ctx: dict, elapsed: float = 0.0, status: str = "failed",
             checks: ValidationReport | None = None) -> SimulationResult:
    report = ValidationReport((checks.checks if checks is not None else ()) +
                              (Check('execution_input', 'fail', message),))
    diag = (f"backend={_BACKEND}", message)
    return SimulationResult(
        status=status,
        checks=report,
        diagnostics=diag,
        backend=_BACKEND,
        elapsed_s=elapsed,
    )


def simulate(
    artifact: ModelArtifact,
    experiment: ExperimentSpec,
    observations: tuple[ObservationSpec, ...] = (),
    solver: SolverConfig | None = None,
) -> SimulationResult:
    """Simulate a validated artifact in an isolated spawned process.

    Model/experiment problems never raise: they come back as ``failed`` (or
    ``unsupported`` when the artifact reports an unsupported contract), and no
    worker is spawned for them.
    """
    cfg = solver if solver is not None else SolverConfig()
    wall = _time.monotonic()

    if not isinstance(experiment, ExperimentSpec):
        raise TypeError(f"simulate expects ExperimentSpec, got {type(experiment).__name__}")

    # --- static rejection before any process is spawned --------------------
    report = modeling.validate_artifact(artifact)
    if not report.can_execute:
        failed = [c for c in report.checks if c.status == "fail"]
        unsupported = any(
            c.code.startswith("unsupported") or c.code == "artifact_valid"
            for c in failed
        )
        status = "unsupported" if unsupported else "failed"
        return SimulationResult(
            status=status,
            checks=report,
            diagnostics=(f"backends={_BACKEND}", "; ".join(f"{c.code}: {c.message}" for c in failed)),
            backend=_BACKEND,
            elapsed_s=_time.monotonic() - wall,
        )

    ctx = _inspect_sbml(artifact.sbml)
    state = ctx["state"]

    def bad(message: str) -> SimulationResult:
        return _failure(message, ctx, _time.monotonic() - wall, checks=report)

    seen_obs: set[str] = set()
    for obs in observations:
        if obs.id in seen_obs:
            return bad(f"duplicate observation id {obs.id!r}")
        if obs.id in state:
            return bad(f"observation id {obs.id!r} collides with a state id")
        if obs.id in ctx["param"]:
            return bad(f"observation id {obs.id!r} collides with a parameter id")
        if obs.id in ctx["comp"]:
            return bad(f"observation id {obs.id!r} collides with a compartment id")
        if obs.id in ctx["assign"]:
            return bad(f"observation id {obs.id!r} collides with an assignment-rule target")
        seen_obs.add(obs.id)
        try:
            expressions.parse_expression(obs.expression, allowed_symbols=None)
        except expressions.ExpressionError as exc:
            return bad(f"observation {obs.id!r}: {exc}")

    for name in experiment.initial_overrides:
        message = _initial_check(name, ctx, f"initial_overrides[{name!r}]")
        if message:
            return bad(message)
    for name in experiment.parameter_overrides:
        message = _segment_check(name, ctx, f"parameter_overrides[{name!r}]")
        if message:
            return bad(message)

    if len(experiment.times) > _MAX_SAMPLES:
        return SimulationResult(
            status="failed",
            checks=_samples_check(
                f"experiment requests {len(experiment.times)} samples; the limit is {_MAX_SAMPLES}"
            ),
            diagnostics=(f"backend={_BACKEND}",),
            backend=_BACKEND,
            elapsed_s=_time.monotonic() - wall,
        )

    t0 = float(experiment.times[0])
    tlast = float(experiment.times[-1])
    segments: list[list] = []
    for seg in experiment.segments:
        t = float(seg.time)
        if t < t0 or t > tlast:
            return bad(f"segment at t={t!r} is outside [t0={t0!r}, tlast={tlast!r}]")
        for name in seg.values:
            message = _segment_check(name, ctx, f"segment at t={t!r}")
            if message:
                return bad(message)
        segments.append([t, {k: float(v) for k, v in seg.values.items()}])
    segments.sort(key=lambda item: item[0])

    payload = {
        "sbml": artifact.sbml,
        "times": [float(t) for t in experiment.times],
        "state": state,
        "param": ctx["param"],
        "assign": sorted(ctx["assign"]),
        "comp": sorted(ctx["comp"]),
        "derived": sorted(ctx["derived"]),
        "initial_overrides": {k: float(v) for k, v in experiment.initial_overrides.items()},
        "parameter_overrides": {k: float(v) for k, v in experiment.parameter_overrides.items()},
        "segments": segments,
        "observations": [{"id": o.id, "expression": o.expression} for o in observations],
        "rtol": cfg.rtol,
        "atol": cfg.atol,
        "max_steps": cfg.max_steps,
    }

    # --- isolated worker handshake -----------------------------------------
    mp = multiprocessing.get_context("spawn")
    queue = mp.Queue()
    proc = mp.Process(target=_simulation_worker, args=(payload, queue), daemon=True)
    raw = None
    timed_out = False
    worker_error: str | None = None
    try:
        proc.start()
        try:
            raw = queue.get(timeout=cfg.timeout_s)
        except _queue.Empty:
            timed_out = True          # only an empty queue is a timeout
        except Exception as exc:      # anything else is a real failure
            worker_error = f"{type(exc).__name__}: {exc}"
    finally:
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=5.0)
        else:
            proc.join(timeout=5.0)
        try:
            queue.close()
            queue.join_thread()
        except Exception:
            pass
    elapsed = _time.monotonic() - wall

    if timed_out:
        return SimulationResult(
            status="timed_out",
            checks=_finite_check(f"simulation exceeded timeout_s={cfg.timeout_s!r}", "fail"),
            diagnostics=(f"backend={_BACKEND}", f"worker terminated after timeout_s={cfg.timeout_s!r}"),
            backend=_BACKEND,
            elapsed_s=elapsed,
        )
    if worker_error is not None:
        return SimulationResult(
            status="failed",
            checks=_finite_check(f"worker result could not be read: {worker_error}", "fail"),
            diagnostics=(f"backend={_BACKEND}", worker_error),
            backend=_BACKEND,
            elapsed_s=elapsed,
        )
    if raw is None:
        return SimulationResult(
            status="failed",
            checks=_finite_check("worker exited without producing a result", "fail"),
            diagnostics=(f"backend={_BACKEND}", f"worker exitcode={proc.exitcode!r}"),
            backend=_BACKEND,
            elapsed_s=elapsed,
        )
    try:
        data = json.loads(raw)
    except Exception as exc:
        return SimulationResult(
            status="failed",
            checks=_finite_check(f"worker result was not JSON: {exc}", "fail"),
            diagnostics=(f"backend={_BACKEND}",),
            backend=_BACKEND,
            elapsed_s=elapsed,
        )
    diag = tuple(data.get("diagnostics", ()))
    if data.get("status") != "succeeded" or data.get("values") is None:
        return SimulationResult(
            status="failed",
            checks=_finite_check("simulation did not produce finite output", "fail"),
            diagnostics=diag,
            backend=data.get("backend", _BACKEND),
            elapsed_s=elapsed,
        )
    values = data["values"]
    bad_values = [f"{k}[{i}]={v!r}" for k, col in values.items()
                  for i, v in enumerate(col) if not math.isfinite(float(v))]
    if bad_values:
        return SimulationResult(
            status="failed",
            checks=_finite_check("non-finite output: " + ", ".join(bad_values[:8]), "fail"),
            diagnostics=diag,
            backend=data.get("backend", _BACKEND),
            elapsed_s=elapsed,
        )
    return SimulationResult(
        status="succeeded",
        times=tuple(float(t) for t in data["times"]),
        values=FrozenMap({k: tuple(float(v) for v in col) for k, col in values.items()}),
        checks=_finite_check("all output values are finite"),
        diagnostics=diag,
        backend=data.get("backend", _BACKEND),
        elapsed_s=elapsed,
    )
