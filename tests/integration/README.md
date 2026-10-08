# Integration tests (MT5)

Real-broker checks: data retrieval, order placement, modification and reconciliation.
Mark every test with `@pytest.mark.mt5`; they auto-skip unless on Windows with MetaTrader5 installed.
Run in the Windows VM with `uv run pytest -m mt5`.
Use DEMO accounts only.
