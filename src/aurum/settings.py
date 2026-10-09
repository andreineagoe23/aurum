"""Configuration: environment (.env) + YAML files in ``configs/``. Invalid config fails loudly.

* Every YAML model forbids unknown keys, so typos are errors, not silently ignored.
* Clock times in YAML must be quoted ``"HH:MM"`` strings (unquoted ``07:00`` is a YAML 1.1
  base-60 integer and would otherwise be misread).
* Safety-relevant caps (e.g. risk per trade <= 1%) are validated here as a second line of defence.
"""

import os
import re
from dataclasses import dataclass
from datetime import date, time
from itertools import pairwise
from pathlib import Path
from typing import Annotated, Any, Final, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from aurum.core.enums import Mode
from aurum.core.errors import ConfigError
from aurum.core.timeutil import UtcDatetime

WEEKDAYS: dict[str, int] = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _parse_hhmm(value: object) -> time:
    if isinstance(value, time):
        return value
    if not isinstance(value, str) or not _HHMM.match(value):
        raise ValueError(f'expected a quoted "HH:MM" string, got {value!r}')
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


ClockTime = Annotated[time, BeforeValidator(_parse_hhmm)]
Weekday = Literal["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ------------------------------------------------------------------------------ broker.yaml


CurrencyCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
AUTO: Final = "auto"


class CommissionConfig(_Config):
    per_lot_per_side: float = Field(ge=0)
    """Commission per 1.0 lot per side, in ``currency`` (Fusion Zero: USD 2.25). The broker books
    it in the account currency; converting is the cost model's job (Phase 3)."""
    currency: CurrencyCode = "USD"

    @property
    def per_lot_round_trip(self) -> float:
        return 2 * self.per_lot_per_side


class SlippageConfig(_Config):
    model: Literal["fixed_points"] = "fixed_points"
    points_per_side: float = Field(ge=0)


class ServerTimeConfig(_Config):
    """How the MT5 server clock relates to real time (ADR-0004).

    Server wall clock = wall clock of ``timezone`` + ``shift_hours``. Fusion Markets: New York + 7
    (UTC+2 in US winter time, UTC+3 in US summer time; server midnight = 17:00 New York).
    """

    timezone: str = "America/New_York"
    shift_hours: int = Field(default=7, ge=-12, le=14)

    @field_validator("timezone")
    @classmethod
    def _tz_exists(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value


class BrokerConfig(_Config):
    name: str
    account_type: str
    account_currency: CurrencyCode | Literal["auto"] = AUTO
    """``auto``: use whatever the connected account reports. A code (e.g. ``GBP``) is an
    expectation: connecting to an account in another currency is an error."""
    symbol_candidates: list[str] = Field(min_length=1)
    symbol_search_terms: list[str] = Field(min_length=1)
    commission: CommissionConfig
    slippage: SlippageConfig
    server_time: ServerTimeConfig = ServerTimeConfig()
    magic_number: int = Field(default=0, ge=0, le=2**63 - 1)
    """MT5 ``magic`` stamped on every Aurum order, so reconciliation can tell ours apart."""
    max_deviation_points: int = Field(default=50, ge=0)
    """Largest price deviation (points) accepted on a market order fill (MT5 ``deviation``)."""


# ------------------------------------------------------------------------------ sessions.yaml


class SessionWindow(_Config):
    start: ClockTime
    end: ClockTime

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.start >= self.end:
            raise ValueError(f"session start {self.start} must be before end {self.end}")
        return self


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


class SessionsConfig(_Config):
    """Session windows in ``timezone`` wall-clock time. Windows may not cross midnight."""

    timezone: str
    sessions: dict[str, SessionWindow] = Field(min_length=1)
    pre_session_minutes: int = Field(default=30, ge=0, le=180)
    no_new_entries_after: ClockTime
    force_flat_at: ClockTime
    trading_days: list[Weekday] = Field(min_length=1)
    market_close_buffer_minutes: int = Field(default=15, ge=0, le=120)
    """Minutes before the market's close (data.yaml ``market``: the daily break or an early close)
    by which exposure must be flat. The effective cutoff is
    ``min(force_flat_at, market close - buffer)``; see ``aurum.sessions.calendar``."""

    @field_validator("timezone")
    @classmethod
    def _tz_exists(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value

    @field_validator("trading_days")
    @classmethod
    def _unique_days(cls, value: list[Weekday]) -> list[Weekday]:
        if len(set(value)) != len(value):
            raise ValueError("trading_days contains duplicates")
        return value

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.no_new_entries_after >= self.force_flat_at:
            raise ValueError("no_new_entries_after must be before force_flat_at")
        ordered = sorted(self.sessions.items(), key=lambda kv: kv[1].start)
        for name, window in ordered:
            if window.start >= self.no_new_entries_after:
                raise ValueError(f"session {name} starts at/after no_new_entries_after")
            if window.end > self.force_flat_at:
                raise ValueError(f"session {name} ends after force_flat_at")
        for (a_name, a), (b_name, b) in pairwise(ordered):
            if b.start < a.end:
                raise ValueError(f"sessions {a_name} and {b_name} overlap")
        if _minutes(ordered[0][1].start) < self.pre_session_minutes:
            raise ValueError("pre-session would start before local midnight")
        return self

    @property
    def weekday_numbers(self) -> frozenset[int]:
        return frozenset(WEEKDAYS[d] for d in self.trading_days)


# ------------------------------------------------------------------------------ risk.yaml


class RiskConfig(_Config):
    """Provisional demo safety limits. Upper caps are hard sanity bounds, not targets."""

    risk_per_trade_pct: float = Field(gt=0, le=1.0)
    max_open_positions: Literal[1]
    """Frozen by the v1 mandate: one XAUUSD position at a time."""
    max_trades_per_day: int = Field(ge=1, le=10)
    daily_loss_limit_pct: float = Field(gt=0, le=5.0)
    weekly_loss_limit_pct: float = Field(gt=0, le=10.0)
    consecutive_loss_cooldown: int = Field(ge=1)
    max_spread_points: int | None = Field(default=None, gt=0)
    """None = not calibrated yet; order-sending modes refuse to start (see readiness_issues)."""
    max_spread_atr_fraction: float = Field(gt=0, le=1.0)
    min_minutes_before_cutoff: int = Field(ge=0, le=240)
    stale_quote_seconds: float = Field(gt=0, le=60)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.weekly_loss_limit_pct < self.daily_loss_limit_pct:
            raise ValueError("weekly_loss_limit_pct must be >= daily_loss_limit_pct")
        if self.daily_loss_limit_pct < self.risk_per_trade_pct:
            raise ValueError("daily_loss_limit_pct must be >= risk_per_trade_pct")
        return self


# ------------------------------------------------------------------------------ promotion.yaml


class PromotionConfig(_Config):
    live_enabled: bool = False
    signed_off_date: date | None = None
    signed_off_by: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.live_enabled and (self.signed_off_date is None or not self.signed_off_by):
            raise ValueError("live_enabled requires signed_off_date and signed_off_by")
        return self


# ------------------------------------------------------------------------------ data.yaml


def _check_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone {value!r}") from exc
    return value


class EarlyClose(_Config):
    close: ClockTime
    reason: str = Field(min_length=1)


class MarketHoursConfig(_Config):
    """When the instrument trades, in ``timezone`` wall clock (measured, see data.yaml).

    Trading date D runs from ``open`` on D-1 to ``close`` on D, Monday to Friday. A date in
    ``closed_dates`` has no trading at all; ``early_closes`` replaces ``close`` for that date.
    """

    timezone: str
    open: ClockTime
    close: ClockTime
    open_grace_minutes: int = Field(default=2, ge=0, le=30)
    closed_dates: dict[date, str] = Field(default_factory=dict)
    early_closes: dict[date, EarlyClose] = Field(default_factory=dict)

    _tz = field_validator("timezone")(_check_timezone)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.close >= self.open:
            raise ValueError("market close must be before open (the session spans midnight)")
        for d in (*self.closed_dates, *self.early_closes):
            if d.weekday() >= 5:
                raise ValueError(f"{d} is a weekend: only weekday trading dates can be listed")
        for d, early in self.early_closes.items():
            if early.close >= self.close:
                raise ValueError(f"early close {early.close} on {d} is not before the normal close")
            if d in self.closed_dates:
                raise ValueError(f"{d} is both closed and an early close")
        return self


class BackfillConfig(_Config):
    bars_from: date
    ticks_from: date
    timeframes: list[Literal["M1", "M5", "M15", "H1"]] = Field(min_length=1)
    bar_chunk_days: int = Field(default=31, ge=1, le=366)
    empty_retries: int = Field(default=3, ge=0, le=20)
    retry_seconds: float = Field(default=2.0, ge=0, le=60)


class CollectorConfig(_Config):
    poll_seconds: float = Field(gt=0, le=60)
    flush_seconds: float = Field(gt=0, le=3600)
    heartbeat_seconds: float = Field(gt=0, le=3600)
    gap_record_seconds: float = Field(gt=0)
    settle_seconds: float = Field(ge=0, le=120)
    pause_detect_seconds: float = Field(gt=0)
    reconnect_initial_seconds: float = Field(gt=0)
    reconnect_max_seconds: float = Field(gt=0, le=3600)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.reconnect_max_seconds < self.reconnect_initial_seconds:
            raise ValueError("reconnect_max_seconds must be >= reconnect_initial_seconds")
        if self.pause_detect_seconds <= self.poll_seconds:
            raise ValueError("pause_detect_seconds must exceed poll_seconds")
        return self


class KnownGap(_Config):
    """A gap inside the trading sessions that is in the broker's own history, with a reason."""

    start: UtcDatetime
    end: UtcDatetime
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.end <= self.start:
            raise ValueError("known gap must end after it starts")
        return self


class QaConfig(_Config):
    spread_outlier_factor: float = Field(gt=1)
    spread_spike_factor: float = Field(gt=1)
    known_gaps: list[KnownGap] = Field(default_factory=list)


class DataConfig(_Config):
    market: MarketHoursConfig
    backfill: BackfillConfig
    collector: CollectorConfig
    qa: QaConfig


# ------------------------------------------------------------------------------ strategies/


class StrategyConfig(_Config):
    name: str = Field(pattern=r"^[a-z0-9_]+$")
    family: Literal["trend_pullback", "opening_range_breakout", "vwap_reversion"]
    enabled: bool = False
    variants: list[Literal["m15", "m5"]] = Field(min_length=1)
    sessions: list[str] = Field(min_length=1)
    spec: str | None = None
    """Path to the filled-in spec (docs/strategies/<name>.md). Required before enabling."""
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.enabled and not self.spec:
            raise ValueError(f"strategy {self.name} cannot be enabled without a spec document")
        return self


# ------------------------------------------------------------------------------ aggregate


class AppConfig(_Config):
    broker: BrokerConfig
    sessions: SessionsConfig
    risk: RiskConfig
    promotion: PromotionConfig
    data: DataConfig
    strategies: dict[str, StrategyConfig]

    @model_validator(mode="after")
    def _cross_check(self) -> Self:
        for key, strat in self.strategies.items():
            if key != strat.name:
                raise ValueError(f"strategies/{key}.yaml declares name {strat.name!r}")
            unknown = set(strat.sessions) - set(self.sessions.sessions)
            if unknown:
                raise ValueError(f"strategy {strat.name} references unknown sessions {unknown}")
        return self


def _read_yaml(path: Path) -> Any:
    if not path.is_file():
        raise ConfigError(f"missing config file: {path}")
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc


def load_config(config_dir: Path) -> AppConfig:
    """Load and validate every YAML in ``config_dir``. Raises ``ConfigError`` on any problem."""
    raw: dict[str, Any] = {
        name: _read_yaml(config_dir / f"{name}.yaml")
        for name in ("broker", "sessions", "risk", "promotion", "data")
    }
    strategies_dir = config_dir / "strategies"
    raw["strategies"] = (
        {p.stem: _read_yaml(p) for p in sorted(strategies_dir.glob("*.yaml"))}
        if strategies_dir.is_dir()
        else {}
    )
    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration in {config_dir}:\n{exc}") from exc


# ------------------------------------------------------------------------------ environment


class EnvSettings(BaseSettings):
    """Values from the process environment and ``.env``. Secrets are ``SecretStr``."""

    model_config = SettingsConfigDict(extra="ignore", env_file_encoding="utf-8")

    aurum_mode: Mode = Mode.BACKTEST
    aurum_data_dir: Path | None = None
    aurum_config_dir: Path | None = None
    aurum_live_ack: str | None = None
    mt5_login: int | None = None
    mt5_password: SecretStr | None = None
    mt5_server: str | None = None
    mt5_path: Path | None = None

    @field_validator("aurum_mode", mode="before")
    @classmethod
    def _upper(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("mt5_login", "mt5_server", "mt5_path", "aurum_live_ack", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("mt5_password", mode="before")
    @classmethod
    def _blank_password_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value else value

    @property
    def has_mt5_credentials(self) -> bool:
        return bool(self.mt5_login and self.mt5_password and self.mt5_server)


def find_project_root(start: Path | None = None) -> Path:
    """Nearest ancestor of ``start`` (default: cwd) containing pyproject.toml and configs/."""
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "configs").is_dir():
            return candidate
    return here


def default_data_dir(project_root: Path) -> Path:
    """Windows: C:\\aurum-data (keep big data out of the repo). Elsewhere: ./data."""
    if os.name == "nt":  # not sys.platform: mypy would mark one branch unreachable per OS
        return Path("C:/aurum-data")
    return project_root / "data"


@dataclass(frozen=True)
class Settings:
    env: EnvSettings
    config: AppConfig
    project_root: Path
    config_dir: Path
    data_dir: Path

    @property
    def mode(self) -> Mode:
        return self.env.aurum_mode


def load_settings(project_root: Path | None = None, *, env_file: Path | None = None) -> Settings:
    """Load environment + YAML configs. Raises ``ConfigError`` on any invalid value."""
    root = project_root or find_project_root()
    env_path = env_file if env_file is not None else root / ".env"
    try:
        env = EnvSettings(_env_file=env_path if env_path.is_file() else None)
    except ValidationError as exc:
        raise ConfigError(f"invalid environment settings:\n{exc}") from exc
    config_dir = env.aurum_config_dir or root / "configs"
    data_dir = env.aurum_data_dir or default_data_dir(root)
    return Settings(
        env=env,
        config=load_config(config_dir),
        project_root=root,
        config_dir=config_dir,
        data_dir=data_dir,
    )


DEMO_TRADING_ENABLED: Final = False
"""Owner decision (2026-10-09): demo order execution stays disabled until Phase 8, even though
``risk.max_spread_points`` is now calibrated. Phase 8 flips this after sign-off."""


def readiness_issues(settings: Settings) -> list[str]:
    """Reasons the configured mode must not start yet (empty = ready)."""
    issues: list[str] = []
    if settings.mode is Mode.DEMO and not DEMO_TRADING_ENABLED:
        issues.append("demo trading is disabled until Phase 8 (owner decision)")
    if settings.mode in (Mode.DEMO, Mode.LIVE):
        if settings.config.risk.max_spread_points is None:
            issues.append("risk.max_spread_points is not calibrated (Phase 2)")
        if not settings.env.has_mt5_credentials:
            issues.append("MT5_LOGIN / MT5_PASSWORD / MT5_SERVER not set in .env")
    if settings.mode is Mode.LIVE and not settings.config.promotion.live_enabled:
        issues.append("AURUM_MODE=LIVE but promotion.live_enabled is false")
    return issues
