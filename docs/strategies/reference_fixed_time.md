# reference_fixed_time: simulator reference strategy and MT5 parity check

**Not a research strategy.** No hypothesis and no edge by design, so it does not follow
`STRATEGY_SPEC_TEMPLATE.md`. It exists only to exercise the Phase 3 simulator end to end and to
be replicated exactly by an MT5 Strategy Tester EA (`mql5/AurumReference.mq5`). It is never
traded on demo (`enabled: false`). Results go in the trial ledger like every run; they say
nothing about any trading idea.

## Rules

Code: `aurum.strategies.reference.FixedTimeReference`. Params: `configs/strategies/reference_fixed_time.yaml`.

All times are UTC. Decisions happen at closes of the decision timeframe (M5 or M15; the
results are identical, because both entry times are closes on both grids).

| Entry time (UTC) | Side | Stop | Target | Max hold |
|---|---|---|---|---|
| 08:00 | BUY | close - 1000 points | close + 1500 points | 120 min |
| 14:30 | SELL | close + 3000 points | close - 4500 points | none |

- `close` = bid close of the decision-timeframe bar that closed at the entry time. If that bar
  is missing (data gap), there is no entry.
- Market order, one position at a time. Volume: the Phase 4 risk engine by default
  (`--risk policy`); the parity check uses a fixed `backtest.fixed_volume_lots` (0.10) with
  `--risk fixed`, because the EA trades a fixed volume.
- Exits: broker-side stop and target, `max_hold` measured from the decision instant (TIMEOUT),
  and force-flat at `min(20:00 London, market close - 15 min)`.
- 08:00 UTC and 14:30 UTC are inside `london_morning` and `ldn_ny_overlap` in summer and winter
  time, so the session filter never blocks an entry on a trading day.

## Parity check with the MT5 Strategy Tester (Phase 3 exit gate)

### What is compared

Aurum's run, with **latency 0 and slippage 0**, against the tester in **"Every tick based on
real ticks"** with **"Zero latency, ideal execution"**, over **2026-09-14 to 2026-10-08**. That
window is 19 trading days, all with exact tick fidelity (ADR-0007) and no holidays or early
closes (the EA does not model early closes).

Aurum run: `20261009T221629Z_reference_fixed_time_m5_be6c94` (38 trades), made with

```
uv run aurum backtest run reference_fixed_time --from 2026-09-14 --to 2026-10-08 --variant m5 --latency-ms 0 --slippage-points 0 --risk fixed --parity
```

Trades are paired by UTC entry date and side. Tolerances (owner-confirmed 2026-10-10;
`aurum backtest parity` options):

| Check | Tolerance |
|---|---|
| trade count inside the window | **exactly equal** |
| entry and exit fill times | 1 s |
| entry/exit price, stop, target | 1 point (0.01) |
| exit reason | identical |
| booked profit, commission (per trade) | 0.02 account currency |
| total net P&L over all in-window trades | 0.02 account currency (`--cumulative-money-tolerance`, separate, never `count × 0.02`) |

Any difference outside these is listed and must be explained, not tuned away. A tolerance is
never loosened to make a run pass; if a legitimate larger difference is found, its cause is
recorded first.

### Tester settings that must match Aurum's run

Most parity mismatches come from these settings, not from engine bugs. Check each before
comparing:

| Setting | Must be | Aurum's value comes from |
|---|---|---|
| Modelling | Every tick based on real ticks | — |
| Delays | Zero latency, ideal execution | `--latency-ms 0 --slippage-points 0` |
| Deposit | 10000 USD | `backtest.yaml initial_balance` |
| Leverage | 1:500 | `backtest.yaml leverage` |
| Commission | 2.25 USD per lot per side (0.23 per deal at 0.10 lots) | `broker.yaml commission` |
| Volume | 0.10 lots | `backtest.yaml fixed_volume_lots`, `--risk fixed` |

Verify the commission in the deals CSV: every deal must show `-0.23`. If it shows `0.00`, the
tester is not applying the account's commission. Fix the tester setup before comparing; don't
accept it as an explained difference.

### Steps (Windows, in the Fusion MT5 terminal logged in to demo 504691)

