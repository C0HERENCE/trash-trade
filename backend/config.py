from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseModel):
    env: str = "dev"
    timezone: str = "UTC"
    log_level: str = "INFO"


class WsReconnectConfig(BaseModel):
    max_retries: int = 0
    base_delay_ms: int = 500
    max_delay_ms: int = 10000


class BinanceConfig(BaseModel):
    rest_base: str = "https://fapi.binance.com"
    ws_base: str = "wss://fstream.binance.com"
    symbol: str = "BTCUSDT"
    intervals: List[str] = Field(default_factory=lambda: ["15m", "1h"])
    ws_reconnect: WsReconnectConfig = Field(default_factory=WsReconnectConfig)


class SimConfig(BaseModel):
    initial_capital: float = 1000.0
    max_leverage: int = 20
    fee_rate: float = 0.0004
    slippage: float = 0.0


class RiskConfig(BaseModel):
    max_position_notional: float = 20000.0
    max_position_pct_equity: float = 1.0
    liquidation_buffer_pct: float = 0.0
    allow_reduce_only: bool = True
    mmr_tiers: List[Dict[str, float]] = Field(
        default_factory=lambda: [
            {"notional_usdt": 5000, "mmr": 0.004, "maint_amount": 0},
            {"notional_usdt": 50000, "mmr": 0.004, "maint_amount": 0},
            {"notional_usdt": 250000, "mmr": 0.005, "maint_amount": 50},
            {"notional_usdt": 1000000, "mmr": 0.01, "maint_amount": 900},
            {"notional_usdt": 1_000_000_000, "mmr": 0.025, "maint_amount": 10000},
        ]
    )


class IndicatorConfig(BaseModel):
    rsi: Dict[str, Any] = Field(default_factory=lambda: {"length": 14})
    ema_fast: Dict[str, Any] = Field(default_factory=lambda: {"length": 12})
    ema_slow: Dict[str, Any] = Field(default_factory=lambda: {"length": 26})
    macd: Dict[str, Any] = Field(default_factory=lambda: {"fast": 12, "slow": 26, "signal": 9})
    atr: Dict[str, Any] = Field(default_factory=lambda: {"length": 14})
    ema_trend: Dict[str, Any] = Field(default_factory=lambda: {"fast": 20, "slow": 60})


class CooldownConfig(BaseModel):
    enabled: bool = True
    bars_after_exit: int = 2
    min_ms_after_exit: int = 900000


class StrategyConfig(BaseModel):
    trend_strength_min: float = 0.003
    atr_stop_mult: float = 1.5
    cooldown_after_stop: int = 4
    rsi_long_lower: float = 50.0
    rsi_long_upper: float = 60.0
    rsi_short_upper: float = 50.0
    rsi_short_lower: float = 40.0
    rsi_slope_required: bool = True


class StrategyEntryConfig(BaseModel):
    id: str = "default"
    type: str = "test"  # test | ma_cross
    initial_capital: Optional[float] = None
    config_path: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)


class KlineCacheConfig(BaseModel):
    max_bars_15m: int = 2000
    max_bars_1h: int = 2000
    warmup_extra_bars: int = 200
    warmup_buffer_mult: float = 3.0


class TelegramAlertConfig(BaseModel):
    enabled: bool = False
    token: str = ""
    chat_id: str = ""


class BarkAlertConfig(BaseModel):
    enabled: bool = False
    url: str = ""
    key: str = ""


class WeComAlertConfig(BaseModel):
    enabled: bool = False
    webhook: str = ""


class AlertsConfig(BaseModel):
    enabled: bool = True
    dedup_ttl_ms: int = 300000
    telegram: TelegramAlertConfig = Field(default_factory=TelegramAlertConfig)
    bark: BarkAlertConfig = Field(default_factory=BarkAlertConfig)
    wecom: WeComAlertConfig = Field(default_factory=WeComAlertConfig)


class StorageConfig(BaseModel):
    sqlite_path: str = "./db/app.db"


class ApiConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    cors_allow_origins: List[str] = Field(default_factory=lambda: ["*"])
    ws_push_interval: str = "raw"  # "raw" or seconds number as string
    base_path: str = ""


class FrontendConfig(BaseModel):
    static_path: str = "./frontend/dist"
    dev_server_url: str = "http://localhost:5173"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        extra="ignore",
    )

    app: AppConfig = Field(default_factory=AppConfig)
    binance: BinanceConfig = Field(default_factory=BinanceConfig)
    sim: SimConfig = Field(default_factory=SimConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    indicators: IndicatorConfig = Field(default_factory=IndicatorConfig)
    cooldown: CooldownConfig = Field(default_factory=CooldownConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    strategies: List[StrategyEntryConfig] = Field(default_factory=lambda: [StrategyEntryConfig()])
    kline_cache: KlineCacheConfig = Field(default_factory=KlineCacheConfig)
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    frontend: FrontendConfig = Field(default_factory=FrontendConfig)

    @field_validator("binance")
    @classmethod
    def _validate_intervals(cls, v: BinanceConfig) -> BinanceConfig:
        if not v.intervals:
            raise ValueError("binance.intervals must not be empty")
        return v

    @field_validator("sim")
    @classmethod
    def _validate_sim(cls, v: SimConfig) -> SimConfig:
        if v.initial_capital <= 0:
            raise ValueError("sim.initial_capital must be > 0")
        if v.max_leverage <= 0:
            raise ValueError("sim.max_leverage must be > 0")
        if v.fee_rate < 0:
            raise ValueError("sim.fee_rate must be >= 0")
        return v

def load_settings(config_path: Optional[str] = None) -> Settings:
    path = Path(config_path or "./configs/config.yaml")
    data: Dict[str, Any] = {}
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded

    def deep_update(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                deep_update(dst[k], v)
            else:
                dst[k] = v
        return dst

    def env_overrides() -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        valid_roots = set(Settings.model_fields.keys())
        for key, value in os.environ.items():
            if "__" not in key:
                continue
            parts = [p.strip().lower() for p in key.split("__") if p.strip()]
            if not parts or parts[0] not in valid_roots:
                continue
            cur = out
            for part in parts[:-1]:
                nxt = cur.get(part)
                if not isinstance(nxt, dict):
                    nxt = {}
                    cur[part] = nxt
                cur = nxt
            cur[parts[-1]] = value
        return out

    merged = deep_update(data, env_overrides())
    return Settings(**merged)
