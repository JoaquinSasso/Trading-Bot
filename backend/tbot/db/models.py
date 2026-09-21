"""Modelos ORM de SQLAlchemy para todas las entidades del sistema."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from tbot.db.base import Base


class RuntimeConfig(Base):
    """Parámetros de configuración persistidos en base de datos."""

    __tablename__ = "runtime_config"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConfigHistory(Base):
    """Historial de auditoría de cambios de configuración."""

    __tablename__ = "config_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    old_value: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    new_value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    changed_by: Mapped[str] = mapped_column(String(64), nullable=False)


class UniverseSymbol(Base):
    """Universo de activos con símbolos originales, proxies de ejecución y clusters."""

    __tablename__ = "universe"

    signal_symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    execution_symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    cluster: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DailyBar(Base):
    """Barras diarias históricas consolidadas (SIP diferido o IEX)."""

    __tablename__ = "daily_bars"
    __table_args__ = (
        UniqueConstraint("symbol", "date", "feed", name="uq_daily_bars_symbol_date_feed"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    open: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    adjusted: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    feed: Mapped[str] = mapped_column(String(32), default="sip_delayed", nullable=False)


class RegimeSnapshot(Base):
    """Registro histórico de régimen de mercado detectado."""

    __tablename__ = "regime_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    regime: Mapped[str] = mapped_column(String(32), nullable=False)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    def __init__(
        self,
        *args: Any,
        ts: datetime | None = None,
        timestamp: datetime | None = None,
        regime: str | None = None,
        inputs: dict[str, Any] | None = None,
        spy_close: Decimal | float | None = None,
        sma_200: Decimal | float | None = None,
        realized_vol_20d: float | None = None,
        vol_70th_percentile: float | None = None,
        is_blocked: bool | None = None,
        **kwargs: Any,
    ) -> None:
        effective_ts = ts if ts is not None else timestamp
        effective_inputs = dict(inputs) if inputs is not None else {}
        if spy_close is not None and "spy_close" not in effective_inputs:
            effective_inputs["spy_close"] = float(spy_close)
        if sma_200 is not None and "sma_200" not in effective_inputs:
            effective_inputs["sma_200"] = float(sma_200)
        if realized_vol_20d is not None and "realized_vol_20d" not in effective_inputs:
            effective_inputs["realized_vol_20d"] = float(realized_vol_20d)
        if vol_70th_percentile is not None and "vol_70th_percentile" not in effective_inputs:
            effective_inputs["vol_70th_percentile"] = float(vol_70th_percentile)
        if is_blocked is not None and "is_blocked" not in effective_inputs:
            effective_inputs["is_blocked"] = bool(is_blocked)

        super().__init__(
            *args,
            ts=effective_ts,
            regime=regime,
            inputs=effective_inputs,
            **kwargs,
        )

    @property
    def timestamp(self) -> datetime:
        return self.ts

    @property
    def is_blocked(self) -> bool:
        if self.inputs and "is_blocked" in self.inputs:
            return bool(self.inputs["is_blocked"])
        return self.regime == "UNKNOWN"

    @property
    def spy_close(self) -> Decimal | None:
        val = self.inputs.get("spy_close") if self.inputs else None
        return Decimal(str(val)) if val is not None else None

    @property
    def sma_200(self) -> Decimal | None:
        val = self.inputs.get("sma_200") if self.inputs else None
        return Decimal(str(val)) if val is not None else None

    @property
    def realized_vol_20d(self) -> float | None:
        val = self.inputs.get("realized_vol_20d") if self.inputs else None
        return float(val) if val is not None else None

    @property
    def vol_70th_percentile(self) -> float | None:
        val = self.inputs.get("vol_70th_percentile") if self.inputs else None
        return float(val) if val is not None else None


class MarketEvent(Base):
    """Eventos macroeconómicos y balances corporativos."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    symbol: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    ts_et: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NewsArticle(Base):
    """Artículos y titulares de noticias ingeridas y evaluadas con FinBERT."""

    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    url: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    finbert_label: Mapped[str | None] = mapped_column(String(16), nullable=True)
    finbert_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    whitelisted: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class NewsFeatures(Base):
    """Métricas agregadas de noticias y tópicos en ventanas de tiempo."""

    __tablename__ = "news_features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    window: Mapped[str] = mapped_column(String(16), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    sentiment_mean: Mapped[float] = mapped_column(Float, nullable=False)
    sentiment_min: Mapped[float] = mapped_column(Float, nullable=False)
    negative_share: Mapped[float] = mapped_column(Float, nullable=False)
    sources: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    top_topics: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)


