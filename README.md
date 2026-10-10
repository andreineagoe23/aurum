# Aurum

Aurum is a research and automated-trading platform for intraday XAUUSD on MetaTrader 5. It collects replayable market data, tests simple rule-based strategies with realistic costs and no look-ahead, and promotes only survivors through shadow mode to demo trading.

**Demo only.** No real money. Live trading is blocked by default and out of scope for v1.

## Quickstart (Mac)

```bash
scripts/mac_setup.sh
# or manually:
uv sync && uv run pytest && uv run aurum doctor
```

## Windows VM

MetaTrader 5 and the `MetaTrader5` Python package run only on Windows. See [docs/VM_SETUP.md](docs/VM_SETUP.md).

## Repository layout

- `src/aurum/` : package (core, ports, adapters, data, sessions, features, strategies, risk, execution, backtest, ledger, monitoring, apps, cli; validation is Phase 6). Code map: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- `configs/` : YAML configs (broker, sessions, risk, promotion, data, backtest, strategies/)
- `mql5/` : the MT5 Strategy Tester EA used for the parity check
- `docs/` : documentation and ADRs
- `tests/` : unit, property, replay, integration (Windows only), fault
- `scripts/` : setup scripts
- `notebooks/`, `reports/`, `models/`, `data/` : research output, git-ignored where large

## Docs

- [Status](docs/STATUS.md) : current phase, approved work queue, open decisions
- [Master prompt](docs/MASTER_PROMPT.md) : source of truth for the build
- [Project charter](docs/PROJECT_CHARTER.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Data dictionary](docs/DATA_DICTIONARY.md)
- [Strategy spec template](docs/STRATEGY_SPEC_TEMPLATE.md)
- [Validation protocol](docs/VALIDATION_PROTOCOL.md)
- [VM setup](docs/VM_SETUP.md)
- [Decisions (ADRs)](docs/DECISIONS/) and phase reports in `reports/phase_<n>.md`

## Safety

- Live trading is blocked by default; a non-demo account needs `AURUM_MODE=LIVE`, the exact acknowledgement phrase and `configs/promotion.yaml` with `live_enabled: true` and a past sign-off date.
- Strategies emit Signals only; orders require an approved RiskDecision and carry a broker-side stop loss.
- Every order has an idempotency key; state is reconciled on startup.
- Positions are force-flat before the daily cutoff; a KILL file or `aurum kill` locks trading.
- Secrets live only in `.env` (git-ignored) and are never logged.

## Development

```bash
uv run ruff check
uv run ruff format
uv run mypy
uv run pytest
uv run pytest --cov=aurum
```
