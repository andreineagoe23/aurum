"""Run a strategy's declared search grid as one auditable batch (``aurum backtest grid``).

Each grid point is one evaluation attempt in the trial ledger (``aurum.backtest.pipeline``),
grouped by ``batch_id`` (strategy, variant, period). The batch is **frozen** to the evaluation
context of its first run (code, spec, config, costs/risk, symbol snapshot, input data with
warm-up, declared grid, dates); any later run under another context is refused before any
attempt. Re-running the same grid:

* a point with a trial is **done** and never evaluated again, whatever its attempts' outcome
  rows say; if its run files were not published it is reported as ``artifacts pending``
  (``aurum backtest recover`` publishes them, without simulating);
* a point whose latest attempt failed or never finished, with no trial, is **not retried**
  unless ``retry_failed`` is set; a retry is a new attempt linked to it (``retry_of``), never
  another grid point;
* a refusal that applies to the whole request (unknown strategy, an empty period, the sealed
  period, a changed context) raises before any attempt and stops the batch.

The grid is the one the strategy declares (``registry.declared_grid``); nothing else can be
run through it.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

from aurum.adapters.storage.layout import RunLayout, ledger_path
from aurum.backtest.pipeline import RunRequest, batch_id, execute, prepare
from aurum.core.errors import ArtifactsPendingError, ConfigError, EvaluationFailedError
from aurum.ledger.trial_ledger import TrialLedger, point_key
from aurum.ports.clock import ClockPort
from aurum.settings import Settings
from aurum.strategies.registry import declared_grid


@dataclass(frozen=True)
class PointResult:
    params: dict[str, Any]
    status: str
    """``succeeded`` (now), ``done`` (earlier), ``artifacts pending`` (trial recorded, run
    files not published), ``failed`` (now) or ``not retried``."""
    attempt_id: str | None
    trial_id: str | None
    detail: str


def run_grid(
    settings: Settings,
    *,
    strategy: str,
    variant: str,
    first: date,
    last: date,
    retry_failed: bool,
    runs: RunLayout,
    clock: ClockPort,
) -> list[PointResult]:
    if last < first:
        raise ConfigError(f"empty period {first}..{last} (--from after --to)")
    cfg = settings.config.strategies.get(strategy)
    if cfg is None:
        raise ConfigError(f"unknown strategy {strategy!r}; see configs/strategies/")
    gid = batch_id(strategy, variant, first, last)
    points = declared_grid(cfg)
    probe = RunRequest(strategy, variant, first, last, params=points[0], grid_id=gid)
    context = prepare(settings, probe, clock=clock).context
    with TrialLedger(ledger_path(settings.data_dir), clock=clock) as ledger:
        ledger.bind_batch(gid, context)  # the first run freezes it; any other context: refused
        history = ledger.attempts(grid_id=gid)
        trials = {key: ledger.get_trial(tid) for key, tid in ledger.grid_trials(gid).items()}
    results: list[PointResult] = []
    for point in points:
        key = point_key(point)
        mine = [a for a in history if point_key(a.params) == key]
        latest = mine[-1] if mine else None
        if key in trials:
            trial = trials[key]
            run_id = str(trial.params.get("run_id"))
            published = runs.manifest(run_id).is_file()
            status, detail = ("done", run_id) if published else ("artifacts pending", run_id)
            owner = trial.params.get("attempt_id")
            results.append(
                PointResult(point, status, str(owner) if owner else None, trial.trial_id, detail)
            )
            continue
        retry_of = None
        if latest is not None:  # failed, or crashed without an outcome, and no trial
            if not retry_failed:
                detail = f"{latest.status} at {latest.stage}: {latest.error}"
                results.append(PointResult(point, "not retried", latest.attempt_id, None, detail))
                continue
            retry_of = latest.attempt_id
        request = RunRequest(
            strategy=strategy,
            variant=variant,
            first=first,
            last=last,
            params=point,
            grid_id=gid,
            retry_of=retry_of,
            notes=f"grid {gid}" + (f" (retry of {retry_of})" if retry_of else ""),
        )
        try:
            out = execute(settings, request, runs=runs, clock=clock)
        except EvaluationFailedError as exc:
            results.append(PointResult(point, "failed", exc.attempt_id, None, str(exc)))
            continue
        except ArtifactsPendingError as exc:
            results.append(
                PointResult(point, "artifacts pending", exc.attempt_id, exc.trial_id, str(exc))
            )
            continue
        results.append(PointResult(point, "succeeded", out.attempt_id, out.trial_id, out.run_id))
    return results
