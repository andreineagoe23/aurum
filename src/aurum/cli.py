"""``aurum`` command line. Commands for later phases exist as placeholders that exit with code 2."""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, NoReturn
from zoneinfo import ZoneInfo

import polars as pl
import typer
from rich.console import Console
from rich.table import Table

from aurum.adapters.storage.layout import DataLayout
from aurum.core.clock import SystemClock
from aurum.core.errors import AurumError, ConfigError
from aurum.core.timeframes import Timeframe
from aurum.data.reports import write_report
from aurum.ledger.trial_ledger import TrialLedger
from aurum.monitoring.doctor import Status, run_checks
from aurum.monitoring.logging import configure_logging
from aurum.settings import Settings, load_settings

if TYPE_CHECKING:
    from aurum.adapters.mt5 import MT5Broker
    from aurum.adapters.storage.layout import RunLayout
    from aurum.data.qa import Gap

app = typer.Typer(
    no_args_is_help=True, help="Aurum: intraday XAUUSD research platform (demo only)."
)
mt5_app = typer.Typer(no_args_is_help=True, help="MetaTrader 5 (Windows VM only).")
data_app = typer.Typer(no_args_is_help=True, help="Market data.")
backtest_app = typer.Typer(no_args_is_help=True, help="Backtests.")
validate_app = typer.Typer(no_args_is_help=True, help="Validation.")
ledger_app = typer.Typer(no_args_is_help=True, help="Trial ledger.")
shadow_app = typer.Typer(no_args_is_help=True, help="Shadow mode (no orders).")
trade_app = typer.Typer(no_args_is_help=True, help="Demo trading.")
for sub, name in [
    (mt5_app, "mt5"),
    (data_app, "data"),
    (backtest_app, "backtest"),
    (validate_app, "validate"),
    (ledger_app, "ledger"),
    (shadow_app, "shadow"),
    (trade_app, "trade"),
]:
    app.add_typer(sub, name=name)

console = Console()
NOT_IMPLEMENTED_EXIT = 2

_STATUS_STYLE = {
    Status.OK: "green",
    Status.WARN: "yellow",
    Status.FAIL: "bold red",
    Status.SKIP: "dim",
}


def _not_yet(command: str, phase: int) -> NoReturn:
    console.print(f"[yellow]`aurum {command}` is not implemented until Phase {phase}.[/yellow]")
    raise typer.Exit(NOT_IMPLEMENTED_EXIT)


@app.command()
def doctor() -> None:
    """Check Python/arch/OS, MetaTrader5 availability, configs, data dir and safety settings."""
    results = run_checks()
    table = Table(title="aurum doctor")
    table.add_column("check")
    table.add_column("status")
    table.add_column("detail", overflow="fold")
    for r in results:
        table.add_row(r.name, f"[{_STATUS_STYLE[r.status]}]{r.status}[/]", r.detail)
    console.print(table)
    if any(r.status is Status.FAIL for r in results):
        raise typer.Exit(1)


# ---------------------------------------------------------------------------- mt5 (Phase 1)
def _settings_or_exit() -> Settings:
    try:
        return load_settings()
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


def _connected_broker(log_file: Path | None = None) -> tuple["MT5Broker", Settings]:
    """Load settings, configure JSON logs (stderr or ``log_file``) and connect, or exit 1."""
    from aurum.adapters.mt5 import MT5Broker

    settings = _settings_or_exit()
    configure_logging(file=log_file)
    broker = MT5Broker(settings)
    try:
        broker.connect()
    except AurumError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    return broker, settings


@mt5_app.command("ping")
def mt5_ping() -> None:
    """Connect to the terminal and print terminal/account info (demo?) and the server clock."""
    broker, settings = _connected_broker()
    try:
        account = broker.account_info()
        symbol = broker.resolve_symbol()
        tick = broker.latest_tick(symbol)
        check = broker.server_time_check
    finally:
        broker.disconnect()
    table = Table(title="aurum mt5 ping", show_header=False)
    demo = "[green]DEMO[/]" if account.is_demo else f"[bold red]{account.trade_mode}[/]"
    rows = [
        ("account", f"{account.login} @ {account.server}"),
        ("trade mode", demo),
        ("currency", f"{account.currency} (config: {settings.config.broker.account_currency})"),
        ("balance / equity", f"{account.balance:.2f} / {account.equity:.2f}"),
        ("leverage", f"1:{account.leverage}"),
        ("aurum mode", str(settings.mode)),
        ("symbol", symbol),
        ("last quote (UTC)", f"{tick.time.isoformat()}  bid {tick.bid}  ask {tick.ask}"),
    ]
    if check is not None:
        rows.append(
            (
                "server clock",
                f"{check.status}: expected UTC{_hours(check.expected)}, "
                f"measured UTC{_hours(check.measured)}",
            )
        )
    for name, value in rows:
        table.add_row(name, value)
    console.print(table)


def _hours(delta: timedelta) -> str:
    return f"{delta.total_seconds() / 3600:+.2f}h"


