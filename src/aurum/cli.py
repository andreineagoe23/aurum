"""``aurum`` command line. Commands for later phases exist as placeholders that exit with code 2."""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, NoReturn
from zoneinfo import ZoneInfo

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
def data_qa() -> None:
    """Data quality report."""
    _not_yet("data qa", 2)


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


# ---------------------------------------------------------------------------- research
@backtest_app.command("run")
def backtest_run(
    strategy: str,
    variant: Annotated[str, typer.Option(help="m15 or m5")] = "m15",
    from_: Annotated[str | None, typer.Option("--from")] = None,
    to: Annotated[str | None, typer.Option("--to")] = None,
) -> None:
    """Run one backtest (recorded in the trial ledger)."""
    _not_yet("backtest run", 3)


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
    path = settings.data_dir / "ledger" / "trials.sqlite"
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


@app.command()
def kill() -> None:
    """Cancel pending orders, close positions and lock trading."""
    _not_yet("kill", 4)


@app.command()
def unkill() -> None:
    """Clear the kill-switch lock."""
    _not_yet("unkill", 4)


if __name__ == "__main__":
    app()