class SignalRecord(Base):
    """Señales deterministas generadas por las estrategias."""

    __tablename__ = "signals"

    signal_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    strategy_version: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    variant: Mapped[str] = mapped_column(String(32), default="A", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class LLMCall(Base):
    """Llamadas de veto al proveedor LLM registradas con entradas y respuestas crudas."""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    request: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    response_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class VetoDecision(Base):
    """Decisión del veto de IA sobre una señal."""

    __tablename__ = "veto_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    variant: Mapped[str] = mapped_column(String(32), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    size_multiplier: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(32), nullable=False)
    llm_call_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RiskDecision(Base):
    """Evaluación del RiskGate sobre la señal aprobada por veto."""

    __tablename__ = "risk_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    variant: Mapped[str] = mapped_column(String(32), nullable=False)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(32), nullable=False)
    sizing: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    portfolio_state: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OrderRecord(Base):
    """Órdenes enviadas al broker con tracking determinístico de client_order_id."""

    __tablename__ = "orders"

    client_order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    owner: Mapped[str] = mapped_column(String(16), nullable=False)
    signal_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    signal_symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    execution_symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    leg: Mapped[str] = mapped_column(String(16), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    tif: Mapped[str] = mapped_column(String(16), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    notional: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    filled_qty: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), default=Decimal("0"), nullable=False
    )
    avg_fill_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)


class PositionMeta(Base):
    """Metadatos de gestión de posiciones abiertas (whole o fractional_fallback)."""

    __tablename__ = "position_meta"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    execution_symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    owner: Mapped[str] = mapped_column(String(16), nullable=False)
    strategy_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    stop_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    tp_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    max_holding: Mapped[str | None] = mapped_column(String(32), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    path: Mapped[str] = mapped_column(String(32), nullable=False)
    exit_at_close: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    unmanaged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class TradeRecord(Base):
    """Operaciones completadas y cerradas con métricas de P&L y P&L en R."""

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    owner: Mapped[str] = mapped_column(String(16), nullable=False)
    strategy_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    exit_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    pnl: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    pnl_r: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    fees_est: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("0"), nullable=False)
    slippage_est: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), default=Decimal("0"), nullable=False
    )
    exit_reason: Mapped[str] = mapped_column(String(64), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class EquitySnapshot(Base):
    """Instantáneas periódicas del balance de la cuenta, capital asignado y P&L."""

    __tablename__ = "equity_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    equity: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    cash: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    bot_exposure: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    manual_exposure: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    pnl_day_bot: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    pnl_day_manual: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)


class Intent(Base):
    """Comandos asíncronos generados por la API para ser procesados por el worker."""

    __tablename__ = "intents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Approval(Base):
    """Aprobaciones de operaciones manuales/bot con tokens HMAC de un solo uso."""

    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AlertRecord(Base):
    """Alertas emitidas por correo electrónico o interfaz web."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(String(32), default="email", nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class ReportRecord(Base):
    """Reportes diarios, semanales y mensuales generados en Markdown."""

    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    period: Mapped[str] = mapped_column(String(16), nullable=False)
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QuotaUsage(Base):
    """Seguimiento de cuotas de consumo por minuto y por día de APIs externas."""

    __tablename__ = "quota_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    minute_bucket: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class AuditLog(Base):
    """Bitácora inmutable de acciones críticas realizadas por usuarios o el bot."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
