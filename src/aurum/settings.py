"""Configuration: environment (.env) + YAML files in ``configs/``. Invalid config fails loudly.

* Every YAML model forbids unknown keys, so typos are errors, not silently ignored.
* Clock times in YAML must be quoted ``"HH:MM"`` strings (unquoted ``07:00`` is a YAML 1.1
  base-60 integer and would otherwise be misread).
* Safety-relevant caps (e.g. risk per trade <= 1%) are validated here as a second line of defence.
"""

import re
import sys
from dataclasses import dataclass
from datetime import date, time
from itertools import pairwise
from pathlib import Path
from typing import Annotated, Any, Literal, Self
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


class CommissionConfig(_Config):
    per_lot_per_side: float = Field(ge=0)
    """Account-currency commission per 1.0 lot per side (Fusion Zero: 2.25)."""
    currency: str = "USD"

    @property
    def per_lot_round_trip(self) -> float:
        return 2 * self.per_lot_per_side


class SlippageConfig(_Config):
    model: Literal["fixed_points"] = "fixed_points"
    points_per_side: float = Field(ge=0)


class BrokerConfig(_Config):
    name: str
    account_type: str
    account_currency: str = "USD"
    symbol_candidates: list[str] = Field(min_length=1)
    symbol_search_terms: list[str] = Field(min_length=1)
    commission: CommissionConfig
    slippage: SlippageConfig


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
        for name in ("broker", "sessions", "risk", "promotion")
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
    """Windows: C:\\aurum-data (keep big data off the shared folder). Elsewhere: ./data."""
    if sys.platform == "win32":
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


def readiness_issues(settings: Settings) -> list[str]:
    """Reasons the configured mode must not start yet (empty = ready)."""
    issues: list[str] = []
    if settings.mode in (Mode.DEMO, Mode.LIVE):
        if settings.config.risk.max_spread_points is None:
            issues.append("risk.max_spread_points is not calibrated (Phase 2)")
        if not settings.env.has_mt5_credentials:
            issues.append("MT5_LOGIN / MT5_PASSWORD / MT5_SERVER not set in .env")
    if settings.mode is Mode.LIVE and not settings.config.promotion.live_enabled:
        issues.append("AURUM_MODE=LIVE but promotion.live_enabled is false")
    return issues