@mt5_app.command("symbols")
def mt5_symbols(find: Annotated[str, typer.Option(help="Search term, e.g. gold")] = "gold") -> None:
    """Discover the broker's XAUUSD symbol and snapshot its spec to <data_dir>/specs/."""
    from aurum.adapters.mt5.snapshot import write_symbol_snapshot

    broker, settings = _connected_broker()
    try:
        matches = broker.find_symbols(find)
        symbol = broker.resolve_symbol()
        spec = broker.symbol_info(symbol)
        raw = broker.symbol_snapshot(symbol)
        server = broker.account_info().server
    finally:
        broker.disconnect()
    console.print(f"symbols matching {find!r}: {', '.join(matches) or '(none)'}")
    table = Table(title=f"trading symbol: {symbol}", show_header=False)
    for name, value in spec.model_dump().items():
        table.add_row(name, str(value))
    console.print(table)
    path = write_symbol_snapshot(settings.data_dir, spec, raw, server=server)
    console.print(f"snapshot written to {path}")


@mt5_app.command("bars")
def mt5_bars(
    tf: Annotated[str, typer.Option(help="M1, M5, M15 or H1")] = "M5",
    count: Annotated[int, typer.Option(min=1, max=500)] = 5,
) -> None:
    """Print the latest completed bars with UTC and London open/close times."""
    timeframe = Timeframe.parse(tf)
    broker, settings = _connected_broker()
    try:
        symbol = broker.resolve_symbol()
        end = SystemClock().now()
        bars = broker.bars(symbol, timeframe, end - timeframe.duration * (count + 600), end)
    finally:
        broker.disconnect()
    london = ZoneInfo(settings.config.sessions.timezone)
    table = Table(title=f"{symbol} {timeframe} (bid, completed only)")
    for col in ("open UTC", "close UTC", "close London", "open", "high", "low", "close", "spread"):
        table.add_column(col)
    for b in bars[-count:]:
        table.add_row(
            b.open_time.strftime("%Y-%m-%d %H:%M"),
            b.close_time.strftime("%H:%M"),
            b.close_time.astimezone(london).strftime("%H:%M %Z"),
            f"{b.open:.2f}",
            f"{b.high:.2f}",
            f"{b.low:.2f}",
            f"{b.close:.2f}",
            str(b.spread_points),
        )
    console.print(table)


# ---------------------------------------------------------------------------- data (Phase 2)
@data_app.command("depth")
def data_depth() -> None:
    """Measure how far back the broker serves bars and ticks, and re-fetch the oldest stored
    tick day of each fidelity to see whether the server's copy has changed (ADR-0007)."""
    from aurum.adapters.storage.parquet import read_parquet
    from aurum.data.depth import measure_depth
    from aurum.data.market_hours import MarketHours
    from aurum.data.schemas import DAY_SOURCES

    broker, settings = _connected_broker()
    layout = DataLayout(settings.data_dir)
    refetch: dict[date, int] = {}
    try:
        symbol = broker.resolve_symbol()
        manifest_path = layout.day_manifest(symbol)
        if manifest_path.exists():
            days = read_parquet(manifest_path, DAY_SOURCES).drop_nulls("tick_fidelity")
            for fidelity in ("incomplete", "exact"):
                part = days.filter(pl.col("tick_fidelity") == fidelity).sort("day")
                if part.height:
                    refetch[part["day"][0]] = int(part["tick_rows"][0])
        result = measure_depth(
            broker,
            symbol,
            MarketHours(settings.config.data.market),
            SystemClock().now(),
            refetch_days=refetch,
        )
    finally:
        broker.disconnect()
    table = Table(title=f"history depth {symbol} (UTC)")
    for col in ("data", "rows", "first", "continuous since"):
        table.add_column(col)
    for b in result.bars:
        table.add_row(b.timeframe, str(b.bars), str(b.first_bar), str(b.continuous_since))
    table.add_row("ticks", "-", str(result.first_tick), "-")
    console.print(table)
    for r in result.refetch:
        console.print(
            f"re-fetch {r['day']}: stored {r['stored_rows']} normalized rows, "
            f"server now {r['server_rows_now']} raw rows"
        )
    console.print(f"report: {write_report(layout, 'depth', {'symbol': symbol, 'result': result})}")


