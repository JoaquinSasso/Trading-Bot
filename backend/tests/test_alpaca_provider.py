"""Unit tests for AlpacaProvider facade, SIP clamping, IEX aggregation, and rate limiter."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pandas as pd
import pytest

from tbot.common.clock import SimulatedClock
from tbot.common.errors import ProviderUnavailableError, RateLimitExceededError
from tbot.data.calendar import MarketCalendar
from tbot.data.interfaces import MarketDataProvider
from tbot.data.provider import AlpacaProvider
from tbot.data.rate_limiter import AsyncTokenBucket, BackoffPolicy
from tbot.data.types import MarketClock, PriceQuote, TradeQuote, TradingDay


class MockAlpacaBar:
    def __init__(
        self,
        timestamp: datetime,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        vwap: float | None = None,
        trade_count: int = 10,
    ) -> None:
        self.timestamp = timestamp
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.vwap = vwap if vwap is not None else close
        self.trade_count = trade_count


class MockBarSet:
    def __init__(self, data: dict[str, list[MockAlpacaBar]]) -> None:
        self.data = data
        self.df = None


class MockQuote:
    def __init__(
        self, bid_price: float, ask_price: float, bid_size: int, ask_size: int, timestamp: datetime
    ) -> None:
        self.bid_price = bid_price
        self.ask_price = ask_price
        self.bid_size = bid_size
        self.ask_size = ask_size
        self.timestamp = timestamp


class MockTrade:
    def __init__(self, price: float, size: int, timestamp: datetime) -> None:
        self.price = price
        self.size = size
        self.timestamp = timestamp


@pytest.fixture
def sim_clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 9, 21, 14, 0, 0, tzinfo=UTC))


def test_alpaca_provider_protocol_conformance(sim_clock: SimulatedClock) -> None:
    provider = AlpacaProvider(clock=sim_clock)
    assert isinstance(provider, MarketDataProvider)


def test_sip_clamping_logic(sim_clock: SimulatedClock) -> None:
    provider = AlpacaProvider(clock=sim_clock)

    now = sim_clock.now()
    # 5 minutes ago -> inside 16m blackout -> must clamp to now - 16m
    t_5m_ago = now - timedelta(minutes=5)
    clamped = provider.clamp_sip_end(t_5m_ago)
    assert clamped == now - timedelta(minutes=16)

    # 30 minutes ago -> outside blackout -> untouched
    t_30m_ago = now - timedelta(minutes=30)
    assert provider.clamp_sip_end(t_30m_ago) == t_30m_ago


@pytest.mark.asyncio
async def test_sip_clamping_early_empty_return(sim_clock: SimulatedClock) -> None:
    # Request entirely inside 16m blackout: [now - 10m, now - 5m]
    now = sim_clock.now()
    start = now - timedelta(minutes=10)
    end = now - timedelta(minutes=5)

    mock_client = MagicMock()
    limiter = AsyncTokenBucket(clock=sim_clock)
    provider = AlpacaProvider(clock=sim_clock, historical_client=mock_client, rate_limiter=limiter)

    df = await provider.get_intraday_bars(
        symbols=["SPY"],
        timeframe="1m",
        start=start,
        end=end,
        feed="sip_delayed",
    )

    assert df.empty
    # Must NOT have made any API calls or consumed rate limit tokens
    assert mock_client.get_stock_bars.call_count == 0
    assert limiter.available_tokens == 180.0


@pytest.mark.asyncio
async def test_intraday_bars_iex_local_5m_aggregation(sim_clock: SimulatedClock) -> None:
    now = sim_clock.now()
    start = now - timedelta(minutes=10)
    end = now

    # Generate 5 one-minute mock bars
    base_time = start
    bars_1m = [
        MockAlpacaBar(
            base_time + timedelta(minutes=i), 500.0 + i, 501.0 + i, 499.0 + i, 500.5 + i, 100.0
        )
        for i in range(5)
    ]
    mock_barset = MockBarSet({"SPY": bars_1m})

    mock_client = MagicMock()
    mock_client.get_stock_bars.return_value = mock_barset

    provider = AlpacaProvider(clock=sim_clock, historical_client=mock_client)
    df_5m = await provider.get_intraday_bars(
        symbols=["SPY"],
        timeframe="5m",
        start=start,
        end=end,
        feed="iex",
    )

    assert not df_5m.empty
    assert len(df_5m) == 1
    # Check 1m -> 5m aggregation
    assert df_5m.iloc[0]["open"] == 500.0
    assert df_5m.iloc[0]["close"] == 504.5
    assert df_5m.iloc[0]["volume"] == 500.0
    assert df_5m.iloc[0]["timestamp"] == pd.Timestamp(base_time + timedelta(minutes=5))


@pytest.mark.asyncio
async def test_get_daily_bars_direct_and_proxy_mapping(sim_clock: SimulatedClock) -> None:
    d1 = date(2026, 9, 18)
    bars_daily = [
        MockAlpacaBar(
            datetime(2026, 9, 18, 20, 0, 0, tzinfo=UTC), 500.0, 505.0, 498.0, 502.0, 1000000.0
        )
    ]
    mock_barset = MockBarSet({"SPY": bars_daily})

    mock_client = MagicMock()
    mock_client.get_stock_bars.return_value = mock_barset

    provider = AlpacaProvider(clock=sim_clock, historical_client=mock_client)

    # Query with proxy execution symbol SPYM -> mapped to SPY for query
    df = await provider.get_daily_bars(
        symbols=["SPYM"],
        start=d1,
        end=d1,
        adjusted=True,
    )

    assert len(df) == 1
    assert df.iloc[0]["symbol"] == "SPYM"
    assert df.iloc[0]["date"] == d1
    assert df.iloc[0]["close"] == 502.0


@pytest.mark.asyncio
async def test_get_latest_quote(sim_clock: SimulatedClock) -> None:
    now = sim_clock.now()
    mock_client = MagicMock()
    mock_client.get_stock_latest_quote.return_value = {
        "SPY": MockQuote(
            bid_price=500.10, ask_price=500.20, bid_size=10, ask_size=15, timestamp=now
        )
    }

    provider = AlpacaProvider(clock=sim_clock, historical_client=mock_client)
    quote = await provider.get_latest_quote("SPYM")  # Translates to SPY for API

    assert isinstance(quote, PriceQuote)
    assert quote.symbol == "SPYM"
    assert quote.bid == Decimal("500.10")
    assert quote.ask == Decimal("500.20")
    assert quote.midpoint == Decimal("500.15")
    assert quote.bid_size == 10
    assert quote.ask_size == 15


@pytest.mark.asyncio
async def test_get_latest_trade(sim_clock: SimulatedClock) -> None:
    now = sim_clock.now()
    mock_client = MagicMock()
    mock_client.get_stock_latest_trade.return_value = {
        "SPY": MockTrade(price=500.15, size=100, timestamp=now)
    }

    provider = AlpacaProvider(clock=sim_clock, historical_client=mock_client)
    trade = await provider.get_latest_trade("SPY")

    assert isinstance(trade, TradeQuote)
    assert trade.symbol == "SPY"
    assert trade.price == Decimal("500.15")
    assert trade.size == 100


@pytest.mark.asyncio
async def test_rate_limit_exceeded_error_on_drain(sim_clock: SimulatedClock) -> None:
    limiter = AsyncTokenBucket(rate_limit_per_minute=180.0, clock=sim_clock)
    # Drain all 180 tokens
    for _ in range(180):
        limiter.acquire_now()

    mock_client = MagicMock()
    provider = AlpacaProvider(clock=sim_clock, historical_client=mock_client, rate_limiter=limiter)
    assert provider.rate_limiter is limiter

    with pytest.raises(RateLimitExceededError):
        await limiter.acquire(tokens=1.0, blocking=False)


@pytest.mark.asyncio
async def test_market_clock_and_calendar_delegation(sim_clock: SimulatedClock) -> None:
    days = MarketCalendar.generate_standard_trading_days(date(2026, 1, 1), date(2026, 12, 31))
    calendar = MarketCalendar(clock=sim_clock, initial_days=days)
    provider = AlpacaProvider(clock=sim_clock, calendar=calendar)

    clock_res = await provider.get_market_clock()
    assert isinstance(clock_res, MarketClock)
    assert clock_res.timestamp == sim_clock.now()

    cal_res = await provider.get_calendar(date(2026, 9, 21), date(2026, 9, 25))
    assert isinstance(cal_res, list)
    assert all(isinstance(d, TradingDay) for d in cal_res)


@pytest.mark.asyncio
async def test_backoff_exhaustion_on_persistent_500(sim_clock: SimulatedClock) -> None:
    mock_client = MagicMock()
    mock_client.get_stock_latest_quote.side_effect = Exception("HTTP 500 Internal Server Error")

    policy = BackoffPolicy(max_retries=1, jitter=False)
    provider = AlpacaProvider(
        clock=sim_clock,
        historical_client=mock_client,
        backoff_policy=policy,
    )

    with pytest.raises(ProviderUnavailableError):
        await provider.get_latest_quote("SPY")
