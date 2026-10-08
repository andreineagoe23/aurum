# ADR-0001: Ports and adapters

- Status: Accepted
- Date: 2026-10-08

## Context

Aurum is developed on macOS but trades through MetaTrader 5, whose Python package (`MetaTrader5`) is Windows-only and talks to a local terminal. The same strategy and risk code must run unchanged in unit tests, backtests, shadow mode and demo trading. Order safety must not depend on which environment is running.

## Decision

Use a hexagonal (ports-and-adapters) design.

- `aurum.core` holds domain models, enums, clock and safety logic and imports nothing else from `aurum`.
- `aurum.ports` defines `BrokerPort`, `ClockPort` (and later `DataStorePort`) as Protocols.
- Adapters implement ports: `FakeBroker` (tests), a simulated broker (backtest, Phase 3), a shadow broker (no orders), and `MT5Broker` (Windows).
- Only `aurum.adapters.mt5` imports `MetaTrader5`, lazily inside functions, so the package imports on macOS.
- Order flow is `Signal -> RiskDecision -> OrderIntent -> BrokerPort`. Brokers re-check approval and the live-trading guard.
- Import rules are enforced by `tests/unit/test_architecture.py`.

## Consequences

Positive:
- About 80% of the code is testable on the Mac without a terminal.
- Strategies cannot reach the broker or bypass risk.
- Backtest, shadow and demo share one decision path, which reduces divergence.
- The live-trading guard sits in one place.

Negative:
- More indirection and boilerplate than calling MT5 directly.
- The Protocol must be kept faithful to MT5 behaviour; fakes can hide real-broker quirks, so Windows integration tests are still needed.
- Domain models duplicate part of the MT5 data structures and need mapping code.

## Alternatives considered

- Direct MT5 calls in strategy code: fastest to write, but untestable on macOS, couples logic to one broker and makes risk bypass easy. Rejected.
- Backtesting library (backtrader, vectorbt): quick start, but limited control over bid/ask fills, intra-bar ordering, session force-flat and the as-of rules this project requires; also does not cover shadow or demo execution. Rejected.
- MQL5 Expert Advisor in the terminal: runs natively and has the Strategy Tester, but poor tooling for research, testing and version control, and no reuse of Python data tooling. Rejected, though the Strategy Tester is used as a reference for Phase 3 parity checks.