@data_app.command("backfill")
def data_backfill(
    tf: Annotated[
        str | None, typer.Option(help="Comma-separated timeframes (default: data.yaml)")
    ] = None,
    ticks: Annotated[bool, typer.Option(help="Backfill ticks")] = True,
    bars: Annotated[bool, typer.Option(help="Backfill bars")] = True,
    from_: Annotated[
        str | None,
        typer.Option("--from", help="YYYY-MM-DD UTC (default: measured depth in data.yaml)"),
    ] = None,
    to: Annotated[
        str | None, typer.Option(help="YYYY-MM-DD UTC, inclusive (default: yesterday)")
    ] = None,
    refetch_empty: Annotated[
        bool, typer.Option(help="Ask again for days stored empty; replace them if data appeared")
    ] = False,
    log_file: Annotated[Path | None, typer.Option(help="Append JSON logs to this file")] = None,
) -> None:
    """Backfill complete UTC days of broker ticks and bars into immutable raw Parquet.

    Idempotent and resumable: days already stored are skipped.
    """
    from aurum.data.backfill import Backfiller
    from aurum.data.market_hours import MarketHours

    broker, settings = _connected_broker(log_file)
    cfg = settings.config.data.backfill
    timeframes = [Timeframe.parse(t) for t in (tf.split(",") if tf else cfg.timeframes)]
    start = date.fromisoformat(from_) if from_ else None
    end = date.fromisoformat(to) if to else None
    layout = DataLayout(settings.data_dir)
    layout.ensure()
    filler = Backfiller(
        broker, layout, cfg, MarketHours(settings.config.data.market), clock=SystemClock()
    )
    results = []
    try:
        symbol = broker.resolve_symbol()
        if ticks:
            results.append(
                filler.ticks(symbol, start or cfg.ticks_from, end, refetch_empty=refetch_empty)
            )
        if bars:
            for timeframe in timeframes:
                results.append(
                    filler.bars(
                        symbol, timeframe, start or cfg.bars_from, end, refetch_empty=refetch_empty
                    )
                )
    finally:
        broker.disconnect()
    table = Table(title=f"backfill {symbol}")
    for col in ("dataset", "files written", "rows", "skipped (exist)", "refilled", "empty days"):
        table.add_column(col)
    for r in results:
        table.add_row(
            r.dataset,
            str(r.written),
            str(r.rows),
            str(r.skipped_existing),
            str(r.refilled),
            ", ".join(r.empty_days) or "-",
        )
    console.print(table)
    report = write_report(layout, "backfill", {"symbol": symbol, "results": results})
    console.print(f"report: {report}")


@data_app.command("collect")
def data_collect(
    max_seconds: Annotated[
        float | None, typer.Option(help="Stop after this many seconds (default: until stopped)")
    ] = None,
    log_file: Annotated[
        Path | None, typer.Option(help="Append JSON logs to this file instead of stderr")
    ] = None,
) -> None:
    """Append live ticks to raw_ticks/mt5_live until Ctrl+C, SIGTERM or a STOP file.

    Stop a detached collector gracefully by creating <data_dir>/collector/STOP.
    """
    from aurum.adapters.mt5 import MT5Broker
    from aurum.adapters.storage.layout import DataLayout
    from aurum.apps.collector import run_collector, stop_file_path

    settings = _settings_or_exit()
    configure_logging(file=log_file)
    console.print(
        f"collecting into {settings.data_dir}; stop with Ctrl+C or by creating "
        f"{stop_file_path(DataLayout(settings.data_dir))}"
    )
    stats = run_collector(settings, MT5Broker(settings), max_seconds=max_seconds)
    console.print(
        f"stopped: {stats.rows_written} ticks in {stats.chunks_written} chunks, "
        f"{stats.connects} connects, {stats.disconnects} disconnects, {stats.gaps} gaps, "
        f"{stats.pauses} pauses"
    )


@data_app.command("qa")
def data_qa(
    from_: Annotated[
        str | None, typer.Option("--from", help="First trading date (default: bars_from)")
    ] = None,
    to: Annotated[str | None, typer.Option(help="Last trading date (default: yesterday)")] = None,
    strict: Annotated[
        bool, typer.Option(help="Exit 1 if anything unexplained is found (the exit gate)")
    ] = False,
) -> None:
    """Data quality report: gaps in market hours and trading sessions, the daily break and
    weekends, stale quotes, spread flags, live vs history ticks, collector journal."""
    from aurum.data.market_hours import MarketHours
    from aurum.data.qa import run_qa
    from aurum.sessions.clock import SessionClock

    settings = _settings_or_exit()
    layout = DataLayout(settings.data_dir)
    symbol, _ = _stored_symbol_point(settings)
    data = settings.config.data
    start = date.fromisoformat(from_) if from_ else data.backfill.bars_from
    end = date.fromisoformat(to) if to else SystemClock().now().date() - timedelta(days=1)
    result = run_qa(
        layout,
        symbol,
        MarketHours(data.market),
        SessionClock(settings.config.sessions),
        start=start,
        end=end,
        stale_quote_seconds=settings.config.risk.stale_quote_seconds,
        known_gaps=data.qa.known_gaps,
    )
    table = Table(title=f"data QA {symbol}: trading dates {start} .. {end}", show_header=False)
    rows = [
        ("trading dates", str(result.trading_dates)),
        (
            "closed dates (explained)",
            ", ".join(f"{d} {r}" for d, r in result.closed_dates.items()) or "-",
        ),
        (
            "early closes (explained)",
            ", ".join(f"{d} {r}" for d, r in result.early_closes.items()) or "-",
        ),
        ("missing trading dates", _gap_list([str(d) for d in result.missing_days])),
        ("gaps in trading sessions", _gap_list([_fmt_gap(g) for g in result.session_gaps])),
        ("known source gaps in sessions", str(len(result.explained_session_gaps))),
        ("gaps elsewhere in market hours", _gap_list([_fmt_gap(g) for g in result.market_gaps])),
        ("bars outside market hours", _gap_list([_fmt_gap(g) for g in result.outside_hours])),
        (
            "ticks / crossed / spread outliers",
            " / ".join(str(v) for v in result.tick_flags.values()),
        ),
        ("UNEXPLAINED", f"[{'red' if result.unexplained else 'green'}]{result.unexplained}[/]"),
    ]
    for fidelity, sessions in sorted(result.stale_quotes.items()):
        for session, st in sorted(sessions.items()):
            rows.append(
                (
                    f"stale > {settings.config.risk.stale_quote_seconds:g}s "
                    f"({fidelity}, {session})",
                    f"{st.intervals} intervals, longest {st.max_seconds:.1f}s",
                )
            )
    for lv in result.live_vs_history:
        rows.append(
            (
                f"live vs history {lv['day']}",
                f"only live {lv['only_live']}, only history {lv['only_history']}",
            )
        )
    rows.append(("collector events", str(result.collector["counts"])))
    for name, value in rows:
        table.add_row(name, value)
    console.print(table)
    report = write_report(
        layout,
        "qa",
        {
            "symbol": symbol,
            "start": start,
            "end": end,
            "unexplained": result.unexplained,
            "result": result,
        },
    )
    console.print(f"report: {report}")
    if strict and result.unexplained:
        raise typer.Exit(1)


