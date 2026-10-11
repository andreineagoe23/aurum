# vwap_reversion 0.1.0 — B0 specification audit (2026-10-11)

Independent, read-only final audit of `docs/strategies/vwap_reversion.md` draft revision 2 at
`5174089` (remote `main`), at approval boundary B0. Accepted by the owner on 2026-10-11; the
corrections are applied in draft revision 3 (spec section 19).

## Scope

- **Read:** `CLAUDE.md`, `docs/STATUS.md`, the spec; the cited configs (`sessions.yaml`,
  `risk.yaml`, `backtest.yaml`, `broker.yaml`, `data.yaml`, `strategies/vwap_reversion.yaml`);
  the cited code (engine, calendar, news blackout, risk policy, simulator broker, runner,
  indicators, feature view, settings, pipeline, walk-forward); `reports/phase_2.md` §4–5,
  `reports/phase_3.md` §4; ADR-0009; `docs/VALIDATION_PROTOCOL.md`; opening_range_breakout
  §8a, §14, §16.
- **Not read:** bars, ticks, feature stores, trade tables, the ledger, symbol snapshots,
  sealed data. No feasibility script, backtest, trace or test was run. The DSR and cost
  arithmetic, and the DST checks of the cutoff and FOMC lists, were recomputed with a
  standard-library Python calculation using no repository code or data.

## Verified

DST and blackout tables (`NewsBlackout` windows are half-open); setup closes (10 / 8) and
decision-instant ranges; early-close cutoffs (sessions are clipped at the cutoff, not
cutoff − 45); hold bounds 12:15 / 17:45 London; §8a (24.905 → 2,490; 20.095; floors 8,038 /
8,118); §13.1 (9.75 USD); §13.2 (489 / 516 ≈ 0.948); §13.3 (zero-edge 15.5–20.5 points,
break-even 51.8 / 50.4 / 51.2%); gate-12 approximation (SR0 0.0597 / 0.0613; required daily
SR 0.111, 1.76 annualised, 0.110, 0.113); both synthetic traces; the cited spread (20, 24, 6)
and intra-bar figures; `Signal` fields; section 16 against frozen ORB §16. No look-ahead and no
mirroring defect found.

## Blocking corrections (patch: `vwap_reversion_2026-10-11_b0_audit.patch`)

The patch applies with `git apply -p1` to the spec at `5174089` (blob `d280cd4`).

1. **Decision instants and statuses.** Revision 2 made `T = A` a decision instant (OPEN in the
   calendar), where `B(A)` is empty and `Σ w = 0`, so `twv_undefined` counted in every session,
   and statuses had no precedence. Fix: `A + 60 min < T ≤ E − 15 min`; other OPEN calls
   return and count nothing; explicit status precedence. No trade changes.
2. **V17 scope.** The spread statistics lie wholly (not partly) in the sealed period, and V17
   omitted the §13.3 cost illustration behind V8 (also §2) and the release-spike remarks.
3. **Snapshot read at B2.** §8a required a re-read "at freeze", but no approval boundary
   authorised a snapshot read.

## Non-blocking corrections (applied in revision 3, not in the patch)

1. §13.2: the stops-level distance uses the closing-side quote (bid long, ask short), not the
   fill side.
2. §8.2: the Phase 3 report's ">10%" proposal is not gate 11; the ADR-0009 §9 rule is stated.
3. §3.2: `atr_period` 14 is `RiskConfig.atr_period` (default in `aurum/settings.py`, used by
   the pipeline), not a `risk.yaml` key.
4. M15 read-set completeness at non-M15 instants: slots with `open_time ≥ A` and
   `close_time ≤ T`.
5. §16: frozen ORB §16 applies verbatim where silent; five inherited details restated.
6. §2: the MASTER_PROMPT wording is a paraphrase.
7. `setups_expired` excludes replaced setups.
8. Boundary alignment holds for configured session times, not for a clipped end.
9. The FOMC-mismatch and early-cutoff lists are complete for the evaluated period; the
   45-minute entry cutoff never binds there.
10. The mirror property test needs exactly reflectable inputs or excludes equality boundaries.

## Owner decision (2026-10-11)

Audit accepted. V1–V17 accepted with the proposed defaults; V17 explicitly, with the corrected
disclosure; V14 accepted. Not authorised: B1, B2 (including the snapshot read), freezing,
implementation, tests, traces, feasibility work, market-data, ledger, parity or sealed access,
trials.
