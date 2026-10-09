"""``aurum`` command line. Commands for later phases exist as placeholders that exit with code 2."""

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Annotated, NoReturn
from zoneinfo import ZoneInfo

import typer
from rich.console import Console
from rich.table import Table

from aurum.core.clock import SystemClock
from aurum.core.errors import AurumError, ConfigError
from aurum.core.timeframes import Timeframe
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
def _connected_broker() -> tuple["MT5Broker", Settings]:
    """Load settings, configure JSON logs (stderr) and connect, or exit 1 with the reason."""
    from aurum.adapters.mt5 import MT5Broker

    try:
        settings = load_settings()
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    configure_logging()
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
    tf: Annotated[str, typer.Option(help="Comma-separated timeframes")] = "M1,M5,M15,H1",
    ticks: Annotated[bool, typer.Option(help="Also backfill ticks")] = False,
    from_: Annotated[str | None, typer.Option("--from", help="YYYY-MM-DD (UTC)")] = None,
) -> None:
    """Backfill bars/ticks in bounded, idempotent chunks."""
    _not_yet("data backfill", 2)


@data_app.command("collect")
def data_collect() -> None:
    """Append live ticks while MT5 is connected."""
    _not_yet("data collect", 2)


@data_app.command("qa")
def data_qa() -> None:
    """Data quality report."""
    _not_yet("data qa", 2)


@data_app.command("resample")
def data_resample() -> None:
    """Build canonical bars from ticks/M1."""
    _not_yet("data resample", 2)


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
