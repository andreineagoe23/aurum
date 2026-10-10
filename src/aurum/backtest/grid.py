"""Run a strategy's declared search grid as one auditable batch (``aurum backtest grid``).

Each grid point is one evaluation attempt in the trial ledger (``aurum.backtest.pipeline``),
grouped by a deterministic ``grid_id`` (strategy, variant, period). Re-running the same grid:

* a point whose latest attempt succeeded is **done** and not run again;
* a point whose latest attempt failed or never finished is **not retried** unless
  ``retry_failed`` is set; a retry is a new attempt linked to it (``retry_of``), never another
  grid point;
* a refusal that applies to the whole request (unknown strategy, invalid params, the sealed
  period) raises before any attempt and stops the batch.

The grid is the one the strategy declares (``registry.declared_grid``); nothing else can be
run through it.
"""

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from aurum.adapters.storage.layout import RunLayout, ledger_path
from aurum.backtest.pipeline import RunRequest, execute
from aurum.core.errors import ConfigError, EvaluationFailedError
from aurum.ledger.trial_ledger import TrialLedger
from aurum.ports.clock import ClockPort
from aurum.settings import Settings
from aurum.strategies.registry import declared_grid


@dataclass(frozen=True)
class PointResult:
    params: dict[str, Any]
    status: str
    """``succeeded`` (now), ``done`` (earlier), ``failed`` (now) or ``not retried``."""
    attempt_id: str | None
    trial_id: str | None
    detail: str


def grid_id(strategy: str, variant: str, first: date, last: date) -> str:
    return f"{strategy}/{variant}/{first}..{last}"


def _key(params: dict[str, Any]) -> str:
    return json.dumps(params, sort_keys=True)


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
    cfg = settings.config.strategies.get(strategy)
    if cfg is None:
        raise ConfigError(f"unknown strategy {strategy!r}; see configs/strategies/")
    gid = grid_id(strategy, variant, first, last)
    with TrialLedger(ledger_path(settings.data_dir), clock=clock) as ledger:
        history = ledger.attempts(grid_id=gid)
    results: list[PointResult] = []
    for point in declared_grid(cfg):
        mine = [a for a in history if _key(a.params) == _key(point)]
        latest = mine[-1] if mine else None
        retry_of = None
        if latest is not None and latest.status == "succeeded":
            results.append(
                PointResult(point, "done", latest.attempt_id, latest.trial_id, "succeeded earlier")
            )
            continue
        if latest is not None:  # failed, or crashed without an outcome
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
        results.append(PointResult(point, "succeeded", out.attempt_id, out.trial_id, out.run_id))
    return results
