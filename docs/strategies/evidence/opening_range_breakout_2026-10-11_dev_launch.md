# opening_range_breakout 0.1.0: interpreter launch and source identity evidence (2026-10-11)

Written by hand to preserve how the 12 development trials were launched and what their
recorded source identity covers. Nothing here was normalised or rerun; the trials stand as
recorded (`opening_range_breakout_2026-10-11_dev_batch.md`).

## Why the approved command was not typed literally

The approved commands were `uv run --no-sync aurum backtest grid opening_range_breakout
--variant m15|m5 --from 2020-12-15 --to 2025-09-30`. From the agent's process on this VM,
`uv run --no-sync python -c "import aurum"` fails before starting Python:

```text
error: Failed to query Python interpreter
  cause: The path cannot be traversed because it contains an untrusted mount point. (os error 448)
```

The venv's `home` (`pyvenv.cfg`) is a junction, and the agent's process may not traverse it:

```text
home = C:\Users\neago\AppData\Roaming\uv\python\cpython-3.12-windows-x86_64-none
implementation = CPython
uv = 0.12.24
version_info = 3.12
include-system-site-packages = false
prompt = aurum
```

## What ran instead

The junction's target interpreter, with `-S` (no base site-packages), through a wrapper that
puts only the venv's site-packages on the path (processing its `.pth` files, which hold the
editable install) and then runs the module. Nothing was synced, installed or rebuilt.

- Interpreter: `C:\Users\neago\AppData\Roaming\uv\python\cpython-3.12.15-windows-x86_64-none\python.exe`,
  `3.12.15 (main, Oct  3 2026, 01:05:37) [MSC v.1944 64 bit (AMD64)]`, file sha256
  `4a3bbd9702bb52d720920a85443e43d90bc405e795cc2d6c1b16620cc0ca396b`.
- Wrapper (agent scratch directory, not in the repository), `venv_exec.py`, sha256
  `7714dc0e7959cd747cc8484df558dde1be58df18720b05018d44e54174e6d7cd`, verbatim (Python source in a
  `text` block so no formatter rewrites it):

```text
"""Run a module or script inside the aurum venv's environment, without the base interpreter's
site-packages (start the base python with -S). Workaround for `uv run` failing from this
process (os error 448); sys.path then matches the venv's."""
import runpy
import site
import sys

site.addsitedir(r"C:\venvs\aurum\Lib\site-packages")
args = sys.argv[1:]
if args[0] == "-m":
    sys.argv = [args[1], *args[2:]]
    runpy.run_module(args[1], run_name="__main__", alter_sys=True)
else:
    sys.argv = args
    runpy.run_path(args[0], run_name="__main__")
```

- Exact launches (PowerShell 5.1, working directory `C:\code\aurum`, `$env:COLUMNS = "200"`
  for the table width; `$py` = the interpreter above, `$S` = the scratch directory):

```powershell
& $py -S "$S\venv_exec.py" -m aurum.cli backtest grid opening_range_breakout --variant m15 --from 2020-12-15 --to 2025-09-30 *> reports\grid_logs\orb_m15.log
& $py -S "$S\venv_exec.py" -m aurum.cli backtest grid opening_range_breakout --variant m5 --from 2020-12-15 --to 2025-09-30 *> reports\grid_logs\orb_m5.log
```

  Each was followed by `"exit $LASTEXITCODE"` appended to its log; both logs end `exit 0`.
  m15: process started 08:38:56Z, last outcome 08:46:39Z. m5: batch frozen 08:47:21Z, last
  outcome 08:55:35Z. One process at a time.

## Why the environment is the same

`aurum.backtest.pipeline.code_identity()` hashes the installed distributions
(`environment_sha256`). Measured before the run with each launch:

| launch | `sys.path` site-packages | environment sha256 |
|---|---|---|
| `python -S` + wrapper (used) | `C:\venvs\aurum\Lib\site-packages` only | `f63558cc3a9b48d4f72b1d8054227a8f3d506c9e3504904c7df69869b8f0ce8b` |
| `python` + wrapper, without `-S` (not used) | the base interpreter's site-packages and the venv's | `669a24dc66b81e81d992cefb528630a2a30ff5395da2260d4319abcfa5c0ffa7` |
| `uv run --no-sync` (trend_pullback batches, 2026-10-10) | the venv's | `f63558cc3a9b48d4f72b1d8054227a8f3d506c9e3504904c7df69869b8f0ce8b` |

The launch used gives the same environment identity as `uv run --no-sync` did.

## Raw source identity and the Windows line-ending caveat

- Recorded in both batch contexts (raw working-tree bytes of `src/aurum/**/*.py`, 95 files):
  **`70fe4b3ab2d4ff9e6781073361e9572df1f09f59072b37218c8a086eb4d01c3a`**.
- This checkout has `core.autocrlf = true`. Git content of every source file equals
  `b5ea565`, but two source files have CRLF line endings in the working tree
  (`git ls-files --eol`: `i/lf w/crlf`): `src/aurum/backtest/parity.py` and
  `src/aurum/strategies/opening_range_breakout.py`. The recorded hash covers those CRLF bytes.
- A fresh LF checkout of `b5ea565` would hash to
  `89a8182b710ac0c50369bb879c34ec8a1690aafa2d68b6c731d66b7ecbed7e16` (computed from the git
  blobs in memory; no file was touched). It is a different identity for the same git content,
  so a batch resumed from such a checkout would be refused as a context change.
- The working-tree files were **not** normalised, before or after the trials.
- Later commits change `src/aurum` (the assessment's `aurum.validation.walkforward`
  additions), so the current tree no longer hashes to either value. The 12 trials, their
  contexts and their files are unchanged.

## The read-only scripts

`scripts/opening_range_breakout_dev_batch.py` and `scripts/opening_range_breakout_dev_analysis.py`
were launched the same way (`& $py -S "$S\venv_exec.py" scripts\<script>.py`, plus the post-run
backup's time stamp `0855Z` for the batch script). Each regenerates its files byte for byte.