def _fmt_gap(g: "Gap") -> str:
    where = f" in {'+'.join(g.sessions)}" if g.sessions else ""
    return f"{g.start:%Y-%m-%d %H:%M}..{g.end:%H:%M}Z ({g.minutes} min{where})"


def _gap_list(items: list[str], limit: int = 12) -> str:
    if not items:
        return "none"
    more = f" ... (+{len(items) - limit} more)" if len(items) > limit else ""
    return f"{len(items)}: " + "; ".join(items[:limit]) + more


@data_app.command("resample")
def data_resample(
    from_: Annotated[str | None, typer.Option("--from", help="YYYY-MM-DD UTC")] = None,
    to: Annotated[
        str | None, typer.Option(help="YYYY-MM-DD UTC, inclusive (default: yesterday)")
    ] = None,
) -> None:
    """Normalize ticks and build canonical M1/M5/M15/H1 bars for complete UTC days, then
    compare them with the broker's bars. Deterministic: re-runs give identical files."""
    from aurum.data.pipeline import available_days, build_range
    from aurum.data.summary import summarize_build

    settings = _settings_or_exit()
    configure_logging()
    layout = DataLayout(settings.data_dir)
    symbol, point = _stored_symbol_point(settings)
    last = SystemClock().now().date() - timedelta(days=1)
    lo = date.fromisoformat(from_) if from_ else date.min
    hi = min(date.fromisoformat(to), last) if to else last
    days = [d for d in available_days(layout, symbol) if lo <= d <= hi]
    results = build_range(
        layout,
        symbol,
        days,
        point=point,
        outlier_factor=settings.config.data.qa.spread_outlier_factor,
    )
    summary = summarize_build(results)
    table = Table(title=f"canonical bars vs broker bars ({len(days)} days)")
    for col in (
        "source",
        "tf",
        "matched",
        "only ours",
        "only broker",
        "OHLC differ",
        "max diff pts",
    ):
        table.add_column(col)
    for row in summary["comparisons"]:
        table.add_row(
            row["source"],
            row["timeframe"],
            str(row["matched"]),
            str(row["only_canonical"]),
            str(row["only_broker"]),
            str(row["ohlc_mismatch"]),
            str(max(row["max_diff_points"].values())),
        )
    console.print(table)
    for name, info in summary["tick_fidelity"].items():
        console.print(
            f"tick fidelity {name}: {info['days']} days ({info['first']} .. {info['last']})"
        )
    report = write_report(layout, "resample", {"symbol": symbol, **summary})
    console.print(f"report: {report}")


def _stored_symbol_point(settings: Settings) -> tuple[str, float]:
    """The broker symbol and its point size from the newest spec snapshot (never hard-coded)."""
    specs = settings.data_dir / "specs"
    snapshots = sorted(specs.glob("*/*.json"), key=lambda p: p.name) if specs.is_dir() else []
    if not snapshots:
        console.print("[red]no spec snapshot yet: run `aurum mt5 symbols` or the collector[/red]")
        raise typer.Exit(1)
    spec = json.loads(snapshots[-1].read_text(encoding="utf-8"))["spec"]
    return str(spec["name"]), float(spec["point"])


