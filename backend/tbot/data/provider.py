"""Fachada e implementación principal de AlpacaProvider cumpliendo MarketDataProvider."""

from __future__ import annotations

import inspect
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pandas as pd
import structlog

from tbot.common.clock import Clock, SystemClock
from tbot.common.errors import MarketDataError, RateLimitExceededError
from tbot.config.settings import settings
from tbot.data.calendar import MarketCalendar
from tbot.data.proxies import map_symbol as proxy_map_symbol
from tbot.data.proxies import resolve_data_proxy, resolve_trading_proxy
from tbot.data.rate_limiter import (
    DEFAULT_BACKOFF_POLICY,
    AsyncTokenBucket,
    BackoffPolicy,
    RateLimiter,
    execute_with_retry,
)
from tbot.data.resampler import resample_1m_to_5m
from tbot.data.types import MarketClock, PriceQuote, TradeQuote, TradingDay

if TYPE_CHECKING:
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.trading.client import TradingClient

    from tbot.data.cache import DailyBarCache

logger = structlog.get_logger(__name__)


def _get_bar_field(b: Any, field: str, default: float = 0.0) -> float:
    val = b.get(field, default) if isinstance(b, dict) else getattr(b, field, default)
    return float(val) if val is not None else default


def _get_bar_timestamp(b: Any) -> datetime | None:
    if isinstance(b, dict):
        return b.get("timestamp")
    return getattr(b, "timestamp", None)


