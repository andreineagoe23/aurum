"""Where every dataset lives under ``AURUM_DATA_DIR`` (see docs/DATA_DICTIONARY.md).

Partitions are by symbol and **UTC** date (``YYYY-MM-DD``), one Parquet file per partition,
except live tick chunks, which are many small immutable files per UTC date.

    raw_ticks/<source>/<symbol>/<date>.parquet          source = mt5_history (complete days)
    raw_ticks/mt5_live/<symbol>/<date>/<first>_<last>_<rows>.parquet   (collector chunks)
    raw_broker_bars/mt5/<symbol>/<tf>/<date>.parquet
    normalized_ticks/<symbol>/<date>.parquet
    canonical_bars/<tf>/<symbol>/<date>.parquet
    canonical_bars/days/<symbol>.parquet                one row per day: source, tick fidelity
    features/                                           (reserved for Phase 5)
    quality_reports/<name>/<...>.json
    collector/<symbol>/heartbeat.json, events.jsonl
    specs/<symbol>/<UTC stamp>.json
    ledger/trials.sqlite                                trial ledger (every backtest)

Backtest runs live outside the data dir, under the project's ``reports/runs/<run_id>/``
(``RunLayout``): manifest.json, summary.json, trades.parquet, equity.parquet, journal.parquet.
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from aurum.core.timeframes import Timeframe

MT5_HISTORY = "mt5_history"
MT5_LIVE = "mt5_live"
MT5_BARS = "mt5"
DATASETS = (
    "raw_ticks",
    "raw_broker_bars",
    "normalized_ticks",
    "canonical_bars",
    "features",
    "quality_reports",
)


def day_name(d: date) -> str:
    return d.isoformat()


def parse_day(name: str) -> date:
    return date.fromisoformat(name.removesuffix(".parquet"))


@dataclass(frozen=True)
class DataLayout:
    root: Path

    def ensure(self) -> None:
        for name in DATASETS:
            (self.root / name).mkdir(parents=True, exist_ok=True)

    # raw ------------------------------------------------------------------------------
    def raw_ticks_dir(self, symbol: str, source: str = MT5_HISTORY) -> Path:
        return self.root / "raw_ticks" / source / symbol

    def raw_ticks(self, symbol: str, day: date, source: str = MT5_HISTORY) -> Path:
        return self.raw_ticks_dir(symbol, source) / f"{day_name(day)}.parquet"

    def live_ticks_dir(self, symbol: str, day: date | None = None) -> Path:
        base = self.raw_ticks_dir(symbol, MT5_LIVE)
        return base if day is None else base / day_name(day)

    def raw_bars_dir(self, symbol: str, timeframe: Timeframe) -> Path:
        return self.root / "raw_broker_bars" / MT5_BARS / symbol / timeframe.value

    def raw_bars(self, symbol: str, timeframe: Timeframe, day: date) -> Path:
        return self.raw_bars_dir(symbol, timeframe) / f"{day_name(day)}.parquet"

    # derived --------------------------------------------------------------------------
    def normalized_ticks_dir(self, symbol: str) -> Path:
        return self.root / "normalized_ticks" / symbol

    def normalized_ticks(self, symbol: str, day: date) -> Path:
        return self.normalized_ticks_dir(symbol) / f"{day_name(day)}.parquet"

    def canonical_bars_dir(self, symbol: str, timeframe: Timeframe) -> Path:
        return self.root / "canonical_bars" / timeframe.value / symbol

    def canonical_bars(self, symbol: str, timeframe: Timeframe, day: date) -> Path:
        return self.canonical_bars_dir(symbol, timeframe) / f"{day_name(day)}.parquet"

    def day_manifest(self, symbol: str) -> Path:
        return self.root / "canonical_bars" / "days" / f"{symbol}.parquet"

    def quality_reports(self, name: str) -> Path:
        return self.root / "quality_reports" / name

    # collector state ------------------------------------------------------------------
    def collector_dir(self, symbol: str) -> Path:
        return self.root / "collector" / symbol

    def specs_dir(self, symbol: str) -> Path:
        return self.root / "specs" / symbol


def days_in(directory: Path) -> list[date]:
    """UTC dates of the ``<date>.parquet`` files (or ``<date>/`` dirs) in ``directory``, sorted."""
    if not directory.is_dir():
        return []
    out = []
    for p in directory.iterdir():
        try:
            out.append(parse_day(p.name))
        except ValueError:
            continue
    return sorted(out)


@dataclass(frozen=True)
class RunLayout:
    """Files of backtest runs under ``root`` (default: <project>/reports/runs)."""

    root: Path

    def run_dir(self, run_id: str) -> Path:
        return self.root / run_id

    def manifest(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "manifest.json"

    def summary(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "summary.json"

    def trades(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "trades.parquet"

    def equity(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "equity.parquet"

    def journal(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "journal.parquet"

    def runs(self) -> list[str]:
        """Run ids with a manifest, oldest first (ids start with a UTC stamp)."""
        if not self.root.is_dir():
            return []
        return sorted(p.parent.name for p in self.root.glob("*/manifest.json"))


def ledger_path(data_dir: Path) -> Path:
    return data_dir / "ledger" / "trials.sqlite"
