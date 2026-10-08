# ADR-0002: Session phase semantics

- **Status:** Accepted (provisional — confirm with owner, see reports/phase_0.md)
- **Date:** 2026-10-08

## Context

MASTER_PROMPT names six phases (CLOSED → PRE_SESSION → OPEN → NO_NEW_ENTRIES → EXIT_ONLY →
FORCE_FLAT) but not their exact boundaries. `configs/sessions.yaml` gives two session windows
(07:00–11:00, 13:00–16:30 London), `no_new_entries_after` 19:30 and `force_flat_at` 20:00.
Restarts must still flatten exposure after the cutoff.

## Decision

For each trading day D, in Europe/London wall-clock time, converted to UTC per date with `zoneinfo`:

| Phase | From | To | Entries | Exposure |
|---|---|---|---|---|
| CLOSED | 00:00 | first session start − `pre_session_minutes` (30) | no | must be flat |
| PRE_SESSION | 06:30 | 07:00 | no | must be flat |
| OPEN | inside a session window (clipped at `no_new_entries_after`) | | yes (risk permitting) | allowed |
| NO_NEW_ENTRIES | between/after session windows | 19:30 | no | open positions managed normally |
| EXIT_ONLY | 19:30 | 20:00 | no | only exits / risk-reducing changes |
| FORCE_FLAT | 20:00 | 24:00 | no | must be flat; close everything |

Non-trading days (Sat, Sun) are CLOSED all day. All intervals are half-open `[from, to)`.
`must_be_flat` is true in CLOSED, PRE_SESSION and FORCE_FLAT, so any exposure found then
(e.g. after a crash or reconnect) is closed immediately.

`SessionStateMachine` emits a transition on its **first** update, so a process restarting at
21:00 sees "entered FORCE_FLAT" exactly as if it had been running at 20:00.

`risk.min_minutes_before_cutoff` (45) is a separate risk-engine rule (Phase 4), not a phase.

## Consequences

- Entries happen only inside the configured session windows; 11:00–13:00 and 16:30–19:30 are
  management-only.
- With the default config, `no_new_entries_after` (19:30) never cuts a session short; it matters
  only if sessions are extended.
- Config validation forbids overlapping windows, windows ending after `force_flat_at`, and
  windows crossing local midnight.