The live tick collector runs in the main terminal; don't stop it. Run the tester in a **second
MT5 installed in portable mode** in its own folder (`terminal64.exe /portable`), which has its
own data directory: "Data Folder" below is then the portable install's folder. The EA uses
`FILE_COMMON`, so the CSV normally appears under `%APPDATA%\MetaQuotes\Terminal\Common\Files`,
including when the terminal runs in portable mode. To verify the exact path on this VM, use
MetaEditor's *File > Open Common Data Folder* or print
`TerminalInfoString(TERMINAL_COMMONDATA_PATH)`.

1. **Install the EA.** In MT5, choose *File > Open Data Folder*. Copy
   `C:\code\aurum\mql5\AurumReference.mq5` into `MQL5\Experts\Aurum\` (create the `Aurum` folder).
2. **Compile.** Open it in MetaEditor (F4) and press *Compile* (F7). The log must say
   `0 errors, 0 warnings`. (Compiled here on 2026-10-09 with the installed MetaEditor: clean.)
3. **Open the Strategy Tester** (*View > Strategy Tester*, Ctrl+R) and choose *Overview > Single*
   (or the *Settings* tab).
4. **Settings tab:**
   - Expert: `Aurum\AurumReference.ex5`
   - Symbol: `XAUUSD`. Timeframe: `M5`.
   - Date: *Custom period*, from `2026.09.14` to `2026.10.09`. If the tester also trades on the
     end date, `aurum backtest parity` ignores those trades and reports how many it dropped.
   - Forward: `No`
   - Delays: `Zero latency, ideal execution`
   - Modelling: `Every tick based on real ticks`
   - Deposit: `10000` `USD`. Leverage: `1:500`.
   - Optimization: `Disabled`. Visual mode: off.
5. **Inputs tab:** leave every default (lots 0.10, magic 7020261, 08:00/14:30 UTC entries,
   distances as in the table above, force-flat 20:00 London, server shift 7 h).
6. **Start.** The tester downloads Fusion's real tick history for the period first, which can
   take several minutes. When it finishes, the *Journal* tab shows
   `AurumReference: N deals written to Common\Files\AurumReference_deals.csv`.
7. **Save the tester report** for the record: on the *Backtest* tab, right-click, choose
   *Report > HTML (Internet Explorer)*, and save it as `AurumReference_parity.html`.
8. **Send me** both files:
   - `%APPDATA%\MetaQuotes\Terminal\Common\Files\AurumReference_deals.csv` (the deals, exact
     server-time milliseconds). This is the file the comparison reads.
   - `AurumReference_parity.html` (the tester's own report, kept as evidence).

### Comparison (I run this)

```
uv run aurum backtest parity 20261009T221629Z_reference_fixed_time_m5_be6c94 "%APPDATA%\MetaQuotes\Terminal\Common\Files\AurumReference_deals.csv"
```

Exit code 0 means equal trade counts, every trade within tolerance and the total net P&L
within the cumulative tolerance. Otherwise each difference is printed and
written to `reports/runs/<run_id>/parity.csv` (summary in `parity.json`). The EA's times are
converted to UTC by Aurum's `ServerTimeZone` (ADR-0004), not by the EA, so a mistake in the
EA's own clock arithmetic shows up as an entry-time difference instead of being hidden.

### Differences to expect, and how they would be explained

- **Commission.** If the tester does not apply the account's commission, every trade differs
  by 0.46 (2 x 0.23 at 0.10 lots) and the cumulative gate fails. That is a setup error, not a
  result: fix the tester's commission (see the settings table) and rerun.
- **Stop fills.** Aurum fills a stop at the bid/ask of the first tick that reaches it, so a jump
  through the stop is paid in full. If the tester fills at the stop price instead, exit prices
  differ by the jump size on those trades, and Aurum is the more conservative of the two.
- **Ticks.** The tester uses Fusion's server tick history. Aurum uses its stored, normalised
  copy (consecutive duplicates removed, invalid quotes dropped). On exact days the two produce
  the same M1 bars, so fills should coincide. A broker-side revision of history would show up
  as a time or price difference on single trades.
