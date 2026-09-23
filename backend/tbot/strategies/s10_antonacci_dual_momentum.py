"""Estrategia S5: Dual Momentum Leader (Rotación de Líderes con Filtro de Régimen y Trailing Stop EMA25).

Estrategia cuantitativa de momentum transversal y preservación de capital:
1. Filtro Macro / Régimen: Solo opera en regímenes alcistas (BULL_CALM, BULL_VOLATILE).
   Si el régimen es bajista (SPY < SMA200/EMA50), mantiene 100% en efectivo remunerado (BIL).
2. Momentum Transversal (Cross-Sectional): Calcula la fuerza relativa a 45 sesiones en el universo
   y selecciona los N activos líderes (por defecto top 4, cap 25%) que coticen sobre su EMA(25).
3. Asimetría de Retorno (Let Winners Run): Trailing Stop dinámico anclado a la EMA(25)
   con buffer del 3.5%, con límite máximo de retención de 30 sesiones de mercado.
4. Nota de Gobernanza (Decisión D-01): El veto FinBERT fue retirado del camino crítico de ejecución
   en producción; la toma de decisiones es 100% determinista basada en precio y volumen.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from tbot.indicators.pure import ema
from tbot.regime.filter import MarketRegime
from tbot.strategies.interfaces import (
    Signal,
    StrategyContext,
    StrategyDataRequirements,
    compute_signal_id,
)


class AntonacciDualMomentumStrategy:
    """Implementación de S10 - Dual Momentum (Gary Antonacci)."""

    id: str = "antonacci_dual_momentum"
    version: str = "1.0.0"
    schedule: list[str] = ["15:45 America/New_York"]
    allowed_regimes: set[MarketRegime] = {
        MarketRegime.BULL_CALM,
        MarketRegime.BULL_VOLATILE,
        MarketRegime.BEAR, # Antonacci maneja el bear market refugiándose en IEF
    }
    allows_open_window: bool = False

    DEFAULT_UNIVERSE: list[str] = [
        "SPY", "QQQ", "IEF"
    ]

    data_requirements: StrategyDataRequirements = StrategyDataRequirements(
        needs_daily_bars=True,
        daily_lookback_days=260,  # 252 sesiones de momentum
        needs_intraday_bars=False,
        requires_sip_delayed=True,
    )

    def __init__(
        self,
        momentum_lookback_days: int = 252,
        top_n_leaders: int = 1,
        stop_buffer_pct: float = 0.15,  # Stop muy amplio, rebalanceo mensual
        max_holding_sessions: int = 20,  # Rebalanceo mensual
        universe: list[str] | None = None,
    ) -> None:
        self.universe = list(universe) if universe is not None else list(self.DEFAULT_UNIVERSE)
        self.momentum_lookback_days = momentum_lookback_days
        self.top_n_leaders = top_n_leaders
        self.stop_buffer_pct = stop_buffer_pct
        self.max_holding_sessions = max_holding_sessions
        self.max_holding_days = self.max_holding_sessions
        self.safe_asset = "IEF"

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        """Aplica momentum relativo y absoluto."""
        # Se ejecuta sin importar el régimen general, ya que IEF protege.

        target_universe = self.universe if self.universe is not None else list(ctx.current_prices.keys())
        ranked_candidates: list[tuple[str, float, float]] = []

        safe_asset = self.safe_asset
        safe_asset_return = 0.0

        if safe_asset in ctx.daily_bars and safe_asset in ctx.current_prices:
            daily_df_safe = ctx.daily_bars[safe_asset]
            if len(daily_df_safe) >= self.momentum_lookback_days + 2:
                closes_safe = daily_df_safe["close"].astype(float).copy()
                cur_safe_f = float(ctx.current_prices[safe_asset])
                if float(closes_safe.iloc[-1]) != cur_safe_f:
                    closes_safe.iloc[-1] = cur_safe_f
                p_past_safe = float(closes_safe.iloc[-(self.momentum_lookback_days + 1)])
                if p_past_safe > 0:
                    safe_asset_return = (cur_safe_f - p_past_safe) / p_past_safe

        for symbol in target_universe:
            daily_df = ctx.daily_bars.get(symbol)
            current_price = ctx.current_prices.get(symbol)

            if daily_df is None or current_price is None:
                continue
            if len(daily_df) < self.momentum_lookback_days + 2:
                continue

            closes = daily_df["close"].astype(float).copy()
            cur_price_f = float(current_price)
            if float(closes.iloc[-1]) != cur_price_f:
                closes.iloc[-1] = cur_price_f

            p_past = float(closes.iloc[-(self.momentum_lookback_days + 1)])
            if p_past <= 0:
                continue
            mom_12m = (cur_price_f - p_past) / p_past

            ranked_candidates.append((symbol, mom_12m, cur_price_f))

        # Ordenar descendente por momentum (fuerza relativa)
        ranked_candidates.sort(key=lambda x: x[1], reverse=True)

        selected_symbol = safe_asset
        selected_price = 0.0
        max_mom = -999.0
        
        # Dual Momentum Absoluto: el mejor activo supera al activo seguro?
        for sym, mom, price in ranked_candidates:
            if sym != safe_asset:
                if mom > safe_asset_return and mom > 0:
                    selected_symbol = sym
                    selected_price = price
                    max_mom = mom
                break

        if selected_symbol == safe_asset:
            if safe_asset in ctx.current_prices:
                selected_price = float(ctx.current_prices[safe_asset])
                max_mom = safe_asset_return
            else:
                return []

        if selected_symbol in ctx.portfolio_positions:
            return []

        stop_price_f = selected_price * (1.0 - self.stop_buffer_pct)

        sig_id = compute_signal_id(self.id, self.version, selected_symbol, ctx.now)
        
        return [
            Signal(
                signal_id=sig_id,
                strategy_id=self.id,
                symbol=selected_symbol,
                side="buy",
                entry_type="limit",
                entry_price_ref=Decimal(str(round(selected_price, 4))),
                stop_price=Decimal(str(round(stop_price_f, 4))),
                created_at=ctx.now,
                limit_price=Decimal(str(round(selected_price, 4))),
                take_profit_price=None,
                max_holding=timedelta(days=self.max_holding_sessions),
                exit_at_close=False,
                score=1.0,
            )
        ]