@data_app.command("spreads")
def data_spreads(
    fidelity: Annotated[
        str, typer.Option(help="Tick days to use: exact, incomplete or all (ADR-0007)")
    ] = "exact",
) -> None:
    """Spread statistics by session and at M5 decision instants; proposes
    risk.max_spread_points (does not change risk.yaml)."""
    from aurum.adapters.storage.parquet import read_parquet
    from aurum.data.schemas import DAY_SOURCES
    from aurum.data.spreads import CANDIDATE_CAPS, propose_max_spread, spread_stats
    from aurum.sessions.clock import SessionClock

    settings = _settings_or_exit()
    layout = DataLayout(settings.data_dir)
    symbol, _ = _stored_symbol_point(settings)
    manifest = read_parquet(layout.day_manifest(symbol), DAY_SOURCES).drop_nulls("tick_fidelity")
    if fidelity != "all":
        manifest = manifest.filter(pl.col("tick_fidelity") == fidelity)
    days = manifest["day"].to_list()
    files = [layout.normalized_ticks(symbol, d) for d in days]
    stats = spread_stats(
        files,
        SessionClock(settings.config.sessions),
        days,
        spike_factor=settings.config.data.qa.spread_spike_factor,
    )
    if not days:
        console.print(f"no tick days with fidelity {fidelity}")
        raise typer.Exit(1)
    table = Table(
        title=f"spread (points), {fidelity} tick days {stats['first_day']}..{stats['last_day']}"
    )
    for col in ("measure", "session", "n", "median", "p90", "p95", "p99", "p99.9", "max"):
        table.add_column(col)
    for measure, rows in (
        ("per quote", stats["quote_weighted"]["by_session"]),
        ("at M5 close", stats["at_decision_m5_close"]),
    ):
        for r in rows:
            table.add_row(
                measure,
                r["session"],
                str(r["n"]),
                *(str(r[k]) for k in ("p50", "p90", "p95", "p99", "p99.9")),
                str(r["max"]),
            )
    console.print(table)
    blocked = stats["decisions_blocked_by_cap"]
    console.print(
        "share of M5 decision instants blocked by a cap: "
        + ", ".join(f"{c}: {blocked[f'cap_{c}'] * 100:.2f}%" for c in CANDIDATE_CAPS)
    )
    proposal = propose_max_spread(stats)
    if proposal is not None:
        console.print(
            f"[bold]proposed risk.max_spread_points = {proposal.max_spread_points}[/] "
            f"({proposal.basis}). Not written to risk.yaml; DEMO stays disabled."
        )
    report = write_report(
        layout, "spreads", {"symbol": symbol, "fidelity": fidelity, "proposal": proposal, **stats}
    )
    console.print(f"report: {report}")


@data_app.command("hashes")
def data_hashes(
    compare_to: Annotated[
        Path | None,
        typer.Option("--compare", help="An earlier manifest (JSON) or another data dir to compare"),
    ] = None,
) -> None:
    """Write a SHA-256 manifest of raw history, normalized and canonical data. With --compare,
    exit 1 unless every file is identical (the Phase 2 exit gate)."""
    from aurum.data.hashes import COVERED, compare, load_manifest, manifest

    settings = _settings_or_exit()
    layout = DataLayout(settings.data_dir)
    current = manifest(layout)
    path = write_report(layout, "hashes", current)
    console.print(f"{len(current['files'])} files hashed; manifest: {path}")
    if compare_to is None:
        return
    other = manifest(DataLayout(compare_to)) if compare_to.is_dir() else load_manifest(compare_to)
    table = Table(title=f"identical file hashes vs {compare_to}")
    for col in ("dataset", "identical", "differ", "only here", "only there"):
        table.add_column(col)
    failed = False
    for prefix in COVERED:
        d = compare(current, other, prefix)
        failed |= not d.ok
        table.add_row(
            prefix,
            str(d.identical),
            str(len(d.differ)),
            str(len(d.only_left)),
            str(len(d.only_right)),
        )
        for name in (d.differ + d.only_left + d.only_right)[:10]:
            console.print(f"  [yellow]{name}[/yellow]")
    console.print(table)
    if failed:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------- research
def _runs(settings: Settings) -> "RunLayout":
    from aurum.adapters.storage.layout import RunLayout

    return RunLayout(settings.runs_dir)


_SUMMARY_ROWS = (
    ("trades", "trades"),
    ("long / short", ("long", "short")),
    ("win rate", "win_rate"),
    ("net P&L", "net_pnl"),
    ("gross P&L (mid to mid)", "gross_pnl"),
    ("spread cost", "spread_cost"),
    ("slippage cost", "slippage_cost"),
    ("commission", "commission"),
    ("expectancy / trade", "expectancy"),
    ("profit factor", "profit_factor"),
    ("max drawdown", ("max_drawdown", "max_drawdown_pct")),
    ("avg holding (min)", "avg_holding_minutes"),
    ("trades on ticks / bars", ("trades_tick_mode", "trades_bar_mode")),
    ("worst-case intra-bar trades", ("worst_case_trades", "worst_case_share")),
    ("  stop+target same bar", "intrabar_sl_tp_same_bar"),
    ("  entry+exit same bar", "intrabar_entry_bar"),
    ("  close+level same bar", "intrabar_close_bar"),
    ("  cancel+entry same bar", "intrabar_cancel_bar"),
    (
        "exits stop/target/inval/timeout/flat/eod",
        (
            "exit_stop",
            "exit_target",
            "exit_invalidation",
            "exit_timeout",
            "exit_force_flat",
            "exit_end_of_data",
        ),
    ),
    ("tick days / bar days", ("tick_days", "bar_days")),
    ("open at end", "open_at_end"),
)


def _print_summary(title: str, summary: dict[str, object]) -> None:
    table = Table(title=title, show_header=False)
    for label, keys in _SUMMARY_ROWS:
        names = (keys,) if isinstance(keys, str) else keys
        table.add_row(label, " / ".join(str(summary.get(k)) for k in names))
    console.print(table)


