# Daily data maintenance on the Windows VM (Phase 2). Safe to run any number of times:
# backfill skips stored days, resample is deterministic, QA only reads.
#   1. backfill yesterday's complete UTC day of ticks and bars (and anything missed)
#   2. rebuild normalized ticks + canonical bars, compare with the broker's bars
#   3. data QA (exit 1 if anything unexplained is found)
# Schedule it daily after 00:15 UTC (see docs/VM_SETUP.md section 13).
$ErrorActionPreference = "Stop"
Set-Location "C:\code\aurum"
$aurum = "C:\venvs\aurum\Scripts\aurum.exe"
$log = "C:\aurum-data\logs\daily.jsonl"

& $aurum data backfill --log-file $log
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $aurum data resample
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $aurum data qa --strict
exit $LASTEXITCODE
