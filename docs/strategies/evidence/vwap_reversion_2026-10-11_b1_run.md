# vwap_reversion B1 feasibility diagnostic: run record (2026-10-11)

Hand-written record of how the B1 diagnostic was run. It holds what the generated report
(`vwap_reversion_2026-10-11_b1_feasibility.md`) cannot know about itself: the launch, the output
hashes and the rerun check. Authorisation and rulings: spec `docs/strategies/vwap_reversion.md`
section 18c (draft revision 4).

## Methodology commit

- **`a09b1964eeb39dbb75e35c50130eb35f206d4a0b`**, committed and pushed to `origin/main` before
  any market data was read. It holds spec revision 4, the STATUS entry, and the script with its
  synthetic tests. The pre-ruling draft of the script was `7e11f46`.
- Both runs started from that commit with a clean worktree. The script refuses any tracked
  change and any untracked file other than its two outputs. It also checked the spec blob,
  `22d1ffacaf618e39e687d77563b491a8d57809db`, before reading data.
- Before any market data, each run also passed **500 synthetic test cases**. It then checked
  that the R6 cut equals the market open of trading date 2025-10-01 in `configs/data.yaml`.
- No script or rule was changed after the market data was opened.

## Launch

- Intended command: `uv run --no-sync python scripts/vwap_reversion_feasibility.py`.
- `uv run` cannot start from the agent's process on the VM (os error 448). The equivalent
  launch recorded in `opening_range_breakout_2026-10-11_dev_launch.md` was used: the venv's
  base interpreter with `-S`, through the `venv_exec.py` wrapper, which adds only
  `C:\venvs\aurum\Lib\site-packages`. Nothing was synced or installed.
  - Interpreter: `C:\Users\neago\AppData\Roaming\uv\python\cpython-3.12.15-windows-x86_64-none\python.exe`,
    Python 3.12.15, sha256 `4a3bbd9702bb52d720920a85443e43d90bc405e795cc2d6c1b16620cc0ca396b`
    (equal to the recorded value).
  - Wrapper: agent scratch directory, recreated verbatim from that record, sha256
    `7714dc0e7959cd747cc8484df558dde1be58df18720b05018d44e54174e6d7cd` (equal to the recorded
    value).
  - PowerShell 5.1, working directory `C:\code\aurum`, `$env:COLUMNS = "200"`:

    ```powershell
    & $py -S "$S\venv_exec.py" scripts\vwap_reversion_feasibility.py *> "$S\b1_run1.log"
    ```

- Run 1: 21:54:45Z–21:55:43Z. Run 2, the same command after copying run 1's outputs aside:
  21:56:22Z–21:56:54Z. Both logs read `written to docs\strategies\evidence`, exit 0.
- The live collector was not running (no `aurum` process), as STATUS records; it was not
  touched.

## Inputs (canonical XAUUSD bid bars, `C:\aurum-data`)

Files were selected by UTC date in the name before any was opened. No partition dated after
2025-09-30 was opened. Warm-up was exactly by bar count (13 H1, 100 M15). Every file's sha256
was taken before reading and re-checked after the run, unchanged.

| Timeframe | Files | Dates | Rows in files | Rows excluded by R6 | Digest of (name, sha256) |
|---|---|---|---|---|---|
| M5 | 1,751 | 2020-12-15 … 2025-09-30 | 339,999 | 24 | `64135e792fb7376fd85ddfafce32757a636971e6d1d8d55717dbfe76ccdf2c24` |
| M15 | 1,755 | 2020-12-11 … 2025-09-30 | 113,536 | 8 | `44a88edbcd833b14617ee4d8b9110284e4b146101f63ff9b027d298bd56d683a` |
| H1 | 1,752 | 2020-12-14 … 2025-09-30 | 28,377 | 2 | `b010da9d63168cf4ee173d7ecd37e3ec7e9216b3f7ae75573fa459017f254479` |

- **R6:** predicate `open_time < 2025-09-30T22:00:00Z`. The excluded rows were counted from
  Parquet row counts minus the rows returned; their values were never collected, printed or
  used. The script asserted that no returned frame holds a row at or after the cut.
- No snapshot, ledger, tick, trial, backtest or sealed file was read. The `aurum.ledger`,
  `aurum.backtest`, `aurum.adapters.storage.specs`, `aurum.adapters.mt5`, `aurum.execution`,
  `aurum.risk` and `aurum.strategies` modules were never imported (asserted).

## Outputs and the byte-identity check

| File | Bytes | sha256 (run 1) | sha256 (run 2) |
|---|---|---|---|
| `vwap_reversion_2026-10-11_b1_feasibility.md` | 21,155 | `e9e4090b998c2df57edda3097b7529a4676158f56a855920f212851e331fdb35` | identical |
| `vwap_reversion_2026-10-11_b1_feasibility.csv` | 380,986 (7,084 lines) | `587cc87405bc977ac810283417deee40017d4dd88a2983cdf251396d76b14e77` | identical |

The second run reproduced both files byte for byte.