@backtest_app.command("run")
def backtest_run(
    strategy: str,
    from_: Annotated[str, typer.Option("--from", help="First trading date YYYY-MM-DD")],
    to: Annotated[str, typer.Option("--to", help="Last trading date YYYY-MM-DD (inclusive)")],
    variant: Annotated[str, typer.Option(help="m15 or m5 (decision timeframe)")] = "m15",
    latency_ms: Annotated[
        int | None, typer.Option(help="Override backtest.latency_ms (e.g. 0 for tester parity)")
    ] = None,
    slippage_points: Annotated[
        float | None, typer.Option(help="Override broker.slippage.points_per_side")
    ] = None,
    risk: Annotated[
        str, typer.Option(help="policy (risk.yaml) or fixed (parity check only)")
    ] = "policy",
    notes: Annotated[str, typer.Option(help="Free text stored with the run and the trial")] = "",
    open_sealed: Annotated[
        bool,
        typer.Option(
            "--open-sealed",
            help="Open the sealed final-test period: once per strategy and variant, journalled",
        ),
    ] = False,
    by: Annotated[str, typer.Option("--by", help="Who opens the sealed period")] = "",
    reason: Annotated[str, typer.Option("--reason", help="Why the sealed period is opened")] = "",
    parity: Annotated[
        bool,
        typer.Option(
            "--parity",
            help="MT5 tester parity check: only the designated reference strategy, inside its "
            "approved window, with --risk fixed --latency-ms 0 --slippage-points 0 (journalled)",
        ),
    ] = False,
) -> None:
    """Run one backtest; writes reports/runs/<run_id>/ and records a trial in the ledger.

    No backtest reads the sealed final-test period (configs/backtest.yaml) except a research
    strategy's one-time --open-sealed --by --reason, or --parity for the designated reference
    strategy inside its approved window; both are journalled first."""
    from aurum.backtest.pipeline import RunRequest, execute

    settings = _settings_or_exit()
    request = RunRequest(
        strategy=strategy,
        variant=variant,
        first=date.fromisoformat(from_),
        last=date.fromisoformat(to),
        latency_ms=latency_ms,
        slippage_points=slippage_points,
        risk="fixed" if risk == "fixed" else "policy",
        notes=notes,
        open_sealed=open_sealed,
        opened_by=by,
        open_reason=reason,
        parity=parity,
    )
    if risk not in ("policy", "fixed"):
        console.print(f"[red]--risk must be policy or fixed, not {risk!r}[/red]")
        raise typer.Exit(1)
    try:
        outcome = execute(settings, request, runs=_runs(settings), clock=SystemClock())
    except (AurumError, FileNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    _print_summary(f"backtest {outcome.run_id}", outcome.summary)
    console.print(f"run: {outcome.run_dir}")
    console.print(f"trial: {outcome.trial_id}")
    console.print(f"trades sha256: {outcome.manifest.get('outputs', {}).get('trades', '?')}")


@backtest_app.command("grid")
def backtest_grid(
    strategy: str,
    from_: Annotated[str, typer.Option("--from", help="First trading date YYYY-MM-DD")],
    to: Annotated[str, typer.Option("--to", help="Last trading date YYYY-MM-DD (inclusive)")],
    variant: Annotated[str, typer.Option(help="m15 or m5 (decision timeframe)")] = "m15",
    retry_failed: Annotated[
        bool, typer.Option("--retry-failed", help="Retry points whose last attempt failed")
    ] = False,
) -> None:
    """Run the strategy's declared search grid: one ledger attempt per point, every outcome
    kept. Points that succeeded earlier are not rerun; failed ones only with --retry-failed.
    Exits 1 while any point is unresolved."""
    from aurum.backtest.grid import run_grid

    settings = _settings_or_exit()
    try:
        results = run_grid(
            settings,
            strategy=strategy,
            variant=variant,
            first=date.fromisoformat(from_),
            last=date.fromisoformat(to),
            retry_failed=retry_failed,
            runs=_runs(settings),
            clock=SystemClock(),
        )
    except (AurumError, FileNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    table = Table(title=f"grid {strategy} {variant} {from_}..{to}")
    for col in ("params", "status", "attempt", "trial / detail"):
        table.add_column(col)
    for r in results:
        params = " ".join(f"{k}={v}" for k, v in r.params.items())
        table.add_row(params, r.status, r.attempt_id or "", r.trial_id or r.detail)
    console.print(table)
    unresolved = [r for r in results if r.status in ("failed", "not retried")]
    console.print(f"{len(results)} points: {len(results) - len(unresolved)} resolved, "
                  f"{len(unresolved)} unresolved")  # fmt: skip
    if unresolved:
        raise typer.Exit(1)


@backtest_app.command("report")
def backtest_report(
    run_id: str,
    trades: Annotated[int, typer.Option(help="How many trades to list (last ones)")] = 20,
) -> None:
    """Summary, provenance and recent trades of a stored run; re-checks the output hashes."""
    from aurum.backtest.storage import read_manifest, read_summary, read_trades, verify_outputs

    settings = _settings_or_exit()
    runs = _runs(settings)
    try:
        manifest = read_manifest(runs, run_id)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    _print_summary(f"backtest {run_id}", read_summary(runs, run_id))
    info = Table(title="provenance", show_header=False)
    for key in (
        "strategy",
        "variant",
        "first",
        "last",
        "code_version",
        "config_hash",
        "data_hash",
        "data_files",
        "spec_snapshot",
        "trial_id",
    ):
        info.add_row(key, str(manifest.get(key)))
    info.add_row("params", json.dumps(manifest.get("params"), sort_keys=True))
    checks = verify_outputs(runs, run_id)
    info.add_row("outputs unchanged", ", ".join(f"{k}: {v}" for k, v in checks.items()))
    console.print(info)
    df = read_trades(runs, run_id).tail(trades)
    table = Table(title=f"last {df.height} trades (UTC)")
    cols = (
        "trade_no",
        "side",
        "entry_time",
        "entry_price",
        "exit_time",
        "exit_price",
        "exit_reason",
        "net_pnl",
        "entry_mode",
        "intrabar_rule",
    )
    for c in cols:
        table.add_column(c)
    for row in df.iter_rows(named=True):
        table.add_row(
            *(
                f"{row[c]:%Y-%m-%d %H:%M:%S}" if isinstance(row[c], datetime) else str(row[c])
                for c in cols
            )
        )
    console.print(table)
    if not all(checks.values()):
        raise typer.Exit(1)


@backtest_app.command("compare")
def backtest_compare(run_a: str, run_b: str) -> None:
    """Exit 0 only if two runs produced byte-identical trades, equity and journal files."""
    from aurum.backtest.storage import OUTPUTS, read_manifest

    settings = _settings_or_exit()
    runs = _runs(settings)
    a, b = read_manifest(runs, run_a), read_manifest(runs, run_b)
    table = Table(title=f"{run_a} vs {run_b}")
    for col in ("output", "identical", run_a, run_b):
        table.add_column(col)
    same = True
    for name in OUTPUTS:
        ha, hb = a["outputs"][name], b["outputs"][name]
        same &= ha == hb
        table.add_row(name, str(ha == hb), ha[:16], hb[:16])
    for key in ("code_version", "config_hash", "data_hash"):
        table.add_row(
            key, str(a.get(key) == b.get(key)), str(a.get(key))[:16], str(b.get(key))[:16]
        )
    console.print(table)
    if not same:
        raise typer.Exit(1)


@backtest_app.command("parity")
def backtest_parity(
    run_id: str,
    tester_csv: Annotated[
        Path, typer.Argument(help="Deals CSV written by mql5/AurumReference.mq5 (Common\\Files)")
    ],
    time_tolerance_ms: Annotated[int, typer.Option(help="Fill time tolerance")] = 1000,
    price_tolerance_points: Annotated[float, typer.Option(help="Price tolerance")] = 1.0,
    money_tolerance: Annotated[
        float, typer.Option(help="Per-trade profit/commission tolerance")
    ] = 0.02,
    cumulative_money_tolerance: Annotated[
        float, typer.Option(help="Tolerance on the total net P&L of the whole run")
    ] = 0.02,
) -> None:
    """Compare a run with the MT5 Strategy Tester's trades; exit 0 only if the trade counts are
    equal, every trade is within tolerance and the total net P&L is within the cumulative
    tolerance. Writes parity.csv and parity.json next to the run."""
    from aurum.adapters.mt5.servertime import ServerTimeZone
    from aurum.adapters.mt5.tester import read_tester_deals
    from aurum.backtest.parity import Tolerance, compare, from_trades_frame
    from aurum.backtest.storage import read_manifest, read_trades

    settings = _settings_or_exit()
    runs = _runs(settings)
    st = settings.config.broker.server_time
    try:
        manifest = read_manifest(runs, run_id)
        tester = read_tester_deals(tester_csv, ServerTimeZone(st.timezone, st.shift_hours))
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    report = compare(
        from_trades_frame(read_trades(runs, run_id)),
        tester,
        point=float(manifest["spec"]["point"]),
        tolerance=Tolerance(
            time=timedelta(milliseconds=time_tolerance_ms),
            price_points=price_tolerance_points,
            money=money_tolerance,
            cumulative_money=cumulative_money_tolerance,
        ),
        window=(date.fromisoformat(manifest["first"]), date.fromisoformat(manifest["last"])),
    )
    summary = {**report.summary(), "run_id": run_id, "tester_csv": str(tester_csv)}
    report.frame().write_csv(runs.parity(run_id))
    runs.parity_summary(run_id).write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    table = Table(title=f"parity {run_id} vs MT5 Strategy Tester", show_header=False)
    for key, value in summary.items():
        table.add_row(key, str(value))
    console.print(table)
    for row in report.differing:
        side = "aurum only" if row.tester is None else "tester only" if row.aurum is None else ""
        what = side or "; ".join(
            f"{d.name} {d.aurum} vs {d.tester} ({d.difference})" for d in row.diffs
        )
        console.print(f"{row.key[0]} {row.key[1]}: {what}")
    if not report.trade_count_matches:
        console.print(
            f"[red]trade count: aurum {report.aurum_trades} vs tester {report.tester_trades}[/red]"
        )
    if not report.cumulative_net_pnl_within_tolerance:
        difference = report.total_net_pnl_difference
        console.print(
            f"[red]total net P&L: aurum {report.total_net_pnl_aurum:.2f} vs tester "
            f"{report.total_net_pnl_tester:.2f}; difference {difference:+.2f}, "
            f"absolute difference {abs(difference):.2f} > "
            f"{report.tolerance.cumulative_money:.2f}[/red]"
        )
    console.print(f"details: {runs.parity(run_id)}")
    if not report.ok:
        raise typer.Exit(1)


@validate_app.command("walkforward")
def validate_walkforward(strategy: str) -> None:
    """Walk-forward validation."""
    _not_yet("validate walkforward", 6)


@ledger_app.command("list")
def ledger_list(
    strategy: Annotated[str | None, typer.Option(help="Filter by strategy")] = None,
) -> None:
    """List every recorded trial (all of them, not just the best)."""
    try:
        settings = load_settings()
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    from aurum.adapters.storage.layout import ledger_path

    path = ledger_path(settings.data_dir)
    if not path.exists():
        console.print(f"No ledger yet at {path}.")
        return
    with TrialLedger(path) as ledger:
        trials = ledger.list_trials(strategy=strategy)
    table = Table(title=f"trials ({len(trials)})")
    for col in ("trial_id", "created", "strategy", "variant", "data", "sealed"):
        table.add_column(col)

    def day(d: datetime) -> str:
        return d.date().isoformat()

    for t in trials:
        table.add_row(
            t.trial_id,
            t.created_at.isoformat(timespec="seconds"),
            t.strategy,
            t.variant,
            f"{day(t.data_start)}..{day(t.data_end)}",
            "OPENED" if t.sealed_test_opened else "",
        )
    console.print(table)


# ---------------------------------------------------------------------------- live loops
@shadow_app.command("run")
def shadow_run() -> None:
    """Live decisions against live quotes; never sends orders."""
    _not_yet("shadow run", 7)


@trade_app.command("demo")
def trade_demo() -> None:
    """Demo trading loop."""
    _not_yet("trade demo", 8)


def _flatten_now(settings: Settings) -> bool:
    """DEMO/LIVE: connect and close Aurum's positions and orders now. True if all succeeded."""
    from aurum.core.enums import ExecutionStatus, Mode
    from aurum.execution.orders import flatten_owned

    if settings.mode not in (Mode.DEMO, Mode.LIVE):
        console.print(
            f"AURUM_MODE={settings.mode}: this command sends nothing to the broker. "
            "A running DEMO trader flattens on its next step."
        )
        return True
    broker, _ = _connected_broker()
    reports = flatten_owned(broker, settings.config.broker.magic_number)
    ok = all(r.status in (ExecutionStatus.CLOSED, ExecutionStatus.CANCELLED) for r in reports)
    for r in reports:
        console.print(f"{r.status} key={r.idempotency_key} {r.message}")
    console.print(
        f"{len(reports)} Aurum orders/positions flattened"
        if ok
        else "[red]flatten incomplete[/red]"
    )
    return ok


@app.command()
def kill(
    reason: Annotated[str, typer.Option(help="Why (written into the KILL file)")] = "manual",
) -> None:
    """Lock trading (KILL file) and, in DEMO mode, close Aurum's positions and orders now."""
    from aurum.execution.killswitch import KillSwitch

    settings = _settings_or_exit()
    switch = KillSwitch(DataLayout(settings.data_dir).kill_switch())
    switch.engage(reason, SystemClock().now())
    console.print(f"[bold red]kill switch engaged[/bold red]: {switch.path} (clear: aurum unkill)")
    if not _flatten_now(settings):
        raise typer.Exit(1)


@app.command()
def unkill() -> None:
    """Clear the kill-switch lock (delete the KILL file)."""
    from aurum.execution.killswitch import KillSwitch

    settings = _settings_or_exit()
    switch = KillSwitch(DataLayout(settings.data_dir).kill_switch())
    reason = switch.reason()
    if switch.clear():
        console.print(f"kill switch cleared (was: {reason})")
    else:
        console.print("kill switch was not engaged")


@app.command()
def watchdog(
    process: Annotated[str, typer.Option(help="Heartbeat to check")] = "trader",
    max_age_seconds: Annotated[int, typer.Option(help="Oldest acceptable heartbeat")] = 120,
) -> None:
    """Check the trader's heartbeat; if it is stale or missing while the trader should run,
    engage the kill switch (and flatten now in DEMO mode). Exit 1 on alarm."""
    from aurum.data.market_hours import MarketHours
    from aurum.execution.killswitch import KillSwitch
    from aurum.monitoring.watchdog import check_heartbeat
    from aurum.sessions.calendar import TradingCalendar

    settings = _settings_or_exit()
    layout = DataLayout(settings.data_dir)
    cfg = settings.config
    now = SystemClock().now()
    result = check_heartbeat(
        layout.heartbeat(process),
        now,
        max_age=timedelta(seconds=max_age_seconds),
        calendar=TradingCalendar(cfg.sessions, MarketHours(cfg.data.market)),
    )
    console.print(f"{result.verdict}: {result.detail}")
    if not result.alarm:
        return
    switch = KillSwitch(layout.kill_switch())
    switch.engage(f"watchdog: {process} heartbeat {result.verdict}", now)
    console.print(f"[bold red]kill switch engaged[/bold red]: {switch.path}")
    _flatten_now(settings)
    raise typer.Exit(1)


if __name__ == "__main__":
    app()
