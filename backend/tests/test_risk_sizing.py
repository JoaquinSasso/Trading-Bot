"""Tests unitarios para el dimensionamiento de posición (Sizing) y proxies."""

from decimal import Decimal

from tbot.risk.sizing import calculate_position_size


def test_sizing_whole_shares_with_proxy() -> None:
    # SPY cotiza a $590. Si operáramos SPY directo, 1 acción arriesga ~$10 (con stop a $580).
    # Con SPYM proxy cotizando a ~$70 y stop a $68.80 (delta ~$1.20):
    # Capital bot = $2,000. Riesgo objetivo 0.5% = $10 USD.
    # Shares = floor(10 / 1.20) = 8 acciones de SPYM.
    # Nocional = 8 * $70 = $560 (si max_notional es $600 y cash es $1000).
    sizing = calculate_position_size(
        signal_symbol="SPY",
        entry_price=Decimal("590.00"),
        stop_price=Decimal("580.00"),
        take_profit_price=Decimal("610.00"),
        bot_equity=Decimal("2000.00"),
        available_buying_power=Decimal("1000.00"),
        risk_per_trade_pct=0.5,
        max_position_notional=Decimal("600.00"),
        proxy_price=Decimal("70.00"),
    )

    assert sizing is not None
    assert sizing.execution_symbol == "SPYM"
    assert sizing.path == "whole"
    assert sizing.is_fractional is False
    assert sizing.shares >= Decimal("1")
    assert sizing.notional_usd <= Decimal("600.00")
    assert sizing.entry_price == Decimal("70.00")
    assert sizing.stop_price < sizing.entry_price
    assert sizing.take_profit_price is not None
    assert sizing.take_profit_price > sizing.entry_price


def test_sizing_fractional_fallback_when_allowed() -> None:
    # Acción cara sin proxy (ej. NVDA a $140 con stop a $120, delta $20).
    # Capital = $1,000. Riesgo 0.5% = $5 USD.
    # 1 acción entera arriesga $20 (2.0%), lo que supera hard max (1.0% = $10).
    # Se debe activar el camino fractional_fallback.
    sizing = calculate_position_size(
        signal_symbol="NVDA",
        entry_price=Decimal("140.00"),
        stop_price=Decimal("120.00"),
        take_profit_price=Decimal("180.00"),
        bot_equity=Decimal("1000.00"),
        available_buying_power=Decimal("500.00"),
        risk_per_trade_pct=0.5,
        max_risk_per_trade_pct_hard=1.0,
        allow_fractional_bot=True,
    )

    assert sizing is not None
    assert sizing.path == "fractional_fallback"
    assert sizing.is_fractional is True
    # 5 / 20 = 0.25 acciones
    assert sizing.shares == Decimal("0.2500")
    # Nocional = 0.25 * 140 = $35.00
    assert sizing.notional_usd == Decimal("35.00")
    assert sizing.risk_usd == Decimal("5.00")


def test_sizing_rejects_when_fractional_disabled_and_whole_exceeds_hard_risk() -> None:
    # Igual que el anterior pero allow_fractional_bot = False
    sizing = calculate_position_size(
        signal_symbol="NVDA",
        entry_price=Decimal("140.00"),
        stop_price=Decimal("120.00"),
        take_profit_price=Decimal("180.00"),
        bot_equity=Decimal("1000.00"),
        available_buying_power=Decimal("500.00"),
        risk_per_trade_pct=0.5,
        max_risk_per_trade_pct_hard=1.0,
        allow_fractional_bot=False,
    )

    assert sizing is None


def test_sizing_ai_multiplier_reduces_size() -> None:
    # Multiplicador del veto de IA = 0.5
    sizing_full = calculate_position_size(
        signal_symbol="AAPL",
        entry_price=Decimal("100.00"),
        stop_price=Decimal("95.00"),
        take_profit_price=Decimal("110.00"),
        bot_equity=Decimal("2000.00"),
        available_buying_power=Decimal("1000.00"),
        risk_per_trade_pct=0.5,  # $10 USD
        size_multiplier=1.0,
    )
    sizing_half = calculate_position_size(
        signal_symbol="AAPL",
        entry_price=Decimal("100.00"),
        stop_price=Decimal("95.00"),
        take_profit_price=Decimal("110.00"),
        bot_equity=Decimal("2000.00"),
        available_buying_power=Decimal("1000.00"),
        risk_per_trade_pct=0.5,  # $10 USD * 0.5 = $5 USD
        size_multiplier=0.5,
    )

    assert sizing_full is not None
    assert sizing_half is not None
    assert sizing_half.shares < sizing_full.shares
    assert sizing_half.risk_usd < sizing_full.risk_usd
