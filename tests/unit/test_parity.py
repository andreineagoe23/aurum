"""Parity with the MT5 Strategy Tester: comparison, the EA's deals CSV, and the CLI."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aurum.adapters.mt5.servertime import ServerTimeZone
from aurum.adapters.mt5.tester import COLUMNS, read_tester_deals
from aurum.adapters.storage.layout import RunLayout
from aurum.backtest.parity import ParityTrade, Tolerance, compare, from_trades_frame
from aurum.backtest.storage import read_trades
from aurum.cli import app
from aurum.core.enums import ExitReason, Side
from tests.sim_store import write_store

TZ = ServerTimeZone("America/New_York", 7)
runner = CliRunner()


def trade(**kw: object) -> ParityTrade:
    base = ParityTrade(
        side=Side.BUY,
        volume=0.10,
        entry_time=datetime(2026, 9, 15, 8, 0, 0, 120_000, tzinfo=UTC),
        entry_price=2000.10,
        exit_time=datetime(2026, 9, 15, 9, 12, 3, 450_000, tzinfo=UTC),
        exit_price=1990.00,
        exit_reason=ExitReason.STOP,
        stop_loss=1990.00,
        take_profit=2015.00,
        profit=-101.00,
        commission=0.46,
    )
    return replace(base, **kw)  # type: ignore[arg-type]


def test_identical_trades_pass() -> None:
    report = compare([trade()], [trade()], point=0.01)
    assert report.ok
    assert report.summary()["within_tolerance"] == 1


def test_differences_beyond_tolerance_are_listed() -> None:
    later = trade(
        exit_time=trade().exit_time + timedelta(seconds=2),
        exit_price=1989.98,  # 2 points
        exit_reason=ExitReason.TIMEOUT,
        commission=0.50,
    )
    report = compare([trade()], [later], point=0.01)
    assert not report.ok
    (row,) = report.differing
    assert {d.name for d in row.diffs} == {"exit_time", "exit_price", "exit_reason", "commission"}


def test_within_tolerance_is_ok() -> None:
    near = trade(
        entry_time=trade().entry_time + timedelta(milliseconds=900),
        exit_price=1990.01,  # 1 point
        profit=-101.02,
    )
    assert compare([trade()], [near], point=0.01).ok
    strict = Tolerance(time=timedelta(milliseconds=100), price_points=0.0, money=0.0)
    assert not compare([trade()], [near], point=0.01, tolerance=strict).ok


def test_unpaired_trades_on_either_side() -> None:
    sell = trade(side=Side.SELL, entry_time=trade().entry_time + timedelta(hours=6))
    next_day = trade(entry_time=trade().entry_time + timedelta(days=1))
    report = compare([trade(), sell], [trade(), next_day], point=0.01)
    assert [r.key for r in report.only_aurum] == [(date(2026, 9, 15), Side.SELL)]
    assert [r.key for r in report.only_tester] == [(date(2026, 9, 16), Side.BUY)]
    assert report.frame()["status"].to_list() == ["ok", "only_aurum", "only_tester"]


def test_tester_trades_outside_the_run_window_are_ignored() -> None:
    next_day = trade(entry_time=trade().entry_time + timedelta(days=1))
    day = trade().entry_time.date()
    report = compare([trade()], [trade(), next_day], point=0.01, window=(day, day))
    assert report.ok
    assert report.summary()["tester_outside_window"] == 1


def test_two_trades_on_one_key_is_an_error() -> None:
    with pytest.raises(ValueError, match="two BUY trades"):
        compare([trade(), trade()], [], point=0.01)


# ------------------------------------------------------------------------------ EA CSV


def server_ms(t: datetime) -> int:
    return round(TZ.to_server_seconds(t) * 1000)


def csv_row(
    deal: int,
    pos: int,
    t: datetime,
    side: str,
    entry: str,
    price: float,
    *,
    reason: str = "EXPERT",
    ea_reason: str = "",
    sl: float = 0.0,
    tp: float = 0.0,
    profit: float = 0.0,
) -> list[str]:
    return [
        str(deal),
        str(pos),
        str(server_ms(t)),
        side,
        entry,
        reason,
        ea_reason,
        "0.10",
        f"{price:.2f}",
        f"{sl:.2f}",
        f"{tp:.2f}",
        "-0.23",
        "0.00",
        f"{profit:.2f}",
        "XAUUSD",
    ]


def write_csv(path: Path, rows: list[list[str]]) -> Path:
    path.write_text("\n".join(",".join(r) for r in [list(COLUMNS), *rows]) + "\n", "ascii")
    return path


def test_reader_converts_server_time_and_maps_reasons(tmp_path: Path) -> None:
    # Summer (server = UTC+3) and winter (server = UTC+2) positions.
    summer = datetime(2026, 9, 15, 8, 0, 0, 120_000, tzinfo=UTC)
    winter = datetime(2026, 12, 15, 14, 30, 0, 5_000, tzinfo=UTC)
    rows = [
        csv_row(1, 10, summer, "BUY", "IN", 2000.10, sl=1990.0, tp=2015.0),
        csv_row(2, 10, summer + timedelta(hours=2), "SELL", "OUT", 2001.0, ea_reason="TIMEOUT"),
        csv_row(3, 11, winter, "SELL", "IN", 2100.00, sl=2130.0, tp=2055.0),
        csv_row(4, 11, winter + timedelta(hours=1), "BUY", "OUT", 2130.0, reason="SL", sl=2130.0),
    ]
    assert TZ.offset_at(summer) == timedelta(hours=3)
    assert TZ.offset_at(winter) == timedelta(hours=2)
    a, b = read_tester_deals(write_csv(tmp_path / "deals.csv", rows), TZ)
    assert a.entry_time == summer
    assert a.exit_time == summer + timedelta(hours=2)
    assert (a.exit_reason, a.stop_loss, a.take_profit) == (ExitReason.TIMEOUT, 1990.0, 2015.0)
    assert a.commission == 0.46
    assert b.entry_time == winter
    assert b.exit_reason is ExitReason.STOP


def test_reader_rejects_open_positions_and_bad_headers(tmp_path: Path) -> None:
    t = datetime(2026, 9, 15, 8, tzinfo=UTC)
    open_only = write_csv(tmp_path / "a.csv", [csv_row(1, 10, t, "BUY", "IN", 2000.0)])
    with pytest.raises(ValueError, match="never closed"):
        read_tester_deals(open_only, TZ)
    bad = tmp_path / "b.csv"
    bad.write_text("deal,position\n1,2\n", "ascii")
    with pytest.raises(ValueError, match="unexpected columns"):
        read_tester_deals(bad, TZ)


# ------------------------------------------------------------------------------ CLI


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    data, runs = tmp_path / "data", tmp_path / "runs"
    write_store(data, tick_days=[date(2026, 9, 15)], bar_days=[date(2026, 9, 14)])
    monkeypatch.setenv("AURUM_DATA_DIR", str(data))
    monkeypatch.setenv("AURUM_RUNS_DIR", str(runs))
    return data, runs


def csv_from_run(path: Path, trades: list[ParityTrade], nudge_points: int = 0) -> Path:
    rows = []
    for n, t in enumerate(trades):
        out_side = "SELL" if t.side is Side.BUY else "BUY"
        reason = {ExitReason.STOP: "SL", ExitReason.TARGET: "TP"}.get(t.exit_reason, "EXPERT")
        ea = "" if reason != "EXPERT" else str(t.exit_reason)
        rows.append(
            csv_row(
                2 * n,
                n,
                t.entry_time,
                str(t.side),
                "IN",
                t.entry_price,
                sl=t.stop_loss or 0.0,
                tp=t.take_profit or 0.0,
            )
        )
        exit_price = t.exit_price + (nudge_points * 0.01 if n == 0 else 0.0)
        rows.append(
            csv_row(
                2 * n + 1,
                n,
                t.exit_time,
                out_side,
                "OUT",
                exit_price,
                reason=reason,
                ea_reason=ea,
                profit=t.profit,
            )
        )
    return write_csv(path, rows)


def test_parity_cli_end_to_end(env: tuple[Path, Path], tmp_path: Path) -> None:
    _, runs = env
    args = ["backtest", "run", "reference_fixed_time", "--from", "2026-09-14", "--to", "2026-09-15"]
    ran = runner.invoke(app, [*args, "--variant", "m5", "--latency-ms", "0", "--risk", "fixed"])
    assert ran.exit_code == 0, ran.output
    (run_id,) = RunLayout(runs).runs()
    trades = from_trades_frame(read_trades(RunLayout(runs), run_id))
    assert len(trades) == 4

    same = csv_from_run(tmp_path / "same.csv", trades)
    ok = runner.invoke(app, ["backtest", "parity", run_id, str(same)])
    assert ok.exit_code == 0, ok.output
    assert RunLayout(runs).parity(run_id).is_file()
    assert RunLayout(runs).parity_summary(run_id).is_file()

    off = csv_from_run(tmp_path / "off.csv", trades, nudge_points=5)
    bad = runner.invoke(app, ["backtest", "parity", run_id, str(off)])
    assert bad.exit_code == 1
    assert "exit_price" in bad.output