class AlpacaProvider:
    """Implementación de MarketDataProvider para Alpaca con arquitectura híbrida (SIP + IEX)."""

    def __init__(
        self,
        clock: Clock | None = None,
        rate_limiter: AsyncTokenBucket | RateLimiter | Any | None = None,
        backoff_policy: BackoffPolicy | None = None,
        historical_client: StockHistoricalDataClient | None = None,
        trading_client: TradingClient | None = None,
        calendar: MarketCalendar | None = None,
        cache_repo: DailyBarCache | Any | None = None,
    ) -> None:
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._rate_limiter = rate_limiter or AsyncTokenBucket(
            rate_limit_per_minute=180.0, clock=self._clock
        )
        self._backoff_policy = backoff_policy or DEFAULT_BACKOFF_POLICY
        self._historical_client = historical_client
        self._trading_client = trading_client
        self._calendar = calendar or MarketCalendar(
            clock=self._clock, trading_client=trading_client
        )
        self._cache_repo = cache_repo

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def rate_limiter(self) -> Any:
        return self._rate_limiter

    def map_symbol(self, symbol: str, direction: str = "signal_to_exec") -> str:
        """Mapeo bidireccional de proxies (SPY->SPYM, QQQ->QQQM, GLD->GLDM)."""
        if direction == "signal_to_exec":
            return resolve_trading_proxy(symbol)
        elif direction == "exec_to_signal":
            return resolve_data_proxy(symbol)
        return proxy_map_symbol(symbol, direction)  # type: ignore[arg-type]

    def clamp_sip_end(self, end: datetime) -> datetime:
        """Aplica la regla de corte SIP: end <= clock.now() - 16 min."""
        now = self._clock.now()
        if end.tzinfo is None and now.tzinfo is not None:
            end = end.replace(tzinfo=now.tzinfo)
        elif end.tzinfo is not None and now.tzinfo is None:
            now = now.replace(tzinfo=end.tzinfo)
        max_allowed = now - timedelta(minutes=16)
        return min(end, max_allowed)

    async def _acquire_rate_limit(self) -> None:
        """Adquiere un token del rate limiter respetando interfaces async y sync."""
        if hasattr(self._rate_limiter, "acquire"):
            if inspect.iscoroutinefunction(self._rate_limiter.acquire):
                await self._rate_limiter.acquire()
            else:
                acquired = self._rate_limiter.acquire(self._clock.now())
                if not acquired:
                    raise RateLimitExceededError("Rate limit exceeded (180 req/min budget)")

    def _ensure_historical_client(self) -> StockHistoricalDataClient:
        if self._historical_client is None:
            api_key = settings.ALPACA_API_KEY
            secret_key = settings.ALPACA_SECRET_KEY
            if not api_key or not secret_key:
                raise MarketDataError(
                    "Alpaca credentials not configured (ALPACA_API_KEY / ALPACA_SECRET_KEY missing)"
                )
            from alpaca.data.historical import StockHistoricalDataClient

            self._historical_client = StockHistoricalDataClient(
                api_key=api_key, secret_key=secret_key
            )
        return self._historical_client

    async def get_daily_bars(
        self,
        symbols: list[str],
        start: date,
        end: date,
        adjusted: bool = True,
    ) -> pd.DataFrame:
        """Obtiene barras diarias históricas consolidadas (SIP diferido)."""
        cols = ["symbol", "date", "open", "high", "low", "close", "volume", "adjusted", "feed"]
        if not symbols or start > end:
            return pd.DataFrame(columns=cols)

        # Si hay cache configurado, usar estrategia de cache + missing ranges
        if self._cache_repo is not None:
            return await self._cache_repo.get_or_fetch_daily_bars(
                symbols=symbols,
                start=start,
                end=end,
                feed="sip_delayed",
                adjusted=adjusted,
                fetch_fn=self._fetch_daily_bars_from_api,
            )

        # Sin cache, consultar directo a la API
        raw_bars = await self._fetch_daily_bars_from_api(
            symbols, start, end, "sip_delayed", adjusted
        )
        if not raw_bars:
            return pd.DataFrame(columns=cols)
        df = pd.DataFrame(raw_bars)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df.drop_duplicates(subset=["symbol", "date"], keep="last")
        return df[cols].sort_values(by=["symbol", "date"]).reset_index(drop=True)

    async def _fetch_daily_bars_from_api(
        self,
        symbols: list[str],
        start: date,
        end: date,
        feed: str,
        adjusted: bool,
    ) -> list[dict[str, Any]]:
        """Consulta barras diarias directamente a la API de Alpaca."""
        await self._acquire_rate_limit()

        client = self._ensure_historical_client()
        from alpaca.data.enums import Adjustment, DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        start_dt = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
        end_dt = datetime.combine(end, datetime.max.time(), tzinfo=UTC)
        adj = Adjustment.ALL if adjusted else Adjustment.RAW
        alpaca_feed = DataFeed.SIP if feed in ("sip", "sip_delayed") else DataFeed.IEX

        # Mapear símbolos de ejecución a señales/datos si corresponde
        mapped_to_orig = {self.map_symbol(s, "exec_to_signal"): s for s in symbols}
        query_symbols = list(mapped_to_orig.keys())

        req = StockBarsRequest(
            symbol_or_symbols=query_symbols,
            timeframe=TimeFrame.Day,
            start=start_dt,
            end=end_dt,
            adjustment=adj,
            feed=alpaca_feed,
        )

        bars_data = await execute_with_retry(
            client.get_stock_bars,
            req,
            policy=self._backoff_policy,
        )

        return self._extract_daily_bar_records(bars_data, mapped_to_orig, adjusted, feed)

    def _extract_daily_bar_records(
        self,
        bars_data: Any,
        mapped_to_orig: dict[str, str],
        adjusted: bool,
        feed: str,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []

        # Caso DataFrame de Alpaca (MultiIndex symbol, timestamp)
        df_raw = getattr(bars_data, "df", None)
        if df_raw is not None and not df_raw.empty:
            df = df_raw.reset_index()
            time_col = "timestamp" if "timestamp" in df.columns else "time"
            for _, row in df.iterrows():
                query_sym = str(row["symbol"])
                orig_sym = mapped_to_orig.get(query_sym, query_sym)
                ts = row[time_col]
                d = ts.date() if hasattr(ts, "date") else pd.to_datetime(ts).date()
                records.append(
                    {
                        "symbol": orig_sym,
                        "date": d,
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row["volume"]),
                        "adjusted": adjusted,
                        "feed": feed,
                    }
                )
            return records

        # Caso diccionario o BarSet.data
        data_dict = getattr(bars_data, "data", {})
        if isinstance(bars_data, dict):
            data_dict = bars_data

        for query_sym, bar_list in data_dict.items():
            orig_sym = mapped_to_orig.get(query_sym, query_sym)
            for b in bar_list:
                ts = _get_bar_timestamp(b)
                d = ts.date() if hasattr(ts, "date") else pd.to_datetime(ts).date()
                records.append(
                    {
                        "symbol": orig_sym,
                        "date": d,
                        "open": _get_bar_field(b, "open", 0.0),
                        "high": _get_bar_field(b, "high", 0.0),
                        "low": _get_bar_field(b, "low", 0.0),
                        "close": _get_bar_field(b, "close", 0.0),
                        "volume": _get_bar_field(b, "volume", 0.0),
                        "adjusted": adjusted,
                        "feed": feed,
                    }
                )

        return records

    async def get_intraday_bars(
        self,
        symbols: list[str],
        timeframe: str,
        start: datetime,
        end: datetime,
        feed: str = "iex",
        adjusted: bool = True,
    ) -> pd.DataFrame:
        """Obtiene barras intradía (1m o 5m) con soporte de feed híbrido SIP e IEX."""
        empty_cols = [
            "symbol",
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "vwap",
            "trade_count",
            "adjusted",
            "feed",
            "is_synthetic",
        ]
        if not symbols or start > end:
            return pd.DataFrame(columns=empty_cols)

        # Validar y normalizar zonas horarias (UTC)
        start_utc = start.astimezone(UTC) if start.tzinfo else start.replace(tzinfo=UTC)
        end_utc = end.astimezone(UTC) if end.tzinfo else end.replace(tzinfo=UTC)

        norm_feed = "sip_delayed" if feed in ("sip", "sip_delayed") else "iex"

        # Aplicar regla de corte SIP
        if norm_feed == "sip_delayed":
            clamped_end = self.clamp_sip_end(end_utc)
            if start_utc >= clamped_end:
                # Ventana completamente dentro de los 16 min de embargo -> retorno vacío inmediato
                return pd.DataFrame(columns=empty_cols)
            eff_end = clamped_end
        else:
            eff_end = end_utc

        if start_utc >= eff_end:
            return pd.DataFrame(columns=empty_cols)

        await self._acquire_rate_limit()

        client = self._ensure_historical_client()
        from alpaca.data.enums import Adjustment, DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        # Para 5m en IEX: consultar 1m y agregar localmente
        query_timeframe_1m = timeframe == "5m" and norm_feed == "iex"
        if query_timeframe_1m or timeframe == "1m":
            alpaca_tf = TimeFrame.Minute
        elif timeframe == "5m":
            alpaca_tf = TimeFrame(5, TimeFrameUnit.Minute)
        elif timeframe == "15m":
            alpaca_tf = TimeFrame(15, TimeFrameUnit.Minute)
        else:
            tf_num = int(timeframe[:-1]) if timeframe[:-1].isdigit() else 1
            alpaca_tf = TimeFrame(tf_num, TimeFrameUnit.Minute)

        alpaca_feed = DataFeed.SIP if norm_feed == "sip_delayed" else DataFeed.IEX
        adj = Adjustment.ALL if adjusted else Adjustment.RAW

        mapped_to_orig = {self.map_symbol(s, "exec_to_signal"): s for s in symbols}
        query_symbols = list(mapped_to_orig.keys())

        req = StockBarsRequest(
            symbol_or_symbols=query_symbols,
            timeframe=alpaca_tf,
            start=start_utc,
            end=eff_end,
            adjustment=adj,
            feed=alpaca_feed,
        )

        bars_data = await execute_with_retry(
            client.get_stock_bars,
            req,
            policy=self._backoff_policy,
        )

        df = self._normalize_intraday_bars(bars_data, mapped_to_orig, adjusted, norm_feed)

        # Si se solicitó 5m en IEX, agregar localmente
        if query_timeframe_1m and not df.empty:
            df = resample_1m_to_5m(df)
            df["adjusted"] = adjusted
            df["feed"] = norm_feed

        return df

    def _normalize_intraday_bars(
        self,
        bars_data: Any,
        mapped_to_orig: dict[str, str],
        adjusted: bool,
        feed: str,
    ) -> pd.DataFrame:
        cols = [
            "symbol",
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "vwap",
            "trade_count",
            "adjusted",
            "feed",
            "is_synthetic",
        ]
        records: list[dict[str, Any]] = []

        df_raw = getattr(bars_data, "df", None)
        if df_raw is not None and not df_raw.empty:
            df = df_raw.reset_index()
            time_col = "timestamp" if "timestamp" in df.columns else "time"
            for _, row in df.iterrows():
                query_sym = str(row["symbol"])
                orig_sym = mapped_to_orig.get(query_sym, query_sym)
                ts = row[time_col]
                ts_utc = (
                    ts.astimezone(UTC)
                    if hasattr(ts, "astimezone")
                    else pd.to_datetime(ts, utc=True)
                )
                records.append(
                    {
                        "symbol": orig_sym,
                        "timestamp": ts_utc,
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row["volume"]),
                        "vwap": float(row.get("vwap", row["close"])),
                        "trade_count": int(row.get("trade_count", 0)),
                        "adjusted": adjusted,
                        "feed": feed,
                        "is_synthetic": False,
                    }
                )
            return (
                pd.DataFrame(records)[cols]
                .sort_values(by=["symbol", "timestamp"])
                .reset_index(drop=True)
            )

        data_dict = getattr(bars_data, "data", {})
        if isinstance(bars_data, dict):
            data_dict = bars_data

        for query_sym, bar_list in data_dict.items():
            orig_sym = mapped_to_orig.get(query_sym, query_sym)
            for b in bar_list:
                ts = _get_bar_timestamp(b)
                ts_utc = (
                    ts.astimezone(UTC)
                    if hasattr(ts, "astimezone") and ts.tzinfo
                    else pd.to_datetime(ts, utc=True)
                )
                close_val = _get_bar_field(b, "close", 0.0)
                vwap_val = _get_bar_field(b, "vwap", close_val)
                trade_count_val = int(_get_bar_field(b, "trade_count", 0.0))
                records.append(
                    {
                        "symbol": orig_sym,
                        "timestamp": ts_utc,
                        "open": _get_bar_field(b, "open", 0.0),
                        "high": _get_bar_field(b, "high", 0.0),
                        "low": _get_bar_field(b, "low", 0.0),
                        "close": close_val,
                        "volume": _get_bar_field(b, "volume", 0.0),
                        "vwap": vwap_val,
                        "trade_count": trade_count_val,
                        "adjusted": adjusted,
                        "feed": feed,
                        "is_synthetic": False,
                    }
                )

        if not records:
            return pd.DataFrame(columns=cols)
        return (
            pd.DataFrame(records)[cols]
            .sort_values(by=["symbol", "timestamp"])
            .reset_index(drop=True)
        )

    async def get_latest_quote(self, symbol: str) -> PriceQuote:
        """Obtiene la última cotización bid/ask en tiempo real desde el feed IEX."""
        await self._acquire_rate_limit()

        client = self._ensure_historical_client()
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockLatestQuoteRequest

        clean_sym = symbol.strip().upper()
        query_sym = self.map_symbol(clean_sym, "exec_to_signal")

        req = StockLatestQuoteRequest(symbol_or_symbols=query_sym, feed=DataFeed.IEX)

        resp = await execute_with_retry(
            client.get_stock_latest_quote,
            req,
            policy=self._backoff_policy,
        )

        quote = resp.get(query_sym) if hasattr(resp, "get") else getattr(resp, query_sym, None)
        if quote is None:
            raise MarketDataError(f"No quote returned for symbol {clean_sym}")

        ts = quote.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)

        return PriceQuote(
            symbol=clean_sym,
            bid=Decimal(str(quote.bid_price)),
            ask=Decimal(str(quote.ask_price)),
            bid_size=int(quote.bid_size),
            ask_size=int(quote.ask_size),
            timestamp=ts,
        )

    async def get_latest_trade(self, symbol: str) -> TradeQuote:
        """Obtiene el último trade reportado en tiempo real desde el feed IEX."""
        await self._acquire_rate_limit()

        client = self._ensure_historical_client()
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockLatestTradeRequest

        clean_sym = symbol.strip().upper()
        query_sym = self.map_symbol(clean_sym, "exec_to_signal")

        req = StockLatestTradeRequest(symbol_or_symbols=query_sym, feed=DataFeed.IEX)

        resp = await execute_with_retry(
            client.get_stock_latest_trade,
            req,
            policy=self._backoff_policy,
        )

        trade = resp.get(query_sym) if hasattr(resp, "get") else getattr(resp, query_sym, None)
        if trade is None:
            raise MarketDataError(f"No trade returned for symbol {clean_sym}")

        ts = trade.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)

        return TradeQuote(
            symbol=clean_sym,
            price=Decimal(str(trade.price)),
            size=int(trade.size),
            timestamp=ts,
        )

    async def get_market_clock(self) -> MarketClock:
        """Retorna el estado y proyección temporal del reloj de mercado."""
        return self._calendar.get_market_clock(self._clock.now())

    async def get_calendar(self, start: date, end: date) -> list[TradingDay]:
        """Retorna las sesiones de negociación hábiles en el rango dado."""
        return self._calendar.get_calendar(start, end)
