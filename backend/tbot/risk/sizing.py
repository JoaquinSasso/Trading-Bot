"""Motor de dimensionamiento de posición (Sizing) con priorización de acciones enteras y proxies."""

from __future__ import annotations

import math
from decimal import Decimal

from tbot.data.proxies import resolve_trading_proxy
from tbot.risk.models import SizingResult


def calculate_position_size(
    signal_symbol: str,
    entry_price: Decimal,
    stop_price: Decimal,
    take_profit_price: Decimal | None,
    bot_equity: Decimal,
    available_buying_power: Decimal,
    risk_per_trade_pct: float = 0.5,
    max_risk_per_trade_pct_hard: float = 1.0,
    size_multiplier: float = 1.0,
    allow_fractional_bot: bool = True,
    max_position_notional: Decimal = Decimal("400.0"),
    min_order_notional: Decimal = Decimal("10.0"),
    proxy_price: Decimal | None = None,
) -> SizingResult | None:
    """Calcula la cantidad de acciones a operar siguiendo la política de riesgo y proxies.

    Prioridad:
    1. Si el activo tiene un proxy de menor valor nominal (ej: SPY -> SPYM), se utiliza
       el proxy para facilitar la operativa de acciones enteras y brackets nativos GTC.
    2. Se arriesga: Dollar Risk = bot_equity * min(risk_pct, max_hard) * size_multiplier.
    3. Si las acciones enteras floor(Risk / Delta) >= 1, se elige el camino 'whole'.
    4. Si 1 acción entera supera el riesgo tolerado pero no excede max_risk_per_trade_pct_hard,
       se puede redondear a 1 acción entera.
    5. Si se requieren fraccionales (< 1 acción o para no superar el riesgo duro), y
       allow_fractional_bot=True, se usa el camino 'fractional_fallback'.
    6. Se respeta estrictamente:
       - No apalancamiento: notional <= available_buying_power
       - Tope nocional: notional <= max_position_notional
       - Mínimo nocional: notional >= min_order_notional
    """
    if entry_price <= Decimal("0") or stop_price <= Decimal("0"):
        return None

    # Determinar si se usa proxy de bajo costo nominal
    exec_symbol = resolve_trading_proxy(signal_symbol)
    exec_entry = entry_price
    exec_stop = stop_price
    exec_tp = take_profit_price

    if exec_symbol != signal_symbol and proxy_price is not None and proxy_price > Decimal("0"):
        # Escalar precios proporcionalmente al valor nominal del proxy
        ratio = proxy_price / entry_price
        exec_entry = proxy_price
        exec_stop = (stop_price * ratio).quantize(Decimal("0.0001"))
        if take_profit_price is not None:
            exec_tp = (take_profit_price * ratio).quantize(Decimal("0.0001"))

    risk_per_unit = exec_entry - exec_stop
    if risk_per_unit <= Decimal("0"):
        return None  # Stop inválido o superior a la entrada para orden larga

    # Riesgo objetivo en USD
    effective_risk_pct = min(risk_per_trade_pct, max_risk_per_trade_pct_hard)
    target_risk_usd = (
        bot_equity * Decimal(str(effective_risk_pct / 100.0)) * Decimal(str(size_multiplier))
    )
    hard_max_risk_usd = bot_equity * Decimal(str(max_risk_per_trade_pct_hard / 100.0))

    if target_risk_usd <= Decimal("0"):
        return None

    # 1. Intentar cálculo de acciones enteras
    exact_shares = target_risk_usd / risk_per_unit
    whole_shares_int = math.floor(float(exact_shares))

    # Verificar si podemos redondear a 1 acción entera si el riesgo de 1 acción está bajo el hard max
    if whole_shares_int == 0:
        single_share_risk = risk_per_unit
        if single_share_risk <= hard_max_risk_usd:
            # Tolerable redondear a 1 acción entera
            whole_shares_int = 1

    # Capacidad máxima por efectivo y tope nocional
    effective_max_notional = min(max_position_notional, available_buying_power)

    if whole_shares_int >= 1:
        whole_shares = Decimal(str(whole_shares_int))
        notional = whole_shares * exec_entry

        # Si el nocional supera el disponible o el tope, reducir cantidad
        if notional > effective_max_notional:
            max_affordable_shares = math.floor(float(effective_max_notional / exec_entry))
            whole_shares = Decimal(str(max_affordable_shares))
            notional = whole_shares * exec_entry

        if whole_shares >= Decimal("1"):
            if notional < min_order_notional:
                return None
            actual_risk = whole_shares * risk_per_unit
            return SizingResult(
                signal_symbol=signal_symbol,
                execution_symbol=exec_symbol,
                shares=whole_shares,
                is_fractional=False,
                entry_price=exec_entry,
                stop_price=exec_stop,
                take_profit_price=exec_tp,
                risk_usd=actual_risk.quantize(Decimal("0.01")),
                notional_usd=notional.quantize(Decimal("0.01")),
                path="whole",
            )

    # 2. Fallback fraccional si está permitido
    if allow_fractional_bot:
        fractional_shares = (target_risk_usd / risk_per_unit).quantize(Decimal("0.0001"))
        notional = fractional_shares * exec_entry

        # Ajustar si supera poder de compra
        if notional > effective_max_notional:
            fractional_shares = (effective_max_notional / exec_entry).quantize(Decimal("0.0001"))
            notional = fractional_shares * exec_entry

        if notional < min_order_notional or fractional_shares <= Decimal("0"):
            return None

        actual_risk = fractional_shares * risk_per_unit
        return SizingResult(
            signal_symbol=signal_symbol,
            execution_symbol=exec_symbol,
            shares=fractional_shares,
            is_fractional=True,
            entry_price=exec_entry,
            stop_price=exec_stop,
            take_profit_price=exec_tp,
            risk_usd=actual_risk.quantize(Decimal("0.01")),
            notional_usd=notional.quantize(Decimal("0.01")),
            path="fractional_fallback",
        )

    return None
